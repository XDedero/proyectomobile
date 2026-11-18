"""Bind a verified company account to its private drawing workspace."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
import json
import logging
import os
import re
from threading import Lock
from time import monotonic

from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection, transaction


workspace = ContextVar('company_workspace', default=None)
schema_override = ContextVar('company_schema_override', default=None)
_provisioner = ThreadPoolExecutor(max_workers=1, thread_name_prefix='company-provision')
_provision_lock = Lock()
_provision_jobs = {}


@dataclass(frozen=True)
class Workspace:
    schema: str
    company_id: str
    brand: dict
    drive: dict = field(repr=False)


def schema_name(company_id):
    return 'tenant_' + company_id.hex


def drive_environment():
    company = workspace.get()
    if company is None:
        return None
    return {**os.environ, 'PLANOS_COMPANY_DRIVE': json.dumps(company.drive)}


def capture_workspace(callback):
    context = copy_context()
    return lambda *args, **kwargs: context.run(callback, *args, **kwargs)


def load_drive(company_id, connection_info):
    if connection_info is None:
        return None
    try:
        if connection_info.drive_secret_id:
            with connection.cursor() as cursor:
                cursor.execute('SELECT decrypted_secret FROM vault.decrypted_secrets WHERE id=%s AND name=%s',
                               [connection_info.drive_secret_id, f'planos_drive_{company_id.hex}'])
                secret = cursor.fetchone()
            if not secret:
                return None
            payload = json.loads(secret[0])
        else:
            key = os.environ.get('PANEL_DRIVE_ENCRYPTION_KEY', '').strip()
            if not key:
                return None
            from cryptography.fernet import Fernet, InvalidToken
            try:
                payload = json.loads(Fernet(key.encode()).decrypt(bytes(connection_info.encrypted_credentials)))
            except InvalidToken:
                return None
        folders = {role: connection_info.folder_ids[role] for role in ('pdf', 'excel', 'originals')}
        folders['root'] = connection_info.root_id
        if payload.get('company_id') != str(company_id):
            return None
        if len(set(folders.values())) != len(folders) or not all(
            re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value) for value in folders.values()
        ):
            return None
        credentials = {name: payload[name] for name in ('client_id', 'client_secret', 'refresh_token')}
        if not all(isinstance(value, str) and value.strip() for value in credentials.values()):
            return None
        return {'credentials': credentials, 'folders': folders}
    except DatabaseError as exc:
        logging.getLogger(__name__).error('Company storage secret unavailable (%s).', type(exc).__name__)
        return None
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


@contextmanager
def using_schema(schema):
    from psycopg import sql

    previous = schema_override.get() or settings.PLANOS_DATABASE_SCHEMA
    token = schema_override.set(schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        yield
    finally:
        schema_override.reset(token)
        if connection.connection is not None:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(previous)))
            except Exception:
                connection.close()
                raise


def workspace_is_ready(schema):
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', [schema + '.workspace_ready'])
        return cursor.fetchone()[0] is not None


def schedule_workspace(schema):
    from .management.commands.prepare_company_workspaces import prepare_workspace

    def provision():
        close_old_connections()
        try:
            prepare_workspace(schema, only_if_missing=True)
        except Exception as exc:
            logging.getLogger(__name__).error('Company workspace preparation failed (%s).', type(exc).__name__)
        finally:
            close_old_connections()
            with _provision_lock:
                _provision_jobs[schema] = monotonic() + 30

    with _provision_lock:
        if monotonic() < _provision_jobs.get(schema, 0):
            return
        _provision_jobs[schema] = float('inf')
        _provisioner.submit(provision)


def resolve_workspace(account, *, include_brand=False, prepare=False):
    from .company_brand import brand_context
    from .models import CompanyDriveConnection

    if not settings.PLANOS_CLOUD_MODE:
        return None, False
    schema = schema_name(account.company_id)
    drive = load_drive(account.company_id, CompanyDriveConnection.objects.filter(company_id=account.company_id).first())
    if drive is None:
        return None, False
    if not workspace_is_ready(schema):
        if prepare:
            schedule_workspace(schema)
        return None, True
    brand = brand_context(account.company) if include_brand else {}
    return Workspace(schema, str(account.company_id), brand, drive), False


@transaction.atomic
def workspace_user(account):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from .models import CompanyAdminSetup, UserProfile

    owner = isinstance(account, CompanyAdminSetup)
    role = UserProfile.Role.ADMINISTRATOR if owner else account.role
    username = 'company_owner' if owner else f'company_member_{account.pk}'
    user, created = get_user_model().objects.select_for_update().get_or_create(username=username)
    values = {'first_name': account.first_name, 'last_name': account.last_name, 'is_staff': role == UserProfile.Role.ADMINISTRATOR}
    if created or any(getattr(user, name) != value for name, value in values.items()):
        for name, value in values.items():
            setattr(user, name, value)
        user.set_unusable_password()
        user.save()
    profile, profile_created = UserProfile.objects.select_for_update().get_or_create(
        user=user, defaults={'rut': account.rut, 'role': role, 'is_primary_administrator': owner})
    role_changed = profile.role != role
    if profile.rut != account.rut or role_changed:
        profile.rut, profile.role = account.rut, role
        profile.save(update_fields=['rut', 'role'])
    profile.user = user
    if profile_created or role_changed:
        permission = Permission.objects.get(content_type__app_label='mobileApp', codename='administer_application')
        if role == UserProfile.Role.ADMINISTRATOR:
            user.user_permissions.add(permission)
        elif role_changed:
            user.user_permissions.remove(permission)
    return user


def offline_user_id(request):
    if not request.user.is_authenticated:
        return None
    company = workspace.get()
    return f'{company.company_id}:{request.user.pk}' if company else request.user.pk


class CompanyWorkspaceMiddleware:
    """Resolve the central login before any company drawing query can run."""

    central_paths = frozenset({
        '/api/auth/login/', '/api/auth/logout/', '/empresa/', '/empresa/salir/',
        '/api/auth/register/', '/api/auth/register/companies/', '/marca/logo.png', '/manifest.webmanifest',
        '/service-worker.js', '/healthz/',
    })

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .company_access import SESSION_KEY, current_account

        if (request.path in self.central_paths or request.path.startswith(('/marca/icono/', '/admin/'))
                or SESSION_KEY not in request.session):
            return self.get_response(request)
        account = current_account(request)
        if account is None:
            return self.get_response(request)
        company, _ = resolve_workspace(account, include_brand=request.path in ('/', '/administracion/', '/api/auth/session/'))
        if company is None:
            return self.get_response(request)
        token = workspace.set(company)
        try:
            with using_schema(company.schema):
                request.company_account = account
                request.user = workspace_user(account)
                return self.get_response(request)
        finally:
            workspace.reset(token)
