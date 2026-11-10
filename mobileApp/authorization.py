"""Object permissions shared by drawing endpoints and the interface."""
from functools import wraps
import json

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache


def is_administrator(user):
    profile = getattr(user, 'app_profile', None) if user.is_authenticated else None
    if profile is not None and profile.role != 'administrator':
        return False
    return bool(user.is_authenticated and user.is_active and (
        user.is_superuser or (user.is_staff and user.has_perm('mobileApp.administer_application'))))


def application_profile(user):
    return getattr(user, 'app_profile', None) if user.is_authenticated else None


def owns_plan(user, plan):
    if getattr(plan, 'owner_username', ''):
        return plan.owner_username == user.get_username()
    if getattr(plan, 'cloud_origin', ''):
        return False
    return plan.imported_by_id == user.pk


def can_delete_plan(user, plan):
    return bool(user.is_authenticated and user.is_active and (
        is_administrator(user) or (can_edit_plan(user, plan) and owns_plan(user, plan))))


def roles_for(user):
    from .models import PlanRole
    return PlanRole.objects.filter(Q(users=user) | Q(role_groups__users=user)).distinct()


def has_role_configuration(user):
    return user.plan_roles.exists() or user.plan_role_groups.exists()


def can_edit_plan(user, plan):
    if not user.is_authenticated or not user.is_active:
        return False
    if is_administrator(user):
        return True
    profile = application_profile(user)
    if profile is not None:
        return profile.role == 'worker'
    if getattr(plan, 'cloud_origin', ''):
        return bool(plan.owner_username and plan.owner_username == user.get_username())
    return plan.imported_by_id == user.pk


def can_access_plan(user, plan, action='can_view'):
    if not user.is_authenticated or not user.is_active:
        return False
    profile = application_profile(user)
    if profile is not None:
        if profile.role == 'viewer' and plan.imported_by_id != user.pk and not getattr(plan, 'cloud_origin', '') and not hasattr(plan, 'saved_project'):
            return False
        return action != 'can_export' or profile.role != 'viewer'
    if can_edit_plan(user, plan):
        return True
    roles = roles_for(user)
    if not has_role_configuration(user):
        return True
    relation = 'pdf_plans' if plan._meta.model_name == 'pdfplan' else 'cad_plans'
    return roles.filter(**{action: True}).filter(Q(all_plans=True) | Q(**{relation: plan})).exists()


def visible_plans(user, queryset):
    profile = application_profile(user)
    if profile is not None:
        if profile.role == 'viewer':
            condition = Q(saved_project__isnull=False) | Q(imported_by=user)
            if queryset.model._meta.model_name == 'pdfplan':
                condition |= ~Q(cloud_origin='')
            return queryset.filter(condition)
        return queryset
    if is_administrator(user):
        return queryset
    roles = roles_for(user)
    if not has_role_configuration(user) or roles.filter(can_view=True, all_plans=True).exists():
        return queryset
    relation = 'pdf_plans' if queryset.model._meta.model_name == 'pdfplan' else 'cad_plans'
    ids = roles.filter(can_view=True).values_list(relation, flat=True)
    return queryset.filter(Q(pk__in=ids) | Q(imported_by=user, owner_username='') | Q(owner_username=user.get_username()))


def workbook_plans(document):
    from .models import Plan, PdfPlan
    condition = Q(workbook=document) | Q(listing=document)
    cad = Plan.objects.defer('svg').filter(condition | Q(motors__workbook=document) | Q(motors__source_workbook=document)).distinct()
    pdf = PdfPlan.objects.filter(condition | Q(markers__workbook=document) | Q(markers__source_workbook=document)).distinct()
    return [*cad, *pdf]


def can_edit_workbook(user, document):
    if not user.is_authenticated or not user.is_active:
        return False
    if is_administrator(user):
        return True
    profile = application_profile(user)
    if profile is not None:
        return profile.role == 'worker'
    plans = workbook_plans(document)
    return all(can_edit_plan(user, plan) for plan in plans) if plans else document.updated_by_id == user.pk


def capabilities(user, plan=None):
    active = bool(user.is_authenticated and user.is_active)
    admin = is_administrator(user)
    create = active
    profile = application_profile(user)
    if profile is not None:
        create = active and profile.role in ('worker', 'administrator')
    elif active and not admin:
        roles = roles_for(user)
        create = not has_role_configuration(user) or roles.filter(can_create=True).exists()
    edit = active and (create if plan is None else can_edit_plan(user, plan))
    result = {key: edit for key in (
        'can_assign', 'can_change_excel', 'can_save_plan', 'can_save_pdf_plan',
        'can_change_plan', 'can_delete_plan', 'can_change_pdf_plan', 'can_delete_pdf_plan',
        'can_delete_cad_marker', 'can_delete_pdf_marker')}
    delete = active and (edit if plan is None else can_delete_plan(user, plan))
    result.update(can_delete_plan=delete, can_delete_pdf_plan=delete,
                  can_view=active, can_import=active if profile is not None else bool(create),
                  can_create=bool(create), can_administer=admin,
                  can_create_category=admin or bool(profile and profile.role == 'worker' and active),
                  can_history=bool(create), can_export=bool(create))
    if plan is not None:
        result['can_export'] = can_access_plan(user, plan, 'can_export')
    return result


def _target(view_name, kwargs):
    from .models import Motor, PdfMarker, Plan, PdfPlan, WorkbookDocument
    if 'plan_id' in kwargs:
        kind = kwargs.get('kind') or ('pdf' if 'pdf' in view_name or view_name == 'create_marker' else 'dxf')
        if kind not in ('pdf', 'dxf'):
            return None, None
        queryset = PdfPlan.objects.all() if kind == 'pdf' else Plan.objects.defer('svg')
        return get_object_or_404(queryset, pk=kwargs['plan_id']), None
    if 'motor_id' in kwargs or 'marker_id' in kwargs or 'button_id' in kwargs:
        key = next(key for key in ('motor_id', 'marker_id', 'button_id') if key in kwargs)
        model = Motor if key == 'motor_id' or kwargs.get('kind') == 'dxf' else PdfMarker
        queryset = model.objects.select_related('plan')
        if model is Motor:
            queryset = queryset.defer('plan__svg')
        return get_object_or_404(queryset, pk=kwargs[key]).plan, None
    if 'workbook_id' in kwargs:
        return None, get_object_or_404(WorkbookDocument.objects.defer('content'), pk=kwargs['workbook_id'])
    return None, None


def api_permissions(*permissions):
    def decorate(view):
        @wraps(view)
        @never_cache
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated or not request.user.is_active:
                return JsonResponse({'error': 'Inicie sesión con su usuario de Django.'}, status=401)
            plan, document = _target(view.__name__, kwargs)
            if plan is not None and not can_access_plan(request.user, plan):
                return JsonResponse({'error': 'No tiene acceso a este plano.'}, status=403)
            if document is not None and any(not can_access_plan(request.user, p) for p in workbook_plans(document)):
                return JsonResponse({'error': 'No tiene acceso al Excel de este plano.'}, status=403)
            mutation = request.method not in ('GET', 'HEAD', 'OPTIONS')
            local_action = view.__name__ in ('clear_local_project', 'refresh_project')
            profile = application_profile(request.user)
            if mutation and profile is not None and profile.role == 'viewer' and view.__name__ not in (
                    'plan_import', 'import_pdf', 'upload_project', 'open_cloud_project', 'refresh_project', 'clear_local_project'):
                return JsonResponse({'error': 'El espectador solo puede consultar e importar planos.'}, status=403)
            if mutation and plan is not None and view.__name__ in ('delete_plan', 'delete_pdf_plan') and not can_delete_plan(request.user, plan):
                return JsonResponse({'error': 'Solo puede eliminar sus propios planos. Un administrador puede eliminar cualquier plano.'}, status=403)
            if mutation and plan is not None and not local_action and not can_edit_plan(request.user, plan):
                return JsonResponse({'error': 'Su rol no permite modificar o guardar este plano.'}, status=403)
            if mutation and document is not None and not can_edit_workbook(request.user, document):
                return JsonResponse({'error': 'Solo el creador del plano o un administrador puede modificar su Excel.'}, status=403)
            if view.__name__ in ('plan_import', 'import_pdf', 'upload_project') and not capabilities(request.user)['can_import']:
                return JsonResponse({'error': 'Su rol no permite crear planos.'}, status=403)
            if plan is not None and view.__name__ == 'download_project' and not can_access_plan(request.user, plan, 'can_export'):
                return JsonResponse({'error': 'Su rol no permite exportar este plano.'}, status=403)
            if view.__name__ in ('workbook_download', 'workbook_csv', 'motor_excel_download'):
                if profile is not None and not capabilities(request.user)['can_export']:
                    return JsonResponse({'error': 'El espectador solo puede consultar los Excel.'}, status=403)
                exports = workbook_plans(document) if document is not None else ([plan] if plan is not None else [])
                if any(not can_access_plan(request.user, p, 'can_export') for p in exports):
                    return JsonResponse({'error': 'Su rol no permite exportar el Excel de este plano.'}, status=403)
            if permissions and plan is None and not request.user.has_perms(permissions):
                return JsonResponse({'error': 'No tiene permisos para realizar esta acción.'}, status=403)
            response = view(request, *args, **kwargs)
            if response.get('Content-Type', '').startswith('application/json') and response.status_code < 300:
                data = json.loads(response.content)
                def attach(value):
                    if isinstance(value, dict):
                        if 'workbook_url' in value and 'kind' in value and 'id' in value:
                            from .models import Plan, PdfPlan
                            queryset = PdfPlan.objects.all() if value['kind'] == 'pdf' else Plan.objects.defer('svg')
                            obj = queryset.filter(pk=value['id']).first()
                            if obj:
                                value['permissions'] = capabilities(request.user, obj)
                        for item in list(value.values()):
                            attach(item)
                    elif isinstance(value, list):
                        for item in value:
                            attach(item)
                attach(data)
                response.content = json.dumps(data, ensure_ascii=False).encode('utf-8')
            return response
        return wrapped
    return decorate
