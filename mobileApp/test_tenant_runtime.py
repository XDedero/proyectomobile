from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from .cloud_projects import company_sql
from .piece_service import catalog_state
from .tenant_runtime import (Workspace, capture_workspace, drive_environment, offline_user_id, resolve_workspace,
                             schema_name, workspace)


class TenantIsolationTests(SimpleTestCase):
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
