"""Failures must preserve submissions and never report a local-only save as shared."""
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .cloud_projects import CloudUnavailable
from .models import RegistrationRequest


@override_settings(APP_SHARED_ACCOUNTS=True,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SharedRegistrationTests(TestCase):
    def payload(self):
        return {'first_name': 'Prueba', 'last_name': 'Compartida', 'rut': '12345678-5',
                'password': 'Cascada!Sur2026', 'confirm_password': 'Cascada!Sur2026'}

    def test_connection_failure_retains_unsent_request_and_returns_failure(self):
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            response = self.client.post(reverse('request_registration'), self.payload(), content_type='application/json')
        self.assertEqual(response.status_code, 503)
        item = RegistrationRequest.objects.get()
        self.assertFalse(item.cloud_synced)
        self.assertNotEqual(item.password_hash, self.payload()['password'])

    def test_cloud_duplicate_does_not_leave_an_extra_local_pending_request(self):
        with patch('mobileApp.shared_accounts.query', return_value=[{'result': {'error': 'Ya hay una solicitud pendiente para ese RUT.'}}]):
            response = self.client.post(reverse('request_registration'), self.payload(), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_pending_list_imports_global_identity_without_a_password(self):
        admin = get_user_model().objects.create_superuser('shared-test-admin')
        self.client.force_login(admin)
        identifier = str(uuid4())
        data = {key: self.payload()[key] for key in ('first_name', 'last_name', 'rut')}
        data.update(id=identifier, status='pending', created_at='2026-11-10T20:00:00+00:00')
        with patch('mobileApp.shared_accounts.query', return_value=[data]):
            response = self.client.get(reverse('pending_registrations'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(RegistrationRequest.objects.get().cloud_id), identifier)
        self.assertNotContains(response, 'password')
        self.assertTrue(RegistrationRequest.objects.get().cloud_synced)

    def test_pending_api_does_not_claim_an_empty_list_during_cloud_failure(self):
        admin = get_user_model().objects.create_superuser('shared-test-admin')
        self.client.force_login(admin)
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            response = self.client.get(reverse('pending_registrations'))
            page = self.client.get(reverse('management_panel'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Sin conexión')
