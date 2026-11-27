from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, connection, transaction
from django.db.models import F, Q
from django.http import HttpResponse, HttpResponseForbidden, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .authorization import is_administrator
from .company_brand import brand_context
from .device_push import queue_notifications
from .maintenance_alerts import ACTION_KINDS, account_name, current_buttons, generate_alerts, touch_inboxes
from .maintenance_assignments import assigned_buttons, can_report, eligible_users, roles_for_button
from .maintenance_reports import (
    OPEN_STATUSES, button_users, cleanup_report, matches_cycle, published_button, reserve_report,
    review_report, upload_evidence,
)
from .maintenance_storage import EvidenceDrive, EvidenceUnavailable, prepared_images
from .models import LatestMaintenance, MaintenanceInboxState, MaintenanceNotification, MaintenanceSubmission, Motor, PdfMarker
from .pwa import asset_version
from .tenant_runtime import workspace


def inbox_revision(user):
    revision = MaintenanceInboxState.objects.filter(pk=user.pk).values_list('revision', flat=True).first()
    company = workspace.get()
    roles = f'{int(can_report(user))}{int(is_administrator(user))}'
    return f'{company.company_id if company else "main"}:{user.pk}:{roles}:{revision or ""}:{timezone.localdate()}'


@require_GET
@never_cache
def notification_changes(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return HttpResponse(status=401)
    return JsonResponse({'changed': request.GET.get('revision') != inbox_revision(request.user)})


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
    revision = inbox_revision(request.user)
    items = MaintenanceNotification.objects.filter(recipient=request.user).filter(
        Q(button_deleted=True) | Q(kind__in=('reply', 'confirmed', 'general')) |
        Q(kind__in=ACTION_KINDS, responsible_id=F('recipient_id')),
    )
    open_reports = MaintenanceSubmission.objects.filter(status__in=OPEN_STATUSES)
    page = Paginator(items, 25).get_page(request.GET.get('page'))
    own_notices = [item for item in page if can_report(request.user) and
                   item.kind in ACTION_KINDS and not item.button_deleted]
    own_keys = {item.maintenance_key for item in own_notices}
    latest = LatestMaintenance.objects.in_bulk({item.maintenance_key for item in page if item.kind == 'confirmed'})
    for item in page:
        item.latest = latest.get(item.maintenance_key)
    pending = open_reports.filter(status=MaintenanceSubmission.Status.PENDING)
    review_keys = set(pending.values_list('maintenance_key', flat=True)) if admin else set()
    users = eligible_users() if own_keys or review_keys else []
    buttons, authorized_keys = {}, set()
    for model in (Motor, PdfMarker):
        candidates = current_buttons(model).filter(maintenance_key__in=own_keys | review_keys).select_related('plan')
        if not own_keys and admin:
            candidates = assigned_buttons(candidates, request.user)
        for button in candidates:
            responsible, supervisors = roles_for_button(button, users)
            buttons[button.maintenance_key] = (button, responsible, supervisors)
            if request.user in supervisors:
                authorized_keys.add(button.maintenance_key)
    waiting = set(open_reports.filter(maintenance_key__in=own_keys).values_list('maintenance_key', flat=True))
    for item in own_notices:
        button, responsible, supervisors = buttons.get(item.maintenance_key, (None, [], []))
        if request.user in responsible and matches_cycle(button, item):
            item.awaiting_review = item.maintenance_key in waiting
            item.can_submit = bool(supervisors)
            item.needs_supervisor = not supervisors
    review_page = None
    if admin:
        pending = pending.filter(maintenance_key__in=authorized_keys).order_by('created_at', 'pk')
        review_page = Paginator(pending, 25).get_page(request.GET.get('revision'))
    company = workspace.get()
    return render(request, 'mobileApp/maintenance_inbox.html', {
        'page': page,
        'can_reply': admin, 'inbox_revision': revision,
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
                             pk=notification_id, kind=MaintenanceNotification.Kind.ALERT, recipient=request.user,
                             button_deleted=False)
    button = published_button(alert.maintenance_key)
    if button is None or request.user not in button_users(button)[1]:
        return HttpResponseForbidden('Solo un supervisor asignado puede responder a esta mantención.')
    message = request.POST.get('message', '').strip()
    if not alert.responsible or not alert.responsible.is_active:
        messages.error(request, 'La cuenta responsable ya no está disponible.')
    elif not 1 <= len(message) <= 2000:
        messages.error(request, 'Escribe un mensaje de entre 1 y 2.000 caracteres.')
    else:
        item = MaintenanceNotification.objects.create(
            kind=MaintenanceNotification.Kind.REPLY, reply_to=alert,
            recipient=alert.responsible, responsible=alert.responsible, responsible_name=alert.responsible_name,
            maintenance_key=alert.maintenance_key, last_maintenance=alert.last_maintenance, due_date=alert.due_date,
            plan_name=alert.plan_name, button_name=alert.button_name, category_name=alert.category_name,
            day=timezone.localdate(), sender_name=account_name(request.user), message=message,
        )
        queue_notifications([item])
        messages.success(request, f'Mensaje enviado a {alert.responsible_name}.')
        touch_inboxes({alert.responsible_id})
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
        messages.success(request, 'Reporte enviado a los supervisores de este botón.')
    return redirect('notifications')


@require_GET
@never_cache
def maintenance_image(request, report_id, image_number):
    if not is_administrator(request.user):
        return HttpResponseForbidden('Solo los administradores pueden ver estas imágenes.')
    report = get_object_or_404(MaintenanceSubmission, pk=report_id, status=MaintenanceSubmission.Status.PENDING)
    button = published_button(report.maintenance_key)
    if button is None or request.user not in button_users(button)[1]:
        raise Http404
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
        report = review_report(report, request.user, accept=decision == 'accept')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    else:
        cleanup_report(report)
        messages.success(request, 'Mantención confirmada.' if decision == 'accept' else 'Reporte rechazado.')
    return redirect('notifications')
