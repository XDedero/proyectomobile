"""Registration requests and administrator-managed accounts."""
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .accounts import (application_role, can_edit_user, invalidate_user_sessions, is_primary_administrator, next_test_rut,
                       normalize_rut, rut_in_use, user_data, validate_name)
from .access import is_administrator
from .management_views import administrators_only
from .models import RegistrationRequest, UserProfile
from .pdf_views import error_response, json_body


def record_id(data):
    value = data.get('id')
    if type(value) is not int or value <= 0:
        raise ValidationError('Seleccione un registro válido.')
    return value


@require_POST
@never_cache
def request_registration(request):
    try:
        data = json_body(request)
        first_name = validate_name(data.get('first_name'), 'nombre')
        last_name = validate_name(data.get('last_name'), 'apellido')
        rut = normalize_rut(data.get('rut'), strict=True)
        password = data.get('password')
        if not isinstance(password, str) or len(password) > 256:
            raise ValidationError('Ingrese una contraseña válida de hasta 256 caracteres.')
        if 'confirm_password' in data and data['confirm_password'] != password:
            raise ValidationError('Las contraseñas no coinciden.')
        user = get_user_model()(username=rut, first_name=first_name, last_name=last_name)
        validate_password(password, user=user)
        with transaction.atomic():
            if rut_in_use(rut):
                raise ValidationError('Ya existe un usuario con ese RUT.')
            if RegistrationRequest.objects.filter(rut=rut, status='pending').exists():
                raise ValidationError('Ya hay una solicitud pendiente para ese RUT.')
            item = RegistrationRequest.objects.create(first_name=first_name, last_name=last_name,
                                                      rut=rut, password_hash=make_password(password))
        return JsonResponse({'id': item.pk, 'message': 'Solicitud enviada. Un administrador debe aprobar su cuenta.'}, status=201)
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe un usuario o una solicitud pendiente con ese RUT.')


@require_POST
@administrators_only
def manage_registration(request):
    try:
        data = json_body(request)
        action = data.get('action')
        if action not in ('approve', 'reject'):
            raise ValidationError('Seleccione aceptar o eliminar la solicitud.')
        with transaction.atomic():
            item = get_object_or_404(RegistrationRequest.objects.select_for_update(), pk=record_id(data))
            if item.status != 'pending':
                return error_response('La solicitud ya fue resuelta. Recargue la lista.', 409)
            if action == 'approve':
                if rut_in_use(item.rut):
                    raise ValidationError('Ya existe un usuario con ese RUT.')
                user = get_user_model().objects.create(username=f'cuenta_{uuid4().hex}', first_name=item.first_name,
                                                       last_name=item.last_name, password=item.password_hash)
                UserProfile.objects.create(user=user, rut=item.rut, role='viewer')
            item.delete()
        return JsonResponse({'deleted': True, 'message': 'Cuenta creada como espectador.' if action == 'approve' else 'Solicitud eliminada.'})
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe un usuario con ese RUT.')


def ensure_other_administrator(user):
    """Lock account rows so two administrators cannot remove each other concurrently."""
    others = get_user_model().objects.select_for_update().filter(is_active=True).exclude(pk=user.pk)
    if not any(is_administrator(other) for other in others):
        raise ValidationError('Debe conservarse al menos un administrador activo.')


@require_POST
@administrators_only
def manage_user(request):
    try:
        data = json_body(request)
        action = data.get('action', 'update')
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
            if role == 'administrator' and previous_role != 'administrator' and not is_primary_administrator(request.user):
                return error_response('Solo el administrador principal puede crear u otorgar el rol de administrador.', 403)
            if rut_in_use(rut, exclude_user=user.pk):
                raise ValidationError('Ya existe un usuario con ese RUT.')
            if RegistrationRequest.objects.filter(rut=rut, status='pending').exists() and rut != profile.rut:
                raise ValidationError('Ese RUT ya tiene una solicitud pendiente.')
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
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe un usuario con ese RUT o nombre de acceso.')
