"""Permissions and permanent removal for drawing-level and marker actions."""
import json
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Motor, PdfMarker, PdfPlan, Plan, SavedProject, WorkbookDocument
from .test_pdf import pdf_upload
from .tests import dxf_file


class PlanActionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = get_user_model().objects.create_user('actions-reader')
        cls.admin = get_user_model().objects.create_superuser('actions-admin', password='test')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='plan-actions-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.reader)

    def grant(self, *names):
        self.reader.user_permissions.set(Permission.objects.filter(content_type__app_label='mobileApp', codename__in=names))

    def post(self, url, data=None, client=None):
        return (client or self.client).post(url, json.dumps(data or {}), content_type='application/json')

    def import_plan(self, kind):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'), {
            'file': pdf_upload() if kind == 'pdf' else dxf_file(),
        })
        self.assertEqual(response.status_code, 201, response.content[:300])
        return response.json()

    def test_creator_can_save_both_formats_without_legacy_permissions(self):
        for kind in ('pdf', 'dxf'):
            plan = self.import_plan(kind)
            self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
            self.assertEqual(self.client.get(plan['export_project_url']).status_code, 200)
            self.assertTrue(SavedProject.objects.exists())
            self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
            self.assertEqual(SavedProject.objects.count(), 1)
            SavedProject.objects.all().delete()

    def test_legacy_format_permissions_do_not_limit_creator_save_rights(self):
        pdf, cad = self.import_plan('pdf'), self.import_plan('dxf')
        for permission in ('add_pdfplan', 'change_pdfplan', 'add_pdfmarker', 'change_pdfmarker'):
            self.grant(permission)
            self.assertEqual(self.post(pdf['save_project_url']).status_code, 200)
            self.assertEqual(self.post(cad['save_project_url']).status_code, 200)

    def test_plan_deletion_by_creator_removes_saved_copy_and_file(self):
        for kind, model, children in (('pdf', PdfPlan, PdfMarker), ('dxf', Plan, Motor)):
            with self.subTest(kind=kind):
                self.client.force_login(self.reader)
                self.grant('add_plan')
                plan = self.import_plan(kind)
                if kind == 'pdf':
                    self.post(plan['markers_url'], {'page': 1, 'x': .4, 'y': .4})
                self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
                source = Path(model.objects.get(pk=plan['id']).source.path)
                self.grant('change_plan')
                self.assertTrue(source.exists())
                self.grant('delete_pdfplan' if kind == 'pdf' else 'delete_plan')
                self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
                saved = self.client.get(reverse('saved_projects')).json()['plans'][0]
                self.assertEqual(saved['delete_url'], plan['delete_url'])
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(self.post(plan['delete_url']).status_code, 200)
                self.assertFalse(source.exists())
                self.assertFalse(model.objects.filter(pk=plan['id']).exists())
                self.assertFalse(children.objects.filter(plan_id=plan['id']).exists())
                self.assertEqual(self.client.get(reverse('saved_projects')).json()['plans'], [])
                self.assertEqual(self.client.get(plan['detail_url']).status_code, 404)
                self.grant('add_plan')
                self.assertEqual(self.post(plan['save_project_url']).status_code, 404)
                # A new import is a new working plan; it is listed only on Save.
                replacement = self.import_plan(kind)
                self.assertEqual(self.client.get(reverse('saved_projects')).json()['plans'], [])
                self.assertEqual(self.post(replacement['save_project_url']).status_code, 200)
                self.assertEqual(len(self.client.get(reverse('saved_projects')).json()['plans']), 1)
                self.client.force_login(self.admin)
                with self.captureOnCommitCallbacks(execute=True):
                    self.post(replacement['delete_url'])

    def test_deleting_markers_preserves_drawing_other_buttons_and_workbook(self):
        for kind in ('pdf', 'dxf'):
            plan = self.import_plan(kind)
            if kind == 'pdf':
                marker = self.post(plan['markers_url'], {'page': 1, 'x': .2, 'y': .3}).json()['marker']
                model, workbook_id = PdfMarker, marker['workbook']['id']
            else:
                marker = plan['motors'][0]
                model, workbook_id = Motor, marker['excel']['id']
            count = model.objects.filter(plan_id=plan['id']).count()
            self.grant('delete_pdfmarker' if kind == 'pdf' else 'delete_motor')
            self.assertEqual(self.post(marker['delete_url']).status_code, 200)
            self.assertFalse(model.objects.filter(pk=marker['id']).exists())
            self.assertEqual(model.objects.filter(plan_id=plan['id']).count(), count - 1)
            self.assertTrue(WorkbookDocument.objects.filter(pk=workbook_id).exists())
            self.assertEqual(self.client.get(plan['detail_url']).status_code, 200)
            self.assertEqual(self.client.get(marker['delete_url']).status_code, 405)
            self.grant()

    def test_deletion_is_authoritative_for_other_devices_and_stale_saves(self):
        deleting_device = Client()
        deleting_device.force_login(self.admin)
        other_device = Client()
        other_device.force_login(self.reader)
        self.grant('add_plan')
        for kind in ('pdf', 'dxf'):
            with self.subTest(kind=kind):
                plan = self.import_plan(kind)
                # Unsaved imports must not be treated as deleted by live checks.
                response = other_device.get(reverse('saved_projects'), {kind: plan['id']})
                self.assertTrue(response.json()['availability'][kind]['exists'])
                self.assertEqual(response.json()['plans'], [])
                self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
                saved = other_device.get(reverse('saved_projects')).json()['plans'][0]
                self.assertEqual(other_device.get(plan['detail_url']).status_code, 200)
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(self.post(plan['delete_url'], client=deleting_device).status_code, 200)
                state = other_device.get(reverse('saved_projects'), {kind: plan['id']})
                self.assertEqual(state.json()['plans'], [])
                self.assertEqual(state.json()['availability'][kind], {'id': plan['id'], 'exists': False})
                self.assertIn('no-store', state.headers['Cache-Control'])
                for endpoint in (plan['detail_url'], saved['export_url'], plan['export_project_url']):
                    self.assertEqual(other_device.get(endpoint).status_code, 404)
                self.assertEqual(self.post(plan['save_project_url'], client=other_device).status_code, 404)
                self.assertEqual(other_device.get(reverse('saved_projects')).json()['plans'], [])

    def test_availability_checks_require_login_and_valid_plan_ids(self):
        self.assertEqual(Client().get(reverse('saved_projects'), {'pdf': 1}).status_code, 401)
        for value in ('', 'abc', '-1', '0', '1.5'):
            self.assertEqual(self.client.get(reverse('saved_projects'), {'pdf': value}).status_code, 400)
        pdf, dxf = self.import_plan('pdf'), self.import_plan('dxf')
        state = self.client.get(reverse('saved_projects'), {'pdf': pdf['id'], 'dxf': dxf['id']}).json()
        self.assertEqual(state['availability'], {'pdf': {'id': pdf['id'], 'exists': True}, 'dxf': {'id': dxf['id'], 'exists': True}})

    def test_marker_deletion_retains_csrf_and_authentication_requirements(self):
        plan = self.import_plan('dxf')
        url = plan['motors'][0]['delete_url']
        self.assertEqual(self.post(url, client=Client()).status_code, 401)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        self.assertEqual(self.post(url, client=csrf).status_code, 403)
