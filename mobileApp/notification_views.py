"""Compose general announcements and manage the current device's subscription."""
import json

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .authorization import is_administrator
from .cloud_projects import CloudUnavailable
from .company_brand import brand_context
from .device_push import public_key, queue_notifications, validate_subscription
from .maintenance_alerts import account_name, touch_inboxes
from .models import CompanyAccount, CompanyAdminSetup, DevicePushSubscription, MaintenanceNotification
from .pwa import asset_version
from .tenant_runtime import using_schema, workspace, workspace_user


class AnnouncementForm(forms.Form):
    title = forms.CharField(label='Título', max_length=120, widget=forms.TextInput(attrs={'class': 'form-control'}))
    message = forms.CharField(label='Mensaje', max_length=2000,
                              widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5}))
    all_users = forms.BooleanField(label='Todos los usuarios', required=False,
                                   widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    recipients = forms.MultipleChoiceField(label='Usuarios', required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, recipients, data=None):
        super().__init__(data)
        self.fields['recipients'].choices = [(key, label) for key, (_, label) in recipients.items()]

    def clean(self):
        values = super().clean()
        if 'recipients' in values and not (values['all_users'] or values['recipients']):
            self.add_error('recipients', 'Selecciona uno o más usuarios o marca «Todos los usuarios».')
        return values


def _recipient_catalog(company):
    if company:
        with using_schema(settings.PLANOS_DATABASE_SCHEMA):
            owners = list(CompanyAdminSetup.objects.filter(company_id=company.company_id).order_by('pk'))
            members = list(CompanyAccount.objects.filter(company_id=company.company_id)
                           .order_by('first_name', 'last_name', 'pk'))
        return {f'{prefix}:{account.pk}': (account, f'{account.first_name} {account.last_name} · {account.rut}')
                for prefix, accounts in (('admin', owners), ('user', members)) for account in accounts}

    from . import shared_accounts

    if shared_accounts.enabled():
        shared_accounts.sync_accounts()
    users = get_user_model().objects.filter(is_active=True).select_related('app_profile').order_by(
        'first_name', 'last_name', 'username')
    return {str(user.pk): (user, f'{account_name(user)} · {user.app_profile.rut}'
                           if getattr(user, 'app_profile', None) and user.app_profile.rut else account_name(user))
            for user in users}


@require_http_methods(['GET', 'POST'])
@never_cache
def compose_announcement(request):
    if not is_administrator(request.user):
        return HttpResponse('Solo los administradores pueden crear notificaciones.', status=403)
    company = workspace.get()
    try:
        catalog = _recipient_catalog(company)
    except (CloudUnavailable, ValidationError):
        return HttpResponse('No se pudo consultar la lista de usuarios. Intenta de nuevo.', status=503)
    form = AnnouncementForm(catalog, request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        selection = catalog if form.cleaned_data['all_users'] else form.cleaned_data['recipients']
        if not selection:
            form.add_error('recipients', 'No hay usuarios activos para notificar.')
        else:
            with transaction.atomic():
                recipients = [catalog[key][0] for key in selection]
                if company:
                    recipients = [workspace_user(account) for account in recipients]
                recipient_ids = sorted({user.pk for user in recipients})
                items = MaintenanceNotification.objects.bulk_create([
                    MaintenanceNotification(recipient_id=recipient_id, kind=MaintenanceNotification.Kind.GENERAL,
                                            day=timezone.localdate(), sender_name=account_name(request.user),
                                            title=form.cleaned_data['title'], message=form.cleaned_data['message'])
                    for recipient_id in recipient_ids
                ], batch_size=500)
                queue_notifications(items)
                touch_inboxes(recipient_ids)
            count = len(items)
            messages.success(request, 'Notificación enviada a 1 usuario.' if count == 1
                             else f'Notificación enviada a {count} usuarios.')
            return redirect('compose_announcement')
    return render(request, 'mobileApp/compose_announcement.html', {
        'form': form, 'asset_version': asset_version(),
        **(company.brand if company else brand_context()),
    }, status=400 if form.errors else 200)


@require_POST
@never_cache
@transaction.atomic
def dismiss_notification(request, notification_id):
    if not request.user.is_authenticated or not request.user.is_active:
        return HttpResponse(status=403)
    item = get_object_or_404(MaintenanceNotification.objects.select_for_update(),
                            pk=notification_id, recipient=request.user)
    if not item.can_dismiss:
        return HttpResponse('La mantención debe confirmarse antes de retirar el aviso.', status=403)
    item.delete()
    touch_inboxes([request.user.pk])
    return redirect('notifications')


@require_http_methods(['GET', 'POST', 'DELETE'])
@never_cache
def push_subscription(request):
    if not request.user.is_authenticated or not request.user.is_active or not request.session.session_key:
        return JsonResponse({'error': 'Inicia sesión para activar las notificaciones.'}, status=401)
    session_key = request.session.session_key
    subscriptions = DevicePushSubscription.objects.filter(recipient=request.user, session_key=session_key)
    if request.method == 'GET':
        return JsonResponse({'public_key': public_key(), 'registered': subscriptions.exists()})
    if request.method == 'DELETE':
        subscriptions.delete()
        return HttpResponse(status=204)
    try:
        if len(request.body) > 4096:
            raise ValueError('Subscription too large')
        data = validate_subscription(json.loads(request.body))
    except (ValueError, UnicodeError):
        return JsonResponse({'error': 'El navegador envió una suscripción no compatible.'}, status=400)
    try:
        with transaction.atomic():
            existing = subscriptions.select_for_update().first()
            if existing is None or any(getattr(existing, key) != value for key, value in data.items()):
                subscriptions.delete()
                DevicePushSubscription.objects.create(recipient=request.user, session_key=session_key, **data)
    except IntegrityError:
        return JsonResponse({'error': 'Vuelve a activar las notificaciones en este dispositivo.'}, status=409)
    return HttpResponse(status=204)
