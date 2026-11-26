from django.contrib import messages
from django.core.paginator import Paginator
from django.db import OperationalError, connection
from django.db.models import F, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .authorization import is_administrator
from .company_brand import brand_context
from .maintenance_alerts import account_name, generate_alerts
from .models import MaintenanceNotification
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
            kind__in=(MaintenanceNotification.Kind.ALERT, MaintenanceNotification.Kind.REMINDER),
            responsible_id=F('recipient_id'),
        ))
    company = workspace.get()
    return render(request, 'mobileApp/maintenance_inbox.html', {
        'page': Paginator(items, 25).get_page(request.GET.get('page')),
        'can_reply': admin, 'asset_version': asset_version(),
        **(company.brand if company else brand_context()),
    })


@require_POST
@never_cache
def reply_to_notification(request, notification_id):
    if not is_administrator(request.user):
        return HttpResponseForbidden('Solo los administradores pueden responder a las alertas de mantención.')
    alert = get_object_or_404(MaintenanceNotification.objects.select_related('responsible'),
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
