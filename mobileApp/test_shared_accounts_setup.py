"""Configuration and registry installation must fail clearly without exposing secrets."""
from io import StringIO
from unittest.mock import MagicMock, patch

import psycopg
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from .cloud_projects import CloudUnavailable, postgres_query, supabase_database_url
from .management.commands.setup_shared_accounts import REQUIRED_OBJECTS


@override_settings(APP_SHARED_ACCOUNTS=True)
class SharedAccountsSetupTests(SimpleTestCase):
    def test_example_password_is_rejected_without_exposing_uri(self):
        private_uri = 'postgresql://postgres:[TU_PASSWORD]@pooler.example:5432/postgres'
        with patch.dict('os.environ', {'SUPABASE_DATABASE_URL': private_uri}):
            with self.assertRaises(CloudUnavailable) as error:
                supabase_database_url()
        self.assertEqual(error.exception.code, 'database_configuration')
        self.assertNotIn(private_uri, str(error.exception))

    def test_ipv6_server_is_a_valid_database_configuration(self):
        private_uri = 'postgresql://postgres:secret@[::1]:5432/postgres'
        with patch.dict('os.environ', {'SUPABASE_DATABASE_URL': private_uri}):
            self.assertEqual(supabase_database_url(), private_uri)

    def test_encoded_password_symbols_are_preserved(self):
        private_uri = 'postgresql://postgres:%5BReal%40Secret%5D%20%3C42%3E@pooler.example:5432/postgres'
        with patch.dict('os.environ', {'SUPABASE_DATABASE_URL': private_uri}):
            self.assertEqual(supabase_database_url(), private_uri)

    def test_encoded_example_password_is_also_rejected(self):
        with patch.dict('os.environ', {
            'SUPABASE_DATABASE_URL': 'postgresql://postgres:%5BYOUR-PASSWORD%5D@pooler.example/postgres',
        }):
            with self.assertRaises(CloudUnavailable) as error:
                supabase_database_url()
        self.assertEqual(error.exception.code, 'database_configuration')

    def test_missing_registry_provides_the_installation_command(self):
        with patch('mobileApp.management.commands.setup_shared_accounts.supabase_query',
                   return_value={'rows': [{key: False for key in REQUIRED_OBJECTS}]}):
            with self.assertRaisesMessage(CommandError, 'setup_shared_accounts --initialize'):
                call_command('setup_shared_accounts', stdout=StringIO())

    def test_check_does_not_modify_the_database(self):
        with patch('mobileApp.management.commands.setup_shared_accounts.supabase_query',
                   return_value={'rows': [{key: True for key in REQUIRED_OBJECTS}]}) as query:
            output = StringIO()
            call_command('setup_shared_accounts', stdout=output)
        self.assertIn('verificados', output.getvalue())
        self.assertEqual(query.call_count, 1)
        self.assertIs(query.call_args.kwargs['read_only'], True)

    def test_initialize_installs_private_schema_then_verifies_it(self):
        with patch('mobileApp.management.commands.setup_shared_accounts.supabase_query', side_effect=[
            {'rows': []}, {'rows': [{key: True for key in REQUIRED_OBJECTS}]},
        ]) as query:
            call_command('setup_shared_accounts', initialize=True, stdout=StringIO())
        install, check = query.call_args_list
        self.assertIs(install.kwargs['read_only'], False)
        self.assertIn('CREATE SCHEMA IF NOT EXISTS app_identity', install.args[0])
        self.assertIn('REVOKE ALL', install.args[0])
        self.assertNotIn('TRUNCATE', install.args[0])
        self.assertIs(check.kwargs['read_only'], True)

    def test_connection_failure_is_reported(self):
        with patch('mobileApp.management.commands.setup_shared_accounts.supabase_query',
                   side_effect=CloudUnavailable('Sin conexión', code='database_connection')):
            with self.assertRaisesMessage(CommandError, 'database_connection'):
                call_command('setup_shared_accounts', stdout=StringIO())

    def test_missing_postgres_relation_has_a_distinct_error(self):
        with patch('psycopg.connect', side_effect=psycopg.errors.UndefinedTable('Missing private table')):
            with self.assertRaises(CloudUnavailable) as error:
                postgres_query('postgresql://server:secret@pooler.example/postgres', 'SELECT 1', read_only=True)
        self.assertEqual(error.exception.code, 'database_schema')
        self.assertNotIn('secret', str(error.exception))

    def test_write_queries_do_not_inherit_a_read_only_session_setting(self):
        cursor = MagicMock()
        cursor.description = None
        cursor.nextset.return_value = None
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connector = MagicMock()
        connector.return_value.__enter__.return_value = connection
        with patch('psycopg.connect', connector):
            postgres_query('postgresql://server:secret@pooler.example/postgres', 'SELECT 1', read_only=False)
        self.assertTrue(connector.call_args.kwargs['autocommit'])
        self.assertNotIn('options', connector.call_args.kwargs)
