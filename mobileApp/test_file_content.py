"""SQL contains references; lazy files preserve exact bytes and revision checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from django.core import serializers
from django.core.files.storage import default_storage
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from .drive_storage import GoogleDriveStorage
from .file_content import read_content
from .models import PdfPlan, SavedProject, WorkbookDocument
from .plan_workbook import WorkbookConflict, empty_workbook, store


class PrivateContentTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def test_queries_and_metadata_updates_do_not_download_or_reupload_the_file(self):
        original = b'PK\x00\xff' * 100_000
        document = WorkbookDocument.objects.create(name='Exacto.xlsx', content=original)
        with connection.cursor() as cursor:
            cursor.execute('SELECT content FROM "mobileApp_workbookdocument" WHERE id = %s', [document.pk])
            reference = cursor.fetchone()[0]
        self.assertIsInstance(reference, str)
        self.assertLess(len(reference), 255)
        with patch.object(default_storage, 'open', wraps=default_storage.open) as download:
            loaded = WorkbookDocument.objects.get(pk=document.pk)
            loaded.name = 'Renombrado.xlsx'
            loaded.save(update_fields=['name'])
            download.assert_not_called()
            self.assertEqual(loaded.content, original)
            self.assertEqual(loaded.content, original)
            self.assertEqual(download.call_count, 1)
        with patch.object(default_storage, 'save', wraps=default_storage.save) as upload:
            loaded.save()
            upload.assert_not_called()
        self.assertEqual(WorkbookDocument.objects.values_list('content', flat=True).get(pk=document.pk), reference)

    def test_conditional_updates_preserve_stale_revision_protection_and_old_bytes(self):
        document = WorkbookDocument.objects.create(name='Libro.xlsx', content=empty_workbook())
        stale = WorkbookDocument.objects.get(pk=document.pk)
        old_reference = document.__dict__['content']
        replacement = b'new-exact-bytes\x00\xff'
        store(document, replacement, None)
        self.assertEqual(document.content, replacement)
        with self.assertRaises(WorkbookConflict):
            store(stale, b'stale', None)
        document.refresh_from_db()
        self.assertEqual(document.content, replacement)
        self.assertNotEqual(document.__dict__['content'], old_reference)
        self.assertTrue(read_content(old_reference).startswith(b'PK'))

    def test_failed_storage_write_never_replaces_the_database_reference(self):
        document = WorkbookDocument.objects.create(name='Original.xlsx', content=b'original')
        reference = document.__dict__['content']
        with patch.object(default_storage, 'save', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                with transaction.atomic():
                    WorkbookDocument.objects.filter(pk=document.pk).update(content=b'replacement')
        self.assertEqual(WorkbookDocument.objects.values_list('content', flat=True).get(pk=document.pk), reference)
        document.refresh_from_db()
        self.assertEqual(document.content, b'original')

    def test_serialization_keeps_references_and_detects_corrupt_files(self):
        document = WorkbookDocument.objects.create(name='Portable.xlsx', content=b'original\x00\xff')
        payload = serializers.serialize('json', [document])
        self.assertEqual(json.loads(payload)[0]['fields']['content'], document.__dict__['content'])
        restored = next(serializers.deserialize('json', payload)).object
        self.assertEqual(restored.content, document.content)
        Path(default_storage.path(document.__dict__['content'])).write_bytes(b'corrupted')
        with self.assertRaises(OSError):
            WorkbookDocument.objects.get(pk=document.pk).content

    def test_unavailable_saved_archive_does_not_block_opening_a_remote_snapshot(self):
        from .cloud_projects import saved_manifest
        plan = PdfPlan.objects.create(name='Plano.pdf', source='original.pdf', size=1)
        SavedProject.objects.create(pdf_plan=plan, name=plan.name, content=b'archive')
        with patch.object(default_storage, 'open', side_effect=FileNotFoundError):
            self.assertIsNone(saved_manifest({'projectKey': plan.cloud_key}, b'pdf', b'xlsx'))

    def test_deletion_waits_for_commit_and_does_not_remove_a_shared_reference(self):
        document = WorkbookDocument.objects.create(name='Original.xlsx', content=b'original')
        name = document.__dict__['content']
        other = WorkbookDocument.objects.create(name='Shared.xlsx', content=name)
        with self.captureOnCommitCallbacks(execute=True):
            document.delete()
        self.assertTrue(default_storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            other.delete()
            self.assertTrue(default_storage.exists(name))
        self.assertFalse(default_storage.exists(name))

    def test_pdf_and_excel_upload_overlap_and_registration_waits_for_both(self):
        from . import terminal_monitor as monitor
        from .project_service import export_project
        from .test_pdf import pdf_upload
        plan = PdfPlan.objects.create(name='Plano.pdf', source=pdf_upload(), size=100, pages=[])
        bundle = export_project('pdf', plan)
        saved = SavedProject.objects.create(pdf_plan=plan, name=plan.name, content=bundle)
        pdf_started, excel_started = Event(), Event()
        finished = set()

        def pdf(*args, **kwargs):
            self.assertFalse(kwargs['register'])
            pdf_started.set()
            self.assertTrue(excel_started.wait(5), 'Excel was serialized after PDF')
            finished.add('pdf')
            return {'drive_file_id': 'pdf1', 'modified_time': 'v1'}

        def excel(*args, **kwargs):
            self.assertFalse(kwargs['register'])
            excel_started.set()
            self.assertTrue(pdf_started.wait(5), 'PDF did not start independently')
            finished.add('excel')
            return {'drive_file_id': 'xlsx1', 'modified_time': 'v1'}

        def register(*args, **kwargs):
            self.assertEqual(finished, {'pdf', 'excel'})
            return True

        with (
            patch.object(monitor, 'is_monitor_enabled', return_value=True),
            patch.object(monitor, '_log_line'),
            patch.object(monitor, '_sync_pdf_plan_worker', side_effect=pdf),
            patch.object(monitor, '_sync_workbook_worker', side_effect=excel),
            patch.object(monitor, 'run_supabase_sql_sync', side_effect=register) as sql,
            patch.object(monitor, 'enqueue_sync_task', side_effect=lambda fn: fn()),
            self.captureOnCommitCallbacks(execute=True),
        ):
            monitor.queue_saved_project_sync('pdf', plan, bundle, saved.saved_at)
        saved.refresh_from_db()
        self.assertEqual(saved.cloud_status, 'synced')
        sql.assert_called_once()


class DriveCacheTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name, PLANOS_FILE_CACHE_BYTES=32)
        settings.enable()
        self.addCleanup(settings.disable)

    def test_repeated_reads_use_bounded_disk_cache_and_delete_invalidates_it(self):
        storage = GoogleDriveStorage()
        def command(*args):
            if args[0] == '--open':
                Path(args[-1]).write_bytes(b'private')
            return {'file': {}}
        with patch.object(storage, 'command', side_effect=command) as remote:
            for _ in range(2):
                with storage.open('gdrive/id1/original.pdf') as stream:
                    self.assertEqual(stream.read(), b'private')
            self.assertEqual(remote.call_count, 1)
            storage.delete('gdrive/id1/original.pdf')
            with storage.open('gdrive/id1/original.pdf') as stream:
                self.assertEqual(stream.read(), b'private')
            self.assertEqual(remote.call_count, 3)

    def test_cache_eviction_and_disabled_cache_keep_durable_storage(self):
        storage = GoogleDriveStorage()
        with TemporaryDirectory() as directory:
            source = Path(directory) / 'content'
            source.write_bytes(b'x' * 20)
            storage._cache('gdrive/one/a.pdf', source)
            storage._cache('gdrive/two/b.pdf', source)
            self.assertFalse(storage._cache_path('gdrive/one/a.pdf').exists())
            self.assertTrue(storage._cache_path('gdrive/two/b.pdf').exists())
            with override_settings(PLANOS_FILE_CACHE_BYTES=0):
                storage._cache('gdrive/three/c.pdf', source)
            self.assertFalse(storage._cache_path('gdrive/three/c.pdf').exists())


class ContentMigrationTests(TransactionTestCase):
    old = [('mobileApp', '0032_remove_category_workbook')]
    latest = [('mobileApp', '0036_private_plan_drafts_and_edit_grants')]

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        self.apps = executor.loader.project_state(self.old).apps
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(self.latest))

    def test_existing_blobs_are_copied_exactly_and_can_be_restored(self):
        original = b'PK\x00\xff\x01' * 1000
        document = self.apps.get_model('mobileApp', 'WorkbookDocument').objects.create(name='Legacy.xlsx', content=original)
        plan = self.apps.get_model('mobileApp', 'PdfPlan').objects.create(name='Legacy.pdf', source='old.pdf', size=1)
        saved = self.apps.get_model('mobileApp', 'SavedProject').objects.create(pdf_plan=plan, name='Legacy', content=original)
        MigrationExecutor(connection).migrate(self.latest)
        self.assertEqual(WorkbookDocument.objects.get(pk=document.pk).content, original)
        self.assertEqual(SavedProject.objects.get(pk=saved.pk).content, original)
        MigrationExecutor(connection).migrate(self.old)
        self.assertEqual(self.apps.get_model('mobileApp', 'WorkbookDocument').objects.get(pk=document.pk).content, original)
        self.assertEqual(self.apps.get_model('mobileApp', 'SavedProject').objects.get(pk=saved.pk).content, original)

    def test_failed_copy_keeps_blobs_and_a_retry_resumes_verified_files(self):
        from importlib import import_module
        migration = import_module('mobileApp.migrations.0034_copy_binary_files')
        target = [('mobileApp', '0033_content_file_references')]
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        model = apps.get_model('mobileApp', 'WorkbookDocument')
        one = model.objects.create(name='One.xlsx', content=b'one')
        two = model.objects.create(name='Two.xlsx', content=b'two')
        real_write = migration.write_content
        def fail_second(content, extension):
            if content == b'two':
                raise OSError('offline')
            return real_write(content, extension)
        editor = SimpleNamespace(connection=connection)
        with patch.object(migration, 'write_content', side_effect=fail_second):
            with self.assertRaises(OSError):
                migration.copy_files(apps, editor)
        one.refresh_from_db()
        two.refresh_from_db()
        self.assertTrue(one.content_file)
        self.assertEqual((one.content, two.content), (b'one', b'two'))
        with patch.object(migration, 'write_content', wraps=real_write) as upload:
            migration.copy_files(apps, editor)
            upload.assert_called_once_with(b'two', 'xlsx')
