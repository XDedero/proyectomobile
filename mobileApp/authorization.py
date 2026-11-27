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
    if getattr(plan, 'draft_of_id', None):
        return owns_plan(user, plan.draft_of)
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


def prefetch_plan_edit_keys(user):
    profile = application_profile(user)
    if profile is not None and profile.role == 'worker':
        # This instance belongs to the current read request. Grant changes are
        # still checked on the next request and before every write.
        user._listed_plan_edit_keys = set(user.plan_edit_grants.values_list('cloud_key', flat=True))


def can_edit_plan(user, plan):
    if not user.is_authenticated or not user.is_active:
        return False
    if getattr(plan, 'draft_of_id', None):
        return plan.draft_user_id == user.pk and can_edit_plan(user, plan.draft_of)
    if is_administrator(user):
        return True
    profile = application_profile(user)
    if profile is not None:
        if profile.role != 'worker':
            return False
        if owns_plan(user, plan):
            return True
        keys = getattr(user, '_listed_plan_edit_keys', None)
        return plan.cloud_key in keys if keys is not None else user.plan_edit_grants.filter(cloud_key=plan.cloud_key).exists()
    return owns_plan(user, plan)


def can_edit_button(user, button):
    return can_edit_plan(user, button.plan) and can_edit_button_creator(user, button.button_creator, button.plan)


def can_edit_button_creator(user, creator, plan):
    return (is_administrator(user) or application_profile(user) is None or
            (creator == user.get_username() if creator else owns_plan(user, plan)))


def can_access_plan(user, plan, action='can_view'):
    if not user.is_authenticated or not user.is_active:
        return False
    if getattr(plan, 'draft_of_id', None):
        return can_edit_plan(user, plan)
    profile = application_profile(user)
    if profile is not None:
        if action == 'can_export':
            return can_edit_plan(user, plan)
        if profile.role == 'viewer' and plan.imported_by_id != user.pk and not getattr(plan, 'cloud_origin', '') and not hasattr(plan, 'saved_project'):
            return False
        return True
    if can_edit_plan(user, plan):
        return True
    roles = roles_for(user)
    if not has_role_configuration(user):
        return True
    relation = 'pdf_plans' if plan._meta.model_name == 'pdfplan' else 'cad_plans'
    return roles.filter(**{action: True}).filter(Q(all_plans=True) | Q(**{relation: plan})).exists()


def visible_plans(user, queryset):
    queryset = queryset.filter(draft_of__isnull=True)
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


def can_edit_workbook(user, document, *, plans=None):
    if not user.is_authenticated or not user.is_active:
        return False
    if is_administrator(user):
        return True
    profile = application_profile(user)
    if profile is not None and profile.role != 'worker':
        return False
    if plans is None:
        plans = workbook_plans(document)
    return all(can_edit_plan(user, plan) for plan in plans) if plans else document.updated_by_id == user.pk


def can_edit_button_sheets(user, document, sheets):
    profile = application_profile(user)
    if is_administrator(user) or profile is None or not sheets:
        return True
    from .models import Motor, PdfMarker
    for model in (PdfMarker, Motor):
        linked = Q(source_workbook=document) | Q(workbook=document, sheet_name__in=sheets)
        if not document.plan_mode:
            linked |= Q(workbook=document)
        buttons = model.objects.filter(linked).exclude(button_creator=user.get_username())
        if buttons.exclude(button_creator='').exists():
            return False
        if any(not owns_plan(user, button.plan) for button in buttons.select_related('plan', 'plan__draft_of')):
            return False
    return True


def can_export_workbook(user, plans):
    if not user.is_authenticated or not user.is_active:
        return False
    profile = application_profile(user)
    return (profile is None or profile.role != 'viewer') and all(
        can_access_plan(user, plan, 'can_export') for plan in plans)


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
        result['can_copy_button'] = edit
        result['can_manage_plan_permissions'] = bool(edit and owns_plan(user, plan))
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
        return get_object_or_404(queryset, pk=kwargs[key]), None
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
            button = plan if any(key in kwargs for key in ('motor_id', 'marker_id', 'button_id')) else None
            if button is not None:
                plan = button.plan
            plans = workbook_plans(document) if document is not None else []
            if plan is not None and not can_access_plan(request.user, plan):
                return JsonResponse({'error': 'No tiene acceso a este plano.'}, status=403)
            if any(not can_access_plan(request.user, p) for p in plans):
                return JsonResponse({'error': 'No tiene acceso al Excel de este plano.'}, status=403)
            mutation = request.method not in ('GET', 'HEAD', 'OPTIONS')
            local_action = view.__name__ in ('clear_local_project', 'refresh_project')
            profile = application_profile(request.user)
            if mutation and profile is not None and profile.role == 'viewer' and view.__name__ not in (
                    'import_pdf', 'upload_project', 'open_cloud_project', 'refresh_project', 'clear_local_project'):
                return JsonResponse({'error': 'El espectador solo puede consultar e importar planos.'}, status=403)
            if mutation and plan is not None and view.__name__ in ('delete_plan', 'delete_pdf_plan') and not can_delete_plan(request.user, plan):
                return JsonResponse({'error': 'Solo puede eliminar sus propios planos. Un administrador puede eliminar cualquier plano.'}, status=403)
            if mutation and plan is not None and not local_action and not can_edit_plan(request.user, plan):
                return JsonResponse({'error': 'Su rol no permite modificar o guardar este plano.'}, status=403)
            if mutation and button is not None and not can_edit_button_creator(request.user, button.button_creator, plan):
                return JsonResponse({'error': 'Solo puedes editar los botones que creaste.'}, status=403)
            if mutation and document is not None and not can_edit_workbook(request.user, document, plans=plans):
                return JsonResponse({'error': 'Solo el creador del plano o un administrador puede modificar su Excel.'}, status=403)
            if view.__name__ in ('import_pdf', 'upload_project') and not capabilities(request.user)['can_import']:
                return JsonResponse({'error': 'Su rol no permite crear planos.'}, status=403)
            if plan is not None and view.__name__ == 'download_project' and not can_access_plan(request.user, plan, 'can_export'):
                return JsonResponse({'error': 'Su rol no permite exportar este plano.'}, status=403)
            if view.__name__ in ('workbook_download', 'workbook_csv', 'motor_excel_download'):
                if not can_export_workbook(request.user, [plan] if plan is not None else plans):
                    return JsonResponse({'error': 'Su rol no permite exportar el Excel de este plano.'}, status=403)
            if permissions and plan is None and not request.user.has_perms(permissions):
                return JsonResponse({'error': 'No tiene permisos para realizar esta acción.'}, status=403)
            response = view(request, *args, **kwargs)
            if (plan is not None and plan.draft_of_id) or any(p.draft_of_id for p in plans) or view.__name__ == 'edit_plan_draft':
                response['X-Reloncavi-Transient'] = '1'
            if mutation and response.status_code < 300 and view.__name__ not in ('save_project', 'edit_plan_draft', 'plan_edit_permissions', 'refresh_project'):
                for changed_plan in ([plan] if plan is not None else plans):
                    if changed_plan.draft_of_id:
                        type(changed_plan).objects.filter(pk=changed_plan.pk).update(draft_dirty=True)
            if (view.__name__ != 'workbook_cells' and response.get('Content-Type', '').startswith('application/json')
                    and response.status_code < 300):
                data = json.loads(response.content)
                def attach(value, editable=False, target_plan=None):
                    if isinstance(value, dict):
                        if 'workbook_url' in value and 'kind' in value and 'id' in value:
                            from .models import Plan, PdfPlan
                            kind = PdfPlan if value['kind'] == 'pdf' else Plan
                            if (request.method in ('GET', 'HEAD') and isinstance(target_plan, kind)
                                    and target_plan.pk == value['id']):
                                obj = target_plan
                            elif view.__name__ in ('plans', 'list_pdf_plans') and 'permissions' in value:
                                # Plan lists already serialize their own permissions and carry no buttons.
                                obj = None
                            else:
                                queryset = kind.objects.all() if kind is PdfPlan else kind.objects.defer('svg')
                                obj = queryset.filter(pk=value['id']).first()
                            if obj:
                                value['permissions'] = capabilities(request.user, obj)
                                editable = value['permissions']['can_assign']
                                target_plan = obj
                        if '_button_creator' in value:
                            creator = value.pop('_button_creator')
                            value['can_edit'] = editable and can_edit_button_creator(request.user, creator, target_plan)
                        for item in list(value.values()):
                            attach(item, editable, target_plan)
                    elif isinstance(value, list):
                        for item in value:
                            attach(item, editable, target_plan)
                attach(data, plan is not None and can_edit_plan(request.user, plan), plan)
                response.content = json.dumps(data, ensure_ascii=False).encode('utf-8')
            return response
        return wrapped
    return decorate
