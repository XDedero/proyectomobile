"""Create one inbox alert per overdue day and recipient."""
from datetime import timedelta
import logging
from threading import Thread
from time import sleep

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, close_old_connections, transaction
from django.db.models import Exists, Max, OuterRef, Q
from django.utils import timezone

from .authorization import is_administrator
from .models import MaintenanceNotification, Motor, PdfMarker


logger = logging.getLogger(__name__)


def account_name(user):
    return user.get_full_name() or user.get_username()


def administrators():
    candidates = get_user_model().objects.filter(is_active=True).filter(
        Q(is_staff=True) | Q(is_superuser=True)
    ).select_related('app_profile')
    return {user.pk: user for user in candidates if is_administrator(user)}


def record_alerts(button, through, admins, responsible):
    recipients = set(admins)
    if responsible and responsible.is_active:
        recipients.add(responsible.pk)
    if not recipients:
        return
    alerts = MaintenanceNotification.objects.filter(
        kind=MaintenanceNotification.Kind.ALERT, maintenance_key=button.maintenance_key,
        last_maintenance=button.last_maintenance, recipient_id__in=recipients,
    )
    latest = dict(alerts.order_by().values('recipient_id').annotate(latest_day=Max('day')).values_list(
        'recipient_id', 'latest_day'
    ))
    days = {recipient: max(button.next_maintenance, latest.get(recipient, button.next_maintenance))
            for recipient in recipients}
    if all(day >= through for day in days.values()):
        return
    name = (button.name if isinstance(button, Motor) else button.label).strip()
    if not name:
        buttons = button.plan.motors if isinstance(button, Motor) else button.plan.markers
        name = f'Botón {buttons.filter(pk__lt=button.pk).count() + 1}'
    context = dict(
        kind=MaintenanceNotification.Kind.ALERT, maintenance_key=button.maintenance_key,
        last_maintenance=button.last_maintenance, due_date=button.next_maintenance, plan_name=button.plan.name,
        button_name=name, category_name=button.category.name if button.category_id else '',
        responsible=responsible, responsible_name=account_name(responsible) if responsible else button.button_creator,
    )
    for recipient, day in days.items():
        while day < through:
            count = min(250, (through - day).days)
            MaintenanceNotification.objects.bulk_create([
                MaintenanceNotification(recipient_id=recipient, day=day + timedelta(days=offset), **context)
                for offset in range(1, count + 1)
            ], ignore_conflicts=True)
            day += timedelta(days=count)


def finish_cycle(button, changes):
    """Catch up elapsed days before a published schedule is replaced."""
    today = timezone.localdate()
    if not button.next_maintenance or button.next_maintenance >= today:
        return
    if 'last_maintenance' not in changes and 'next_maintenance' not in changes:
        return
    creator = get_user_model().objects.filter(username=button.button_creator).first()
    record_alerts(button, today, administrators(), creator)


def current_buttons(model):
    published = model.objects.filter(plan__draft_of__isnull=True, maintenance_key__isnull=False)
    # A cloud refresh may briefly leave two versions of the same button.
    newer = published.filter(maintenance_key=OuterRef('maintenance_key'), pk__gt=OuterRef('pk'))
    result = published.filter(~Exists(newer))
    return result.defer('plan__svg') if model is Motor else result


def generate_alerts(*, today=None, recipient=None):
    today = today or timezone.localdate()
    if recipient is None:
        admins = administrators()
    else:
        admins = {recipient.pk: recipient} if is_administrator(recipient) else {}
    for model in (Motor, PdfMarker):
        overdue = current_buttons(model).filter(next_maintenance__lt=today)
        if recipient is not None and not admins:
            overdue = overdue.filter(button_creator=recipient.get_username())
        overdue = overdue.select_related('plan', 'category')
        for pk in overdue.order_by('pk').values_list('pk', flat=True).iterator(chunk_size=200):
            with transaction.atomic():
                button = overdue.select_for_update(of=('self',)).filter(pk=pk).first()
                if button is None:
                    continue
                creator = get_user_model().objects.filter(username=button.button_creator).first()
                record_alerts(button, today, admins, creator)


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


def watch_for_overdue_maintenance():
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
    Thread(target=watch_for_overdue_maintenance, name='maintenance-alerts', daemon=True).start()
