"""Shared registration and credentials, accessed only by the trusted app server."""
import json
import threading
import time
from datetime import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime

from .cloud_projects import CloudUnavailable, supabase_query
from .models import RegistrationRequest, UserProfile


def enabled():
    return settings.APP_SHARED_ACCOUNTS


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


def publish_request(item):
    rows = query(
        'SELECT app_identity.submit_registration(' +
        ','.join(literal(value) for value in (
            item.cloud_id, item.rut, item.first_name, item.last_name,
            item.password_hash, item.created_at.isoformat())) + ') AS result;', write=True)
    result = rows[0]['result']
    if result.get('error'):
        raise ValidationError(result['error'])
    RegistrationRequest.objects.filter(pk=item.pk).update(cloud_synced=True)
    item.cloud_synced = True
    return result


def refresh_request(item):
    """Retire a local replica resolved by an administrator on another installation."""
    rows = query('SELECT status FROM app_identity.registration_requests WHERE id=' +
                 literal(item.cloud_id) + '::uuid;')
    if not rows or rows[0]['status'] != 'pending':
        item.delete()


def sync_requests():
    """Publish retained submissions, then reconcile using global UUIDs, never local IDs."""
    for item in RegistrationRequest.objects.filter(status='pending', cloud_synced=False):
        try:
            publish_request(item)
        except ValidationError:
            # Another installation already submitted this RUT. Its request is authoritative.
            item.delete()
    rows = query("""SELECT id::text, rut, first_name, last_name, created_at, status
        FROM app_identity.registration_requests
        WHERE status='pending' OR id IN (
            """ + (','.join(literal(identifier) + '::uuid' for identifier in
                           RegistrationRequest.objects.values_list('cloud_id', flat=True)) or 'NULL') + ');')
    with transaction.atomic():
        for row in rows:
            if row['status'] != 'pending':
                RegistrationRequest.objects.filter(cloud_id=row['id']).delete()
                continue
            item = RegistrationRequest.objects.filter(cloud_id=row['id']).first()
            if item is None:
                # A pre-migration local duplicate can use the canonical request UUID.
                item = RegistrationRequest.objects.filter(rut=row['rut'], status='pending').first()
            values = {key: row[key] for key in ('rut', 'first_name', 'last_name')}
            values.update(cloud_id=row['id'], cloud_synced=True)
            if item:
                RegistrationRequest.objects.filter(pk=item.pk).update(**values)
            else:
                item = RegistrationRequest.objects.create(**values)
            # psycopg returns timestamptz as datetime; the legacy CLI returns ISO text.
            created_at = row['created_at']
            if not isinstance(created_at, datetime):
                created_at = parse_datetime(created_at)
            RegistrationRequest.objects.filter(pk=item.pk).update(created_at=created_at)


def apply_account(data):
    """Materialize an approved identity while preserving local plan foreign keys."""
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


def review_request(item, action, role, actor):
    if not item.cloud_synced:
        publish_request(item)
    rows = query('SELECT app_identity.review_registration(' +
                 ','.join(literal(value) for value in (item.cloud_id, action, role, actor.username)) +
                 ') AS result;', write=True)
    result = rows[0]['result']
    if result.get('error'):
        raise ValidationError(result['error'])
    # Old database functions could return a composite with null fields on reject.
    # Rejection must never attempt to create an account in this installation.
    if action == 'approve' and result.get('account'):
        apply_account(result['account'])
    item.delete()


def sync_login(identifier):
    from .accounts import normalize_rut
    try:
        rut = normalize_rut(identifier)
    except ValidationError:
        rut = None
    rows = query('SELECT * FROM app_identity.accounts WHERE ' +
                 ('rut=' + literal(rut) if rut else 'username=' + literal(identifier)) +
                 ' ORDER BY is_active DESC, revision DESC LIMIT 1;')
    if not rows:
        profiles = UserProfile.objects.filter(cloud_id__isnull=False)
        profile = (profiles.filter(rut=rut) if rut else profiles.filter(user__username=identifier)).first()
        if profile:
            rows = query('SELECT * FROM app_identity.accounts WHERE id=' + literal(profile.cloud_id) + '::uuid;')
            if not rows:
                remove_local_account(profile.cloud_id)
    if rows:
        if not rows[0]['is_active']:
            remove_local_account(rows[0]['id'])
            return None
        return apply_account(rows[0])[0]
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
