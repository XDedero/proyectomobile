"""Administrator-managed accounts and personal password changes."""
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_POST

from .accounts import (application_role, can_edit_user, invalidate_user_sessions, is_primary_administrator,
                       next_test_rut, normalize_rut, rut_in_use, user_data, validate_account_password, validate_name)
from .access import is_administrator
from .management_views import administrators_only
from .models import Company, CompanyAccount, CompanyAdminSetup, CompanyAudit, RELONCAVI_COMPANY_ID, UserProfile
from .pdf_views import error_response, json_body
from .cloud_projects import CloudUnavailable


def record_id(data):
    value = data.get('id')
    if type(value) is not int or value <= 0:
        raise ValidationError('Seleccione un registro válido.')
    return value


def account_input(data):
    values = {
        'first_name': validate_name(data.get('first_name'), 'nombre'),
        'last_name': validate_name(data.get('last_name'), 'apellido'),
        'rut': normalize_rut(data.get('rut')),
        'role': data.get('role'),
    }
    if values['role'] not in UserProfile.Role.values:
        raise ValidationError('Seleccione un rol válido para la cuenta.')
    validate_account_password(data.get('password'), rut=values['rut'],
                              first_name=values['first_name'], last_name=values['last_name'])
    return {**values, 'password_hash': make_password(data['password'])}


@transaction.atomic
def save_account(values, *, company_owner=None):
    from . import shared_accounts

    # Every company shares the RUT namespace, including the legacy workspace.
    Company.objects.select_for_update().get(pk=RELONCAVI_COMPANY_ID)
    rut = values['rut']
    if rut_in_use(rut):
        raise ValidationError('Ya existe un usuario con ese RUT.')
    if company_owner is not None:
        if not CompanyAdminSetup.objects.select_for_update().filter(pk=company_owner.pk).exists():
            raise ValidationError('El administrador principal de la empresa cambió.')
        if settings.APP_SHARED_ACCOUNTS and shared_accounts.account_rut_exists(rut):
            raise ValidationError('Ya existe un usuario con ese RUT.')
        account = CompanyAccount.objects.create(company_id=company_owner.company_id, **values)
        CompanyAudit.objects.create(company_id=company_owner.company_id, company_code=company_owner.company_id,
                                    actor=company_owner.rut, action='usuario_creado')
        from .company_access import company_user_data
        return company_user_data(account)
    if shared_accounts.enabled():
        return user_data(shared_accounts.create_account(**values))

    administrator = values['role'] == UserProfile.Role.ADMINISTRATOR
    user = get_user_model().objects.create(
        username=f'cuenta_{uuid4().hex}', first_name=values['first_name'], last_name=values['last_name'],
        password=values['password_hash'], is_staff=administrator,
    )
    UserProfile.objects.create(user=user, rut=rut, role=values['role'])
    if administrator:
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label='mobileApp', codename='administer_application',
        ))
    return user_data(user)


def create_user(request, data):
    if not is_primary_administrator(request.user):
        return error_response('Solo el administrador principal puede crear usuarios.', 403)
    company_account = getattr(request, 'company_account', None)
    values = account_input(data)
    if company_account is not None:
        if not isinstance(company_account, CompanyAdminSetup):
            return error_response('Solo el administrador principal puede crear usuarios.', 403)
        from .tenant_runtime import using_schema
        with using_schema(settings.PLANOS_DATABASE_SCHEMA):
            account = save_account(values, company_owner=company_account)
    else:
        account = save_account(values)
    return JsonResponse({'user': account, 'message': 'Usuario creado. Ya puede iniciar sesión con su RUT.'}, status=201)


def ensure_other_administrator(user):
    """Lock account rows so two administrators cannot remove each other concurrently."""
    others = get_user_model().objects.select_for_update().filter(is_active=True).exclude(pk=user.pk)
    if not any(is_administrator(other) for other in others):
        raise ValidationError('Debe conservarse al menos un administrador activo.')


@require_POST
@administrators_only
def manage_user(request):
    from .tenant_runtime import workspace
    try:
        data = json_body(request)
        action = data.get('action', 'update')
        if action == 'create':
            return create_user(request, data)
        if workspace.get() is not None:
            return error_response('Los usuarios de esta empresa cambian su contraseña desde su propia cuenta.', 403)
        if action not in ('update', 'delete'):
            raise ValidationError('La acción de usuario no es válida.')
        with transaction.atomic():
            user = get_object_or_404(get_user_model().objects.select_for_update(), pk=record_id(data))
            if not can_edit_user(request.user, user):
                return error_response('No puede modificar ni eliminar la cuenta de otro administrador.', 403)
            previous_role = application_role(user)
            if action == 'delete':
                if user.pk == request.user.pk:
                    raise ValidationError('No puede eliminar su propia cuenta.')
                if previous_role == 'administrator':
                    ensure_other_administrator(user)
                from . import shared_accounts
                profile = getattr(user, 'app_profile', None)
                if shared_accounts.enabled() and profile and profile.cloud_id:
                    shared_accounts.update_account(user, profile, delete=True)
                invalidate_user_sessions(user.pk)
                user.delete()
                return JsonResponse({'deleted': True})
            profile = getattr(user, 'app_profile', None)
            if profile is None:
                profile = UserProfile(user=user, rut=next_test_rut(), rut_is_test=True, role=previous_role)
            first_name, last_name = user.first_name, user.last_name
            if 'first_name' in data and data['first_name'] != first_name:
                first_name = validate_name(data['first_name'], 'nombre')
            if 'last_name' in data and data['last_name'] != last_name:
                last_name = validate_name(data['last_name'], 'apellido')
            rut = normalize_rut(data.get('rut', profile.rut))
            role = data.get('role', profile.role)
            if role not in UserProfile.Role.values:
                raise ValidationError('Seleccione un rol válido.')
            if profile.is_primary_administrator and role != 'administrator':
                raise ValidationError('El administrador principal debe conservar su rol de administrador.')
            if rut_in_use(rut, exclude_user=user.pk):
                raise ValidationError('Ya existe un usuario con ese RUT.')
            if previous_role == 'administrator' and role != 'administrator':
                ensure_other_administrator(user)
            user.first_name, user.last_name = first_name, last_name
            # Ownership in saved and cloud plans uses this stable username.
            # The public login resolves the editable RUT through the profile.
            if rut != profile.rut:
                profile.rut_is_test = False
            profile.rut, profile.role = rut, role
            changed_role = previous_role != role
            if changed_role:
                user.is_staff = role == 'administrator'
                if role != 'administrator':
                    user.is_superuser = False
            password = data.get('password')
            if password is not None:
                if not isinstance(password, str) or len(password) > 256:
                    raise ValidationError('Ingrese una contraseña válida de hasta 256 caracteres.')
                validate_password(password, user=user)
                user.set_password(password)
            user.save()
            profile.full_clean()
            from . import shared_accounts
            if shared_accounts.enabled() and profile.cloud_id:
                shared_accounts.update_account(user, profile)
            profile.save()
            if changed_role:
                permission = Permission.objects.get(content_type__app_label='mobileApp', codename='administer_application')
                if role == 'administrator':
                    user.user_permissions.add(permission)
                else:
                    user.user_permissions.remove(permission)
            if changed_role or password is not None:
                invalidate_user_sessions(user.pk)
        return JsonResponse({'user': user_data(user), 'requires_login': changed_role or password is not None,
                             'message': 'Cambios guardados. El usuario debe volver a iniciar sesión.' if changed_role or password is not None else 'Usuario actualizado.'})
    except CloudUnavailable as exc:
        return error_response(exc, 503)
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe un usuario con ese RUT.')


@transaction.atomic
def change_local_password(request, old_password, new_password):
    from . import shared_accounts

    user = get_user_model().objects.select_for_update().filter(pk=request.user.pk, is_active=True).first()
    if user is None or not user.check_password(old_password):
        raise ValidationError('La contraseña actual es incorrecta.')
    profile = getattr(user, 'app_profile', None)
    validate_account_password(new_password, rut=profile.rut if profile else user.username,
                              first_name=user.first_name, last_name=user.last_name)
    user.set_password(new_password)
    if shared_accounts.enabled() and profile and profile.cloud_id:
        shared_accounts.update_account(user, profile)
        profile.save(update_fields=['cloud_revision'])
    user.save(update_fields=['password'])
    update_session_auth_hash(request, user)


@require_http_methods(['GET', 'POST'])
@never_cache
@sensitive_post_parameters('current_password', 'new_password', 'confirm_password')
def change_password(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return redirect('index')
    from .company_brand import brand_context
    from .pwa import asset_version
    from .tenant_runtime import using_schema, workspace

    errors = []
    status = 200
    if request.method == 'POST':
        old_password = request.POST.get('current_password', '')
        new_password = request.POST.get('new_password', '')
        try:
            if not old_password or len(old_password) > 256:
                raise ValidationError('Ingrese la contraseña actual.')
            if new_password != request.POST.get('confirm_password'):
                raise ValidationError('Las contraseñas nuevas no coinciden.')
            company_account = getattr(request, 'company_account', None)
            if company_account is not None:
                from .company_access import change_password as change_company_password
                with using_schema(settings.PLANOS_DATABASE_SCHEMA):
                    change_company_password(request, company_account, old_password, new_password)
            else:
                change_local_password(request, old_password, new_password)
            messages.success(request, 'Contraseña actualizada.')
            return redirect('change_password')
        except ValidationError as exc:
            errors, status = exc.messages, 400
        except CloudUnavailable as exc:
            errors, status = [str(exc)], 503
    company = workspace.get()
    return render(request, 'mobileApp/change_password.html', {
        'errors': errors, 'asset_version': asset_version(),
        **(company.brand if company else brand_context()),
    }, status=status)
