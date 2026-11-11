"""Failures must preserve submissions and never report a local-only save as shared."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .cloud_projects import (
    CloudUnavailable, postgres_query, supabase_command, supabase_database_url, supabase_query,
)
from .models import RegistrationRequest


@override_settings(APP_SHARED_ACCOUNTS=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SharedRegistrationTests(TestCase):
    def test_private_database_url_can_be_read_from_the_ignored_local_profile(self):
        with TemporaryDirectory() as temporary:
            profile = Path(temporary) / 'conexion-base-datos.txt'
            profile.write_text('\ufeffpostgresql://server:secret@pooler.example/postgres', encoding='utf-8')
            with self.settings(BASE_DIR=Path(temporary)):
                with patch.dict('os.environ', {'SUPABASE_DATABASE_URL': ''}):
                    self.assertEqual(
                        supabase_database_url(),
                        'postgresql://server:secret@pooler.example/postgres',
                    )

    def test_native_database_connection_is_preferred_over_the_developer_cli(self):
        database_url = 'postgresql://server:secret@pooler.example/postgres'
        with patch.dict('os.environ', {'SUPABASE_DATABASE_URL': database_url}):
            with patch('mobileApp.cloud_projects.postgres_query', return_value={'rows': [{'ok': 1}]}) as native:
                with patch('mobileApp.cloud_projects.supabase_command') as cli:
                    result = supabase_query('SELECT 1 AS ok;', read_only=True)
        self.assertEqual(result['rows'], [{'ok': 1}])
        native.assert_called_once_with(database_url, 'SELECT 1 AS ok;', read_only=True)
        cli.assert_not_called()

    def test_invalid_private_database_url_is_rejected_before_connecting(self):
        with patch.dict('os.environ', {'SUPABASE_DATABASE_URL': 'https://not-a-database.example'}):
            with self.assertRaises(CloudUnavailable) as error:
                supabase_database_url()
        self.assertEqual(error.exception.code, 'database_configuration')

    def test_native_read_is_forced_to_a_read_only_database_transaction(self):
        cursor = MagicMock()
        cursor.description = [('answer',)]
        cursor.fetchall.return_value = [{'answer': 42}]
        cursor.nextset.return_value = None
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connector = MagicMock()
        connector.return_value.__enter__.return_value = connection
        with patch('psycopg.connect', connector):
            result = postgres_query('postgresql://server:secret@pooler.example/postgres',
                                    'SELECT 42 AS answer;', read_only=True)
        self.assertEqual(result, {'rows': [{'answer': 42}]})
        self.assertEqual(connector.call_args.kwargs['options'],
                         '-c default_transaction_read_only=on')
        cursor.execute.assert_called_once_with('SELECT 42 AS answer;', prepare=False)

    def test_npm_client_is_resolved_from_the_windows_launcher(self):
        with TemporaryDirectory() as temporary:
            npm = Path(temporary) / 'custom-npm'
            entry = npm / 'node_modules' / 'supabase' / 'dist' / 'supabase.js'
            entry.parent.mkdir(parents=True)
            entry.touch()
            launcher = str(npm / 'supabase.cmd')
            with patch.dict('os.environ', {'APPDATA': str(Path(temporary) / 'other-appdata')}):
                with patch('mobileApp.cloud_projects.shutil.which', side_effect=[launcher, 'node.exe']):
                    self.assertEqual(supabase_command(), ['node.exe', str(entry)])

    def payload(self):
        return {'first_name': 'Prueba', 'last_name': 'Compartida', 'rut': '12345678-5',
                'password': 'Cascada!Sur2026', 'confirm_password': 'Cascada!Sur2026'}

    def test_connection_failure_retains_unsent_request_and_returns_failure(self):
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            response = self.client.post(reverse('request_registration'), self.payload(), content_type='application/json')
        self.assertEqual(response.status_code, 503)
        item = RegistrationRequest.objects.get()
        self.assertFalse(item.cloud_synced)
        self.assertNotEqual(item.password_hash, self.payload()['password'])

    def test_cloud_duplicate_does_not_leave_an_extra_local_pending_request(self):
        with patch('mobileApp.shared_accounts.query', return_value=[{'result': {'error': 'Ya hay una solicitud pendiente para ese RUT.'}}]):
            response = self.client.post(reverse('request_registration'), self.payload(), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_retry_uses_the_original_request_after_connection_failure(self):
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            first = self.client.post(reverse('request_registration'), self.payload(), content_type='application/json')
        item = RegistrationRequest.objects.get()
        self.assertEqual(first.status_code, 503)
        changed = {**self.payload(), 'password': 'OtraClave!2026', 'confirm_password': 'OtraClave!2026'}
        with patch('mobileApp.shared_accounts.query', return_value=[{'result': {'status': 'pending'}}]) as query:
            denied = self.client.post(reverse('request_registration'), changed, content_type='application/json')
            self.assertEqual(denied.status_code, 400)
            query.assert_not_called()
            retried = self.client.post(reverse('request_registration'), self.payload(), content_type='application/json')
        self.assertEqual(retried.status_code, 201)
        self.assertEqual(retried.json()['id'], item.pk)
        self.assertIn(str(item.cloud_id), query.call_args.args[0])
        self.assertTrue(RegistrationRequest.objects.get().cloud_synced)

    def test_pending_list_imports_global_identity_without_a_password(self):
        admin = get_user_model().objects.create_superuser('shared-test-admin')
        self.client.force_login(admin)
        identifier = str(uuid4())
        data = {key: self.payload()[key] for key in ('first_name', 'last_name', 'rut')}
        data.update(id=identifier, status='pending', created_at='2026-11-10T20:00:00+00:00')
        with patch('mobileApp.shared_accounts.query', return_value=[data]):
            response = self.client.get(reverse('pending_registrations'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(RegistrationRequest.objects.get().cloud_id), identifier)
        self.assertNotContains(response, 'password')
        self.assertTrue(RegistrationRequest.objects.get().cloud_synced)

    def test_pending_api_does_not_claim_an_empty_list_during_cloud_failure(self):
        admin = get_user_model().objects.create_superuser('shared-test-admin')
        self.client.force_login(admin)
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            response = self.client.get(reverse('pending_registrations'))
            page = self.client.get(reverse('management_panel'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Sin conexión')
