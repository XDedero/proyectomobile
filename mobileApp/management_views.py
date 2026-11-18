"""Administration of plan roles, user groups and button categories."""
from functools import wraps
from re import fullmatch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .access import api_permissions, is_administrator
from .accounts import can_edit_user, is_primary_administrator, normalize_rut, registration_data, user_data
from .company_brand import brand_context, normalize_logo
from .models import (ButtonCategory, Company, CompanyAudit, CompanyBrand, PlanRole, PlanRoleGroup, Plan, PdfPlan,
                     RegistrationRequest, RELONCAVI_COMPANY_ID)
from .pdf_views import error_response, json_body
from .pwa import asset_version


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
    from .tenant_runtime import offline_user_id, workspace
    company = workspace.get()
    from .cloud_projects import CloudUnavailable
    try:
        pending = pending_registration_data(request)
        error = ''
    except (CloudUnavailable, ValidationError) as exc:
        pending = local_registration_data()
        error = str(exc)
    return render(request, 'mobileApp/management.html', {
        'interface_user_id': offline_user_id(request),
        'asset_version': asset_version(),
        'pending_requests': pending, 'registration_error': error,
        'can_edit_company': is_primary_administrator(request.user),
        **(company.brand if company else {}),
    })


@require_http_methods(['GET', 'POST'])
@administrators_only
def edit_company_profile(request):
    if not is_primary_administrator(request.user):
        return error_response('Solo el administrador general puede editar la empresa.', 403)
    company_account = getattr(request, 'company_account', None)
    if company_account is not None:
        from django.conf import settings
        from .tenant_runtime import using_schema
        with using_schema(settings.PLANOS_DATABASE_SCHEMA):
            return _edit_company_brand(request, company_account.company_id, company_account.rut)
    return _edit_company_brand(request, RELONCAVI_COMPANY_ID, request.user.get_username())


def _edit_company_brand(request, company_id, actor):
    company = get_object_or_404(Company, pk=company_id)
    current = CompanyBrand.objects.filter(company=company).defer('logo_png').first()
    values = {'name': company.name, 'tax_id': company.tax_id or '',
              'color': current.base_color if current else ''}
    error = ''
    if request.method == 'POST':
        values = {key: request.POST.get(key, '').strip() for key in values}
        try:
            if not values['name'] or len(values['name']) > 200:
                raise ValidationError('Ingrese un nombre de hasta 200 caracteres.')
            if values['color'] and not fullmatch(r'#[0-9a-fA-F]{6}', values['color']):
                raise ValidationError('Ingrese un color con formato #RRGGBB.')
            tax_id = normalize_rut(values['tax_id']) if values['tax_id'] else None
            logo = normalize_logo(request.FILES['logo']) if 'logo' in request.FILES else None
            with transaction.atomic():
                company = Company.objects.select_for_update().get(pk=company.pk)
                brand, created = CompanyBrand.objects.get_or_create(company=company)
                if request.POST.get('revision') != str(0 if created else brand.revision):
                    raise ValidationError('La marca cambió en otra sesión. Recargue el formulario.')
                company.name = values['name']
                company.tax_id = tax_id
                company.save(update_fields=['name', 'tax_id'])
                brand.base_color = values['color'].lower()
                fields = ['base_color', 'revision']
                if logo is not None:
                    brand.logo_png = logo
                    fields.append('logo_png')
                brand.revision += 1
                brand.save(update_fields=fields)
                CompanyAudit.objects.create(company=company, company_code=company.pk,
                                            actor=actor, action='marca_editada')
            return redirect('management_panel')
        except (ValidationError, IntegrityError) as exc:
            error = exc.messages[0] if isinstance(exc, ValidationError) else 'Ese RUT de empresa ya está en uso.'
    return render(request, 'mobileApp/company_profile.html', {
        'asset_version': asset_version(),
        **(brand_context(company) if company_id != RELONCAVI_COMPANY_ID else {}),
        'values': values, 'error': error, 'revision': request.POST.get('revision', '') if error else
        current.revision if current else 0,
    }, status=400 if error else 200)


def pending_registration_data(request=None):
    company_account = getattr(request, 'company_account', None)
    if company_account is not None:
        from django.conf import settings
        from .company_access import company_administrator
        from .tenant_runtime import using_schema
        if not company_administrator(company_account):
            return []
        with using_schema(settings.PLANOS_DATABASE_SCHEMA):
            return [registration_data(item) for item in RegistrationRequest.objects.filter(
                company_id=company_account.company_id, status='pending').select_related('company', 'reviewed_by')]
    from . import shared_accounts
    if shared_accounts.enabled():
        shared_accounts.sync_requests()
    return local_registration_data()


def local_registration_data():
    requests = RegistrationRequest.objects.filter(
        company_id=RELONCAVI_COMPANY_ID, status='pending',
    ).select_related('company', 'reviewed_by')
    return [
        registration_data(item)
        for item in requests
    ]


@require_GET
@administrators_only
def pending_registrations(request):
    from .cloud_projects import CloudUnavailable
    try:
        return JsonResponse({'requests': pending_registration_data(request)})
    except (CloudUnavailable, ValidationError) as exc:
        return error_response(exc, 503)


def category_data(category):
    return {'id': str(category.pk), 'name': category.name}


@require_GET
@administrators_only
def management_data(request):
    from . import shared_accounts
    from .cloud_projects import CloudUnavailable
    from .tenant_runtime import workspace
    company = workspace.get()
    try:
        if shared_accounts.enabled():
            shared_accounts.sync_accounts()
        requests = pending_registration_data(request)
    except (CloudUnavailable, ValidationError) as exc:
        return error_response(exc, 503)
    return JsonResponse({
        'can_manage_administrators': is_administrator(request.user),
        'users': [{**user_data(u), 'can_edit': company is None and can_edit_user(request.user, u),
                   'can_delete': company is None and u.pk != request.user.pk and can_edit_user(request.user, u)}
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
