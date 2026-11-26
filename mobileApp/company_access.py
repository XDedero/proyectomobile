"""Authenticate company accounts in the central schema."""
from django.contrib.auth import logout
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect, render
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .accounts import validate_account_password
from .company_brand import brand_context
from .pwa import asset_version
from .models import Company, CompanyAccount, CompanyAdminSetup, CompanyDriveConnection, RELONCAVI_COMPANY_ID, UserProfile


SESSION_KEY = 'company_access'


def usable_companies():
    legacy = Q(pk=RELONCAVI_COMPANY_ID, status=Company.Status.ACTIVE)
    provisioned = (Q(status__in=(Company.Status.PENDING_STORAGE, Company.Status.ACTIVE),
                     pk__in=CompanyDriveConnection.objects.values('company_id'))
                   & Q(pk__in=CompanyAdminSetup.objects.values('company_id'))
                   & ~Q(pk=RELONCAVI_COMPANY_ID))
    return Company.objects.filter(legacy | provisioned, drive_ready=True)


def company_user_data(account):
    owner = isinstance(account, CompanyAdminSetup)
    role = UserProfile.Role.ADMINISTRATOR if owner else account.role
    return {
        'id': 'principal' if owner else f'member:{account.pk}',
        'name': f'{account.first_name} {account.last_name}',
        'first_name': account.first_name, 'last_name': account.last_name, 'rut': account.rut,
        'role': role, 'primary_administrator': owner, 'is_active': True,
        'can_edit': False, 'can_delete': False,
    }


def _signature(account):
    return salted_hmac('mobileApp.company_access', f'{account.company_id}/{account.rut}/{account.password_hash}',
                       algorithm='sha256').hexdigest()


def bind_account(request, account):
    rotate_token(request)
    request.session[SESSION_KEY] = {
        'id': str(account.pk), 'administrator': isinstance(account, CompanyAdminSetup),
        'signature': _signature(account),
    }


def sign_in(request, rut, password):
    companies = usable_companies().exclude(pk=RELONCAVI_COMPANY_ID)
    for model in (CompanyAdminSetup, CompanyAccount):
        account = model.objects.filter(rut=rut, company__in=companies).first()
        if account and check_password(password, account.password_hash):
            logout(request)
            bind_account(request, account)
            return True
    return False


def current_account(request):
    entry = request.session.get(SESSION_KEY)
    if not entry or request.user.is_authenticated:
        return None
    model = CompanyAdminSetup if entry['administrator'] else CompanyAccount
    companies = usable_companies().exclude(pk=RELONCAVI_COMPANY_ID)
    account = model.objects.select_related('company').filter(pk=entry['id'], company__in=companies).first()
    if account and constant_time_compare(entry['signature'], _signature(account)):
        return account
    request.session.pop(SESSION_KEY, None)
    return None


@transaction.atomic
def change_password(request, account, old_password, new_password):
    current = type(account).objects.select_for_update().filter(pk=account.pk, company_id=account.company_id).first()
    if current is None or not check_password(old_password, current.password_hash):
        raise ValidationError('La contraseña actual es incorrecta.')
    validate_account_password(new_password, rut=current.rut, first_name=current.first_name, last_name=current.last_name)
    current.password_hash = make_password(new_password)
    current.save(update_fields=['password_hash'])
    request.session.cycle_key()
    bind_account(request, current)


@require_GET
@never_cache
def company_home(request):
    account = current_account(request)
    if account is None:
        return redirect('index')
    from .tenant_runtime import resolve_workspace
    workspace, preparing = resolve_workspace(account, prepare=True)
    if workspace is not None:
        return redirect('index')
    return render(request, 'mobileApp/company_home.html', {
        'account': account, 'preparing': preparing, 'asset_version': asset_version(),
        **brand_context(account.company),
    })


@require_POST
@never_cache
def company_logout(request):
    logout(request)
    return redirect('index')
