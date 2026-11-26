"""Submission and review of maintenance work against a published button."""
from datetime import timedelta
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .authorization import application_profile, can_access_plan
from .maintenance import next_date
from .maintenance_alerts import current_buttons
from .maintenance_storage import EvidenceDrive, EvidenceUnavailable
from .models import LatestMaintenance, MaintenanceNotification, MaintenanceSubmission, Motor, PdfMarker


OPEN_STATUSES = (MaintenanceSubmission.Status.UPLOADING, MaintenanceSubmission.Status.PENDING)
ACTION_KINDS = (MaintenanceNotification.Kind.ALERT, MaintenanceNotification.Kind.REMINDER,
                MaintenanceNotification.Kind.RETRY)
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


def is_responsible(user, button):
    profile = application_profile(user)
    return bool(button and user.is_authenticated and user.is_active and
                (profile is None or profile.role in ('worker', 'administrator')) and
                button.button_creator == user.get_username() and can_access_plan(user, button.plan))


@transaction.atomic
def reserve_report(user, notice, title, description):
    button = published_button(notice.maintenance_key, lock=True)
    if notice.recipient_id != user.pk or notice.responsible_id != user.pk or (
        button and not is_responsible(user, button)
    ):
        raise PermissionDenied
    if not matches_cycle(button, notice):
        raise ValidationError('La programación cambió. Revisa el aviso actualizado en tu buzón.')
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
            # Record every ID first so even an interrupted upload can be cleaned up.
            report.save(update_fields=['drive_images'])
            for file_id, photo in zip(report.drive_images, images, strict=True):
                drive.upload(file_id, photo)
        report.status = MaintenanceSubmission.Status.PENDING
        report.save(update_fields=['status'])
    except EvidenceUnavailable:
        mark_for_cleanup(report)
        cleanup_report(report, drive)
        raise


@transaction.atomic
def review_report(report, *, accept):
    button = published_button(report.maintenance_key, lock=True)
    report = MaintenanceSubmission.objects.select_for_update().filter(
        pk=report.pk, status=MaintenanceSubmission.Status.PENDING,
    ).first()
    if report is None:
        raise ValidationError('Este reporte ya fue revisado.')
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
        MaintenanceNotification.objects.filter(
            maintenance_key=report.maintenance_key, kind__in=ACTION_KINDS,
        ).delete()
    elif button and button.next_maintenance and report.submitted_by and is_responsible(report.submitted_by, button):
        MaintenanceNotification.objects.filter(
            maintenance_key=report.maintenance_key, recipient=report.submitted_by,
            kind__in=(MaintenanceNotification.Kind.ALERT, MaintenanceNotification.Kind.REMINDER),
        ).delete()
        MaintenanceNotification.objects.update_or_create(
            kind=MaintenanceNotification.Kind.RETRY, maintenance_key=report.maintenance_key,
            recipient=report.submitted_by, defaults={
                'responsible': report.submitted_by, 'responsible_name': report.responsible_name,
                'last_maintenance': button.last_maintenance, 'due_date': button.next_maintenance,
                'plan_name': button.plan.name, 'button_name': report.button_name,
                'day': timezone.localdate(),
            },
        )
    mark_for_cleanup(report)
    return report


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
