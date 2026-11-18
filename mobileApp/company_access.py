"""Company onboarding sessions are independent of the legacy drawing permissions."""
from django.contrib.auth import logout
from django.contrib.auth.hashers import check_password
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .accounts import rut_in_use
from .company_brand import brand_context
from .pwa import asset_version
from .cloud_projects import CloudUnavailable
from .models import (Company, CompanyAccount, CompanyAdminSetup, CompanyAudit, CompanyDriveConnection,
                     RegistrationRequest, RELONCAVI_COMPANY_ID, UserProfile)


SESSION_KEY = 'company_access'


def company_administrator(account):
    return isinstance(account, CompanyAdminSetup) or account.role == UserProfile.Role.ADMINISTRATOR


def registration_companies():
    legacy = Q(pk=RELONCAVI_COMPANY_ID, status=Company.Status.ACTIVE)
    provisioned = (Q(status__in=(Company.Status.PENDING_STORAGE, Company.Status.ACTIVE),
                     pk__in=CompanyDriveConnection.objects.values('company_id'))
                   & Q(pk__in=CompanyAdminSetup.objects.values('company_id'))
                   & ~Q(pk=RELONCAVI_COMPANY_ID))
    return Company.objects.filter(legacy | provisioned, drive_ready=True)


def _signature(account):
    return salted_hmac('mobileApp.company_access', f'{account.company_id}/{account.rut}/{account.password_hash}',
                       algorithm='sha256').hexdigest()


def sign_in(request, rut, password):
    companies = registration_companies().exclude(pk=RELONCAVI_COMPANY_ID)
    for model in (CompanyAdminSetup, CompanyAccount):
        account = model.objects.filter(rut=rut, company__in=companies).first()
        if account and check_password(password, account.password_hash):
            logout(request)
            rotate_token(request)
            request.session[SESSION_KEY] = {
                'id': str(account.pk), 'administrator': model is CompanyAdminSetup,
                'signature': _signature(account),
            }
            return True
    return False


def current_account(request):
    entry = request.session.get(SESSION_KEY)
    if not entry or request.user.is_authenticated:
        return None
    model = CompanyAdminSetup if entry['administrator'] else CompanyAccount
    companies = registration_companies().exclude(pk=RELONCAVI_COMPANY_ID)
    account = model.objects.select_related('company').filter(pk=entry['id'], company__in=companies).first()
    if account and constant_time_compare(entry['signature'], _signature(account)):
        return account
    request.session.pop(SESSION_KEY, None)
    return None


def review_company_request(account, data):
    action = data.get('action')
    if action not in ('approve', 'reject'):
        return 'Seleccione una acción válida.'
    with transaction.atomic():
        item = RegistrationRequest.objects.select_for_update().filter(
            company_id=account.company_id, pk=data.get('id'), status=RegistrationRequest.Status.PENDING,
        ).first()
        if not item:
            return 'La solicitud ya fue atendida o no está disponible.'
        if action == 'approve':
            role = data.get('role')
            if role not in UserProfile.Role.values:
                return 'Seleccione un rol válido.'
            if rut_in_use(item.rut):
                return 'Ya existe una cuenta con ese RUT.'
            from . import shared_accounts
            if settings.APP_SHARED_ACCOUNTS and shared_accounts.query(
                'SELECT 1 FROM app_identity.accounts WHERE rut=' + shared_accounts.literal(item.rut) + ';'
            ):
                return 'Ya existe una cuenta con ese RUT.'
            CompanyAccount.objects.create(company_id=item.company_id, rut=item.rut, first_name=item.first_name,
                                          last_name=item.last_name, password_hash=item.password_hash, role=role)
        item.status = RegistrationRequest.Status.APPROVED if action == 'approve' else RegistrationRequest.Status.REJECTED
        item.reviewed_at = timezone.now()
        item.password_hash = ''
        item.save(update_fields=['status', 'reviewed_at', 'password_hash'])
        CompanyAudit.objects.create(company_id=account.company_id, company_code=account.company_id,
                                    actor=account.rut, action='solicitud_' + item.status)
    return None


@require_http_methods(['GET', 'POST'])
@never_cache
def company_home(request):
    account = current_account(request)
    if account is None:
        return redirect('index')
    administrator = company_administrator(account)
    from .tenant_runtime import resolve_workspace
    workspace, preparing = resolve_workspace(account, prepare=True)
    if workspace is not None and request.method == 'GET':
        return redirect('index')
    error = None
    if request.method == 'POST':
        if not administrator:
            return render(request, 'mobileApp/company_home.html', status=403, context={
                'account': account, 'error': 'Su cuenta no puede revisar solicitudes.', 'asset_version': asset_version(),
                **brand_context(account.company),
            })
        try:
            request_id = int(request.POST.get('id', ''))
            error = review_company_request(account, {**request.POST.dict(), 'id': request_id}) if 0 < request_id < 2**63 else 'Solicitud inválida.'
        except (ValueError, IntegrityError, CloudUnavailable):
            error = 'No se pudo resolver la solicitud. Actualice la página e intente nuevamente.'
        if error is None:
            return redirect('company_home')
    pending = (account.company.registration_requests.filter(status=RegistrationRequest.Status.PENDING)
               if administrator else [])
    return render(request, 'mobileApp/company_home.html', {
        'account': account, 'administrator': administrator, 'pending': pending, 'error': error, 'preparing': preparing,
        'asset_version': asset_version(),
        **brand_context(account.company),
    }, status=400 if error else 200)


@require_POST
@never_cache
def company_logout(request):
    logout(request)
    return redirect('index')
