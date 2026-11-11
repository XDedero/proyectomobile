"""Use the existing Supabase installation and report authentication failures accurately."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from .cloud_projects import CloudUnavailable, command_json, supabase_command, supabase_query
from .shared_accounts import query


class SupabaseConnectorTests(SimpleTestCase):
    def test_remote_json_array_preserves_every_row(self):
        response = subprocess.CompletedProcess(['supabase.exe'], 0, stdout='[{"id": 1}, {"id": 2}]', stderr='')
        with patch('mobileApp.cloud_projects.subprocess.run', return_value=response):
            self.assertEqual(command_json(['supabase.exe'], 'Failed', timeout=5), [{'id': 1}, {'id': 2}])

    def test_empty_remote_json_array_is_a_valid_empty_query_result(self):
        response = subprocess.CompletedProcess(['supabase.exe'], 0, stdout='[]', stderr='')
        with patch('mobileApp.cloud_projects.subprocess.run', return_value=response):
            self.assertEqual(command_json(['supabase.exe'], 'Failed', timeout=5), [])

    def test_cli_array_is_normalized_to_the_same_row_shape_as_postgres(self):
        with patch('mobileApp.cloud_projects.supabase_database_url', return_value=''):
            with patch('mobileApp.cloud_projects.supabase_command', return_value=['supabase.exe']):
                with patch('mobileApp.cloud_projects.command_json', return_value=[{'id': 1}, {'id': 2}]):
                    self.assertEqual(supabase_query('SELECT id FROM example;', read_only=True),
                                     {'rows': [{'id': 1}, {'id': 2}]})

    def test_npx_installed_cli_is_found_without_a_path_entry(self):
        with TemporaryDirectory() as temporary:
            cache = Path(temporary) / 'npm-cache'
            executable = cache / '_npx/example/node_modules/@supabase/cli-windows-x64/bin/supabase.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            with patch.dict('os.environ', {'npm_config_cache': str(cache), 'APPDATA': temporary}):
                with patch('mobileApp.cloud_projects.shutil.which', return_value=None):
                    self.assertEqual(supabase_command(), [str(executable)])

    def test_queries_use_json_and_disable_unnecessary_cli_writes(self):
        with patch('mobileApp.cloud_projects.supabase_database_url', return_value=''):
            with patch('mobileApp.cloud_projects.supabase_command', return_value=['supabase.exe']):
                with patch('mobileApp.cloud_projects.command_json', return_value={'rows': [{'ok': 1}]}) as run:
                    self.assertEqual(supabase_query('SELECT 1 AS ok;', read_only=True)['rows'], [{'ok': 1}])
        command = run.call_args.args[0]
        self.assertIn('--agent=no', command)
        self.assertEqual(command[command.index('--output') + 1], 'json')
        self.assertIn('--read-only', command)
        self.assertEqual(command[-1], '--remote')
        self.assertEqual(run.call_args.kwargs['env']['DO_NOT_TRACK'], '1')

    def test_unexpected_json_is_not_reported_as_an_empty_pending_list(self):
        with patch('mobileApp.cloud_projects.supabase_database_url', return_value=''):
            with patch('mobileApp.cloud_projects.supabase_command', return_value=['supabase.exe']):
                with patch('mobileApp.cloud_projects.command_json', return_value={'unexpected': []}):
                    with self.assertRaises(CloudUnavailable) as error:
                        supabase_query('SELECT 1;', read_only=True)
        self.assertEqual(error.exception.code, 'invalid_response')

    def test_permission_failure_reports_the_account_problem_without_exposing_output(self):
        failure = subprocess.CalledProcessError(
            1, ['supabase.exe'], stderr='unexpected status 403: Your account does not have the necessary privileges. private-token',
        )
        with patch('mobileApp.cloud_projects.subprocess.run', side_effect=failure):
            with self.assertRaises(CloudUnavailable) as error:
                command_json(['supabase.exe'], 'No se pudo conectar.', timeout=5)
        self.assertEqual(error.exception.code, 'account_permission')
        self.assertNotIn('private-token', str(error.exception))

    def test_missing_login_is_distinguished_from_a_missing_connector(self):
        failure = subprocess.CalledProcessError(1, ['supabase.exe'], stderr='Access token not provided. Run supabase login.')
        with patch('mobileApp.cloud_projects.subprocess.run', side_effect=failure):
            with self.assertRaises(CloudUnavailable) as error:
                command_json(['supabase.exe'], 'No se pudo conectar.', timeout=5)
        self.assertEqual(error.exception.code, 'account_authentication')

    def test_administrator_is_told_about_the_wrong_account(self):
        with patch('mobileApp.shared_accounts.supabase_query',
                   side_effect=CloudUnavailable('Failed', code='account_permission')):
            with self.assertRaisesMessage(CloudUnavailable, 'no tiene acceso al proyecto'):
                query('SELECT 1;')
