"""Approval, unique Chilean identities, account editing and session changes."""
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .accounts import next_test_rut, normalize_rut, rut_check_digit
from .models import PdfPlan, RegistrationRequest, UserProfile


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RegistrationTests(TestCase):
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

    def payload(self, **changes):
        return {'first_name': 'María José', 'last_name': 'Muñoz Ochoa', 'rut': '12345678-5',
                'password': 'Cascada!Sur2026', 'confirm_password': 'Cascada!Sur2026', **changes}

    def submit(self, **changes):
        response = self.post('request_registration', self.payload(**changes))
        self.assertEqual(response.status_code, 201, response.content)
        return RegistrationRequest.objects.get(pk=response.json()['id'])

    def decide(self, item, action):
        self.client.force_login(self.admin)
        return self.post('manage_registration', {'id': item.pk, 'action': action})

    def test_rut_validation_normalizes_equivalent_formats_and_checks_verifier(self):
        for value in ('12.345.678-5', '12345678-5', '123456785'):
            self.assertEqual(normalize_rut(value), '12345678-5')
        self.assertEqual(normalize_rut('10.000.013-k'), '10000013-K')
        for invalid in ('12.345.678-9', '123', '<script>', None, '00000000-0', '1２345678-5'):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                normalize_rut(invalid)

    def test_registration_requires_rut_without_dots_and_with_hyphen(self):
        for rut in ('12.345.678-5', '123456785', '012345678-5', '1２345678-5'):
            with self.subTest(rut=rut):
                self.assertEqual(self.post('request_registration', self.payload(rut=rut)).status_code, 400)
        self.assertFalse(RegistrationRequest.objects.exists())
        self.assertEqual(normalize_rut('10000013-k', strict=True), '10000013-K')
        self.submit()

    def test_submission_hashes_password_and_never_creates_or_logs_in_user(self):
        item = self.submit()
        self.assertEqual(item.rut, '12345678-5')
        self.assertNotEqual(item.password_hash, 'Cascada!Sur2026')
        self.assertTrue(check_password('Cascada!Sur2026', item.password_hash))
        self.assertFalse(get_user_model().objects.filter(username=item.rut).exists())
        self.assertIsNone(self.client.get(reverse('auth_session')).json()['user'])
        self.assertEqual(self.post('auth_login', {'username': item.rut, 'password': 'Cascada!Sur2026'}).status_code, 401)

    def test_duplicate_pending_and_registered_ruts_are_rejected(self):
        self.submit()
        response = self.post('request_registration', self.payload(rut='12345678-5'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RegistrationRequest.objects.count(), 1)
        self.assertEqual(self.post('request_registration', self.payload(rut='11111111-1')).status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.create(user=self.admin, rut='11.111.111-1')

    def test_invalid_names_password_confirmation_and_existing_password_policy(self):
        for changes in ({'first_name': ''}, {'last_name': '1234'}, {'rut': '12.345.678-0'},
                        {'password': 'abc', 'confirm_password': 'abc'},
                        {'password': '12345678', 'confirm_password': '12345678'}, {'confirm_password': 'distinta'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post('request_registration', self.payload(**changes)).status_code, 400)
        self.assertEqual(RegistrationRequest.objects.count(), 0)

    def test_only_admin_can_review_or_edit_users_and_password_hash_is_private(self):
        item = self.submit()
        for actor in (None, self.worker):
            if actor:
                self.client.force_login(actor)
            expected = 403 if actor else 401
            self.assertEqual(self.post('manage_registration', {'id': item.pk, 'action': 'approve'}).status_code, expected)
            self.assertEqual(self.post('manage_user', {'id': self.worker.pk, 'action': 'delete'}).status_code, expected)
        self.client.force_login(self.admin)
        data = self.client.get(reverse('management_data')).json()
        self.assertEqual(data['requests'][0]['status'], 'pending')
        self.assertNotIn('password', str(data))
        self.assertEqual(next(user for user in data['users'] if user['id'] == self.worker.pk)['rut'], '11111111-1')

    def test_approval_creates_viewer_who_can_login_with_rut_and_cannot_edit(self):
        item = self.submit()
        response = self.decide(item, 'approve')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(RegistrationRequest.objects.filter(pk=item.pk).exists())
        self.assertEqual(UserProfile.objects.get(rut=item.rut).role, 'viewer')
        client = Client()
        response = self.post('auth_login', {'username': '12.345.678-5', 'password': 'Cascada!Sur2026'}, client)
        self.assertEqual(response.status_code, 200, response.content)
        user = response.json()['user']
        self.assertEqual(user['role'], 'viewer')
        self.assertTrue(user['permissions']['can_import'])
        self.assertFalse(user['permissions']['can_administer'])
        self.assertFalse(user['permissions']['can_save_plan'])
        self.assertEqual(self.decide(item, 'approve').status_code, 404)
        self.assertEqual(self.post('request_registration', self.payload()).status_code, 400)

    def test_rejection_deletes_request_and_credential_and_allows_new_request(self):
        item = self.submit()
        self.assertEqual(self.decide(item, 'reject').status_code, 200)
        self.assertFalse(RegistrationRequest.objects.filter(pk=item.pk).exists())
        self.assertFalse(UserProfile.objects.filter(rut=item.rut).exists())
        self.assertEqual(self.client.get(reverse('management_data')).json()['requests'], [])
        self.submit()
        self.assertEqual(RegistrationRequest.objects.count(), 1)

    def test_administration_lists_all_users_and_only_pending_requests(self):
        inactive = get_user_model().objects.create_user('cuenta-inactiva', is_active=False)
        pending = self.submit()
        for status in ('approved', 'rejected'):
            RegistrationRequest.objects.create(first_name='Solicitud', last_name='Resuelta',
                                                rut='22222222-2', status=status)
        self.client.force_login(self.admin)
        data = self.client.get(reverse('management_data')).json()
        self.assertEqual({user['id'] for user in data['users']},
                         set(get_user_model().objects.values_list('pk', flat=True)))
        self.assertIn(inactive.pk, [user['id'] for user in data['users']])
        self.assertEqual([item['id'] for item in data['requests']], [pending.pk])

    def test_cleanup_removes_resolved_requests_and_preserves_users_and_pending(self):
        pending = self.submit()
        for status in ('approved', 'rejected'):
            RegistrationRequest.objects.create(first_name='Solicitud', last_name='Resuelta',
                                                rut='22222222-2', status=status, user=self.worker)
        cleanup = import_module('mobileApp.migrations.0029_pending_registration_requests').remove_resolved_requests
        cleanup(apps, SimpleNamespace(connection=connection))
        self.assertEqual(list(RegistrationRequest.objects.values_list('pk', flat=True)), [pending.pk])
        self.assertEqual(get_user_model().objects.count(), 2)

    def test_approval_rechecks_identity_uniqueness(self):
        item = self.submit()
        user = get_user_model().objects.create_user('conflicto')
        UserProfile.objects.create(user=user, rut=item.rut)
        self.assertEqual(self.decide(item, 'approve').status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.status, 'pending')
        self.assertIsNone(item.user_id)

    def test_role_promotion_invalidates_prior_sessions_and_requires_login(self):
        client = Client()
        client.force_login(self.worker)
        self.client.force_login(self.primary_administrator())
        response = self.post('manage_user', {'id': self.worker.pk, 'role': 'administrator'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['requires_login'])
        self.assertIsNone(client.get(reverse('auth_session')).json()['user'])
        response = self.post('auth_login', {'username': self.worker.username, 'password': 'Zorro!Luna2026'}, client)
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
        login = self.post('auth_login', {'username': self.admin.username, 'password': 'Bosque!Invierno2026'})
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
        item = self.submit()
        self.assertEqual(self.decide(item, 'approve').status_code, 200)
        user = UserProfile.objects.get(rut=item.rut).user
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

    def test_registration_requires_csrf_and_supported_methods(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('index'))
        self.assertEqual(self.post('request_registration', self.payload(), client).status_code, 403)
        token = client.cookies['csrftoken'].value
        response = client.post(reverse('request_registration'), self.payload(), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.client.get(reverse('request_registration')).status_code, 405)
        for payload in ('{', '[]', '{}'):
            self.assertEqual(self.client.post(reverse('request_registration'), payload, content_type='application/json').status_code, 400)

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
