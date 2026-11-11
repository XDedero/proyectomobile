"""Local login remains independent while shared credentials require verification."""
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .cloud_projects import CloudUnavailable
from .models import UserProfile


@override_settings(APP_SHARED_ACCOUNTS=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AuthenticationAvailabilityTests(TestCase):
    password = 'Bosque!Invierno2026'

    def login(self, identifier, password=None):
        return self.client.post(reverse('auth_login'), {
            'username': identifier, 'password': password or self.password,
        }, content_type='application/json')

    def create_admin(self, *, shared=False, profile=True):
        user = get_user_model().objects.create_superuser('admin', password=self.password)
        if profile:
            UserProfile.objects.create(
                user=user, rut='12345678-5', role='administrator',
                is_primary_administrator=not shared, cloud_id=uuid4() if shared else None,
            )
        return user

    def test_local_primary_admin_logs_in_without_querying_the_shared_database(self):
        user = self.create_admin()
        with patch('mobileApp.shared_accounts.sync_login', side_effect=CloudUnavailable('Sin conexión')) as sync:
            response = self.login('admin')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['user']['id'], user.pk)
        self.assertTrue(response.json()['user']['primary_administrator'])
        sync.assert_not_called()

    def test_local_rut_login_keeps_format_normalization_and_whitespace_handling(self):
        user = self.create_admin()
        with patch('mobileApp.shared_accounts.sync_login', side_effect=CloudUnavailable('Sin conexión')) as sync:
            response = self.login(' 12.345.678-5 ')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['user']['id'], user.pk)
        sync.assert_not_called()

    def test_legacy_account_without_profile_still_authenticates_locally(self):
        user = self.create_admin(profile=False)
        with patch('mobileApp.shared_accounts.sync_login') as sync:
            response = self.login(' admin ')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['user']['id'], user.pk)
        sync.assert_not_called()

    def test_incorrect_local_password_is_rejected_even_when_cloud_is_down(self):
        self.create_admin()
        with patch('mobileApp.shared_accounts.sync_login', side_effect=CloudUnavailable('Sin conexión')) as sync:
            response = self.login('admin', 'Incorrecta!2026')
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('_auth_user_id', self.client.session)
        sync.assert_not_called()

    def test_shared_account_cannot_use_stale_local_credentials_during_cloud_failure(self):
        self.create_admin(shared=True)
        with patch('mobileApp.shared_accounts.sync_login', side_effect=CloudUnavailable('Sin conexión')) as sync:
            response = self.login('admin')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', self.client.session)
        sync.assert_called_once_with('admin')

    def test_unknown_identity_needs_the_shared_database(self):
        with patch('mobileApp.shared_accounts.sync_login', side_effect=CloudUnavailable('Sin conexión')) as sync:
            response = self.login('12345678-5')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('_auth_user_id', self.client.session)
        sync.assert_called_once_with('12345678-5')

    def test_new_shared_identity_is_resolved_after_sync(self):
        def synchronize(identifier):
            user = get_user_model().objects.create_user('cuenta_global', password=self.password)
            UserProfile.objects.create(user=user, rut='12345678-5', role='viewer', cloud_id=uuid4())
            return user

        with patch('mobileApp.shared_accounts.sync_login', side_effect=synchronize) as sync:
            response = self.login(' 12.345.678-5 ')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['user']['username'], 'cuenta_global')
        sync.assert_called_once_with('12.345.678-5')
