from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .access import api_permissions, can_edit_plan
from .button_descriptions import TemplateReader, clean_color, clean_rows, effective_descriptions
from .maintenance import schedule_data, update_schedule
from .models import Motor, PdfMarker
from .pdf_views import error_response, json_body, marker_data
from .views import motor_data


@require_http_methods(['GET', 'POST'])
@api_permissions()
@transaction.atomic
def button_details(request, kind, button_id):
    if kind not in ('pdf', 'dxf'):
        raise Http404
    model = PdfMarker if kind == 'pdf' else Motor
    queryset = model.objects.select_for_update() if request.method == 'POST' else model.objects.all()
    button = get_object_or_404(queryset, pk=button_id)
    name_field = 'label' if kind == 'pdf' else 'name'
    can_edit = can_edit_plan(request.user, button.plan)
    if request.method == 'POST' and not can_edit:
        return error_response('No tienes permiso para editar este botón.', status=403)
    with TemplateReader() as reader:
        rows = effective_descriptions(button, reader)
    if request.method == 'POST':
        try:
            data = json_body(request)
            name = data.get('name', getattr(button, name_field))
            limit = 120 if kind == 'pdf' else 255
            if not isinstance(name, str) or len(name.strip()) > limit:
                raise ValidationError(f'El nombre puede tener hasta {limit} caracteres.')
            color = clean_color(data.get('color', button.button_color))
            enabled = data.get('show_description', button.show_description)
            if type(enabled) is not bool:
                raise ValidationError('Indica si el botón mostrará su descripción.')
            maintenance = (update_schedule(button, data['maintenance'], request.user, category=button.category)
                           if 'maintenance' in data else {})
            if 'descriptions' in data:
                rows = clean_rows(data['descriptions'], rows)
                button.descriptions = rows
        except ValidationError as exc:
            return error_response(exc)
        if 'next_maintenance' in maintenance and not button.plan.draft_of_id:
            from .maintenance_alerts import clear_resolved_alerts
            clear_resolved_alerts(button.maintenance_key, maintenance['next_maintenance'])
        for field, value in maintenance.items():
            setattr(button, field, value)
        setattr(button, name_field, name.strip())
        button.button_color = color
        button.show_description = enabled
        button.save(update_fields=[name_field, 'button_color', 'descriptions', 'show_description', *maintenance])
    result = {'name': getattr(button, name_field), 'color': button.button_color, 'can_edit': can_edit,
              'show_description': button.show_description, 'maintenance': schedule_data(button),
              'maintenance_required': button.maintenance_required,
              'descriptions': [{key: row[key] for key in ('id', 'key', 'value')} for row in (rows or [])]}
    if request.method == 'POST':
        result['button'] = marker_data(button) if kind == 'pdf' else motor_data(button)
    return JsonResponse(result)
