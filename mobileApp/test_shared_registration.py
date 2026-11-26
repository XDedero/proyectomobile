"""Connection tests for the private account database."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from django.test import TestCase, override_settings

from .cloud_projects import (
    CloudUnavailable, postgres_query, supabase_command, supabase_database_url, supabase_query,
)


@override_settings(APP_SHARED_ACCOUNTS=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SharedConnectorTests(TestCase):
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
        self.assertNotIn('options', connector.call_args.kwargs)
        self.assertFalse(connector.call_args.kwargs['autocommit'])
        self.assertIs(connection.read_only, True)
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
