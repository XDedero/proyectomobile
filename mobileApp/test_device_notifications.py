from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from requests import Response

from .device_push import deliver_push_notifications, public_key
from .maintenance_alerts import generate_alerts
from .models import DevicePushDelivery, DevicePushSubscription, MaintenanceNotification, PdfMarker, PdfPlan


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class DeviceNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        accounts = get_user_model().objects
        cls.admin = accounts.create_superuser('admin', first_name='Ana')
        cls.worker = accounts.create_user('worker')
        cls.viewer = accounts.create_user('viewer')
        cls.inactive = accounts.create_user('inactive', is_active=False)

    def setUp(self):
        self.client.force_login(self.admin)
        self.worker_client = Client(enforce_csrf_checks=True)
        self.worker_client.force_login(self.worker)
        self.inbox = reverse('notifications')
        self.compose = reverse('compose_announcement')
        self.subscription = reverse('push_subscription')
        self.worker_client.get(self.inbox)

    def enable(self, client, suffix='worker-device', host='fcm.googleapis.com'):
        return client.post(self.subscription, {
            'endpoint': f'https://{host}/fcm/send/{suffix}',
            'keys': {'p256dh': public_key(), 'auth': 'AAAAAAAAAAAAAAAAAAAAAA'},
        }, content_type='application/json', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)

    def announce(self, **recipients):
        return self.client.post(self.compose, {'title': 'Corte eléctrico', 'message': 'Aviso de prueba', **recipients})

    def test_announcements_are_admin_only_and_selectable_or_broadcast(self):
        self.assertContains(self.client.get(reverse('management_panel')), self.compose)
        self.assertEqual(self.worker_client.get(self.compose).status_code, 403)
        self.assertContains(self.announce(), 'Selecciona uno o más usuarios', status_code=400)
        self.assertEqual(self.announce(recipients=[self.inactive.pk]).status_code, 400)
        self.assertEqual(self.enable(self.worker_client).status_code, 204)
        self.assertTrue(self.worker_client.get(self.subscription).json()['registered'])

        self.assertRedirects(self.announce(recipients=[self.worker.pk]), self.compose)
        only_worker = MaintenanceNotification.objects.get()
        self.assertEqual(only_worker.recipient, self.worker)
        self.assertEqual(DevicePushDelivery.objects.get().notification, only_worker)
        self.assertContains(self.worker_client.get(self.inbox), 'Corte eléctrico')
        self.assertNotContains(self.client.get(self.inbox), 'Corte eléctrico')

        self.assertRedirects(self.announce(all_users='on'), self.compose)
        self.assertSetEqual(set(MaintenanceNotification.objects.exclude(pk=only_worker.pk).values_list(
            'recipient_id', flat=True)), {self.admin.pk, self.worker.pk, self.viewer.pk})
        self.assertEqual(DevicePushDelivery.objects.count(), 2)

    def test_dismissal_is_per_recipient_and_requires_a_confirmed_maintenance(self):
        self.announce(all_users='on')
        notice = MaintenanceNotification.objects.get(recipient=self.worker)
        dismiss = reverse('dismiss_notification', args=[notice.pk])
        self.assertContains(self.worker_client.get(self.inbox), dismiss)
        self.assertEqual(self.client.post(dismiss).status_code, 404)
        self.assertEqual(self.worker_client.get(dismiss).status_code, 405)
        self.assertEqual(self.worker_client.post(dismiss).status_code, 403)

        token = self.worker_client.cookies['csrftoken'].value
        self.assertRedirects(self.worker_client.post(dismiss, {'csrfmiddlewaretoken': token}), self.inbox)
        self.assertTrue(MaintenanceNotification.objects.filter(recipient=self.viewer).exists())
        self.assertFalse(MaintenanceNotification.objects.filter(recipient=self.worker).exists())

        maintenance = MaintenanceNotification.objects.create(
            kind=MaintenanceNotification.Kind.ALERT, recipient=self.worker, responsible=self.worker,
            maintenance_key=uuid4(), last_maintenance=timezone.localdate() - timedelta(days=10),
            due_date=timezone.localdate() - timedelta(days=1), day=timezone.localdate(),
        )
        dismiss = reverse('dismiss_notification', args=[maintenance.pk])
        self.assertNotContains(self.worker_client.get(self.inbox), dismiss)
        self.assertEqual(self.worker_client.post(dismiss, {'csrfmiddlewaretoken': token}).status_code, 403)
        maintenance.kind = MaintenanceNotification.Kind.CONFIRMED
        maintenance.save(update_fields=['kind'])
        self.assertContains(self.worker_client.get(self.inbox), dismiss)
        self.assertRedirects(self.worker_client.post(dismiss, {'csrfmiddlewaretoken': token}), self.inbox)

    def test_web_push_encryption_transient_retry_and_logged_out_device(self):
        self.assertEqual(self.enable(self.worker_client, host='fcm.googleapis.com.example.org').status_code, 400)
        self.assertEqual(self.worker_client.post(self.subscription, {}, content_type='application/json').status_code, 403)
        self.assertEqual(self.enable(self.worker_client).status_code, 204)
        self.announce(recipients=[self.worker.pk])
        response = Response()
        response.status_code = 429
        response._content = b''
        with patch('mobileApp.device_push._PushSession.post', return_value=response) as post:
            deliver_push_notifications()
            sent = post.call_args.kwargs
            self.assertIn('k=' + public_key(), sent['headers']['Authorization'])
            self.assertEqual(sent['headers']['content-encoding'], 'aes128gcm')
            self.assertNotIn(b'Aviso de prueba', sent['data'])
            self.assertGreater(DevicePushDelivery.objects.get().available_at, timezone.now())
            deliver_push_notifications()
            self.assertEqual(post.call_count, 1)

        response.status_code = 201
        DevicePushDelivery.objects.update(available_at=timezone.now())
        with patch('mobileApp.device_push._PushSession.post', return_value=response) as post:
            deliver_push_notifications()
            self.assertEqual(post.call_count, 1)
        self.assertFalse(DevicePushDelivery.objects.exists())

        self.announce(recipients=[self.worker.pk])
        self.worker_client.logout()
        with patch('mobileApp.device_push._PushSession.post') as post:
            deliver_push_notifications()
            post.assert_not_called()
        self.assertFalse(DevicePushSubscription.objects.exists())

    def test_maintenance_queues_only_new_reminders_and_overdue_events(self):
        self.assertEqual(self.enable(self.worker_client).status_code, 204)
        today = timezone.localdate()
        plan = PdfPlan.objects.create(name='Sala de bombas', size=0)
        PdfMarker.objects.create(
            plan=plan, label='Bomba', page=1, x=0.5, y=0.5, maintenance_key=uuid4(),
            last_maintenance=today - timedelta(days=15), next_maintenance=today,
            maintenance_responsibles=[self.worker.username], maintenance_supervisors=[self.admin.username],
        )
        generate_alerts(today=today)
        self.assertEqual(DevicePushDelivery.objects.get().notification.kind, MaintenanceNotification.Kind.REMINDER)
        DevicePushDelivery.objects.all().delete()

        generate_alerts(today=today + timedelta(days=1))
        self.assertEqual(DevicePushDelivery.objects.get().notification.kind, MaintenanceNotification.Kind.ALERT)
        DevicePushDelivery.objects.all().delete()

        generate_alerts(today=today + timedelta(days=2))
        self.assertFalse(DevicePushDelivery.objects.exists())
