"""Submission and review of maintenance work against a published button."""
from datetime import timedelta
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .device_push import reset_push
from .maintenance import next_date
from .maintenance_alerts import ACTION_KINDS, account_name, current_buttons, record_notifications, touch_inboxes
from .maintenance_assignments import eligible_users, roles_for_button
from .maintenance_storage import EvidenceDrive, EvidenceUnavailable
from .models import LatestMaintenance, MaintenanceNotification, MaintenanceSubmission, Motor, PdfMarker


OPEN_STATUSES = (MaintenanceSubmission.Status.UPLOADING, MaintenanceSubmission.Status.PENDING)
logger = logging.getLogger(__name__)


def published_button(key, *, lock=False):
    for model in (PdfMarker, Motor):
        buttons = current_buttons(model).filter(maintenance_key=key)
        if lock:
            buttons = buttons.select_for_update(of=('self',))
        button = buttons.first()
        if button is not None:
            return button
    return None


def matches_cycle(button, notice):
    return bool(button and button.last_maintenance == notice.last_maintenance and
                button.next_maintenance == notice.due_date)


def button_users(button):
    return roles_for_button(button, eligible_users())


@transaction.atomic
def reserve_report(user, notice, title, description):
    button = published_button(notice.maintenance_key, lock=True)
    if notice.button_deleted or notice.recipient_id != user.pk or notice.responsible_id != user.pk:
        raise PermissionDenied
    if not matches_cycle(button, notice):
        raise ValidationError('La programación cambió. Revisa el aviso actualizado en tu buzón.')
    responsible, supervisors = button_users(button)
    if user not in responsible:
        raise PermissionDenied
    if not supervisors:
        raise ValidationError('La mantención necesita un supervisor. Pide a un administrador que lo asigne.')
    if MaintenanceSubmission.objects.filter(maintenance_key=notice.maintenance_key, status__in=OPEN_STATUSES).exists():
        raise ValidationError('Ya hay un reporte de esta mantención en revisión.')
    return MaintenanceSubmission.objects.create(
        maintenance_key=notice.maintenance_key, submitted_by=user,
        status=MaintenanceSubmission.Status.UPLOADING,
        title=title, description=description, last_maintenance=notice.last_maintenance,
        due_date=notice.due_date, plan_name=notice.plan_name, button_name=notice.button_name,
        responsible_name=notice.responsible_name,
    )


def mark_for_cleanup(report):
    MaintenanceSubmission.objects.filter(pk=report.pk).update(
        status=MaintenanceSubmission.Status.CLEANUP, submitted_by=None,
        title='', description='', plan_name='', button_name='', responsible_name='',
    )


def cleanup_report(report, drive=None):
    try:
        if report.drive_images:
            drive = drive or EvidenceDrive()
            for file_id in report.drive_images:
                drive.delete(file_id)
    except EvidenceUnavailable:
        logger.warning('Se reintentará retirar evidencia temporal de mantención en Drive.')
        return
    MaintenanceSubmission.objects.filter(pk=report.pk, status=MaintenanceSubmission.Status.CLEANUP).delete()


def upload_evidence(report, images):
    drive = None
    try:
        if images:
            drive = EvidenceDrive()
            report.drive_images = drive.reserve(len(images))
            if not MaintenanceSubmission.objects.filter(pk=report.pk, status=report.Status.UPLOADING).update(
                    drive_images=report.drive_images):
                raise ValidationError('El botón cambió antes de enviar el reporte.')
        with transaction.atomic():
            locked = MaintenanceSubmission.objects.select_for_update().filter(pk=report.pk, status=report.Status.UPLOADING)
            if not locked.first():
                raise ValidationError('El botón cambió antes de enviar el reporte.')
            if images:
                for file_id, photo in zip(report.drive_images, images, strict=True):
                    drive.upload(file_id, photo)
            locked.update(status=report.Status.PENDING)
            button = published_button(report.maintenance_key)
            if button:
                responsible, supervisors = button_users(button)
                touch_inboxes({user.pk for user in (*responsible, *supervisors)})
    except EvidenceUnavailable:
        mark_for_cleanup(report)
        cleanup_report(report, drive)
        raise


@transaction.atomic
def review_report(report, reviewer, *, accept):
    button = published_button(report.maintenance_key, lock=True)
    report = MaintenanceSubmission.objects.select_for_update().filter(
        pk=report.pk, status=MaintenanceSubmission.Status.PENDING,
    ).first()
    if report is None:
        raise ValidationError('Este reporte ya fue revisado.')
    responsible, supervisors = button_users(button) if button else ([], [])
    if reviewer not in supervisors:
        raise PermissionDenied
    same_cycle = matches_cycle(button, report)
    if accept:
        if not same_cycle:
            raise ValidationError('La programación cambió. Rechaza este reporte para cerrar la revisión.')
        performed_on = timezone.localdate(report.created_at)
        next_due = next_date(performed_on, button.maintenance_interval, button.maintenance_unit)
        LatestMaintenance.objects.update_or_create(
            maintenance_key=report.maintenance_key,
            defaults=dict(title=report.title, description=report.description, performed_on=performed_on),
        )
        button.last_maintenance = performed_on
        button.next_maintenance = next_due
        button.save(update_fields=['last_maintenance', 'next_maintenance'])
        active_notices(button).delete()
        confirmations = []
        for user in sorted(set(responsible + supervisors), key=lambda user: user.pk):
            item, _ = MaintenanceNotification.objects.update_or_create(
                kind=MaintenanceNotification.Kind.CONFIRMED, maintenance_key=button.maintenance_key, recipient=user,
                defaults=dict(responsible_id=report.submitted_by_id, responsible_name=report.responsible_name,
                              last_maintenance=performed_on, due_date=next_due, plan_name=button.plan.name,
                              button_name=report.button_name, day=timezone.localdate(), button_deleted=False),
            )
            confirmations.append(item.pk)
        reset_push(MaintenanceNotification.objects.filter(pk__in=confirmations))
    elif button.next_maintenance and report.submitted_by in responsible:
        active_notices(button).filter(recipient=report.submitted_by).delete()
        MaintenanceNotification.objects.create(
            kind=MaintenanceNotification.Kind.RETRY, maintenance_key=button.maintenance_key,
            recipient=report.submitted_by, responsible=report.submitted_by, responsible_name=report.responsible_name,
            last_maintenance=button.last_maintenance, due_date=button.next_maintenance, plan_name=button.plan.name,
            button_name=report.button_name, day=timezone.localdate(),
        )
    mark_for_cleanup(report)
    touch_inboxes({user.pk for user in (*responsible, *supervisors)})
    return report


def active_notices(button):
    return MaintenanceNotification.objects.filter(maintenance_key=button.maintenance_key,
                                                  kind__in=ACTION_KINDS, button_deleted=False)


def refresh_button_inboxes(button, previous=None, *, users=None):
    if not button.maintenance_key:
        return
    if users is None:
        users = eligible_users()
    responsible, supervisors = roles_for_button(button, users)
    old_responsible, old_supervisors = roles_for_button(previous, users) if previous else ([], [])
    alerts = active_notices(button)
    obsolete = alerts if button.next_maintenance is None else alerts.exclude(
        recipient_id__in=[user.pk for user in responsible],
        last_maintenance=button.last_maintenance, due_date=button.next_maintenance,
    )
    affected = set(obsolete.values_list('recipient_id', flat=True))
    obsolete.delete()
    today = timezone.localdate()
    from .maintenance_alerts import REMINDER_LEAD_TIME
    if button.next_maintenance and button.next_maintenance <= today + REMINDER_LEAD_TIME:
        record_notifications(button, today, responsible)
    touch_inboxes(affected | {user.pk for user in (*responsible, *supervisors, *old_responsible, *old_supervisors)})


def archive_deleted_button(button):
    """Keep the notification and latest text after a published button is removed."""
    if not button.maintenance_key or button.plan.draft_of_id:
        return
    notices = MaintenanceNotification.objects.filter(maintenance_key=button.maintenance_key)
    affected = set(notices.values_list('recipient_id', flat=True))
    report = MaintenanceSubmission.objects.select_for_update().filter(
        maintenance_key=button.maintenance_key, status__in=OPEN_STATUSES,
    ).first()
    approved = LatestMaintenance.objects.filter(pk=button.maintenance_key).first()
    text = approved or (report if report and report.status == report.Status.PENDING else None)
    if report or approved:
        responsible, supervisors = button_users(button)
        for user in (*responsible, *supervisors):
            if user.pk not in affected:
                affected.add(user.pk)
                MaintenanceNotification.objects.create(
                    kind=MaintenanceNotification.Kind.CONFIRMED, maintenance_key=button.maintenance_key,
                    recipient=user, responsible_name='', last_maintenance=(
                        button.last_maintenance or (approved.performed_on if approved else report.last_maintenance)),
                    due_date=button.next_maintenance or button.last_maintenance or timezone.localdate(),
                    button_name=(button.name if isinstance(button, Motor) else button.label) or 'Botón',
                    plan_name=button.plan.name, day=timezone.localdate(),
                )
    if report:
        mark_for_cleanup(report)
    values = {'button_deleted': True}
    if text:
        values.update(title=text.title, description=text.description)
    else:
        values['description'] = '\n'.join(
            f"{row['key']}: {row['value']}" for row in (button.descriptions or []) if row.get('value')
        )
    notices.update(**values)
    touch_inboxes(affected)


def archive_plan_buttons(plan, buttons):
    if not plan.draft_of_id:
        for button in buttons.filter(maintenance_key__isnull=False).select_for_update().order_by('pk'):
            button.plan = plan
            archive_deleted_button(button)


def cleanup_reports():
    cutoff = timezone.now() - timedelta(hours=1)
    MaintenanceSubmission.objects.filter(
        status=MaintenanceSubmission.Status.UPLOADING, created_at__lt=cutoff,
    ).update(status=MaintenanceSubmission.Status.CLEANUP, title='', description='', submitted_by=None,
             plan_name='', button_name='', responsible_name='')
    queue = MaintenanceSubmission.objects.filter(status=MaintenanceSubmission.Status.CLEANUP).order_by('created_at')
    reports = list(queue[:20])
    if not reports:
        return
    try:
        drive = EvidenceDrive() if any(report.drive_images for report in reports) else None
    except EvidenceUnavailable:
        logger.warning('Drive no está disponible para retirar evidencia temporal de mantención.')
        return
    for report in reports:
        cleanup_report(report, drive)
