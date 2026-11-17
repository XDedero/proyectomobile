from io import BytesIO
from pathlib import Path

from django.test import TestCase
from PIL import Image

from .models import CompanyBrand, RELONCAVI_COMPANY_ID


class PwaResourcesTests(TestCase):
    def test_manifest_and_iphone_icon(self):
        response = self.client.get('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        manifest = response.json()
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['start_url'], '/')
        root = Path(__file__).parent / 'static' / 'mobileApp' / 'img' / 'pwa'
        for name, size in [('apple-touch-icon', 180), ('icon-192', 192), ('icon-512', 512)]:
            with Image.open(root / f'{name}.png') as icon:
                self.assertEqual(icon.size, (size, size))
                self.assertEqual(icon.mode, 'RGB')

    def test_worker_root_scope_and_cache_policy(self):
        response = self.client.get('/service-worker.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('application/javascript', response['Content-Type'])
        self.assertNotIn(b'{{', response.content)
        self.assertContains(response, "url.pathname.startsWith('/static/mobileApp/')")

    def test_custom_brand_supplies_sized_installation_icons(self):
        logo = BytesIO()
        Image.new('RGBA', (40, 20), 'red').save(logo, format='PNG')
        CompanyBrand.objects.create(company_id=RELONCAVI_COMPANY_ID, base_color='#fedc00', logo_png=logo.getvalue())
        item = self.client.get('/manifest.webmanifest').json()
        response = self.client.get(item['icons'][0]['src'])
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as icon:
            self.assertEqual(icon.size, (192, 192))
        self.assertContains(self.client.get('/'), '/marca/icono/180.png?v=1')

    def test_shell_declares_installation_resources(self):
        response = self.client.get('/')
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, 'rel="apple-touch-icon"')
        self.assertContains(response, 'mobileApp/pwa-offline.js')
        self.assertContains(response, 'viewport-fit=cover')

    def test_anonymous_api_cannot_be_http_cached(self):
        response = self.client.get('/api/auth/session/')
        self.assertEqual(response['X-Reloncavi-User'], '')
        self.assertIn('no-store', response['Cache-Control'])

    def test_outbox_rejects_a_different_server_account(self):
        response = self.client.post('/api/projects/pdf/1/sectors/', {},
                                    HTTP_X_RELONCAVI_EXPECTED_USER='123')
        self.assertEqual(response.status_code, 409)
