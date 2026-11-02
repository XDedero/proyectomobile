"""Saved originals remain readable when a deployment lacks the media files."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import PdfPlan, Plan, SavedProject
from .test_pdf import pdf_upload
from .tests import dxf_file


class PlanSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_superuser('source-owner', password='test')
        cls.reader = get_user_model().objects.create_user('source-reader')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='plan-sources-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.owner)
        self.other = Client()
        self.other.force_login(self.reader)

    def plan(self, kind='pdf', saved=True):
        upload = pdf_upload() if kind == 'pdf' else dxf_file()
        original = upload.read()
        upload.seek(0)
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'), {'file': upload})
        self.assertEqual(response.status_code, 201)
        data = response.json()
        if saved:
            self.assertEqual(self.client.post(data['save_project_url']).status_code, 200)
        model = PdfPlan if kind == 'pdf' else Plan
        Path(model.objects.get(pk=data['id']).source.path).unlink()
        return data, original

    def binary(self, response):
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content)
        response.close()
        return content

    def replace_backup(self, plan, *, version=3, kind='pdf', path='plan/source.pdf', content=b'%PDF-backup'):
        output = BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'motor-plan-project', 'version': version, 'kind': kind, 'source': path}))
            archive.writestr(path, content)
        SavedProject.objects.filter(pdf_plan_id=plan['id']).update(content=output.getvalue())

    def test_another_user_reads_the_exact_saved_pdf_without_its_media_file(self):
        plan, original = self.plan()
        response = self.other.get(plan['source_url'])
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.binary(response), original)
        self.assertEqual(Client().get(plan['source_url']).status_code, 401)
        self.assertEqual(self.other.get(plan['detail_url']).status_code, 200)

    def test_export_and_resave_use_the_saved_original_for_both_formats(self):
        for kind in ('pdf', 'dxf'):
            with self.subTest(kind=kind):
                plan, original = self.plan(kind)
                content = self.binary(self.other.get(plan['export_project_url']))
                with ZipFile(BytesIO(content)) as archive:
                    self.assertEqual(archive.read(f'plan/source.{kind}'), original)
                self.assertEqual(self.client.post(plan['save_project_url']).status_code, 200)

    def test_deleted_plan_cannot_be_recovered_through_the_backup(self):
        plan, original = self.plan()
        self.assertEqual(self.binary(self.other.get(plan['source_url'])), original)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(plan['delete_url']).status_code, 200)
        self.assertFalse(SavedProject.objects.filter(pdf_plan_id=plan['id']).exists())
        self.assertEqual(self.other.get(plan['source_url']).status_code, 404)
        self.assertEqual(self.other.get(plan['detail_url']).status_code, 404)
        self.assertEqual(self.other.get(reverse('saved_projects')).json()['plans'], [])

    def test_missing_unsaved_source_has_a_clear_error_instead_of_a_server_exception(self):
        plan, _ = self.plan(saved=False)
        response = self.other.get(plan['source_url'])
        self.assertEqual(response.status_code, 404)
        self.assertIn('archivo original', response.json()['error'])
        self.assertIn('Vuelva a importar', response.json()['error'])
        self.assertEqual(self.other.get(plan['detail_url']).status_code, 200)

    def test_empty_corrupt_or_invalid_backups_do_not_return_non_pdf_content(self):
        plan, _ = self.plan()
        for content in (b'', b'not a ZIP'):
            SavedProject.objects.filter(pdf_plan_id=plan['id']).update(content=content)
            self.assertEqual(self.other.get(plan['source_url']).status_code, 404)
        for changes in ({'path': '../outside.pdf'}, {'kind': 'dxf'}, {'version': True}, {'version': 99}, {'content': b'not PDF'}):
            with self.subTest(changes=changes):
                self.replace_backup(plan, **changes)
                response = self.other.get(plan['source_url'])
                self.assertEqual(response.status_code, 404)
                self.assertIn('copia válida', response.json()['error'])

    def test_fallback_respects_source_size_limits_and_saved_project_versions(self):
        plan, _ = self.plan()
        for version in (1, 2, 3):
            self.replace_backup(plan, version=version)
            self.assertEqual(self.binary(self.other.get(plan['source_url'])), b'%PDF-backup')
        self.replace_backup(plan, content=b'%PDF-' + b'x' * 100)
        with override_settings(PLAN_UPLOAD_MAX_BYTES=64):
            self.assertEqual(self.other.get(plan['source_url']).status_code, 404)
