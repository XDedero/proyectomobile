from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .access import api_permissions, can_edit_plan
from .pdf_views import error_response, json_body
from .project_views import get_plan
from .sectors import clean_sectors


@require_http_methods(['GET', 'POST'])
@api_permissions()
@transaction.atomic
def plan_sectors(request, kind, plan_id):
    plan = get_plan(kind, plan_id)
    if request.method == 'POST':
        if not can_edit_plan(request.user, plan):
            return error_response('Solo el creador del plano o un administrador puede modificar sus sectores.', 403)
        plan = type(plan).objects.select_for_update().get(pk=plan.pk)
        try:
            data = json_body(request)
            rows = list(plan.sectors)
            action = data.get('action')
            if action == 'save':
                sector = data.get('sector')
                cleaned = clean_sectors([sector], pages=len(plan.pages) if kind == 'pdf' else 1)[0]
                previous = next((row for row in rows if row['id'] == cleaned['id']), None)
                if previous != data.get('previous'):
                    return error_response('Este sector cambió en otra sesión. Cierre y vuelva a abrir Sectores.', 409)
                rows = [row for row in rows if row['id'] != cleaned['id']] + [cleaned]
            elif action == 'delete':
                identifier = str(UUID(data.get('id', '')))
                previous = next((row for row in rows if row['id'] == identifier), None)
                if previous != data.get('previous'):
                    return error_response('Este sector cambió en otra sesión. Cierre y vuelva a abrir Sectores.', 409)
                rows = [row for row in rows if row['id'] != identifier]
            else:
                raise ValidationError('Elija una acción válida para los sectores.')
            plan.sectors = clean_sectors(rows, pages=len(plan.pages) if kind == 'pdf' else 1)
            plan.save(update_fields=['sectors'])
        except (ValidationError, ValueError, TypeError, AttributeError) as exc:
            return error_response(exc)
    return JsonResponse({'sectors': plan.sectors, 'can_edit': can_edit_plan(request.user, plan)})
