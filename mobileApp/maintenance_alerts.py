"""Send advance reminders and keep overdue alerts current in maintenance inboxes."""
from datetime import timedelta
import logging
from threading import Thread
from time import sleep

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .authorization import is_administrator
from .models import MaintenanceNotification, Motor, PdfMarker


logger = logging.getLogger(__name__)
REMINDER_LEAD_TIME = timedelta(days=2)


def account_name(user):
    return user.get_full_name() or user.get_username()


def administrators():
    candidates = get_user_model().objects.filter(is_active=True).filter(
        Q(is_staff=True) | Q(is_superuser=True)
    ).select_related('app_profile')
    return {user.pk: user for user in candidates if is_administrator(user)}


def record_notifications(button, today, admins, responsible):
    recipients = set(admins)
    if responsible and responsible.is_active:
        recipients.add(responsible.pk)
    if not recipients:
        return
    due = button.next_maintenance
    kind = MaintenanceNotification.Kind.ALERT if due < today else MaintenanceNotification.Kind.REMINDER
    notifications = MaintenanceNotification.objects.filter(
        kind=kind, maintenance_key=button.maintenance_key, recipient_id__in=recipients,
    ).order_by()
    refresh_ids = []
    if kind == MaintenanceNotification.Kind.REMINDER:
        existing = set(notifications.filter(
            last_maintenance=button.last_maintenance, due_date=due,
        ).values_list('recipient_id', flat=True))
    else:
        existing = set()
        for pk, recipient, day, previous_last, previous_due in notifications.values_list(
            'pk', 'recipient_id', 'day', 'last_maintenance', 'due_date',
        ):
            existing.add(recipient)
            if day < today or (day == today and (previous_last, previous_due) != (button.last_maintenance, due)):
                refresh_ids.append(pk)
    missing = recipients - existing
    if not missing and not refresh_ids:
        return
    name = (button.name if isinstance(button, Motor) else button.label).strip()
    if not name:
        buttons = button.plan.motors if isinstance(button, Motor) else button.plan.markers
        name = f'Botón {buttons.filter(pk__lt=button.pk).count() + 1}'
    context = dict(
        day=today, last_maintenance=button.last_maintenance, due_date=due, plan_name=button.plan.name,
        button_name=name, category_name=button.category.name if button.category_id else '',
        responsible=responsible, responsible_name=account_name(responsible) if responsible else button.button_creator,
    )
    if refresh_ids:
        notifications.filter(pk__in=refresh_ids, day__lte=today).update(**context)
    if missing:
        MaintenanceNotification.objects.bulk_create([
            MaintenanceNotification(kind=kind, maintenance_key=button.maintenance_key, recipient_id=recipient, **context)
            for recipient in missing
        ], ignore_conflicts=True)


def clear_resolved_alerts(maintenance_key, new_due):
    if maintenance_key and (new_due is None or new_due >= timezone.localdate()):
        alerts = MaintenanceNotification.objects.filter(
            kind=MaintenanceNotification.Kind.ALERT, maintenance_key=maintenance_key,
        )
        # Serialize deletion with administrators who are replying to the alert.
        ids = list(alerts.select_for_update().order_by('pk').values_list('pk', flat=True))
        alerts.filter(pk__in=ids).delete()


def current_buttons(model):
    published = model.objects.filter(plan__draft_of__isnull=True, maintenance_key__isnull=False)
    # A cloud refresh may briefly leave two versions of the same button.
    newer = published.filter(maintenance_key=OuterRef('maintenance_key'), pk__gt=OuterRef('pk'))
    result = published.filter(~Exists(newer))
    return result.defer('plan__svg') if model is Motor else result


def generate_alerts(*, today=None, recipient=None):
    today = today or timezone.localdate()
    admins = administrators()
    for model in (Motor, PdfMarker):
        pending = current_buttons(model).filter(next_maintenance__lte=today + REMINDER_LEAD_TIME)
        if recipient is not None and recipient.pk not in admins:
            pending = pending.filter(button_creator=recipient.get_username())
        pending = pending.select_related('plan', 'category')
        for pk in pending.order_by('pk').values_list('pk', flat=True).iterator(chunk_size=200):
            with transaction.atomic():
                button = pending.select_for_update(of=('self',)).filter(pk=pk).first()
                if button is None:
                    continue
                creator = get_user_model().objects.filter(username=button.button_creator).first()
                record_notifications(button, today, admins, creator)


def generate_all_workspaces():
    generate_alerts()
    if not settings.PLANOS_CLOUD_MODE:
        return
    from .company_access import registration_companies
    from .models import CompanyAccount, CompanyAdminSetup, RELONCAVI_COMPANY_ID, UserProfile
    from .tenant_runtime import schema_name, using_schema, workspace_is_ready, workspace_user

    companies = registration_companies().exclude(pk=RELONCAVI_COMPANY_ID)
    for company_id in companies.values_list('pk', flat=True):
        schema = schema_name(company_id)
        if not workspace_is_ready(schema):
            continue
        accounts = [
            *CompanyAdminSetup.objects.filter(company_id=company_id),
            *CompanyAccount.objects.filter(company_id=company_id, role=UserProfile.Role.ADMINISTRATOR),
        ]
        try:
            with using_schema(schema):
                for account in accounts:
                    workspace_user(account)
                generate_alerts()
        except DatabaseError:
            logger.exception('No se pudieron generar las alertas de mantención del espacio %s.', schema)


def watch_for_maintenance_notifications():
    while True:
        close_old_connections()
        try:
            generate_all_workspaces()
        except Exception:
            logger.exception('No se pudieron generar las alertas de mantención.')
        finally:
            close_old_connections()
        sleep(300)


def start_maintenance_worker():
    Thread(target=watch_for_maintenance_notifications, name='maintenance-alerts', daemon=True).start()
