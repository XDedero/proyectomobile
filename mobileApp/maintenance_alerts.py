"""Send advance reminders and keep overdue alerts current in maintenance inboxes."""
from datetime import timedelta
import logging
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, close_old_connections, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .device_push import deliver_push_notifications, queue_notifications
from .maintenance_assignments import assigned_buttons, can_report, eligible_users, roles_for_button
from .models import LatestMaintenance, MaintenanceInboxState, MaintenanceNotification, Motor, PdfMarker


logger = logging.getLogger(__name__)
REMINDER_LEAD_TIME = timedelta(days=2)
ACTION_KINDS = (MaintenanceNotification.Kind.ALERT, MaintenanceNotification.Kind.REMINDER,
                MaintenanceNotification.Kind.RETRY)


def account_name(user):
    return user.get_full_name() or user.get_username()


def touch_inboxes(user_ids):
    if user_ids:
        revision = uuid4()
        MaintenanceInboxState.objects.bulk_create(
            [MaintenanceInboxState(user_id=pk, revision=revision) for pk in sorted(set(user_ids))],
            update_conflicts=True, update_fields=['revision'], unique_fields=['user'],
        )


def record_notifications(button, today, responsible):
    users = {user.pk: user for user in responsible}
    if not users:
        return
    if LatestMaintenance.objects.filter(pk=button.maintenance_key, performed_on=today).exists():
        return
    waiting = MaintenanceNotification.objects.filter(
        kind=MaintenanceNotification.Kind.RETRY, maintenance_key=button.maintenance_key, recipient_id__in=users,
        last_maintenance=button.last_maintenance, due_date=button.next_maintenance,
        button_deleted=False,
    ).values_list('recipient_id', flat=True)
    recipients = set(users) - set(waiting)
    if not recipients:
        return
    due = button.next_maintenance
    kind = MaintenanceNotification.Kind.ALERT if due < today else MaintenanceNotification.Kind.REMINDER
    notifications = MaintenanceNotification.objects.filter(
        kind=kind, maintenance_key=button.maintenance_key, recipient_id__in=recipients, button_deleted=False,
    ).order_by()
    refresh_ids = []
    rescheduled_ids = []
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
            rescheduled = (previous_last, previous_due) != (button.last_maintenance, due)
            if day < today or (day == today and rescheduled):
                refresh_ids.append(pk)
                if rescheduled:
                    rescheduled_ids.append(pk)
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
    )
    changed = set(missing)
    for pk, recipient in notifications.filter(pk__in=refresh_ids, day__lte=today).values_list('pk', 'recipient_id'):
        notifications.filter(pk=pk).update(**context, responsible_id=recipient, responsible_name=account_name(users[recipient]))
        changed.add(recipient)
    if missing:
        MaintenanceNotification.objects.bulk_create([
            MaintenanceNotification(kind=kind, maintenance_key=button.maintenance_key, recipient_id=recipient,
                                    responsible_id=recipient, responsible_name=account_name(users[recipient]), **context)
            for recipient in missing
        ], ignore_conflicts=True)
    if missing or rescheduled_ids:
        queue_notifications(notifications.filter(
            Q(recipient_id__in=missing) | Q(pk__in=rescheduled_ids),
            last_maintenance=button.last_maintenance, due_date=due,
        ))
    touch_inboxes(changed)


def current_buttons(model):
    published = model.objects.filter(plan__draft_of__isnull=True, maintenance_key__isnull=False)
    # A cloud refresh may briefly leave two versions of the same button.
    newer = published.filter(maintenance_key=OuterRef('maintenance_key'), pk__gt=OuterRef('pk'))
    result = published.filter(~Exists(newer))
    return result.defer('plan__svg') if model is Motor else result


def generate_alerts(*, today=None, recipient=None):
    today = today or timezone.localdate()
    if recipient is not None and not can_report(recipient):
        return
    users = eligible_users() if recipient is None else [recipient]
    for model in (Motor, PdfMarker):
        pending = current_buttons(model).filter(next_maintenance__lte=today + REMINDER_LEAD_TIME)
        if recipient is not None:
            pending = assigned_buttons(pending, recipient, responsible=True)
        pending = pending.select_related('plan', 'category')
        for pk in pending.order_by('pk').values_list('pk', flat=True).iterator(chunk_size=200):
            with transaction.atomic():
                button = pending.select_for_update(of=('self',)).filter(pk=pk).first()
                if button is None:
                    continue
                responsible, _ = roles_for_button(button, users)
                record_notifications(button, today, responsible)


def generate_all_workspaces(*, check_maintenance=True):
    from .maintenance_reports import cleanup_reports

    if check_maintenance:
        generate_alerts()
        cleanup_reports()
    deliver_push_notifications()
    if not settings.PLANOS_CLOUD_MODE:
        return
    from .company_access import usable_companies
    from .models import CompanyAccount, CompanyAdminSetup, CompanyDriveConnection, RELONCAVI_COMPANY_ID, UserProfile
    from .tenant_runtime import Workspace, load_drive, schema_name, using_schema, workspace, workspace_is_ready, workspace_user

    companies = usable_companies().exclude(pk=RELONCAVI_COMPANY_ID)
    for company_id in companies.values_list('pk', flat=True):
        schema = schema_name(company_id)
        if not workspace_is_ready(schema):
            continue
        try:
            accounts, drive = [], None
            if check_maintenance:
                accounts = [
                    *CompanyAdminSetup.objects.filter(company_id=company_id),
                    *CompanyAccount.objects.filter(company_id=company_id, role=UserProfile.Role.ADMINISTRATOR),
                ]
                drive = load_drive(company_id, CompanyDriveConnection.objects.filter(company_id=company_id).first())
            with using_schema(schema):
                for account in accounts:
                    workspace_user(account)
                if check_maintenance:
                    generate_alerts()
                if drive:
                    token = workspace.set(Workspace(schema, str(company_id), {}, drive))
                    try:
                        cleanup_reports()
                    finally:
                        workspace.reset(token)
                deliver_push_notifications(schema=schema)
        except DatabaseError:
            logger.exception('No se pudieron generar las alertas de mantención del espacio %s.', schema)


def watch_for_maintenance_notifications():
    next_maintenance = 0
    while True:
        close_old_connections()
        try:
            now = monotonic()
            check_maintenance = now >= next_maintenance
            if check_maintenance:
                next_maintenance = now + 300
            generate_all_workspaces(check_maintenance=check_maintenance)
        except Exception:
            logger.exception('No se pudieron generar las alertas de mantención.')
        finally:
            close_old_connections()
        sleep(30)


def start_maintenance_worker():
    Thread(target=watch_for_maintenance_notifications, name='maintenance-alerts', daemon=True).start()
