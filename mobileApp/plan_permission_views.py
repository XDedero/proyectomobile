from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_POST

from .authorization import api_permissions, capabilities, owns_plan
from .models import PlanEditGrant
from .pdf_views import error_response, json_body, pdf_data
from .project_views import get_plan


@require_POST
@api_permissions()
def edit_plan_draft(request, kind, plan_id):
    from .plan_drafts import working_copy
    from .views import plan_data
    original = get_plan(kind, plan_id)
    if json_body(request).get('discard') is True:
        parent = original.draft_of if original.draft_of_id else original
        parent.drafts.filter(draft_user=request.user).delete()
        original = parent
    plan = working_copy(original, request.user)
    return JsonResponse({'plan': pdf_data(plan) if kind == 'pdf' else plan_data(plan, detail=True)})


@require_http_methods(['GET', 'POST'])
@api_permissions()
def plan_edit_permissions(request, kind, plan_id):
    from django.db.models import Q
    plan = get_plan(kind, plan_id)
    if plan.draft_of_id:
        plan = plan.draft_of
    if not owns_plan(request.user, plan) or not capabilities(request.user, plan)['can_manage_plan_permissions']:
        return error_response('Solo el creador del plano puede otorgar o retirar estos permisos.', 403)
    users = get_user_model().objects.filter(is_active=True).filter(
        Q(app_profile__role__in=['worker', 'administrator']) | Q(is_superuser=True, app_profile__isnull=True)
    ).exclude(pk=request.user.pk).order_by('first_name', 'last_name', 'username').distinct()
    if request.method == 'POST':
        from django.core.exceptions import ValidationError
        try:
            ids = json_body(request).get('user_ids')
            if not isinstance(ids, list) or len(ids) > 1000 or any(type(pk) is not int or pk <= 0 for pk in ids):
                raise ValidationError('Seleccione una lista válida de usuarios.')
            ids = set(ids)
            if set(users.filter(pk__in=ids).values_list('pk', flat=True)) != ids:
                raise ValidationError('Solo puede autorizar trabajadores o administradores activos.')
            with transaction.atomic():
                type(plan).objects.select_for_update().get(pk=plan.pk)
                PlanEditGrant.objects.filter(cloud_key=plan.cloud_key).exclude(user_id__in=ids).delete()
                for user_id in ids:
                    PlanEditGrant.objects.get_or_create(cloud_key=plan.cloud_key, user_id=user_id)
        except ValidationError as exc:
            return error_response(exc)
    selected = set(PlanEditGrant.objects.filter(cloud_key=plan.cloud_key).values_list('user_id', flat=True))
    return JsonResponse({'users': [
        {'id': user.pk, 'name': user.get_full_name().strip() or user.get_username(), 'username': user.get_username(),
         'can_edit': user.pk in selected} for user in users]})
