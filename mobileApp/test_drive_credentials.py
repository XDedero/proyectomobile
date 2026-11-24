import json
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .drive_credentials import credentials_payload, registered_credentials


class DriveCredentialTests(SimpleTestCase):
    def test_credentials_require_the_expected_identity_and_complete_oauth_material(self):
        valid = {'account_id': 'account-a', 'client_id': 'client', 'client_secret': 'secret', 'refresh_token': 'token'}
        self.assertEqual(credentials_payload(json.dumps(valid), 'account_id', 'account-a')['refresh_token'], 'token')
        for bad in ([], {**valid, 'account_id': 'other'}, {**valid, 'client_secret': ''}, {**valid, 'refresh_token': None}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, KeyError)):
                credentials_payload(json.dumps(bad), 'account_id', 'account-a')

    @override_settings(PLANOS_DATABASE_SCHEMA='app_runtime')
    def test_registry_uses_central_schema_and_company_parameter_even_in_a_tenant_request(self):
        company, identifier = uuid4(), uuid4()
        credentials = {'account_id': 'account-a', 'client_id': 'client', 'client_secret': 'secret', 'refresh_token': 'token'}
        with patch('mobileApp.drive_credentials.connection') as connection:
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (identifier, 'account-a', json.dumps(credentials))
            self.assertEqual(registered_credentials(company, 'account-a')['client_id'], 'client')
            query, parameters = cursor.execute.call_args.args
            self.assertIn('"app_runtime"."mobileApp_drivestorageaccount"', query.as_string())
            self.assertEqual(parameters, [company])
            with self.assertRaises(ValueError):
                registered_credentials(company, 'account-b')
            cursor.fetchone.return_value = (identifier, 'account-a', None)
            with self.assertRaises(ValueError):
                registered_credentials(company)
            cursor.fetchone.return_value = None
            self.assertIsNone(registered_credentials(company))
