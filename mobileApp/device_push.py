"""Web Push subscriptions and delivery through the maintenance worker."""
import base64
import json
import logging
import re
from contextlib import nullcontext
from datetime import timedelta
from functools import lru_cache
from hashlib import sha256
from threading import Event
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.crypto import salted_hmac
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from requests import Session as HttpSession, RequestException

from .models import DevicePushDelivery, DevicePushSubscription, MaintenanceNotification


logger = logging.getLogger(__name__)
DELIVERY_LIFETIME = timedelta(days=1)
RETRY_DELAY = timedelta(minutes=5)
PUSH_BATCH_SIZE = 40
push_worker_wakeup = Event()
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
PUSH_HOSTS = ('fcm.googleapis.com', 'push.services.mozilla.com', 'push.apple.com',
              'notify.windows.com', 'wns.windows.com')


@lru_cache(maxsize=1)
def _private_key(secret):
    # A separate derivation keeps the push identity stable across Render deploys.
    digest = salted_hmac('reloncavi.web-push.v1', '', secret, algorithm='sha512').digest()
    scalar = int.from_bytes(digest, 'big') % (P256_ORDER - 1) + 1
    return ec.derive_private_key(scalar, ec.SECP256R1())


def public_key():
    raw = _private_key(settings.SECRET_KEY).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _endpoint_host(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or not re.fullmatch(r'[!-~]+', endpoint):
        raise ValueError('Invalid push endpoint')
    url = urlsplit(endpoint)
    host = url.hostname or ''
    if (url.scheme != 'https' or url.netloc not in (host, f'{host}:443') or not url.path or url.fragment
            or not any(host == domain or host.endswith('.' + domain) for domain in PUSH_HOSTS)):
        raise ValueError('Invalid push endpoint')
    return host


def _decode_key(value, size):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value) or len(value) > 100:
        raise ValueError('Invalid subscription key')
    raw = base64.urlsafe_b64decode(value.rstrip('=') + '=' * (-len(value.rstrip('=')) % 4))
    if len(raw) != size:
        raise ValueError('Invalid subscription key')
    return raw


def validate_subscription(data):
    if not isinstance(data, dict) or not isinstance(data.get('keys'), dict):
        raise ValueError('Invalid subscription')
    endpoint, keys = data.get('endpoint'), data['keys']
    _endpoint_host(endpoint)
    p256dh, auth = keys.get('p256dh'), keys.get('auth')
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _decode_key(p256dh, 65))
    _decode_key(auth, 16)
    return {'endpoint': endpoint, 'public_key': p256dh, 'auth_secret': auth}


def reset_push(notifications):
    notifications.update(push_state=MaintenanceNotification.PushState.PENDING)
    DevicePushDelivery.objects.filter(notification__in=notifications).delete()


@transaction.atomic
def _queue_pending_notifications():
    notices = MaintenanceNotification.objects.filter(push_state=MaintenanceNotification.PushState.PENDING)
    subscriptions = DevicePushSubscription.objects.filter(recipient_id=OuterRef('recipient_id'))
    notices = notices.filter(Exists(subscriptions), MaintenanceNotification.inbox_visibility())
    batch = notices.order_by('day', 'pk').select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
    by_recipient = {}
    for notification_id, recipient_id in batch.values_list('pk', 'recipient_id')[:PUSH_BATCH_SIZE]:
        by_recipient.setdefault(recipient_id, []).append(notification_id)
    if not by_recipient:
        return
    now = timezone.now()
    subscriptions = DevicePushSubscription.objects.filter(recipient_id__in=by_recipient).values_list('pk', 'recipient_id')
    deliveries = [
        DevicePushDelivery(notification_id=item_id, subscription_id=subscription_id,
                           available_at=now, expires_at=now + DELIVERY_LIFETIME)
        for subscription_id, recipient_id in subscriptions
        for item_id in by_recipient[recipient_id]
    ]
    DevicePushDelivery.objects.bulk_create(deliveries, ignore_conflicts=True, batch_size=500)
    MaintenanceNotification.objects.filter(pk__in={delivery.notification_id for delivery in deliveries}).update(
        push_state=MaintenanceNotification.PushState.QUEUED,
    )


class _PushSession(HttpSession):
    def post(self, url, data=None, json=None, **kwargs):
        return super().post(url, data=data, json=json, allow_redirects=False, **kwargs)


def _send(delivery, ttl, schema):
    item, subscription = delivery.notification, delivery.subscription
    host = _endpoint_host(subscription.endpoint)
    topic = sha256(f'{schema}:{item.pk}'.encode()).hexdigest()[:32]
    body = item.button_name
    if not item.button_deleted:
        body = item.message or (f'{item.button_name} · Vence el {item.due_date:%d-%m-%Y}' if item.due_date else body)
    payload = {
        'title': 'Botón borrado' if item.button_deleted else item.title or item.get_kind_display(),
        'body': body[:240],
        'tag': topic,
    }
    headers = {'Topic': topic, 'Urgency': 'normal'}
    if host.endswith(('.notify.windows.com', '.wns.windows.com')):
        headers.update({'X-WNS-Type': 'wns/raw', 'Content-Type': 'application/octet-stream'})
    with _PushSession() as http:
        webpush(
            {'endpoint': subscription.endpoint, 'keys': {'p256dh': subscription.public_key, 'auth': subscription.auth_secret}},
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=Vapid(private_key=_private_key(settings.SECRET_KEY)),
            vapid_claims={'sub': settings.WEB_PUSH_CONTACT}, headers=headers, ttl=ttl,
            timeout=5, requests_session=http,
        )


def _active_session_keys(keys, schema):
    from .tenant_runtime import using_schema

    central = using_schema(settings.PLANOS_DATABASE_SCHEMA) if schema else nullcontext()
    with central:
        return set(Session.objects.filter(session_key__in=keys, expire_date__gt=timezone.now())
                   .values_list('session_key', flat=True))


def deliver_push_notifications(*, schema=None):
    DevicePushDelivery.objects.filter(expires_at__lte=timezone.now()).delete()
    _queue_pending_notifications()
    pending = DevicePushDelivery.objects.filter(available_at__lte=timezone.now()).order_by('available_at', 'pk')
    batch = list(pending.select_related('notification', 'subscription')[:PUSH_BATCH_SIZE])
    if not batch:
        return
    active = _active_session_keys({row.subscription.session_key for row in batch}, schema)
    for delivery in batch:
        if not pending.filter(pk=delivery.pk).update(available_at=timezone.now() + RETRY_DELAY):
            continue
        subscription = delivery.subscription
        if subscription.session_key not in active:
            subscription.delete()
            continue
        ttl = int((delivery.expires_at - timezone.now()).total_seconds())
        if ttl <= 0:
            delivery.delete()
            continue
        try:
            _send(delivery, ttl, schema)
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                subscription.delete()
                continue
            logger.warning('Web Push provider rejected delivery %s (HTTP %s).', delivery.pk, status)
            if status in (408, 429) or status is None or status >= 500:
                continue
        except RequestException:
            logger.warning('Web Push provider unavailable for delivery %s.', delivery.pk)
            continue
        except ValueError:
            logger.warning('Invalid Web Push data for delivery %s.', delivery.pk)
        else:
            with transaction.atomic():
                notice = MaintenanceNotification.objects.select_for_update().filter(pk=delivery.notification_id).first()
                if notice is not None and delivery.delete()[0]:
                    notice.push_state = MaintenanceNotification.PushState.SENT
                    notice.save(update_fields=['push_state'])
            continue
        delivery.delete()
    if len(batch) == PUSH_BATCH_SIZE:
        push_worker_wakeup.set()
