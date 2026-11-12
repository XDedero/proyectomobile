"""Manual publication, private working copies and creator-managed editing."""
from io import BytesIO
import tempfile

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from .models import PdfPlan, Plan, SavedProject, UserProfile
from .test_pdf import pdf_upload
from .tests import dxf_file


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PlanCollaborationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user('plan-owner')
        cls.worker = users.objects.create_user('plan-worker')
        cls.viewer = users.objects.create_user('plan-viewer')
        cls.admin = users.objects.create_superuser('plan-admin')
        for user, rut, role in [(cls.owner, '11111111-1', 'worker'), (cls.worker, '22222222-2', 'worker'),
                                (cls.viewer, '12345678-5', 'viewer'), (cls.admin, '33333333-3', 'administrator')]:
            UserProfile.objects.create(user=user, rut=rut, role=role)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = override_settings(MEDIA_ROOT=directory.name)
        config.enable()
        self.addCleanup(config.disable)
        self.client.force_login(self.owner)
        self.other = Client()
        self.other.force_login(self.worker)

    def post(self, url, data=None, client=None):
        return (client or self.client).post(url, data or {}, content_type='application/json')

    def original(self, kind, saved=True):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'),
            {'file': pdf_upload() if kind == 'pdf' else dxf_file()})
        self.assertEqual(response.status_code, 201, response.content[:400])
        plan = response.json()
        if saved:
            self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        return plan

    def draft(self, original, client=None):
        response = self.post(original['draft_url'], client=client)
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()['plan']

    def grant(self, original, ids):
        return self.post(original['permissions_url'], {'user_ids': ids})

    def test_add_button_and_excel_changes_are_private_until_explicit_save_in_both_formats(self):
        for kind, model, related in [('pdf', PdfPlan, 'markers'), ('dxf', Plan, 'motors')]:
            with self.subTest(kind=kind):
                original = self.original(kind)
                snapshot = SavedProject.objects.get(**{'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': original['id']})
                previous_archive, previous_time = snapshot.content, snapshot.saved_at
                before = self.other.get(original['detail_url']).json()
                draft = self.draft(original)
                self.assertNotEqual(original['id'], draft['id'])
                self.assertEqual(self.other.get(draft['detail_url']).status_code, 403)
                value = {'page': 1, 'x': .25, 'y': .35} if kind == 'pdf' else {'svg_x': 30, 'svg_y': 30, 'name': 'New motor'}
                response = self.post(draft['markers_url'], value)
                self.assertEqual(response.status_code, 201, response.content[:500])
                workbook = self.client.get(draft['detail_url']).json()['workbook']
                workbook = self.client.get(workbook['metadata_url']).json()
                sheet = workbook['sheets'][0]['name']
                response = self.post(workbook['save_url'], {'revision': workbook['revision'], 'edits': [
                    {'sheet': sheet, 'row': 1, 'col': 1, 'value': 'Private change'}]})
                self.assertEqual(response.status_code, 200, response.content[:400])
                self.assertEqual(self.other.get(original['detail_url']).json()[related], before[related])
                snapshot.refresh_from_db()
                self.assertEqual((snapshot.content, snapshot.saved_at), (previous_archive, previous_time))
                self.assertTrue(self.client.get(draft['detail_url']).json()['has_pending_changes'])
                response = self.post(draft['save_project_url'])
                self.assertEqual(response.status_code, 200, response.content[:500])
                published = self.other.get(original['detail_url']).json()
                self.assertEqual(len(published[related]), len(before[related]) + 1)
                current = model.objects.get(pk=original['id'])
                book = load_workbook(BytesIO(current.workbook.content))
                self.assertEqual(book[sheet]['A1'].value, 'Private change')
                book.close()
                self.assertFalse(response.json()['plan']['has_pending_changes'])
                # Continuing in the same draft must not modify the just-published workbook.
                private = model.objects.get(pk=draft['id'])
                self.assertNotEqual(private.workbook_id, current.workbook_id)

    def test_a_new_private_button_never_creates_a_saved_project(self):
        original = self.original('pdf', saved=False)
        draft = self.draft(original)
        self.assertEqual(self.post(draft['markers_url'], {'page': 1, 'x': .2, 'y': .4}).status_code, 201)
        self.assertFalse(SavedProject.objects.filter(pdf_plan_id=original['id']).exists())
        self.assertEqual(self.client.get(reverse('saved_projects')).json()['plans'], [])
        self.assertEqual(self.client.get(reverse('list_pdf_plans')).json()['plans'][0]['id'], original['id'])

    def test_draft_exports_do_not_publish_and_recover_a_missing_original(self):
        original = self.original('pdf')
        snapshot = SavedProject.objects.get(pdf_plan_id=original['id'])
        stamp = snapshot.saved_at
        PdfPlan.objects.get(pk=original['id']).source.delete(save=False)
        draft = self.draft(original)
        self.assertEqual(self.post(draft['markers_url'], {'page': 1, 'x': .2, 'y': .4}).status_code, 201)
        response = self.client.get(draft['export_project_url'])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'))
        response.close()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.saved_at, stamp)
        self.assertEqual(PdfPlan.objects.get(pk=original['id']).markers.count(), 0)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)

    def test_only_creator_can_delegate_to_active_workers_and_revoke_access(self):
        for kind in ('pdf', 'dxf'):
            original = self.original(kind)
            self.assertFalse(self.other.get(original['detail_url']).json()['permissions']['can_assign'])
            self.assertEqual(self.grant(original, [self.worker.pk]).status_code, 200)
            self.assertTrue(self.other.get(original['detail_url']).json()['permissions']['can_assign'])
            draft = self.draft(original, self.other)
            self.assertFalse(draft['permissions']['can_manage_plan_permissions'])
            self.assertFalse(draft['permissions']['can_delete_plan'])
            self.assertEqual(self.post(original['permissions_url'], {'user_ids': []}, self.other).status_code, 403)
            admin = Client(); admin.force_login(self.admin)
            self.assertFalse(admin.get(original['detail_url']).json()['permissions']['can_manage_plan_permissions'])
            self.assertEqual(self.post(original['permissions_url'], {'user_ids': []}, admin).status_code, 403)
            for ids in ([self.viewer.pk], [True], [999999]):
                self.assertEqual(self.grant(original, ids).status_code, 400)
            self.assertEqual(self.grant(original, []).status_code, 200)
            self.assertEqual(self.other.get(draft['detail_url']).status_code, 403)
            self.assertEqual(self.post(draft['save_project_url'], client=self.other).status_code, 403)

    def test_concurrent_private_drafts_cannot_overwrite_a_newer_saved_version(self):
        original = self.original('pdf')
        self.grant(original, [self.worker.pk])
        first, second = self.draft(original), self.draft(original, self.other)
        self.assertEqual(self.post(first['markers_url'], {'page': 1, 'x': .2, 'y': .4}).status_code, 201)
        self.assertEqual(self.post(first['save_project_url']).status_code, 200)
        response = self.post(second['save_project_url'], client=self.other)
        self.assertEqual(response.status_code, 409, response.content[:400])
        self.assertEqual(PdfPlan.objects.get(pk=original['id']).markers.count(), 1)
        fresh = self.post(original['draft_url'], {'discard': True}, self.other)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(len(fresh.json()['plan']['markers']), 1)

    def test_original_button_ids_survive_saving_a_private_copy(self):
        original = self.original('pdf')
        marker = self.post(original['markers_url'], {'page': 1, 'x': .2, 'y': .4}).json()['marker']
        self.post(original['save_project_url'])
        draft = self.draft(original)
        private = draft['markers'][0]
        self.assertEqual(self.post(private['update_url'], {'x': .8}).status_code, 200)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        published = self.other.get(original['detail_url']).json()['markers'][0]
        self.assertEqual((published['id'], published['x']), (marker['id'], .8))
