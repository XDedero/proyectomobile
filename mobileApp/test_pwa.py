from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from PIL import Image, ImageChops, ImageStat

from .models import CompanyBrand, RELONCAVI_COMPANY_ID


class PwaResourcesTests(TestCase):
    def assert_same_artwork(self, expected, image):
        difference = ImageChops.difference(expected, image)
        self.assertLess(max(ImageStat.Stat(difference).mean), 1)

    def test_manifest_and_iphone_icon(self):
        response = self.client.get('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        manifest = response.json()
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual((manifest['name'], manifest['short_name']), ('planimetry cl', 'planimetry cl'))
        self.assertEqual(manifest['id'], '/')
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['start_url'], '/')
        root = Path(__file__).parent / 'static' / 'mobileApp' / 'img' / 'pwa'
        icons = [(Path(urlsplit(icon['src']).path).name, icon['sizes'], icon['purpose']) for icon in manifest['icons']]
        self.assertEqual(icons, [
            ('icon-192.png', '192x192', 'any'), ('icon-512.png', '512x512', 'any'),
            ('icon-512-maskable.png', '512x512', 'maskable'),
        ])
        with Image.open(root / 'logo_app.jpeg') as source:
            for name, size in [('apple-touch-icon', 180), ('icon-192', 192), ('icon-512', 512)]:
                with Image.open(root / f'{name}.png') as icon:
                    self.assertEqual(icon.size, (size, size))
                    self.assertEqual(icon.mode, 'RGB')
                    scaled = source.resize(icon.size, Image.Resampling.LANCZOS)
                    self.assert_same_artwork(scaled, icon)
            with Image.open(root / 'icon-512-maskable.png') as icon:
                self.assertEqual(icon.size, (512, 512))
                self.assertEqual(icon.mode, 'RGB')
                artwork = source.resize((328, 328), Image.Resampling.LANCZOS)
                self.assert_same_artwork(artwork, icon.crop((92, 92, 420, 420)))

    def test_worker_root_scope_and_cache_policy(self):
        response = self.client.get('/service-worker.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('application/javascript', response['Content-Type'])
        self.assertNotIn(b'{{', response.content)
        self.assertContains(response, "url.pathname.startsWith('/static/mobileApp/')")

    def test_installation_icons_are_independent_of_the_company_logo(self):
        manifest = self.client.get('/manifest.webmanifest').json()
        logo = BytesIO()
        Image.new('RGBA', (40, 20), 'red').save(logo, format='PNG')
        CompanyBrand.objects.create(company_id=RELONCAVI_COMPANY_ID, base_color='#fedc00', logo_png=logo.getvalue())
        guest = self.client.get('/')
        self.assertContains(guest, 'name="apple-mobile-web-app-title" content="planimetry cl"')
        self.assertContains(guest, 'href="/static/mobileApp/img/pwa/apple-touch-icon.png?')
        self.assertContains(guest, 'data-product-brand')
        self.assertNotContains(guest, '/marca/logo.png')
        self.assertTrue(self.client.get('/api/auth/session/').json()['appearance']['is_product_brand'])

        self.client.force_login(get_user_model().objects.create_user(username='instalacion'))
        signed_in = self.client.get('/')
        self.assertContains(signed_in, 'name="apple-mobile-web-app-title" content="planimetry cl"')
        self.assertContains(signed_in, 'src="/marca/logo.png?v=1"')
        self.assertEqual(self.client.get('/manifest.webmanifest').json(), manifest)
        self.assertEqual(self.client.get('/marca/logo.png').content, logo.getvalue())
        logged_out = self.client.post('/api/auth/logout/').json()
        self.assertIsNone(logged_out['user'])
        self.assertTrue(logged_out['appearance']['is_product_brand'])

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
