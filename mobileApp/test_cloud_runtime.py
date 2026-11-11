"""Cloud startup must not depend on a paid disk or expose private originals."""
from io import BytesIO
import json
import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings

from proyectomobile.cloud_config import postgres_database, private_schema
from scripts.prepare_runtime import prepare
from .drive_storage import GoogleDriveStorage
from .management.commands.migrate_local_to_cloud import local_original, require_empty_target


class CloudConfigurationTests(SimpleTestCase):
    def test_session_pooler_uri_preserves_password_and_enforces_tls(self):
        result = postgres_database('postgresql://postgres.example:secret%40word@aws-0.pooler.supabase.com:5432/postgres')
        self.assertEqual(result['PASSWORD'], 'secret@word')
        self.assertEqual(result['ENGINE'], 'proyectomobile.postgres')
        self.assertEqual(result['OPTIONS']['sslmode'], 'require')
        self.assertIsNone(result['OPTIONS']['prepare_threshold'])
        self.assertEqual(result['CONN_MAX_AGE'], 0)

    def test_examples_and_transaction_pooler_fail_without_revealing_the_uri(self):
        for value in (
            'postgresql://user:REEMPLAZAR_PASSWORD@host/postgres',
            'postgresql://user:%5BYOUR-PASSWORD%5D@host/postgres',
            'postgresql://user:actual-private-secret@aws-0.pooler.supabase.com:6543/postgres',
            'not-a-uri',
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as error:
                    postgres_database(value)
                self.assertNotIn(value, str(error.exception))
                self.assertNotIn('actual-private-secret', str(error.exception))

    def test_runtime_tables_have_a_private_schema(self):
        self.assertEqual(private_schema({}), 'app_runtime')
        for schema in ('public', 'auth', 'app_identity', 'pg_catalog', 'x; DROP SCHEMA public', 'a b'):
            with self.assertRaises(ValueError):
                private_schema({'PLANOS_DATABASE_SCHEMA': schema})

    def test_seed_is_copied_to_writable_runtime_and_existing_tokens_are_preserved(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            secrets = root / 'secrets'
            secrets.mkdir()
            seed = secrets / 'gdrive-token-seed.json'
            seed.write_text(json.dumps({'client_id':'client','client_secret':'secret','refresh_token':'refresh'}))
            config = SimpleNamespace(APP_DATA_DIR=root/'temporary', MEDIA_ROOT=root/'temporary/media', PLANOS_CLOUD_MODE=True)
            prepare(config, root=root, secrets_dir=secrets)
            target = root/'gdrive-token.json'
            self.assertEqual(json.loads(target.read_text())['refresh_token'], 'refresh')
            token = json.loads(target.read_text())
            token['refresh_token'] = 'renewed'
            target.write_text(json.dumps(token))
            prepare(config, root=root, secrets_dir=secrets)
            self.assertEqual(json.loads(target.read_text())['refresh_token'], 'renewed')
            self.assertTrue(config.MEDIA_ROOT.is_dir())

    def test_missing_authorization_fails_before_database_initialization(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = SimpleNamespace(APP_DATA_DIR=root/'runtime', MEDIA_ROOT=root/'runtime/media', PLANOS_CLOUD_MODE=True)
            with self.assertRaisesMessage(ValueError, 'Secret File'):
                prepare(config, root=root, secrets_dir=root/'absent')

    def test_local_installation_does_not_require_cloud_credentials(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            config=SimpleNamespace(APP_DATA_DIR=root,MEDIA_ROOT=root/'media',PLANOS_CLOUD_MODE=False)
            prepare(config,root=root)
            self.assertTrue(config.MEDIA_ROOT.is_dir())
            self.assertFalse((root/'gdrive-token.json').exists())


class DriveStorageTests(SimpleTestCase):
    def test_upload_and_download_keep_bytes_while_storing_only_a_drive_reference(self):
        storage = GoogleDriveStorage()
        content = b'%PDF-private-original\x00\xff'
        def command(*args):
            if args[0]=='--save':
                self.assertEqual(Path(args[args.index('--file')+1]).read_bytes(),content)
                return {'file':{'id':'original-123'}}
            if args[0]=='--open':
                Path(args[args.index('--output')+1]).write_bytes(content)
                return {'file':{'id':'original-123'}}
            raise AssertionError(args)
        with patch.object(storage,'command',side_effect=command):
            name=storage.save('pdf_plans/original.pdf',ContentFile(content))
            self.assertEqual(name,'gdrive/original-123/original.pdf')
            with storage.open(name) as downloaded:
                self.assertEqual(downloaded.read(),content)

    def test_unknown_names_cannot_access_an_arbitrary_drive_file(self):
        storage=GoogleDriveStorage()
        for name in ('../../secret','pdf_plans/local.pdf','gdrive/id/../../secret'):
            with self.assertRaises(FileNotFoundError):
                storage.open(name)

    def test_missing_original_is_reported_as_missing_and_delete_is_idempotent(self):
        storage=GoogleDriveStorage()
        with patch.object(storage,'command',side_effect=FileNotFoundError):
            self.assertFalse(storage.exists('gdrive/original-123/original.pdf'))
            storage.delete('gdrive/original-123/original.pdf')

    def test_copy_refuses_a_local_path_outside_media(self):
        with TemporaryDirectory() as directory:
            media=Path(directory).resolve()
            with self.assertRaisesMessage(Exception,'sale de la carpeta media'):
                local_original({'model':'mobileApp.pdfplan','pk':1,'source':'../private.pdf'},'source',media,'unused')


class CloudDataProtectionTests(TestCase):
    def test_existing_accounts_block_an_import_before_any_upload(self):
        get_user_model().objects.create_user('already-in-cloud')
        with self.assertRaisesMessage(Exception,'no se sobrescribiran'):
            require_empty_target()

    @override_settings(PLANOS_CLOUD_MODE=True)
    def test_clear_local_copy_is_disabled_for_the_shared_database(self):
        from .project_views import can_clear_local
        self.assertFalse(can_clear_local('pdf',None,None))

    def test_startup_preserves_an_existing_administrator_password(self):
        user=get_user_model().objects.create_superuser('admin',password='existing-secret')
        previous=user.password
        with patch.dict(os.environ,{'DJANGO_SUPERUSER_USERNAME':'admin','DJANGO_SUPERUSER_PASSWORD':'different-startup-secret','DJANGO_RESET_SUPERUSER_PASSWORD':'0'}):
            runpy.run_path(str(settings.BASE_DIR/'init_admin.py'),run_name='__main__')
        user.refresh_from_db()
        self.assertEqual(user.password,previous)
