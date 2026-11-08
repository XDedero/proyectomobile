"""Object permissions shared by drawing endpoints and the interface."""
from functools import wraps
import json

from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache


def is_administrator(user):
    return bool(user.is_authenticated and user.is_active and (
        user.is_superuser or (user.is_staff and user.has_perm('mobileApp.administer_application'))))


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
    if getattr(plan, 'cloud_origin', ''):
        return bool(plan.owner_username and plan.owner_username == user.get_username())
    return plan.imported_by_id == user.pk


def can_access_plan(user, plan, action='can_view'):
    if can_edit_plan(user, plan):
        return True
    roles = roles_for(user)
    if not has_role_configuration(user):
        return True
    relation = 'pdf_plans' if plan._meta.model_name == 'pdfplan' else 'cad_plans'
    return roles.filter(**{action: True}).filter(Q(all_plans=True) | Q(**{relation: plan})).exists()


def visible_plans(user, queryset):
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
    cad = Plan.objects.filter(condition | Q(motors__workbook=document) | Q(motors__source_workbook=document)).distinct()
    pdf = PdfPlan.objects.filter(condition | Q(markers__workbook=document) | Q(markers__source_workbook=document)).distinct()
    return [*cad, *pdf]


def can_edit_workbook(user, document):
    if is_administrator(user):
        return True
    plans = workbook_plans(document)
    return all(can_edit_plan(user, plan) for plan in plans) if plans else document.updated_by_id == user.pk


def capabilities(user, plan=None):
    active = bool(user.is_authenticated and user.is_active)
    admin = is_administrator(user)
    create = active
    if active and not admin:
        roles = roles_for(user)
        create = not has_role_configuration(user) or roles.filter(can_create=True).exists()
    edit = active and (plan is None or can_edit_plan(user, plan))
    result = {key: edit for key in (
        'can_assign', 'can_change_excel', 'can_save_plan', 'can_save_pdf_plan',
        'can_change_plan', 'can_delete_plan', 'can_change_pdf_plan', 'can_delete_pdf_plan',
        'can_delete_cad_marker', 'can_delete_pdf_marker')}
    result.update(can_view=active, can_import=bool(create), can_administer=admin)
    if plan is not None:
        result['can_export'] = can_access_plan(user, plan, 'can_export')
    return result


def _target(view_name, kwargs):
    from .models import Motor, PdfMarker, Plan, PdfPlan, WorkbookDocument
    if 'plan_id' in kwargs:
        kind = kwargs.get('kind') or ('pdf' if 'pdf' in view_name or view_name == 'create_marker' else 'dxf')
        if kind not in ('pdf', 'dxf'):
            return None, None
        return get_object_or_404(PdfPlan if kind == 'pdf' else Plan, pk=kwargs['plan_id']), None
    if 'motor_id' in kwargs or 'marker_id' in kwargs or 'button_id' in kwargs:
        key = next(key for key in ('motor_id', 'marker_id', 'button_id') if key in kwargs)
        model = Motor if key == 'motor_id' or kwargs.get('kind') == 'dxf' else PdfMarker
        return get_object_or_404(model.objects.select_related('plan'), pk=kwargs[key]).plan, None
    if 'workbook_id' in kwargs:
        return None, get_object_or_404(WorkbookDocument, pk=kwargs['workbook_id'])
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
            if mutation and plan is not None and not local_action and not can_edit_plan(request.user, plan):
                return JsonResponse({'error': 'Solo el creador del plano o un administrador puede modificarlo y guardarlo.'}, status=403)
            if mutation and document is not None and not can_edit_workbook(request.user, document):
                return JsonResponse({'error': 'Solo el creador del plano o un administrador puede modificar su Excel.'}, status=403)
            if view.__name__ in ('plan_import', 'import_pdf', 'upload_project') and not capabilities(request.user)['can_import']:
                return JsonResponse({'error': 'Su rol no permite crear planos.'}, status=403)
            if plan is not None and view.__name__ == 'download_project' and not can_access_plan(request.user, plan, 'can_export'):
                return JsonResponse({'error': 'Su rol no permite exportar este plano.'}, status=403)
            if view.__name__ in ('workbook_download', 'workbook_csv', 'motor_excel_download'):
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
                            obj = (PdfPlan if value['kind'] == 'pdf' else Plan).objects.filter(pk=value['id']).first()
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
