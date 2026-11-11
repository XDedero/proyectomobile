"""Administration of plan roles, user groups and button categories."""
from functools import wraps
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from .access import api_permissions, is_administrator
from .accounts import can_edit_user, registration_data, user_data
from .models import ButtonCategory, PlanRole, PlanRoleGroup, Plan, PdfPlan, RegistrationRequest
from .pdf_views import error_response, json_body


def administrators_only(view):
    @wraps(view)
    @api_permissions()
    def wrapped(request, *args, **kwargs):
        if not is_administrator(request.user):
            return error_response('Solo los administradores pueden gestionar usuarios, permisos, grupos y categorías.', 403)
        return view(request, *args, **kwargs)
    return wrapped


@require_GET
@administrators_only
def management_panel(request):
    directory = Path(__file__).resolve().parent
    files = [directory / 'templates/mobileApp/management.html',
             directory / 'static/mobileApp/management.js', directory / 'static/mobileApp/management.css']
    version = max(path.stat().st_mtime_ns for path in files)
    from .cloud_projects import CloudUnavailable
    try:
        pending = pending_registration_data()
        error = ''
    except (CloudUnavailable, ValidationError) as exc:
        pending = local_registration_data()
        error = str(exc)
    return render(request, 'mobileApp/management.html', {
        'asset_version': str(version),
        'pending_requests': pending, 'registration_error': error,
    })


def pending_registration_data():
    from . import shared_accounts
    if shared_accounts.enabled():
        shared_accounts.sync_requests()
    return local_registration_data()


def local_registration_data():
    return [
        registration_data(item)
        for item in RegistrationRequest.objects.filter(status='pending').select_related('reviewed_by')
    ]


@require_GET
@administrators_only
def pending_registrations(request):
    from .cloud_projects import CloudUnavailable
    try:
        return JsonResponse({'requests': pending_registration_data()})
    except (CloudUnavailable, ValidationError) as exc:
        return error_response(exc, 503)


def category_data(category):
    return {'id': str(category.pk), 'name': category.name}


@require_GET
@administrators_only
def management_data(request):
    from . import shared_accounts
    from .cloud_projects import CloudUnavailable
    try:
        if shared_accounts.enabled():
            shared_accounts.sync_accounts()
        requests = pending_registration_data()
    except (CloudUnavailable, ValidationError) as exc:
        return error_response(exc, 503)
    return JsonResponse({
        'can_manage_administrators': is_administrator(request.user),
        'users': [{**user_data(u), 'can_edit': can_edit_user(request.user, u),
                   'can_delete': u.pk != request.user.pk and can_edit_user(request.user, u)}
                  for u in get_user_model().objects.select_related('app_profile').order_by('first_name', 'last_name', 'username')],
        'requests': requests,
        'roles': [{'id': r.pk, 'name': r.name, 'description': r.description,
                   **{key: getattr(r, key) for key in ('can_view', 'can_export', 'can_create', 'all_plans')},
                   'users': list(r.users.values_list('pk', flat=True)),
                   'plans': [*[f'dxf:{pk}' for pk in r.cad_plans.values_list('pk', flat=True)],
                             *[f'pdf:{pk}' for pk in r.pdf_plans.values_list('pk', flat=True)]]}
                  for r in PlanRole.objects.prefetch_related('users', 'cad_plans', 'pdf_plans')],
        'groups': [{'id': g.pk, 'name': g.name, 'roles': list(g.roles.values_list('pk', flat=True)),
                    'users': list(g.users.values_list('pk', flat=True))} for g in PlanRoleGroup.objects.prefetch_related('roles', 'users')],
        'plans': [{'id': f'{kind}:{pk}', 'name': name, 'kind': kind.upper()}
                  for kind, model in [('pdf', PdfPlan), ('dxf', Plan)]
                  for pk, name in model.objects.values_list('pk', 'name')],
        'categories': [category_data(c) for c in ButtonCategory.objects.all()],
    })


def name_value(data):
    name = data.get('name')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValidationError('Ingrese un nombre de 1 a 80 caracteres.')
    return name.strip()


def selected_ids(data, key, model):
    ids = data.get(key, [])
    if not isinstance(ids, list) or any(type(pk) is not int or pk <= 0 for pk in ids):
        raise ValidationError('La selección de usuarios o roles no es válida.')
    records = model.objects.filter(pk__in=ids)
    if records.count() != len(set(ids)):
        raise ValidationError('Una selección ya no existe. Recargue el panel.')
    return records


@require_POST
@administrators_only
def manage_role(request):
    try:
        data = json_body(request)
        with transaction.atomic():
            role = get_object_or_404(PlanRole, pk=data['id']) if data.get('id') else PlanRole()
            if data.get('action') == 'delete':
                role.delete()
                return JsonResponse({'deleted': True})
            role.name = name_value(data)
            description = data.get('description', '')
            if not isinstance(description, str) or len(description) > 255:
                raise ValidationError('La descripción debe tener hasta 255 caracteres.')
            role.description = description
            for key in ('can_view', 'can_export', 'can_create', 'all_plans'):
                if type(data.get(key)) is not bool:
                    raise ValidationError('Revise las opciones del rol.')
                setattr(role, key, data[key])
            role.full_clean(exclude=['users', 'cad_plans', 'pdf_plans'])
            role.save()
            role.users.set(selected_ids(data, 'users', get_user_model()))
            plans = data.get('plans', [])
            if not isinstance(plans, list) or any(not isinstance(pk, str) for pk in plans):
                raise ValidationError('La selección de planos no es válida.')
            for kind, model, relation in [('dxf', Plan, role.cad_plans), ('pdf', PdfPlan, role.pdf_plans)]:
                ids = [pk.split(':')[1] for pk in plans if pk.startswith(kind + ':')]
                if any(not pk.isdecimal() for pk in ids):
                    raise ValidationError('Un plano seleccionado no es válido.')
                records = model.objects.filter(pk__in=ids)
                if records.count() != len(set(ids)):
                    raise ValidationError('Un plano seleccionado ya no existe.')
                relation.set(records)
            if any(not pk.startswith(('pdf:', 'dxf:')) for pk in plans):
                raise ValidationError('Un formato de plano no es válido.')
        return JsonResponse({'id': role.pk})
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe un rol con ese nombre.')


@require_POST
@administrators_only
def manage_role_group(request):
    try:
        data = json_body(request)
        with transaction.atomic():
            group = get_object_or_404(PlanRoleGroup, pk=data['id']) if data.get('id') else PlanRoleGroup()
            if data.get('action') == 'delete':
                group.delete()
                return JsonResponse({'deleted': True})
            group.name = name_value(data)
            group.full_clean()
            group.save()
            group.roles.set(selected_ids(data, 'roles', PlanRole))
            group.users.set(selected_ids(data, 'users', get_user_model()))
        return JsonResponse({'id': group.pk})
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe un grupo con ese nombre.')


@require_POST
@api_permissions()
def manage_category(request):
    try:
        data = request.POST if request.FILES else json_body(request)
        if not is_administrator(request.user):
            from .authorization import capabilities
            if not capabilities(request.user)['can_create_category'] or data.get('id') or data.get('action') not in (None, '', 'create'):
                return error_response('Solo los administradores pueden modificar o eliminar categorías. Los trabajadores pueden crearlas.', 403)
        if request.FILES:
            raise ValidationError('Las categorías solo admiten un nombre.')
        action = data.get('action')
        if action not in (None, '', 'create', 'update', 'delete'):
            raise ValidationError('La acción de categoría no es válida.')
        if action == 'delete' and not data.get('id'):
            raise ValidationError('Seleccione una categoría para eliminar.')
        with transaction.atomic():
            category = get_object_or_404(ButtonCategory, pk=data['id']) if data.get('id') else ButtonCategory()
            if action == 'delete':
                category.delete()
                return JsonResponse({'deleted': True})
            category.name = name_value(data)
            category.full_clean()
            category.save()
        return JsonResponse(category_data(category))
    except (ValidationError, IntegrityError) as exc:
        return error_response(exc if isinstance(exc, ValidationError) else 'Ya existe una categoría con ese nombre.')
