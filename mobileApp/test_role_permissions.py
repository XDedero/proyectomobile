"""Application roles protect drawings, categories and personal history at the API."""
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ButtonCategory, PdfPlan, UserProfile
from .test_pdf import pdf_upload
from .tests import dxf_file


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ApplicationRolePermissionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.viewer = User.objects.create_user('viewer', password='Test-password!')
        cls.worker = User.objects.create_user('worker', password='Test-password!')
        cls.other = User.objects.create_user('other-worker', password='Test-password!')
        cls.admin = User.objects.create_superuser('role-admin', password='Test-password!')
        for user, rut, role in [(cls.viewer, '12345678-5', 'viewer'),
                                (cls.worker, '11111111-1', 'worker'),
                                (cls.other, '22222222-2', 'worker'),
                                (cls.admin, '33333333-3', 'administrator')]:
            UserProfile.objects.create(user=user, rut=rut, role=role)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='application-roles-')
        self.addCleanup(temporary.cleanup)
        settings = override_settings(MEDIA_ROOT=temporary.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def post(self, url, data=None):
        return self.client.post(url, data or {}, content_type='application/json')

    def import_plan(self, user, kind='pdf'):
        self.client.force_login(user)
        result = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'),
                                  {'file': pdf_upload() if kind == 'pdf' else dxf_file()})
        self.assertEqual(result.status_code, 201, result.content[:300])
        return result.json()

    def test_viewer_can_import_both_formats_but_cannot_edit_save_or_delete_them(self):
        for kind in ('pdf', 'dxf'):
            plan = self.import_plan(self.viewer, kind)
            self.assertFalse(plan['permissions']['can_assign'])
            self.assertFalse(plan['permissions']['can_copy_button'])
            self.assertFalse(plan['permissions']['can_save_plan'])
            for url, data in [(plan['update_url'], {'name': 'Prohibido'}),
                              (plan['save_project_url'], {}), (plan['delete_url'], {}),
                              (plan['markers_url'], {'page': 1, 'x': .2, 'y': .3}),
                              (plan['workbook_url'], {'action': 'template'}),
                              (plan['assign_listing_url'], {'action': 'add'})]:
                with self.subTest(kind=kind, url=url):
                    self.assertEqual(self.post(url, data).status_code, 403)
            self.assertEqual(self.client.get(plan['detail_url']).status_code, 200)
            self.assertEqual(self.client.get(plan['export_project_url']).status_code, 403)
        self.assertEqual(self.client.get(reverse('saved_projects'), {'mine': '1'}).status_code, 403)
        self.assertFalse(self.client.get(reverse('auth_session')).json()['user']['permissions']['can_history'])

    def test_viewer_sees_shared_saved_drawings_but_not_other_unsaved_work(self):
        saved = self.import_plan(self.worker)
        self.assertEqual(self.post(saved['save_project_url']).status_code, 200)
        unsaved = self.import_plan(self.worker)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(saved['detail_url']).status_code, 200)
        self.assertEqual(self.client.get(unsaved['detail_url']).status_code, 403)
        ids = [plan['id'] for plan in self.client.get(reverse('list_pdf_plans')).json()['plans']]
        self.assertEqual(ids, [saved['id']])
        book = saved['workbook']
        metadata = self.client.get(book['metadata_url']).json()
        self.assertFalse(metadata['can_edit'])
        self.assertFalse(metadata['can_export'])
        self.assertEqual(self.post(book['save_url'], {'revision': 1}).status_code, 403)
        self.assertEqual(self.client.get(book['download_url']).status_code, 403)

    def test_worker_reads_shared_plans_and_edits_own_plans(self):
        for kind in ('pdf', 'dxf'):
            foreign = self.import_plan(self.other, kind)
            self.assertEqual(self.post(foreign['save_project_url']).status_code, 200)
            self.client.force_login(self.worker)
            response = self.client.get(foreign['detail_url'])
            self.assertEqual(response.status_code, 200)
            rights = response.json()['permissions']
            for permission in ('can_assign', 'can_copy_button', 'can_save_plan', 'can_export', 'can_delete_plan'):
                self.assertFalse(rights[permission], permission)
            self.assertEqual(self.post(foreign['update_url'], {'name': 'Compartido'}).status_code, 403)
            self.assertEqual(self.post(foreign['save_project_url']).status_code, 403)
            self.assertEqual(self.post(foreign['markers_url'], {'origin': 'copy', 'copy_button_id': 1}).status_code, 403)
            self.assertEqual(self.post(foreign['delete_url']).status_code, 403)
            self.assertEqual(self.client.get(foreign['export_project_url']).status_code, 403)
            clear = reverse('clear_local_project', args=[kind, foreign['id']])
            self.assertEqual(self.post(clear).status_code, 403)
            book = foreign['workbook']
            response = self.client.get(book['metadata_url'])
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['can_edit'])
            self.assertFalse(response.json()['can_export'])
            for url in (book['download_url'], book['csv_url']):
                self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.post(book['save_url'], {'revision': book['revision']}).status_code, 403)
            self.assertEqual(self.post(reverse('register_piece'), {'workbook_id': book['id']}).status_code, 403)
            self.client.force_login(self.admin)
            self.assertTrue(self.client.get(foreign['detail_url']).json()['permissions']['can_copy_button'])
            metadata = self.client.get(book['metadata_url']).json()
            self.assertTrue(metadata['can_edit'])
            self.assertTrue(metadata['can_export'])
            own = self.import_plan(self.worker, kind)
            self.assertTrue(own['permissions']['can_copy_button'])
            self.assertEqual(self.post(own['save_project_url']).status_code, 200)
            self.assertEqual(self.post(own['delete_url']).status_code, 200)

    def test_history_contains_saved_drawings_created_by_current_user_only(self):
        own = self.import_plan(self.worker)
        self.assertEqual(self.post(own['save_project_url']).status_code, 200)
        self.import_plan(self.worker)  # Unsaved drawings never enter history.
        foreign = self.import_plan(self.other)
        self.assertEqual(self.post(foreign['save_project_url']).status_code, 200)
        self.client.force_login(self.worker)
        history = self.client.get(reverse('saved_projects'), {'mine': '1'}).json()
        self.assertEqual([plan['plan_id'] for plan in history['plans']], [own['id']])
        self.assertEqual(history['cached'], [])
        self.assertEqual(len(self.client.get(reverse('saved_projects')).json()['plans']), 2)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('saved_projects'), {'mine': '1'}).json()['plans'], [])
        self.assertEqual(self.post(foreign['delete_url']).status_code, 200)

    def test_only_administrators_can_change_categories_and_users(self):
        self.client.force_login(self.worker)
        category = self.post(reverse('manage_category'), {'name': 'Sensores de terreno'})
        self.assertEqual(category.status_code, 200, category.content)
        category_id = category.json()['id']
        for data in ({'id': category_id, 'name': 'Renombrado'},
                     {'id': category_id, 'action': 'delete'}, {'action': 'delete', 'name': 'Nueva'}):
            self.assertEqual(self.post(reverse('manage_category'), data).status_code, 403)
        self.assertEqual(self.client.get(reverse('management_data')).status_code, 403)
        self.client.force_login(self.viewer)
        self.assertEqual(self.post(reverse('manage_category'), {'name': 'Prohibida'}).status_code, 403)
        for name in ('import_piece_document', 'register_piece', 'upload_workbook'):
            self.assertEqual(self.post(reverse(name), {'blank': True}).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.post(reverse('manage_category'), {'id': category_id, 'name': 'Sensores'}).status_code, 200)
        self.assertEqual(self.post(reverse('manage_category'), {'id': category_id, 'action': 'delete'}).status_code, 200)
        self.assertFalse(ButtonCategory.objects.filter(pk=category_id).exists())

    def test_worker_cannot_remove_foreign_cloud_project(self):
        foreign = self.import_plan(self.other)
        plan = PdfPlan.objects.get(pk=foreign['id'])
        plan.cloud_origin = 'remote-foreign'
        plan.owner_username = self.other.username
        plan.save()
        self.client.force_login(self.worker)
        remote = {'id': 'remote-foreign', 'name': plan.name, 'projectKey': plan.cloud_key, 'excelId': 'remote-excel'}
        with patch('mobileApp.cloud_projects.list_cloud_projects', return_value=[remote]), \
                patch('mobileApp.cloud_projects.drive_command') as drive:
            self.assertEqual(self.post(reverse('delete_cloud_project', args=['remote-foreign'])).status_code, 403)
            drive.assert_not_called()

    def test_two_viewers_can_consult_same_published_cloud_copy(self):
        published = self.import_plan(self.worker)
        PdfPlan.objects.filter(pk=published['id']).update(cloud_origin='published-drive', owner_username=self.worker.username)
        for user in (self.viewer, get_user_model().objects.create_user('second-viewer')):
            if user != self.viewer:
                UserProfile.objects.create(user=user, rut='44444444-4', role='viewer')
            self.client.force_login(user)
            self.assertEqual(self.client.get(published['detail_url']).status_code, 200)
            self.assertEqual(self.client.get(reverse('list_pdf_plans')).json()['plans'][0]['id'], published['id'])
            self.assertEqual(self.post(published['save_project_url']).status_code, 403)
