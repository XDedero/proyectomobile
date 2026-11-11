"""Registration replication accepts native PostgreSQL and legacy CLI row types."""
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase, override_settings
from django.urls import reverse

from .cloud_projects import CloudUnavailable
from .models import RegistrationRequest
from .shared_accounts import review_request, sync_requests


@override_settings(APP_SHARED_ACCOUNTS=True)
class SharedRegistrySyncTests(TestCase):
    def test_native_timestamp_and_uuid_can_be_imported_and_reconciled(self):
        identifier = uuid4()
        submitted_at = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
        row = {
            'id': identifier, 'rut': '12345678-5', 'first_name': 'Prueba',
            'last_name': 'Compartida', 'status': 'pending', 'created_at': submitted_at,
        }
        with patch('mobileApp.shared_accounts.query', return_value=[row]):
            sync_requests()
        item = RegistrationRequest.objects.get()
        self.assertEqual(item.cloud_id, identifier)
        self.assertEqual(item.created_at, submitted_at)
        self.assertTrue(item.cloud_synced)
        self.assertEqual(item.password_hash, '')

        # A second installation approved this request. Retire its local replica.
        with patch('mobileApp.shared_accounts.query', return_value=[{**row, 'status': 'approved'}]):
            sync_requests()
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_legacy_iso_timestamp_is_preserved(self):
        submitted_at = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
        row = {
            'id': str(uuid4()), 'rut': '12345678-5', 'first_name': 'Prueba',
            'last_name': 'Compartida', 'status': 'pending', 'created_at': submitted_at.isoformat(),
        }
        with patch('mobileApp.shared_accounts.query', return_value=[row]):
            sync_requests()
        self.assertEqual(RegistrationRequest.objects.get().created_at, submitted_at)

    def test_rejection_never_materializes_an_account_from_old_null_composites(self):
        item = RegistrationRequest.objects.create(
            first_name='Prueba', last_name='Compartida', rut='12345678-5', cloud_synced=True,
        )
        actor = get_user_model().objects.create_superuser('review-test-admin')
        result = {'status': 'rejected', 'account': {'id': None, 'rut': None, 'revision': None}}
        with patch('mobileApp.shared_accounts.query', return_value=[{'result': result}]):
            with patch('mobileApp.shared_accounts.apply_account') as apply_account:
                review_request(item, 'reject', 'viewer', actor)
        apply_account.assert_not_called()
        self.assertFalse(RegistrationRequest.objects.exists())

    def test_resubmission_after_another_installation_rejected_uses_a_new_identity(self):
        payload = {
            'first_name': 'Prueba', 'last_name': 'Compartida', 'rut': '12345678-5',
            'password': 'Cascada!Sur2026', 'confirm_password': 'Cascada!Sur2026',
        }
        previous = RegistrationRequest.objects.create(
            first_name=payload['first_name'], last_name=payload['last_name'], rut=payload['rut'],
            password_hash=make_password(payload['password']), cloud_synced=True,
        )
        results = [[{'status': 'rejected'}], [{'result': {'status': 'pending'}}]]
        with patch('mobileApp.shared_accounts.query', side_effect=results):
            response = self.client.post(reverse('request_registration'), payload, content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        item = RegistrationRequest.objects.get()
        self.assertNotEqual(item.cloud_id, previous.cloud_id)
        self.assertTrue(item.cloud_synced)

    def test_an_existing_shared_pending_request_remains_a_duplicate(self):
        payload = {
            'first_name': 'Prueba', 'last_name': 'Compartida', 'rut': '12345678-5',
            'password': 'Cascada!Sur2026', 'confirm_password': 'Cascada!Sur2026',
        }
        previous = RegistrationRequest.objects.create(
            first_name=payload['first_name'], last_name=payload['last_name'], rut=payload['rut'],
            password_hash=make_password(payload['password']), cloud_synced=True,
        )
        with patch('mobileApp.shared_accounts.query', return_value=[{'status': 'pending'}]) as query:
            response = self.client.post(reverse('request_registration'), payload, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RegistrationRequest.objects.get().cloud_id, previous.cloud_id)
        query.assert_called_once()

    def test_failed_refresh_keeps_the_existing_synced_request(self):
        payload = {
            'first_name': 'Prueba', 'last_name': 'Compartida', 'rut': '12345678-5',
            'password': 'Cascada!Sur2026', 'confirm_password': 'Cascada!Sur2026',
        }
        previous = RegistrationRequest.objects.create(
            first_name=payload['first_name'], last_name=payload['last_name'], rut=payload['rut'],
            password_hash=make_password(payload['password']), cloud_synced=True,
        )
        with patch('mobileApp.shared_accounts.query', side_effect=CloudUnavailable('Sin conexión')):
            response = self.client.post(reverse('request_registration'), payload, content_type='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(RegistrationRequest.objects.get().cloud_id, previous.cloud_id)
        self.assertEqual(RegistrationRequest.objects.get().password_hash, previous.password_hash)
