import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import Motor, Plan, WorkbookDocument
from .tests import dxf_file, xlsx_file


class AuthenticationPermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.reader = User.objects.create_user('lector', password='clave-lector')
        cls.reader.groups.add(Group.objects.get(name='Lectores de planos'))
        cls.editor = User.objects.create_user('empleado', password='clave-editor')
        cls.editor.groups.add(Group.objects.get(name='Editores de motores'))
        cls.admin = User.objects.create_superuser('admin', password='clave-admin')
        cls.no_permissions = User.objects.create_user('sin_permisos', password='clave')
        cls.inactive = User.objects.create_user('inactivo', password='clave', is_active=False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='dxf-auth-test-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def login(self, username, password, client=None):
        return (client or self.client).post(
            reverse('auth_login'), json.dumps({'username': username, 'password': password}),
            content_type='application/json',
        )

    def import_plan(self, user=None):
        self.client.force_login(user or self.editor)
        response = self.client.post(reverse('plan_import'), {'file': dxf_file()})
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()

    def assign_excel(self, plan):
        response = self.client.post(plan['motors'][0]['assign_url'], {'file': xlsx_file()})
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response.json()['motor']

    def test_login_uses_real_django_username_and_password(self):
        response = self.login('lector', 'clave-lector')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['id'], self.reader.pk)
        self.assertEqual(self.client.get(reverse('auth_session')).json()['user']['username'], 'lector')
        self.assertIn('_auth_user_id', self.client.session)

    def test_login_page_versions_scripts_to_avoid_mixing_cached_interfaces(self):
        import re
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        scripts = re.findall(r'<script src="([^"]+)"', response.content.decode())
        self.assertTrue(scripts)
        versions = [re.search(r'\?v=(\d+)$', script) for script in scripts]
        self.assertTrue(all(versions), scripts)
        self.assertEqual(len({match.group(1) for match in versions}), 1)
        self.assertIn('no-store', response['Cache-Control'])

    def test_existing_short_username_and_password_are_not_rejected_by_form_rules(self):
        get_user_model().objects.create_user('a', password='x')
        self.assertEqual(self.login('a', 'x').status_code, 200)

    def test_unknown_wrong_password_and_inactive_users_are_rejected(self):
        for username, password in [('ficticio', 'clave'), ('lector', 'incorrecta'), ('inactivo', 'clave')]:
            with self.subTest(username=username):
                self.assertEqual(self.login(username, password).status_code, 401)
                self.assertIsNone(self.client.get(reverse('auth_session')).json()['user'])

    def test_malformed_login_is_rejected(self):
        for data in ['{', '[]', '{"username": null, "password": []}']:
            self.assertEqual(self.client.post(reverse('auth_login'), data, content_type='application/json').status_code, 400)

    def test_logout_invalidates_server_session(self):
        self.login('lector', 'clave-lector')
        self.assertEqual(self.client.post(reverse('auth_logout')).status_code, 200)
        self.assertIsNone(self.client.get(reverse('auth_session')).json()['user'])
        self.assertEqual(self.client.get(reverse('plans')).status_code, 401)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_authentication_changes_require_csrf_and_post(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('index'))
        original_token = client.cookies['csrftoken'].value
        data = {'username': 'lector', 'password': 'clave-lector'}
        self.assertEqual(client.post(reverse('auth_login'), data, content_type='application/json').status_code, 403)
        response = client.post(reverse('auth_login'), data, content_type='application/json', HTTP_X_CSRFTOKEN=original_token)
        self.assertEqual(response.status_code, 200)
        token = client.cookies['csrftoken'].value
        self.assertNotEqual(original_token, token)
        self.assertEqual(client.post(reverse('auth_logout')).status_code, 403)
        self.assertEqual(client.post(reverse('auth_logout'), HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(client.get(reverse('auth_login')).status_code, 405)
        self.assertEqual(client.get(reverse('auth_logout')).status_code, 405)

    def test_reader_can_import_and_assign_but_cannot_edit_excel_content(self):
        user = self.login('lector', 'clave-lector').json()['user']
        self.assertEqual(user['permissions'], {
            'can_view': True, 'can_import': True, 'can_assign': True,
            'can_change_excel': False, 'can_change_plan': False, 'can_delete_plan': False,
            'can_change_pdf_plan': False, 'can_delete_pdf_plan': False, 'can_administer': False,
            'can_save_plan': False, 'can_save_pdf_plan': False,
            'can_delete_cad_marker': False, 'can_delete_pdf_marker': False,
        })
        self.assertFalse(self.reader.has_perm('mobileApp.add_plan'))
        plan = self.import_plan(self.reader)
        self.assertEqual(Plan.objects.get(pk=plan['id']).imported_by_id, self.reader.pk)
        self.assertEqual(self.client.get(plan['detail_url']).status_code, 200)
        self.assertEqual(self.client.get(plan['svg_url']).status_code, 200)
        motor = self.assign_excel(plan)
        metadata = self.client.get(motor['excel']['metadata_url']).json()
        self.assertFalse(metadata['can_edit'])
        self.assertEqual(self.client.post(metadata['save_url'], {
            'revision': metadata['revision'], 'edits': [], 'operations': [],
        }, content_type='application/json').status_code, 403)

    def test_editor_can_assign_and_reader_can_view_same_excel(self):
        plan = self.import_plan()
        motor = self.assign_excel(plan)
        client = Client()
        self.assertEqual(self.login('lector', 'clave-lector', client).status_code, 200)
        self.assertEqual(client.get(reverse('plans')).json()['plans'][0]['id'], plan['id'])
        detail = client.get(plan['detail_url']).json()
        self.assertEqual(detail['motors'][0]['sheet_name'], 'Motor')
        preview = client.get(motor['excel']['preview_url'])
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()['rows'][0][0], 'Potencia (kW)')
        self.assertEqual(preview.json()['rows'][1][0], 15)
        download = client.get(motor['excel']['url'])
        self.assertEqual(download.status_code, 200)
        download.close()

    def test_reader_can_replace_attachment_but_cannot_modify_saved_cells(self):
        plan = self.import_plan()
        self.assign_excel(plan)
        motor_id = plan['motors'][0]['id']
        previous_document = Motor.objects.get(pk=motor_id).workbook_id
        self.client.force_login(self.reader)
        response = self.client.post(plan['motors'][0]['assign_url'], {'file': xlsx_file('nuevo.xlsx')})
        self.assertEqual(response.status_code, 200)
        document = Motor.objects.get(pk=motor_id).workbook
        self.assertEqual(document.pk, previous_document)
        self.assertTrue(WorkbookDocument.objects.filter(pk=previous_document).exists())
        before = bytes(document.content)
        revision = document.revision
        save_url = response.json()['motor']['excel']['save_url']
        denied = self.client.post(save_url, {
            'revision': document.revision,
            'edits': [{'sheet': 'Motor', 'row': 2, 'col': 1, 'value': 999}],
            'operations': [],
        }, content_type='application/json')
        self.assertEqual(denied.status_code, 403)
        document.refresh_from_db()
        self.assertEqual(bytes(document.content), before)
        self.assertEqual(document.revision, revision)

    def test_staff_flag_alone_does_not_allow_excel_changes(self):
        self.reader.is_staff = True
        self.reader.save()
        plan = self.import_plan(self.reader)
        motor = self.assign_excel(plan)
        self.assertFalse(self.client.get(motor['excel']['metadata_url']).json()['can_edit'])
        self.assertFalse(self.client.get(reverse('auth_session')).json()['user']['permissions']['can_administer'])

    def test_permission_revocation_takes_effect_on_next_request(self):
        plan = self.import_plan()
        motor = self.assign_excel(plan)
        self.editor.groups.clear()
        self.assertEqual(self.client.get(plan['svg_url']).status_code, 200)
        self.assertEqual(self.client.post(plan['motors'][0]['assign_url'], {'file': xlsx_file()}).status_code, 200)
        self.assertEqual(self.client.post(motor['excel']['save_url'], {
            'revision': 1, 'edits': [], 'operations': [],
        }, content_type='application/json').status_code, 403)
        permissions = self.client.get(reverse('auth_session')).json()['user']['permissions']
        self.assertTrue(permissions['can_view'])
        self.assertFalse(permissions['can_change_excel'])

    def test_deactivated_users_cannot_keep_using_existing_sessions(self):
        self.client.force_login(self.reader)
        self.reader.is_active = False
        self.reader.save()
        self.assertEqual(self.client.get(reverse('plans')).status_code, 401)

    def test_registered_user_without_groups_can_import_list_and_assign(self):
        self.assertEqual(self.login('sin_permisos', 'clave').status_code, 200)
        self.assertEqual(self.client.get(reverse('plans')).status_code, 200)
        plan = self.import_plan(self.no_permissions)
        motor = self.assign_excel(plan)
        self.assertEqual(Plan.objects.count(), 1)
        self.assertFalse(self.client.get(motor['excel']['metadata_url']).json()['can_edit'])

    def test_change_permission_allows_excel_editing_for_registered_user(self):
        self.no_permissions.user_permissions.add(Permission.objects.get(codename='change_motor', content_type__app_label='mobileApp'))
        self.client.force_login(self.no_permissions)
        self.assertEqual(self.client.get(reverse('plans')).status_code, 200)
        plan = self.import_plan(self.no_permissions)
        motor = self.assign_excel(plan)
        metadata = self.client.get(motor['excel']['metadata_url']).json()
        self.assertTrue(metadata['can_edit'])
        response = self.client.post(metadata['save_url'], {
            'revision': metadata['revision'],
            'edits': [{'sheet': 'Motor', 'row': 2, 'col': 1, 'value': 30}],
            'operations': [],
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(motor['excel']['preview_url']).json()['rows'][1][0], 30)

    def test_superuser_has_admin_button_capability_and_existing_admin_session(self):
        response = self.login('admin', 'clave-admin')
        self.assertTrue(response.json()['user']['permissions']['can_administer'])
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 200)

    def test_staff_user_with_all_permissions_can_administer(self):
        self.no_permissions.is_staff = True
        self.no_permissions.save()
        self.no_permissions.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.no_permissions)
        self.assertTrue(self.client.get(reverse('auth_session')).json()['user']['permissions']['can_administer'])
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 200)

    def test_reader_cannot_access_user_administration(self):
        self.client.force_login(self.reader)
        self.assertFalse(self.client.get(reverse('auth_session')).json()['user']['permissions']['can_administer'])
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 302)

    def test_anonymous_access_to_every_plan_and_excel_endpoint_is_rejected(self):
        plan = self.import_plan()
        motor = self.assign_excel(plan)
        client = Client()
        for url in [reverse('plans'), plan['detail_url'], plan['svg_url'], motor['excel']['url'],
                    motor['excel']['preview_url'], motor['excel']['metadata_url'], motor['excel']['cells_url']]:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 401)
        self.assertEqual(client.post(reverse('plan_import'), {'file': dxf_file()}).status_code, 401)
        self.assertEqual(client.post(motor['assign_url'], {'file': xlsx_file()}).status_code, 401)
        for url in [plan['update_url'], plan['delete_url'], plan['markers_url'], motor['excel']['save_url']]:
            with self.subTest(url=url):
                self.assertEqual(client.post(url, {}, content_type='application/json').status_code, 401)

    def test_plan_owner_requires_separate_change_and_delete_permissions(self):
        plan = self.import_plan(self.no_permissions)
        for url in (plan['update_url'], plan['delete_url']):
            self.assertEqual(self.client.post(url, {'name': 'Cambiado'}, content_type='application/json').status_code, 403)
        self.no_permissions.user_permissions.add(Permission.objects.get(
            codename='change_plan', content_type__app_label='mobileApp',
        ))
        renamed = self.client.post(plan['update_url'], {'name': 'Sala de bombas'}, content_type='application/json')
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(self.client.get(plan['detail_url']).json()['name'], 'Sala de bombas')
        self.assertEqual(self.client.post(plan['delete_url']).status_code, 403)
        self.no_permissions.user_permissions.clear()
        self.no_permissions.user_permissions.add(Permission.objects.get(
            codename='delete_plan', content_type__app_label='mobileApp',
        ))
        self.assertEqual(self.client.post(plan['update_url'], {'name': 'Otra sala'}, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(plan['delete_url']).status_code, 200)
        self.assertFalse(Plan.objects.filter(pk=plan['id']).exists())

    def test_plan_rename_validates_name_without_changing_saved_plan(self):
        plan = self.import_plan(self.admin)
        for name in ('', '  ', 'x' * 256, 123, None):
            with self.subTest(name=name):
                response = self.client.post(plan['update_url'], {'name': name}, content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Plan.objects.get(pk=plan['id']).name, plan['name'])

    def test_all_registered_users_can_add_persistent_cad_buttons_at_world_coordinates(self):
        plan = self.import_plan(self.no_permissions)
        reference = plan['motors'][0]
        response = self.client.post(plan['markers_url'], {
            'svg_x': reference['svg_x'], 'svg_y': reference['svg_y'], 'name': 'Motor manual',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content[:500])
        motor = response.json()['motor']
        self.assertEqual(motor['name'], 'Motor manual')
        self.assertAlmostEqual(motor['x'], reference['x'])
        self.assertAlmostEqual(motor['y'], reference['y'])
        self.assertTrue(motor['entity_key'].startswith('manual/'))
        other = Client()
        other.force_login(self.reader)
        saved = other.get(plan['detail_url']).json()['motors']
        self.assertEqual(len(saved), len(plan['motors']) + 1)
        self.assertIn(motor['id'], [item['id'] for item in saved])

    def test_cad_buttons_reject_invalid_or_outside_positions(self):
        plan = self.import_plan(self.reader)
        reference = plan['motors'][0]
        for x, y in ((True, 2), ('10', 2), (None, 2), (float('nan'), 2), (-1e30, 1e30)):
            with self.subTest(x=x, y=y):
                response = self.client.post(plan['markers_url'], {'svg_x': x, 'svg_y': y}, content_type='application/json')
                self.assertEqual(response.status_code, 400)
        for name in (' ', 'x' * 256, 123):
            with self.subTest(name=name):
                response = self.client.post(plan['markers_url'], {
                    'svg_x': reference['svg_x'], 'svg_y': reference['svg_y'], 'name': name,
                }, content_type='application/json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Motor.objects.filter(plan_id=plan['id']).count(), len(plan['motors']))

    def test_older_saved_svg_recovers_cad_transform_without_losing_attachments(self):
        import re
        plan = self.import_plan(self.reader)
        motor = self.assign_excel(plan)
        stored = Plan.objects.get(pk=plan['id'])
        stored.svg = re.sub(r' data-wcs-transform="[^"]*"', '', stored.svg)
        stored.save(update_fields=['svg'])
        original_svg = stored.svg
        reference = plan['motors'][0]
        response = self.client.post(plan['markers_url'], {
            'svg_x': reference['svg_x'], 'svg_y': reference['svg_y'], 'name': 'Motor nuevo',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content[:500])
        self.assertAlmostEqual(response.json()['motor']['x'], reference['x'])
        self.assertAlmostEqual(response.json()['motor']['y'], reference['y'])
        stored.refresh_from_db()
        self.assertEqual(stored.svg, original_svg)
        self.assertEqual(Motor.objects.get(pk=motor['id']).workbook_id, motor['excel']['id'])

    def test_excel_library_survives_plan_deletion_and_can_be_reused_by_reader(self):
        plan = self.import_plan(self.no_permissions)
        motor = self.assign_excel(plan)
        document = WorkbookDocument.objects.get(pk=motor['excel']['id'])
        registered = self.client.post(reverse('register_motor'), {
            'workbook_id': document.pk, 'revision': document.revision, 'sheet': motor['sheet_name'], 'name': 'Motor reutilizable',
        }, content_type='application/json')
        self.assertEqual(registered.status_code, 201, registered.content[:400])
        record = registered.json()['motor']
        document.refresh_from_db()
        original = bytes(document.content)
        source_path = Path(Plan.objects.get(pk=plan['id']).source.path)
        self.assertFalse(Motor.objects.get(pk=motor['id']).excel)
        self.client.force_login(self.reader)
        self.assertIn(record['id'], [item['id'] for item in self.client.get(reverse('saved_motors')).json()['motors']])
        self.assertEqual(self.client.post(plan['delete_url']).status_code, 403)
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(plan['delete_url']).status_code, 200)
        self.assertFalse(source_path.exists())
        self.assertFalse(Motor.objects.filter(pk=motor['id']).exists())
        document.refresh_from_db()
        self.assertEqual(bytes(document.content), original)
        second_plan = self.import_plan(self.reader)
        response = self.client.post(second_plan['motors'][0]['assign_url'], {
            'saved_motor_id': record['id'],
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json()['motor']['excel']['id'], document.pk)
        preview = self.client.get(response.json()['motor']['excel']['preview_url']).json()
        self.assertEqual(preview['rows'][1][0], 15)
        self.assertTrue(WorkbookDocument.objects.filter(pk=record['workbook']['id']).exists())

    def test_legacy_cad_attachment_upgrades_without_changing_original_bytes(self):
        plan = self.import_plan(self.reader)
        motor = self.assign_excel(plan)
        saved = Motor.objects.get(pk=motor['id'])
        legacy = xlsx_file()
        original = legacy.read()
        legacy.seek(0)
        saved.excel.save(legacy.name, legacy, save=False)
        saved.excel_name = legacy.name
        saved.save(update_fields=['excel', 'excel_name'])
        saved.workbook.delete()
        response = self.client.get(reverse('motor_workbook_metadata', args=[saved.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_edit'])
        saved.refresh_from_db()
        self.assertIsNotNone(saved.workbook_id)
        with saved.excel.open('rb') as source:
            self.assertEqual(source.read(), original)
        self.assertEqual(self.client.get(reverse('motor_excel_preview', args=[saved.pk])).json()['rows'][1][0], 15)

    def test_preview_is_bounded_and_reports_invalid_sheets(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        workbook = Workbook()
        workbook.active.title = 'Grande'
        for row in range(105):
            workbook.active.append([row] * 22)
        workbook.create_sheet('Segunda').append(['Otra hoja'])
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        plan = self.import_plan()
        response = self.client.post(plan['motors'][0]['assign_url'], {'file': SimpleUploadedFile('grande.xlsx', stream.getvalue())})
        url = response.json()['motor']['excel']['preview_url']
        preview = self.client.get(url, {'sheet': 'Grande'}).json()
        self.assertEqual(len(preview['rows']), 100)
        self.assertEqual(len(preview['rows'][0]), 20)
        self.assertTrue(preview['truncated'])
        self.assertEqual(self.client.get(url, {'sheet': 'Segunda'}).json()['rows'][0][0], 'Otra hoja')
        self.assertEqual(self.client.get(url, {'sheet': 'Inexistente'}).status_code, 400)

    def test_authenticated_responses_are_not_cached(self):
        plan = self.import_plan()
        for url in [reverse('auth_session'), reverse('plans'), plan['detail_url'], plan['svg_url']]:
            self.assertIn('no-store', self.client.get(url)['Cache-Control'])
