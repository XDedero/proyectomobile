"""Ownership, administrative boundaries and replacement of stale downloads."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .access import can_edit_plan
from .cloud_projects import CloudUnavailable, open_cloud_project, workbook_version
from .models import ButtonCategory, Plan, PdfPlan, PlanRole, PlanRoleGroup, SavedProject, WorkbookDocument
from .test_pdf import pdf_upload, excel_upload
from .tests import dxf_file


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ManagementAndVersionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user('creador', password='clave')
        cls.other = User.objects.create_user('otro', password='clave')
        cls.staff = User.objects.create_user('personal', password='clave', is_staff=True)
        cls.staff.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=['auth', 'mobileApp']).exclude(codename='administer_application'))
        cls.admin = User.objects.create_superuser('administrador', password='clave')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='plan-management-')
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable(); self.addCleanup(settings.disable)
        self.client.force_login(self.owner)

    def post(self, url, data=None):
        return self.client.post(url, data or {}, content_type='application/json')

    def create_plan(self, kind='pdf'):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'), {'file': pdf_upload() if kind == 'pdf' else dxf_file()})
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()

    def test_creator_without_legacy_permissions_can_edit_save_and_delete_both_formats(self):
        for kind in ('pdf', 'dxf'):
            plan = self.create_plan(kind)
            self.assertTrue(plan['permissions']['can_change_plan'])
            self.assertEqual(self.post(plan['update_url'], {'name': 'Mi plano'}).status_code, 200)
            self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
            self.assertEqual(self.post(plan['delete_url']).status_code, 200)

    def test_other_user_with_legacy_permissions_cannot_mutate_any_part_of_plan(self):
        for kind in ('pdf', 'dxf'):
            self.client.force_login(self.owner)
            plan = self.create_plan(kind)
            marker = self.post(plan['markers_url'], {'page': 1, 'x': .2, 'y': .3}).json()['marker'] if kind == 'pdf' else plan['motors'][0]
            self.client.force_login(self.staff)
            detail = self.client.get(plan['detail_url']).json()
            self.assertFalse(detail['permissions']['can_save_plan'])
            for url, data in [(plan['update_url'], {'name': 'Ajeno'}), (plan['save_project_url'], {}),
                              (plan['delete_url'], {}), (plan['markers_url'], {}), (plan['workbook_url'], {'action': 'template'}),
                              (plan['assign_listing_url'], {'action': 'add'}), (marker['assign_url'], {'blank': True}),
                              (marker['delete_url'], {}), (marker['details_url'], {'name': 'Otro'})]:
                with self.subTest(kind=kind, url=url):
                    self.assertEqual(self.post(url, data).status_code, 403)

    def test_excel_edits_follow_plan_ownership_and_metadata_matches(self):
        plan = self.create_plan()
        book = self.client.get(plan['workbook_url']).json()['workbook']
        self.assertTrue(self.client.get(book['metadata_url']).json()['can_edit'])
        self.client.force_login(self.other)
        self.assertFalse(self.client.get(book['metadata_url']).json()['can_edit'])
        self.assertEqual(self.post(book['save_url'], {'revision': 1}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertTrue(self.client.get(book['metadata_url']).json()['can_edit'])

    def test_administration_and_django_user_permissions_are_admin_only(self):
        for user in (self.owner, self.other, self.staff):
            self.client.force_login(user)
            for name in ('management_panel', 'management_data'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            for name in ('manage_role', 'manage_role_group', 'manage_category'):
                self.assertEqual(self.post(reverse(name), {'name': 'Prohibido'}).status_code, 403)
            self.assertEqual(self.client.get('/admin/auth/user/').status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('management_panel')).status_code, 200)
        self.assertEqual(self.client.get(reverse('management_data')).status_code, 200)

    def test_scoped_role_in_group_controls_views_export_and_import_without_granting_edit(self):
        allowed = self.create_plan(); denied = self.create_plan()
        self.client.force_login(self.admin)
        role = self.post(reverse('manage_role'), {'name': 'Consulta de un plano', 'description': '',
            'can_view': True, 'can_export': False, 'can_create': False, 'all_plans': False,
            'plans': [f"pdf:{allowed['id']}"], 'users': []}).json()
        group = self.post(reverse('manage_role_group'), {'name': 'Turno A', 'roles': [role['id']], 'users': [self.other.pk]})
        self.assertEqual(group.status_code, 200, group.content)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(allowed['detail_url']).status_code, 200)
        self.assertEqual(self.client.get(denied['detail_url']).status_code, 403)
        self.assertEqual(self.client.get(allowed['export_project_url']).status_code, 403)
        self.assertEqual(self.client.get(allowed['workbook']['download_url']).status_code, 403)
        self.assertEqual(self.post(allowed['save_project_url']).status_code, 403)
        self.assertEqual(self.client.post(reverse('import_pdf'), {'file': pdf_upload()}).status_code, 403)
        self.assertEqual([p['id'] for p in self.client.get(reverse('list_pdf_plans')).json()['plans']], [allowed['id']])

    def test_group_without_roles_grants_no_shared_access_or_creation(self):
        plan = self.create_plan()
        group = PlanRoleGroup.objects.create(name='Pendiente de asignación')
        group.users.add(self.other)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(plan['detail_url']).status_code, 403)
        self.assertEqual(self.client.post(reverse('import_pdf'), {'file': pdf_upload()}).status_code, 403)

    def test_category_is_a_label_and_deleting_it_preserves_the_button_workbook(self):
        self.client.force_login(self.admin)
        url = reverse('manage_category')
        response = self.post(url, {'name': 'Válvulas'})
        self.assertEqual(response.status_code, 200, response.content)
        category = response.json()
        self.assertEqual(set(category), {'id', 'name'})
        self.assertIn(category, self.client.get(reverse('management_data')).json()['categories'])
        self.client.force_login(self.owner)
        plan = self.create_plan()
        self.assertIn(category, plan['button_categories'])
        placement = {'page': 1, 'x': .1, 'y': .2, 'origin': 'upload', 'category_id': category['id']}
        response = self.client.post(plan['markers_url'], {'placement': json.dumps(placement), 'file': excel_upload()})
        self.assertEqual(response.status_code, 201, response.content)
        marker = response.json()['marker']
        self.assertEqual(marker['category_name'], 'Válvulas')
        record = PdfPlan.objects.get(pk=plan['id']).markers.get(pk=marker['id'])
        workbook_id, original = record.source_workbook_id, bytes(record.source_workbook.content)
        self.assertEqual(self.post(url, {'name': 'No autorizado'}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.post(url, {'id': category['id'], 'name': 'Válvulas hidráulicas'}).status_code, 200)
        self.assertEqual(record.category.name, 'Válvulas hidráulicas')
        self.assertEqual(self.post(url, {'id': category['id'], 'action': 'delete'}).status_code, 200)
        record.refresh_from_db()
        self.assertIsNone(record.category_id)
        self.assertEqual(record.source_workbook_id, workbook_id)
        self.assertEqual(bytes(record.source_workbook.content), original)

    def test_legacy_category_workbook_upload_and_selection_are_rejected(self):
        category = ButtonCategory.objects.create(name='Sensores')
        self.client.force_login(self.admin)
        response = self.client.post(reverse('manage_category'), {'id': category.pk, 'name': 'Bombas', 'file': excel_upload()})
        self.assertEqual(response.status_code, 400)
        category.refresh_from_db()
        self.assertEqual(category.name, 'Sensores')
        self.client.force_login(self.owner)
        plan = self.create_plan()
        response = self.post(plan['markers_url'], {'page': 1, 'x': .1, 'y': .2, 'origin': 'category',
                                                   'category_id': str(category.pk), 'template_id': 1})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PdfPlan.objects.get(pk=plan['id']).markers.exists())

    def test_local_version_detects_pending_update_for_pdf_and_dxf(self):
        for kind in ('pdf', 'dxf'):
            plan = self.create_plan(kind)
            first = self.post(plan['save_project_url']).json()['saved_at']
            second = self.post(plan['save_project_url']).json()['saved_at']
            self.assertNotEqual(first, second)
            self.client.force_login(self.other)
            result = self.client.get(plan['version_url'], {'version': first}).json()
            self.assertTrue(result['stale']); self.assertEqual(result['latest_version'], second)
            refreshed = self.post(result['refresh_url'])
            self.assertEqual(refreshed.status_code, 200)
            self.assertEqual(refreshed.json()['plan']['project_version'], second)
            self.client.force_login(self.owner)

    def remote_snapshot(self):
        project = {'id': 'remote-pdf', 'name': 'Actualizado.pdf', 'date': '2026-11-07T22:00:00Z',
                   'excelId': 'remote-excel', 'excelName': 'Equipos.xlsx', 'projectKey': 'remote-project'}
        return project, pdf_upload().read(), excel_upload().read(), None, None, ''

    def test_remote_version_check_reports_pending_download_without_deleting_old_copy(self):
        plan = self.create_plan(); obj = PdfPlan.objects.get(pk=plan['id'])
        obj.cloud_origin = 'remote-pdf'; obj.cloud_workbook_version = 'remote-excel:old'; obj.save()
        self.client.force_login(self.other)
        with patch('mobileApp.cloud_projects.cloud_project_reference', return_value=self.remote_snapshot()[0]):
            result = self.client.get(plan['version_url']).json()
        self.assertTrue(result['stale']); self.assertTrue(result['remote'])
        self.assertTrue(PdfPlan.objects.filter(pk=obj.pk).exists())

    def test_successful_cloud_refresh_removes_old_file_and_keeps_role_scope(self):
        plan = self.create_plan(); old = PdfPlan.objects.get(pk=plan['id'])
        original_file = Path(old.source.path); original_workbook = old.workbook_id
        old.cloud_origin = 'remote-pdf'; old.cloud_workbook_version = 'remote-excel:old'; old.save()
        role = PlanRole.objects.create(name='Plano autorizado', all_plans=False); role.pdf_plans.add(old)
        with patch('mobileApp.cloud_projects.read_cloud_snapshot', return_value=self.remote_snapshot()):
            with self.captureOnCommitCallbacks(execute=True):
                new, _ = open_cloud_project('remote-pdf', self.other, replace_plan=old)
        self.assertFalse(PdfPlan.objects.filter(pk=plan['id']).exists())
        self.assertFalse(original_file.exists())
        self.assertFalse(WorkbookDocument.objects.filter(pk=original_workbook).exists())
        self.assertTrue(Path(new.source.path).exists())
        self.assertIn(new, role.pdf_plans.all())
        self.assertFalse(can_edit_plan(self.other, new))

    def test_failed_cloud_download_preserves_original_plan_and_file(self):
        plan = self.create_plan(); old = PdfPlan.objects.get(pk=plan['id'])
        with patch('mobileApp.cloud_projects.read_cloud_snapshot', side_effect=CloudUnavailable('Sin conexión')):
            with self.assertRaises(CloudUnavailable):
                open_cloud_project('remote-pdf', self.other, replace_plan=old)
        self.assertTrue(PdfPlan.objects.filter(pk=old.pk).exists()); self.assertTrue(Path(old.source.path).exists())

    def test_download_does_not_make_other_user_owner_of_published_plan(self):
        from .project_service import export_project
        from zipfile import ZipFile
        plan = self.create_plan(); source_plan = PdfPlan.objects.get(pk=plan['id'])
        with ZipFile(BytesIO(export_project('pdf', source_plan))) as archive:
            manifest = json.loads(archive.read('manifest.json')); source = archive.read('plan/source.pdf'); content = archive.read('workbooks/w1.xlsx')
        project = self.remote_snapshot()[0]
        with patch('mobileApp.cloud_projects.read_cloud_snapshot', return_value=(project, source, content, None, manifest, '')):
            downloaded, _ = open_cloud_project('remote-pdf', self.other)
        self.assertEqual(downloaded.owner_username, self.owner.username)
        self.assertFalse(can_edit_plan(self.other, downloaded)); self.assertTrue(can_edit_plan(self.owner, downloaded))
