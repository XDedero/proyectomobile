from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, connection, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.http import HttpResponse, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .authorization import application_profile, is_administrator
from .company_brand import brand_context
from .maintenance_alerts import account_name, current_buttons, generate_alerts
from .maintenance_reports import (
    ACTION_KINDS, OPEN_STATUSES, cleanup_report, is_responsible, matches_cycle, reserve_report,
    review_report, upload_evidence,
)
from .maintenance_storage import EvidenceDrive, EvidenceUnavailable, prepared_images
from .models import MaintenanceNotification, MaintenanceSubmission, Motor, PdfMarker
from .pwa import asset_version
from .tenant_runtime import workspace


@require_GET
@never_cache
def notifications(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return redirect('index')
    admin = is_administrator(request.user)
    try:
        generate_alerts(recipient=request.user)
    except OperationalError as exc:
        # SQLite can have a concurrent writer when the local worker just started.
        if connection.vendor != 'sqlite' or 'locked' not in str(exc).lower():
            raise
    items = MaintenanceNotification.objects.filter(recipient=request.user).select_related('responsible')
    if not admin:
        items = items.filter(Q(kind=MaintenanceNotification.Kind.REPLY) | Q(
            kind__in=ACTION_KINDS,
            responsible_id=F('recipient_id'),
        ))
    open_reports = MaintenanceSubmission.objects.filter(status__in=OPEN_STATUSES)
    items = items.annotate(awaiting_review=Exists(open_reports.filter(maintenance_key=OuterRef('maintenance_key'))))
    profile = application_profile(request.user)
    can_report = profile is None or profile.role in ('worker', 'administrator')
    page = Paginator(items, 25).get_page(request.GET.get('page'))
    if can_report:
        own_notices = [item for item in page if item.kind in ACTION_KINDS and item.responsible_id == request.user.pk]
        keys = {item.maintenance_key for item in own_notices}
        buttons = {
            button.maintenance_key: button
            for model in (Motor, PdfMarker)
            for button in current_buttons(model).filter(maintenance_key__in=keys).select_related('plan')
        }
        for item in own_notices:
            button = buttons.get(item.maintenance_key)
            item.can_submit = matches_cycle(button, item) and is_responsible(request.user, button)
    review_page = None
    if admin:
        pending = open_reports.filter(status=MaintenanceSubmission.Status.PENDING).order_by('created_at', 'pk')
        review_page = Paginator(pending, 25).get_page(request.GET.get('revision'))
    company = workspace.get()
    return render(request, 'mobileApp/maintenance_inbox.html', {
        'page': page,
        'can_reply': admin, 'can_report': can_report,
        'review_page': review_page, 'asset_version': asset_version(),
        **(company.brand if company else brand_context()),
    })


@require_POST
@never_cache
@transaction.atomic
def reply_to_notification(request, notification_id):
    if not is_administrator(request.user):
        return HttpResponseForbidden('Solo los administradores pueden responder a las alertas de mantención.')
    alert = get_object_or_404(MaintenanceNotification.objects.select_related('responsible').select_for_update(of=('self',)),
                             pk=notification_id, kind=MaintenanceNotification.Kind.ALERT, recipient=request.user)
    message = request.POST.get('message', '').strip()
    if not alert.responsible or not alert.responsible.is_active:
        messages.error(request, 'La cuenta responsable ya no está disponible.')
    elif not 1 <= len(message) <= 2000:
        messages.error(request, 'Escribe un mensaje de entre 1 y 2.000 caracteres.')
    else:
        MaintenanceNotification.objects.create(
            kind=MaintenanceNotification.Kind.REPLY, reply_to=alert,
            recipient=alert.responsible, responsible=alert.responsible, responsible_name=alert.responsible_name,
            maintenance_key=alert.maintenance_key, last_maintenance=alert.last_maintenance, due_date=alert.due_date,
            plan_name=alert.plan_name, button_name=alert.button_name, category_name=alert.category_name,
            day=timezone.localdate(), sender_name=account_name(request.user), message=message,
        )
        messages.success(request, f'Mensaje enviado a {alert.responsible_name}.')
    return redirect('notifications')


@require_POST
@never_cache
def submit_maintenance(request, notification_id):
    if not request.user.is_authenticated or not request.user.is_active:
        return HttpResponseForbidden('Inicia sesión para enviar la mantención.')
    notice = get_object_or_404(MaintenanceNotification, pk=notification_id, kind__in=ACTION_KINDS,
                               recipient=request.user, responsible=request.user)
    title = request.POST.get('title', '').strip()
    description = request.POST.get('description', '').strip()
    try:
        if not 1 <= len(title) <= 120:
            raise ValidationError('Ingresa un título de entre 1 y 120 caracteres.')
        if len(description) > 2000:
            raise ValidationError('La descripción puede tener hasta 2.000 caracteres.')
        images = prepared_images(request.FILES.getlist('images'))
        report = reserve_report(request.user, notice, title, description)
        upload_evidence(report, images)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    except EvidenceUnavailable as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Reporte enviado. El administrador revisará la mantención.')
    return redirect('notifications')


@require_GET
@never_cache
def maintenance_image(request, report_id, image_number):
    if not is_administrator(request.user):
        return HttpResponseForbidden('Solo los administradores pueden ver estas imágenes.')
    report = get_object_or_404(MaintenanceSubmission, pk=report_id, status=MaintenanceSubmission.Status.PENDING)
    if not 1 <= image_number <= len(report.drive_images):
        raise Http404
    try:
        data = EvidenceDrive().download(report.drive_images[image_number - 1])
    except EvidenceUnavailable:
        return HttpResponse('No se pudo cargar la imagen. Intenta nuevamente.', status=503)
    response = HttpResponse(data, content_type='image/jpeg')
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@require_POST
@never_cache
def decide_maintenance(request, report_id):
    if not is_administrator(request.user):
        return HttpResponseForbidden('Solo los administradores pueden revisar la mantención.')
    decision = request.POST.get('decision')
    if decision not in ('accept', 'reject'):
        return HttpResponse('Indica si aceptas o rechazas la mantención.', status=400)
    report = get_object_or_404(MaintenanceSubmission, pk=report_id, status=MaintenanceSubmission.Status.PENDING)
    try:
        report = review_report(report, accept=decision == 'accept')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        cleanup_report(report)
        messages.success(request, 'Mantención confirmada.' if decision == 'accept' else 'Reporte rechazado.')
    return redirect('notifications')
