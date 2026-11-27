"""Resolve the maintenance roles in the current company's workspace."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from .accounts import is_primary_administrator
from .authorization import application_profile, can_access_plan, is_administrator


def can_report(user):
    if not user.is_authenticated or not user.is_active or is_primary_administrator(user):
        return False
    profile = application_profile(user)
    return is_administrator(user) or profile is None or profile.role == 'worker'


def eligible_users(user=None):
    from .tenant_runtime import schema_override, using_schema, workspace, workspace_user

    company = workspace.get()
    if company and user is not None:
        # The administrator can assign accounts that have not opened the PWA yet.
        from django.conf import settings
        from .models import CompanyAccount, CompanyAdminSetup

        tenant = schema_override.get()
        if tenant == company.schema:
            with using_schema(settings.PLANOS_DATABASE_SCHEMA):
                accounts = [
                    *CompanyAdminSetup.objects.filter(company_id=company.company_id),
                    *CompanyAccount.objects.filter(company_id=company.company_id, role__in=('worker', 'administrator')),
                ]
            return [workspace_user(account) for account in accounts]
    return list(get_user_model().objects.filter(is_active=True).select_related('app_profile').order_by('pk'))


def roles_for_button(button, users):
    users = {user.get_username(): user for user in users if user.is_active}
    responsible_names = button.maintenance_responsibles
    if responsible_names is None:
        responsible_names = [button.button_creator]
    supervisor_names = button.maintenance_supervisors
    if supervisor_names is None:
        admins = [user for user in users.values() if is_administrator(user)]
        admins.sort(key=lambda user: (not is_primary_administrator(user), user.pk))
        supervisor_names = [user.get_username() for user in admins[:1]]
    plan = button.plan
    if plan.draft_of_id:
        plan = plan.draft_of
    responsible = [users[name] for name in responsible_names if name in users
                   and can_report(users[name]) and can_access_plan(users[name], plan)]
    supervisors = [users[name] for name in supervisor_names if name in users and is_administrator(users[name])]
    return responsible, supervisors


def assignment_data(user, button=None):
    if not is_administrator(user):
        return None
    users = eligible_users(user)
    def option(candidate):
        return {'value': candidate.username, 'label': candidate.get_full_name() or candidate.username}

    if button:
        responsible, supervisors = roles_for_button(button, users)
    else:
        responsible = [user] if can_report(user) else []
        supervisors = [next((candidate for candidate in users if is_primary_administrator(candidate)), user)]
    return {
        'responsible_options': [option(candidate) for candidate in users if can_report(candidate)
                                and (is_administrator(candidate) or application_profile(candidate) is not None)],
        'supervisor_options': [option(candidate) for candidate in users if is_administrator(candidate)],
        'responsibles': [candidate.username for candidate in responsible],
        'supervisors': [candidate.username for candidate in supervisors],
    }


def assignment_changes(data, user, *, creating=False):
    key = 'maintenance_assignment'
    if key not in data:
        if creating and is_primary_administrator(user):
            return {'maintenance_responsibles': []}
        return {}
    if not is_administrator(user):
        raise PermissionDenied
    assignment = data[key]
    if not isinstance(assignment, dict):
        raise ValidationError('La asignación de mantención no es válida.')
    options = assignment_data(user)
    changes = {}
    for field, choices, maximum in (
        ('responsibles', 'responsible_options', len(options['responsible_options'])),
        ('supervisors', 'supervisor_options', 2),
    ):
        names = assignment.get(field)
        allowed = {option['value'] for option in options[choices]}
        if (not isinstance(names, list) or len(names) > maximum
                or any(not isinstance(name, str) or name not in allowed for name in names)
                or len(set(names)) != len(names)):
            raise ValidationError('Elige responsables habilitados y hasta dos administradores como supervisores.')
        changes['maintenance_' + field] = names
    return changes


def assigned_buttons(queryset, user, *, responsible=False):
    # SQLite does not implement the JSON array contains lookup. Workspaces in
    # production use PostgreSQL and can filter before loading any buttons.
    from django.db import connection

    if connection.vendor != 'postgresql':
        return queryset
    username = user.get_username()
    if responsible:
        return queryset.filter(Q(maintenance_responsibles__contains=[username]) |
                               Q(maintenance_responsibles__isnull=True, button_creator=username))
    return queryset.filter(Q(maintenance_supervisors__contains=[username]) |
                           Q(maintenance_supervisors__isnull=True))
