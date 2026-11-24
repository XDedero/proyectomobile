from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .cloud_projects import company_sql
from .piece_service import catalog_state
from .tenant_runtime import (Workspace, capture_workspace, drive_environment, load_drive, offline_user_id, resolve_workspace,
                             schema_name, workspace)


class TenantIsolationTests(SimpleTestCase):
    def test_drive_vault_uses_only_the_company_secret_without_a_render_key(self):
        company, secret = uuid4(), uuid4()
        creds = {'client_id': 'company-client', 'client_secret': 'test-secret', 'refresh_token': 'test-token'}
        folders = {'pdf': 'pdf-folder', 'excel': 'excel-folder', 'originals': 'originals-folder'}
        info = SimpleNamespace(drive_secret_id=secret, root_id='root-folder', folder_ids=folders, account_id='company-account')
        with (patch('mobileApp.tenant_runtime.connection') as connection,
              patch('mobileApp.drive_credentials.registered_credentials', return_value=None)):
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (json.dumps({'company_id': str(company), **creds}),)
            self.assertEqual(load_drive(company, info), {'credentials': creds, 'folders': {**folders, 'root': info.root_id}})
            self.assertEqual(cursor.execute.call_args.args[1], [secret, f'planos_drive_{company.hex}'])
            self.assertIsNone(load_drive(uuid4(), info))
            cursor.fetchone.return_value = None
            self.assertIsNone(load_drive(company, info))

    @override_settings(PLANOS_CLOUD_MODE=True)
    def test_first_access_schedules_provisioning_only_with_a_valid_company_connection(self):
        account = SimpleNamespace(company_id=uuid4())
        with (patch('mobileApp.models.CompanyDriveConnection.objects.filter'),
              patch('mobileApp.tenant_runtime.load_drive') as drive,
              patch('mobileApp.tenant_runtime.workspace_is_ready', return_value=False),
              patch('mobileApp.tenant_runtime.schedule_workspace') as schedule):
            drive.return_value = None
            self.assertEqual(resolve_workspace(account, prepare=True), (None, False))
            schedule.assert_not_called()
            drive.return_value = {'folders': {'pdf': 'test'}}
            self.assertEqual(resolve_workspace(account, prepare=True), (None, True))
            schedule.assert_called_once_with(schema_name(account.company_id))

    def test_qualified_cloud_queries_keep_user_supplied_text(self):
        schema = schema_name(uuid4())
        sql = "SELECT 'Un título public.pdf_plans' FROM public.pdf_plans; -- public.pdf_plans"
        self.assertEqual(company_sql(sql, schema), sql.replace('FROM public.', f'FROM {schema}.'))

    def test_company_context_follows_worker_and_separates_device_ids_and_piece_catalog(self):
        first, second = uuid4(), uuid4()
        request = SimpleNamespace(user=SimpleNamespace(is_authenticated=True, pk=1))
        self.assertEqual(offline_user_id(request), 1)
        token = workspace.set(Workspace(schema_name(first), str(first), {}, {'folders': {'pdf': 'first'}}))
        try:
            read_identity = capture_workspace(lambda: offline_user_id(request))
            saved_drive = capture_workspace(drive_environment)
            first_catalog = catalog_state()
        finally:
            workspace.reset(token)
        token = workspace.set(Workspace(schema_name(second), str(second), {}, {'folders': {'pdf': 'second'}}))
        try:
            self.assertEqual(offline_user_id(request), f'{second}:1')
            self.assertIsNot(catalog_state(), first_catalog)
            with ThreadPoolExecutor() as worker:
                self.assertEqual(worker.submit(read_identity).result(), f'{first}:1')
                env = worker.submit(saved_drive).result()
                self.assertEqual(json.loads(env['PLANOS_COMPANY_DRIVE'])['folders']['pdf'], 'first')
        finally:
            workspace.reset(token)
        self.assertIsNone(drive_environment())

    @override_settings(PLANOS_CLOUD_MODE=True)
    def test_cloud_account_is_used_before_legacy_company_secret_and_failure_does_not_fallback(self):
        company = uuid4()
        creds = {'client_id': 'client', 'client_secret': 'secret', 'refresh_token': 'renewed-token'}
        folders = {'pdf': 'pdf-folder', 'excel': 'excel-folder', 'originals': 'originals-folder'}
        info = SimpleNamespace(account_id='account', root_id='root-folder', folder_ids=folders, drive_secret_id=uuid4())
        with (patch('mobileApp.drive_credentials.registered_credentials', return_value=creds) as registry,
              patch('mobileApp.tenant_runtime.connection') as connection):
            self.assertEqual(load_drive(company, info)['credentials'], creds)
            registry.assert_called_once_with(company, 'account')
            connection.cursor.assert_not_called()
            registry.side_effect = ValueError('invalid secret')
            self.assertIsNone(load_drive(company, info))
            connection.cursor.assert_not_called()

    @override_settings(PLANOS_CLOUD_MODE=True)
    def test_legacy_company_reads_vault_without_returning_secrets_to_browser(self):
        creds = {'client_id': 'client', 'client_secret': 'secret', 'refresh_token': 'token'}
        with patch('mobileApp.drive_credentials.registered_credentials', return_value=creds):
            data = json.loads(drive_environment()['PLANOS_COMPANY_DRIVE'])
            self.assertTrue(data['legacy'])
            self.assertEqual(data['credentials'], creds)
