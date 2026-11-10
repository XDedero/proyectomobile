import gzip
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import pwa
from .access import capabilities
from .models import Plan, WorkbookDocument
from .plan_workbook import empty_workbook
from .views import plan_data
from .web_performance import CompressTextMiddleware


class TransferTests(SimpleTestCase):
    def test_large_private_json_compresses_without_changing_values_or_privacy(self):
        data = {'cells': [{'row': row, 'value': 'Registro de mantenimiento'} for row in range(500)]}
        response = JsonResponse(data)
        original = response.content
        response['Cache-Control'] = 'private, no-store'
        response['Vary'] = 'Cookie'
        request = RequestFactory().get('/api/pdf/workbooks/1/cells/', HTTP_ACCEPT_ENCODING='gzip')
        result = CompressTextMiddleware(lambda request: response)(request)
        self.assertEqual(result['Content-Encoding'], 'gzip')
        self.assertEqual(json.loads(gzip.decompress(result.content)), data)
        self.assertLess(len(result.content), len(original) / 2)
        self.assertEqual(result['Cache-Control'], 'private, no-store')
        self.assertIn('Cookie', result['Vary'])
        self.assertIn('Accept-Encoding', result['Vary'])

    def test_auth_and_binary_downloads_keep_their_original_transfer(self):
        for path, response in [
            ('/api/auth/session/', JsonResponse({'token': 'x' * 1000})),
            ('/api/pdf/plans/1/source.pdf', HttpResponse(b'%PDF-' + b'x' * 1000, content_type='application/pdf')),
            ('/api/pdf/workbooks/1/download/', StreamingHttpResponse([b'PK' + b'x' * 1000], content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
        ]:
            with self.subTest(path=path):
                request = RequestFactory().get(path, HTTP_ACCEPT_ENCODING='gzip')
                result = CompressTextMiddleware(lambda request: response)(request)
                self.assertIs(result, response)
                self.assertNotIn('Content-Encoding', result)

    @override_settings(SECURE_SSL_REDIRECT=True, DEBUG=False)
    def test_render_health_check_is_public_and_needs_no_database_or_https_redirect(self):
        self.assertEqual(self.client.get('/healthz/').json(), {'status': 'ok'})
        self.assertEqual(self.client.get('/').status_code, 301)

    def test_production_shell_and_worker_reuse_version_but_development_detects_edits(self):
        pwa._production_asset_version.cache_clear()
        self.addCleanup(pwa._production_asset_version.cache_clear)
        with override_settings(DEBUG=False), patch.object(pwa, '_calculate_asset_version', return_value='123') as calculate:
            self.assertEqual(pwa.asset_version(), '123')
            self.assertEqual(pwa.asset_version(), '123')
            self.assertEqual(calculate.call_count, 1)
        with override_settings(DEBUG=True), patch.object(pwa, '_calculate_asset_version', side_effect=['123', '456']):
            self.assertEqual(pwa.asset_version(), '123')
            self.assertEqual(pwa.asset_version(), '456')


class PlanReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('web-performance')
        cls.book = WorkbookDocument.objects.create(name='Plan.xlsx', content=empty_workbook(), plan_mode='template', updated_by=cls.user)
        cls.svg = '<svg xmlns="http://www.w3.org/2000/svg"><!--' + 'x' * 100000 + '--></svg>'
        cls.plan = Plan.objects.create(name='Plano grande', source='plans/fixture.dxf', size=100,
                                       svg=cls.svg, workbook=cls.book, imported_by=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def test_metadata_omits_the_drawing_from_sql_and_preserves_the_entire_response(self):
        expected = plan_data(Plan.objects.get(pk=self.plan.pk))
        expected['permissions'] = capabilities(self.user, self.plan)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/api/plans/')
        self.assertEqual(response.json(), json.loads(JsonResponse({'plans': [expected]}).content))
        self.assertFalse(any('"mobileApp_plan"."svg"' in item['sql'] for item in queries))
        with CaptureQueriesContext(connection) as queries:
            detail = self.client.get(f'/api/plans/{self.plan.pk}/')
        self.assertEqual(detail.json()['id'], self.plan.pk)
        self.assertFalse(any('"mobileApp_plan"."svg"' in item['sql'] for item in queries))
        source = self.client.get(f'/api/plans/{self.plan.pk}/drawing.svg')
        self.assertEqual(source.content.decode(), self.svg)

    def test_workbook_permissions_do_not_read_the_excel_binary_a_second_time(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f'/api/pdf/workbooks/{self.book.pk}/metadata/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], self.book.pk)
        self.assertEqual(sum('"mobileApp_workbookdocument"."content"' in item['sql'] for item in queries), 1)
        self.assertFalse(any('"mobileApp_plan"."svg"' in item['sql'] for item in queries))
