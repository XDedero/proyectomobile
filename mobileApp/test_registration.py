"""Account creation, Chilean identities and personal password changes."""
from importlib import import_module
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .accounts import next_test_rut, normalize_rut, rut_check_digit
from .models import (Company, CompanyAccount, CompanyAdminSetup, CompanyBrand, CompanyDriveConnection, PdfPlan,
                     RegistrationRequest, RELONCAVI_COMPANY_ID, UserProfile)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('admin-registration', password='Zorro!Luna2026')
        cls.worker = get_user_model().objects.create_user('trabajador-registration', password='Zorro!Luna2026')
        UserProfile.objects.create(user=cls.worker, rut='11111111-1', role='worker')

    def post(self, name, data, client=None):
        return (client or self.client).post(reverse(name), data, content_type='application/json')

    def primary_administrator(self):
        user = get_user_model().objects.create_superuser('admin', password='Zorro!Luna2026')
        UserProfile.objects.create(user=user, rut=next_test_rut(), rut_is_test=True,
                                   role='administrator', is_primary_administrator=True)
        return user

    def ordinary_administrator(self):
        user = get_user_model().objects.create_user('supervisor', password='Zorro!Luna2026', is_staff=True)
        UserProfile.objects.create(user=user, rut=next_test_rut(), role='administrator')
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label='mobileApp', codename='administer_application'))
        return user

    def payload(self, **changes):
        return {'action': 'create', 'first_name': 'María José', 'last_name': 'Muñoz Ochoa', 'rut': '12345678-5',
                'password': 'Cascada!Sur2026', 'role': 'worker', **changes}

    def verified_company(self, slug, admin_rut):
        company = Company.objects.create(name=slug, slug=slug, drive_ready=True)
        CompanyDriveConnection.objects.create(company=company, account_id=slug, root_id=slug, folder_ids={},
                                              encrypted_credentials=b'fixture', verified_at=timezone.now())
        CompanyAdminSetup.objects.create(company=company, rut=admin_rut, first_name='Ana', last_name='Sur',
                                         password_hash=make_password('Partida!Sur2026'))
        return company

    def test_only_primary_can_create_accounts_and_users_sign_in_with_rut(self):
        url = reverse('management_panel')
        self.assertNotContains(self.client.get(reverse('index')), 'Solicitar registro')
        for path in ('/api/auth/register/', '/api/auth/register/companies/', '/api/administration/requests/pending/'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.post('manage_user', self.payload()).status_code, 401)
        self.client.force_login(self.worker)
        self.assertEqual(self.post('manage_user', self.payload()).status_code, 403)
        self.client.force_login(self.ordinary_administrator())
        self.assertNotContains(self.client.get(url), 'id="newUser"')
        self.assertEqual(self.post('manage_user', self.payload()).status_code, 403)
        self.client.force_login(self.primary_administrator())
        panel = self.client.get(url)
        self.assertContains(panel, 'id="newUser"')
        self.assertNotContains(panel, 'Solicitudes de registro')
        for change in ({'rut': '12345678-9'}, {'first_name': ''}, {'password': '12345678'}, {'role': 'root'}):
            self.assertEqual(self.post('manage_user', self.payload(**change)).status_code, 400)
        for rut, role in (('12345678-5', 'worker'), ('22222222-2', 'viewer'), ('33333333-3', 'administrator')):
            response = self.post('manage_user', self.payload(rut=rut, role=role))
            self.assertEqual(response.status_code, 201, response.content)
            self.assertNotIn('password_hash', response.json()['user'])
            profile = UserProfile.objects.get(rut=rut)
            self.assertNotEqual(profile.user.password, self.payload()['password'])
            self.assertEqual(profile.role, role)
            self.assertTrue(profile.user.check_password(self.payload()['password']))
            other = Client()
            self.assertEqual(self.post('auth_login', {'rut': profile.user.username, 'password': self.payload()['password']}, other).status_code, 401)
            login = self.post('auth_login', {'rut': rut, 'password': self.payload()['password']}, other)
            self.assertEqual(login.status_code, 200, login.content)
            self.assertEqual(login.json()['user']['role'], role)
            self.assertFalse(login.json()['user']['primary_administrator'])
        self.assertEqual(self.post('manage_user', self.payload(rut='12.345.678-5')).status_code, 400)
        self.assertNotIn('requests', self.client.get(reverse('management_data')).json())

    def test_personal_password_change_requires_current_key_and_keeps_the_current_session(self):
        user = self.worker
        url = reverse('change_password')
        self.assertRedirects(self.client.get(url), reverse('index'))
        old = 'Zorro!Luna2026'
        new = 'Acequia!Norte2026'
        stale = Client()
        stale.force_login(user)
        self.client.force_login(user)
        page = self.client.get(url)
        self.assertContains(page, 'Contraseña actual')
        values = {'current_password': old, 'new_password': new, 'confirm_password': new}
        self.assertEqual(self.client.post(url, {**values, 'current_password': 'incorrecta'}).status_code, 400)
        self.assertEqual(self.client.post(url, {**values, 'confirm_password': 'diferente'}).status_code, 400)
        self.assertRedirects(self.client.post(url, values), url)
        self.assertEqual(self.client.get(reverse('auth_session')).json()['user']['id'], user.pk)
        self.assertIsNone(stale.get(reverse('auth_session')).json()['user'])
        fresh = Client()
        self.assertEqual(self.post('auth_login', {'rut': user.app_profile.rut, 'password': old}, fresh).status_code, 401)
        self.assertEqual(self.post('auth_login', {'rut': user.app_profile.rut, 'password': new}, fresh).status_code, 200)

    def test_account_creation_and_password_change_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.primary_administrator())
        client.get(reverse('index'))
        self.assertEqual(self.post('manage_user', self.payload(), client).status_code, 403)
        self.assertEqual(client.post(reverse('change_password'), {}).status_code, 403)

    @override_settings(PLANOS_DATABASE_SCHEMA='central')
    def test_company_principal_creates_accounts_in_its_company_and_members_change_password(self):
        from .tenant_runtime import Workspace

        company = self.verified_company('pitrufquen', '22222222-2')
        other = self.verified_company('valdivia', '44444444-4')
        CompanyAccount.objects.create(company=other, rut='12345678-5', first_name='Rosa', last_name='Sur',
                                      password_hash=make_password('Partida!Sur2026'), role='viewer')
        with patch('mobileApp.tenant_runtime.resolve_workspace', return_value=(Workspace('', str(company.pk), {}, {}), False)), \
             patch('mobileApp.tenant_runtime.using_schema', side_effect=lambda _: nullcontext()), \
             patch('mobileApp.accounts.local_user_for_rut', return_value=None):
            self.assertEqual(self.post('auth_login', {'rut': '22.222.222-2', 'password': 'Partida!Sur2026'}).status_code, 200)
            self.assertContains(self.client.get(reverse('management_panel')), 'id="newUser"')
            self.assertEqual(self.post('manage_user', self.payload()).status_code, 400)
            created = self.post('manage_user', self.payload(rut='33333333-3', role='administrator', company_id=str(other.pk)))
            self.assertEqual(created.status_code, 201, created.content)
            member = CompanyAccount.objects.get(rut='33333333-3')
            self.assertEqual(member.company_id, company.pk)
            users = self.client.get(reverse('management_data')).json()['users']
            self.assertEqual({user['rut'] for user in users}, {'22222222-2', '33333333-3'})
            member_client = Client()
            credentials = {'rut': member.rut, 'password': self.payload()['password']}
            self.assertEqual(self.post('auth_login', credentials, member_client).status_code, 200)
            self.assertNotContains(member_client.get(reverse('management_panel')), 'id="newUser"')
            self.assertEqual(self.post('manage_user', self.payload(), member_client).status_code, 403)
            new = 'Carretera!Norte2026'
            url = reverse('change_password')
            self.assertRedirects(member_client.post(url, {
                'current_password': credentials['password'], 'new_password': new, 'confirm_password': new,
            }), url)
            self.assertEqual(member_client.get(reverse('auth_session')).json()['user']['rut'], member.rut)
            member.refresh_from_db()
            self.assertTrue(check_password(new, member.password_hash))
            self.assertEqual(self.post('auth_login', credentials, Client()).status_code, 401)
            self.assertEqual(self.post('auth_login', {**credentials, 'password': new}, Client()).status_code, 200)

    def test_only_the_general_admin_can_edit_its_brand(self):
        url = reverse('company_profile')
        data = {'name': 'Reloncaví Taller', 'tax_id': '12.345.678-5', 'color': '#fff000', 'revision': '0'}
        self.client.force_login(self.ordinary_administrator())
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.primary_administrator())
        self.assertContains(self.client.get(url), 'aria-controls="companyPalette"')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(CompanyBrand.objects.get(company_id=RELONCAVI_COMPANY_ID).base_color, '#fff000')
        self.assertEqual(Company.objects.get(pk=RELONCAVI_COMPANY_ID).tax_id, '12345678-5')
        self.assertContains(self.client.get(reverse('index')), 'Reloncaví Taller')
        for page in (url, reverse('management_panel')):
            self.assertContains(self.client.get(page), 'data-company-themed')
        self.assertEqual(self.client.post(url, {**data, 'name': 'Edición antigua'}).status_code, 400)
        self.assertEqual(Company.objects.get(pk=RELONCAVI_COMPANY_ID).name, 'Reloncaví Taller')

    def test_company_interface_theme_is_scoped_to_the_selected_company(self):
        from .company_brand import brand_context, palette
        from .tenant_runtime import Workspace, workspace

        default = self.client.get(reverse('index'))
        self.assertContains(default, 'data-product-brand')
        self.assertContains(default, '<title>planimetry cl</title>')
        CompanyBrand.objects.update_or_create(company_id=RELONCAVI_COMPANY_ID, defaults={'base_color': '#2368a7'})
        company = Company.objects.create(name='Taller Sur', slug='taller-sur')
        CompanyBrand.objects.create(company=company, base_color='#f97316')
        anonymous = self.client.get(reverse('index'))
        self.assertContains(anonymous, 'data-product-brand')
        self.assertNotContains(anonymous, 'data-plan-button-color="#2368a7"')

        self.client.force_login(self.admin)
        legacy = self.client.get(reverse('index'))
        self.assertContains(legacy, 'data-company-themed')
        self.assertNotContains(legacy, 'data-product-brand')
        self.assertContains(legacy, 'data-plan-button-color="#2368a7"')
        self.assertContains(legacy, 'data-company-id=""')

        brand = brand_context(company)
        token = workspace.set(Workspace('', str(company.pk), brand, {}))
        try:
            response = self.client.get(reverse('index'))
        finally:
            workspace.reset(token)
        primary = brand['company_theme']['primary']
        self.assertContains(response, 'company-theme.css')
        self.assertContains(response, f'data-plan-button-color="{primary}"')
        self.assertContains(response, '<title>Taller Sur</title>')
        self.assertContains(response, f'data-company-id="{company.pk}"')

        token = workspace.set(Workspace('', str(company.pk), brand, {}))
        try:
            data = self.client.get(reverse('auth_session')).json()
        finally:
            workspace.reset(token)
        self.assertEqual(data['user']['appearance'], brand)
        self.assertEqual(data['user']['company_id'], str(company.pk))
        legacy_appearance = self.client.get(reverse('auth_session')).json()['user']['appearance']
        self.assertEqual(legacy_appearance['company_identity'], '')
        self.assertEqual(legacy_appearance['company_theme'], palette('#2368a7'))

    def test_second_company_cannot_activate_while_runtime_permissions_are_global(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Company.objects.create(name='Taller Sur', slug='taller-sur', status=Company.Status.ACTIVE, drive_ready=True)

    def test_rut_validation_normalizes_equivalent_formats_and_checks_verifier(self):
        for value in ('12.345.678-5', '12345678-5', '123456785'):
            self.assertEqual(normalize_rut(value), '12345678-5')
        self.assertEqual(normalize_rut('10.000.013-k'), '10000013-K')
        for invalid in ('12.345.678-9', '123', '<script>', None, '00000000-0', '1２345678-5'):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                normalize_rut(invalid)

    def test_cleanup_removes_resolved_requests_and_preserves_users_and_pending(self):
        pending = RegistrationRequest.objects.create(first_name='Solicitud', last_name='Pendiente', rut='12345678-5')
        for status in ('approved', 'rejected'):
            RegistrationRequest.objects.create(first_name='Solicitud', last_name='Resuelta',
                                                rut='22222222-2', status=status, user=self.worker)
        cleanup = import_module('mobileApp.migrations.0029_pending_registration_requests').remove_resolved_requests
        cleanup(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(RegistrationRequest.objects.values_list('pk', flat=True)), [pending.pk])
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_role_promotion_invalidates_prior_sessions_and_requires_login(self):
        client = Client()
        client.force_login(self.worker)
        self.client.force_login(self.ordinary_administrator())
        response = self.post('manage_user', {'id': self.worker.pk, 'role': 'administrator'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['requires_login'])
        self.assertIsNone(client.get(reverse('auth_session')).json()['user'])
        response = self.post('auth_login', {'rut': self.worker.app_profile.rut, 'password': 'Zorro!Luna2026'}, client)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['user']['permissions']['can_administer'])
        self.assertEqual(client.get(reverse('management_data')).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.post('manage_user', {'id': self.worker.pk, 'role': 'viewer'}).status_code, 403)
        self.assertTrue(client.get(reverse('auth_session')).json()['user']['permissions']['can_administer'])

    def test_administrators_cannot_change_or_delete_other_administrators(self):
        User = get_user_model()
        other = User.objects.create_superuser('otro-administrador', password='Bosque!Invierno2026')
        UserProfile.objects.create(user=other, rut='22222222-2', role='administrator')
        legacy = User.objects.create_superuser('administrador-anterior', password='Bosque!Invierno2026')
        self.client.force_login(self.admin)
        for target in (other, legacy):
            original_password = target.password
            for change in ({'first_name': 'Cambio'}, {'last_name': 'Cambio'}, {'rut': '12345678-5'},
                           {'password': 'Otra!Clave2026'}, {'role': 'viewer'}, {'action': 'delete'}):
                with self.subTest(target=target.username, change=change):
                    self.assertEqual(self.post('manage_user', {'id': target.pk, **change}).status_code, 403)
            target.refresh_from_db()
            self.assertEqual(target.first_name, '')
            self.assertEqual(target.last_name, '')
            self.assertEqual(target.password, original_password)
            self.assertTrue(target.is_superuser)
        other.is_active = False
        other.save()
        self.assertEqual(self.post('manage_user', {'id': other.pk, 'role': 'viewer'}).status_code, 403)
        data = self.client.get(reverse('management_data')).json()['users']
        for user in data:
            if user['id'] in (other.pk, legacy.pk):
                self.assertFalse(user['can_edit'])
                self.assertFalse(user['can_delete'])

    def test_administrator_can_edit_own_details_and_password(self):
        self.client.force_login(self.admin)
        users = self.client.get(reverse('management_data')).json()['users']
        own = next(user for user in users if user['id'] == self.admin.pk)
        self.assertTrue(own['can_edit'])
        self.assertFalse(own['can_delete'])
        result = self.post('manage_user', {'id': self.admin.pk, 'first_name': 'María', 'last_name': 'Pérez',
                                         'rut': '12345678-5', 'password': 'Bosque!Invierno2026'})
        self.assertEqual(result.status_code, 200, result.content)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, 'María')
        self.assertEqual(self.admin.last_name, 'Pérez')
        self.assertEqual(self.admin.app_profile.rut, '12345678-5')
        self.assertEqual(self.admin.app_profile.role, 'administrator')
        self.assertTrue(self.admin.check_password('Bosque!Invierno2026'))
        self.assertIsNone(self.client.get(reverse('auth_session')).json()['user'])
        login = self.post('auth_login', {'rut': self.admin.app_profile.rut, 'password': 'Bosque!Invierno2026'})
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()['user']['permissions']['can_administer'])

    def test_django_admin_cannot_bypass_protection_of_another_administrator(self):
        other = get_user_model().objects.create_superuser('otro-administrador', password='Bosque!Invierno2026')
        self.client.force_login(self.admin)
        for name in ('auth_user_change', 'auth_user_password_change', 'auth_user_delete'):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse('admin:' + name, args=[other.pk]),
                                                  {'first_name': 'Cambio', 'post': 'yes'}).status_code, 403)
        response = self.client.post(reverse('admin:auth_user_changelist'),
                                    {'action': 'delete_selected', '_selected_action': [str(other.pk)], 'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(get_user_model().objects.filter(pk=other.pk).exists())
        self.assertEqual(self.client.get(reverse('admin:auth_user_change', args=[self.admin.pk])).status_code, 200)

    def test_administrator_can_change_own_role_if_another_administrator_remains(self):
        get_user_model().objects.create_superuser('administrador-respaldo', password='Bosque!Invierno2026')
        self.client.force_login(self.admin)
        response = self.post('manage_user', {'id': self.admin.pk, 'role': 'worker'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['requires_login'])
        self.assertIsNone(self.client.get(reverse('auth_session')).json()['user'])
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.app_profile.role, 'worker')
        self.assertFalse(self.admin.is_staff)
        self.assertFalse(self.admin.is_superuser)

    def test_editing_rut_preserves_stable_plan_ownership_and_changes_public_login(self):
        self.client.force_login(self.primary_administrator())
        self.assertEqual(self.post('manage_user', self.payload()).status_code, 201)
        user = UserProfile.objects.get(rut=self.payload()['rut']).user
        username = user.username
        plan = PdfPlan.objects.create(name='Nube', size=1, owner_username=username,
                                      cloud_origin='drive-fixture', imported_by=user)
        self.assertEqual(self.post('manage_user', {'id': user.pk, 'rut': '22222222-2'}).status_code, 200)
        user.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(user.username, username)
        self.assertEqual(plan.owner_username, username)
        client = Client()
        self.assertEqual(self.post('auth_login', {'username': '12345678-5', 'password': 'Cascada!Sur2026'}, client).status_code, 401)
        response = self.post('auth_login', {'username': '22.222.222-2', 'password': 'Cascada!Sur2026'}, client)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['user']['id'], user.pk)

    def test_edit_and_delete_accounts_recheck_rut_and_guard_last_admin(self):
        self.client.force_login(self.admin)
        response = self.post('manage_user', {'id': self.worker.pk, 'first_name': 'Luis', 'last_name': 'Ochoa',
                                           'rut': '12345678-5', 'role': 'worker'})
        self.assertEqual(response.status_code, 200, response.content)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.first_name, 'Luis')
        self.assertEqual(self.worker.app_profile.rut, '12345678-5')
        self.assertEqual(self.post('manage_user', {'id': self.admin.pk, 'role': 'viewer'}).status_code, 400)
        self.assertEqual(self.post('manage_user', {'id': self.admin.pk, 'action': 'delete'}).status_code, 400)
        self.assertEqual(self.post('manage_user', {'id': self.worker.pk, 'action': 'delete'}).status_code, 200)
        self.assertFalse(get_user_model().objects.filter(pk=self.worker.pk).exists())

    def test_migration_assigns_unique_valid_test_ruts_without_changing_passwords(self):
        seed = import_module('mobileApp.migrations.0028_registration_accounts').seed_existing_accounts
        original_password = self.admin.password
        legacy_username = f'90.000.000-{rut_check_digit("90000000")}'
        legacy_user = get_user_model().objects.create_user(legacy_username, password='Prueba!Sur2026')
        seed(apps, SimpleNamespace(connection=connection))
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.password, original_password)
        self.assertEqual(self.admin.app_profile.role, 'administrator')
        self.assertTrue(self.admin.app_profile.rut_is_test)
        self.assertEqual(normalize_rut(self.admin.app_profile.rut), self.admin.app_profile.rut)
        self.assertEqual(UserProfile.objects.count(), get_user_model().objects.count())
        self.assertFalse(UserProfile.objects.filter(rut=normalize_rut(legacy_username)).exists())
        response = self.post('auth_login', {'username': legacy_username, 'password': 'Prueba!Sur2026'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['user']['id'], legacy_user.pk)
