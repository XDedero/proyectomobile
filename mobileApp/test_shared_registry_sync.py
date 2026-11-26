"""Direct enrollment in the shared identity registry."""
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.test import TestCase, override_settings
from django.urls import reverse

from .cloud_projects import CloudUnavailable
from .models import UserProfile
from .shared_accounts import apply_account


@override_settings(APP_SHARED_ACCOUNTS=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SharedAccountCreationTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_superuser('general', password='Bosque!Invierno2026')
        UserProfile.objects.create(user=owner, rut='11111111-1', role='administrator', is_primary_administrator=True)
        self.client.force_login(owner)
        self.data = {
            'action': 'create', 'first_name': 'Ana', 'last_name': 'Pérez', 'rut': '12345678-5',
            'role': 'worker', 'password': 'Cascada!Sur2026',
        }

    def post(self):
        return self.client.post(reverse('manage_user'), self.data, content_type='application/json')

    def test_shared_account_is_written_then_materialized_with_a_hash(self):
        identifier = uuid4()
        row = {
            'id': identifier, 'username': f'cuenta_{identifier.hex}', 'rut': self.data['rut'],
            'first_name': self.data['first_name'], 'last_name': self.data['last_name'], 'role': self.data['role'],
            'password_hash': make_password(self.data['password']), 'is_active': True, 'revision': 1,
        }
        with patch('mobileApp.shared_accounts.query', return_value=[row]) as query:
            response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        sql = query.call_args.args[0]
        self.assertIn('INSERT INTO app_identity.accounts', sql)
        self.assertNotIn(self.data['password'], sql)
        self.assertTrue(query.call_args.kwargs['write'])
        profile = UserProfile.objects.get(rut=self.data['rut'])
        self.assertEqual(profile.cloud_id, identifier)
        self.assertTrue(profile.user.check_password(self.data['password']))

    def test_cloud_failures_and_duplicates_do_not_create_local_only_accounts(self):
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            self.assertEqual(self.post().status_code, 503)
        with patch('mobileApp.shared_accounts.query', return_value=[]):
            self.assertEqual(self.post().status_code, 400)
        self.assertFalse(UserProfile.objects.filter(rut=self.data['rut']).exists())

    def test_shared_user_updates_the_registry_when_changing_its_own_password(self):
        identifier = uuid4()
        row = {
            'id': identifier, 'username': f'cuenta_{identifier.hex}', 'rut': self.data['rut'],
            'first_name': self.data['first_name'], 'last_name': self.data['last_name'], 'role': self.data['role'],
            'password_hash': make_password(self.data['password']), 'is_active': True, 'revision': 1,
        }
        user, _ = apply_account(row)
        self.client.force_login(user)
        new_password = 'Navegante!Verde2026'

        def query(sql, *, write=False):
            if not write:
                return [row]
            self.assertIn('UPDATE app_identity.accounts', sql)
            self.assertNotIn(new_password, sql)
            row['password_hash'] = sql.split("password_hash='", 1)[1].split("'", 1)[0]
            row['revision'] += 1
            return [{'revision': row['revision']}]

        with patch('mobileApp.shared_accounts.query', side_effect=query):
            url = reverse('change_password')
            response = self.client.post(url, {
                'current_password': self.data['password'], 'new_password': new_password, 'confirm_password': new_password,
            })
            self.assertRedirects(response, url)
            self.assertEqual(self.client.get(reverse('auth_session')).json()['user']['id'], user.pk)
        self.assertTrue(check_password(new_password, row['password_hash']))
        self.assertEqual(UserProfile.objects.get(user=user).cloud_revision, 2)
