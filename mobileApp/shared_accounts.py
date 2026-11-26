"""Shared accounts and credentials, accessed only by the trusted app server."""
import threading
import time
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse

from .cloud_projects import CloudUnavailable, supabase_query
from .models import UserProfile


def enabled():
    from .tenant_runtime import workspace
    return settings.APP_SHARED_ACCOUNTS and workspace.get() is None


def literal(value):
    if value is None:
        return 'NULL'
    return "'" + str(value).replace("'", "''") + "'"


def query(sql, *, write=False):
    try:
        return supabase_query(sql, read_only=not write).get('rows', [])
    except CloudUnavailable as exc:
        if exc.code == 'client_missing':
            message = ('Configure la conexión privada con SUPABASE_DATABASE_URL o '
                       'conexion-base-datos.txt en esta instalación.')
        elif exc.code in ('project_configuration', 'database_configuration'):
            message = 'La conexión de esta instalación apunta a una configuración de proyecto no válida.'
        elif exc.code == 'database_schema':
            message = ('Falta preparar el registro compartido en Supabase. Ejecute '
                       'manage.py setup_shared_accounts --initialize desde el servidor.')
        elif exc.code == 'database_authentication':
            message = 'Supabase rechazó las credenciales de la conexión privada. Revise su usuario y contraseña PostgreSQL.'
        elif exc.code == 'database_permission':
            message = 'El usuario PostgreSQL de esta instalación no tiene permisos para consultar el registro compartido.'
        elif exc.code == 'account_permission':
            message = ('La sesión guardada de Supabase no tiene acceso al proyecto de esta app. '
                       'Conecte la cuenta administradora del proyecto correcto.')
        elif exc.code == 'account_authentication':
            message = 'La sesión de Supabase no está disponible. Vuelva a conectar la cuenta administradora del proyecto.'
        else:
            message = 'No se pudo consultar el registro compartido. Compruebe la conexión e intente otra vez.'
        raise CloudUnavailable(message, code=exc.code) from exc


def apply_account(data):
    """Materialize a shared identity while preserving local plan foreign keys."""
    from .accounts import invalidate_user_sessions
    with transaction.atomic():
        profile = UserProfile.objects.select_related('user').filter(cloud_id=data['id']).first()
        if profile:
            user = profile.user
            if profile.cloud_revision >= int(data['revision']):
                return user, False
        else:
            if UserProfile.objects.filter(rut=data['rut']).exists() or get_user_model().objects.filter(username=data['username']).exists():
                raise ValidationError('Existe una cuenta local con la misma identidad. Debe revisarse antes de vincularla al registro compartido.')
            user = get_user_model()(username=data['username'])
            profile = UserProfile(user=user, cloud_id=data['id'])
        changed_access = user.pk is not None and (
            user.password != data['password_hash'] or profile.role != data['role'] or user.is_active != data['is_active'])
        user.first_name, user.last_name = data['first_name'], data['last_name']
        user.password, user.is_active = data['password_hash'], data['is_active']
        user.is_staff, user.is_superuser = data['role'] == 'administrator', False
        user.save()
        profile.user = user
        profile.rut, profile.role = data['rut'], data['role']
        profile.rut_is_test = False
        profile.cloud_revision = data['revision']
        profile.save()
        permission = Permission.objects.get(content_type__app_label='mobileApp', codename='administer_application')
        if user.is_staff:
            user.user_permissions.add(permission)
        else:
            user.user_permissions.remove(permission)
        if changed_access:
            invalidate_user_sessions(user.pk)
        return user, changed_access


def account_rut_exists(rut):
    return bool(query('SELECT 1 FROM app_identity.accounts WHERE is_active AND rut=' + literal(rut) + ';'))


def create_account(*, rut, first_name, last_name, password_hash, role):
    identifier = uuid4()
    values = (identifier, f'cuenta_{identifier.hex}', rut, first_name, last_name, password_hash, role)
    rows = query(
        'INSERT INTO app_identity.accounts (id,username,rut,first_name,last_name,password_hash,role) VALUES (' +
        ','.join(literal(value) for value in values) + ') ON CONFLICT DO NOTHING RETURNING *;',
        write=True,
    )
    if not rows:
        raise ValidationError('Ya existe un usuario con ese RUT.')
    return apply_account(rows[0])[0]


def sync_login(rut):
    rows = query('SELECT * FROM app_identity.accounts WHERE rut=' + literal(rut) +
                 ' ORDER BY is_active DESC, revision DESC LIMIT 1;')
    if not rows:
        profile = UserProfile.objects.filter(cloud_id__isnull=False, rut=rut).first()
        if profile:
            rows = query('SELECT * FROM app_identity.accounts WHERE id=' + literal(profile.cloud_id) + '::uuid;')
            if not rows:
                remove_local_account(profile.cloud_id)
    if rows:
        if not rows[0]['is_active']:
            remove_local_account(rows[0]['id'])
            return None
        user, _ = apply_account(rows[0])
        if rows[0]['rut'] == rut:
            return user
    return None


def sync_accounts():
    for data in query('SELECT * FROM app_identity.accounts ORDER BY id;'):
        if data['is_active']:
            apply_account(data)
        else:
            remove_local_account(data['id'])


def remove_local_account(identifier):
    from .accounts import invalidate_user_sessions
    profile = UserProfile.objects.filter(cloud_id=identifier).first()
    if profile:
        invalidate_user_sessions(profile.user_id)
        get_user_model().objects.filter(pk=profile.user_id).delete()


def update_account(user, profile, *, delete=False):
    payload = {
        'rut': profile.rut, 'first_name': user.first_name, 'last_name': user.last_name,
        'password_hash': user.password, 'role': profile.role, 'is_active': not delete and user.is_active,
    }
    assignments = ','.join(key + '=' + ('true' if value is True else 'false' if value is False else literal(value))
                           for key, value in payload.items())
    rows = query('UPDATE app_identity.accounts SET ' + assignments + ',revision=revision+1 WHERE id=' +
                 literal(profile.cloud_id) + '::uuid AND revision=' + str(int(profile.cloud_revision)) +
                 ' RETURNING revision;', write=True)
    if not rows:
        raise ValidationError('La cuenta cambió en otro equipo. Actualice el panel antes de editarla.')
    profile.cloud_revision = rows[0]['revision']
    with _lock:
        _checked.pop(str(profile.cloud_id), None)


_lock = threading.Lock()
_checked = {}


class SharedAccountMiddleware:
    """Apply remote role/password revocations to sessions on every installation."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        profile = getattr(request.user, 'app_profile', None) if request.user.is_authenticated else None
        if enabled() and profile and profile.cloud_id:
            key = str(profile.cloud_id)
            try:
                with _lock:
                    cached = _checked.get(key)
                    if cached is None or time.monotonic() - cached[0] > 5:
                        rows = query('SELECT * FROM app_identity.accounts WHERE id=' + literal(key) + '::uuid;')
                        cached = (time.monotonic(), rows[0] if rows else None)
                        _checked[key] = cached
                if cached[1] is None:
                    return JsonResponse({'error': 'Esta cuenta ya no está disponible.'}, status=401)
                user, changed = apply_account(cached[1])
                if changed or not user.is_active:
                    return JsonResponse({'error': 'Los permisos o la contraseña cambiaron. Vuelva a iniciar sesión.'}, status=401)
                request.user = user
            except (CloudUnavailable, ValidationError) as exc:
                return JsonResponse({'error': str(exc)}, status=503)
        return self.get_response(request)
