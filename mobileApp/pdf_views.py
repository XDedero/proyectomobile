"""Authenticated PDF plans, manually placed markers and editable XLSX library."""

from io import BytesIO
import json
import math
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from pypdf import PdfReader

from .access import api_permissions, capabilities
from .models import Motor, PdfMarker, PdfPlan, WorkbookDocument, SavedMotor
from .views import validate_excel
from .workbook_service import document_sheets, edited_document, positive_integer, read_cells


def error_response(error, status=400):
    return JsonResponse({'error': ' '.join(error.messages) if isinstance(error, ValidationError) else str(error)}, status=status)


def json_body(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError) as exc:
        raise ValidationError('La solicitud debe contener datos JSON válidos.') from exc
    if not isinstance(data, dict):
        raise ValidationError('La solicitud debe contener un objeto JSON.')
    return data


def workbook_data(document, *, sheets=True, user=None, sheet_name=None, context=None):
    snapshot = bool(user is not None and SavedMotor.objects.filter(workbook_id=document.pk).exists())
    result = {
        'id': document.pk, 'name': document.name, 'revision': document.revision,
        'metadata_url': reverse('workbook_metadata', args=[document.pk]),
        'cells_url': reverse('workbook_cells', args=[document.pk]),
        'save_url': reverse('workbook_save', args=[document.pk]),
        'download_url': reverse('workbook_download', args=[document.pk]),
        'csv_url': reverse('workbook_csv', args=[document.pk]),
        'can_edit': bool(user is not None and not snapshot and capabilities(user)['can_change_excel']),
        'is_motor': bool(document.is_motor or (document.plan_mode and sheet_name and context != 'listing')),
    }
    if sheets:
        result['sheets'] = document_sheets(document.content)
        if document.plan_mode and context == 'listing':
            names = set(document.plan_sheets.filter(is_listing=True).values_list('name', flat=True))
            result['sheets'] = [sheet for sheet in result['sheets'] if sheet['name'] in names]
        elif document.plan_mode and sheet_name:
            result['sheets'] = [sheet for sheet in result['sheets'] if sheet['name'] == sheet_name]
    if sheet_name:
        result['preferred_sheet'] = sheet_name
        result['metadata_url'] += '?' + urlencode({'sheet': sheet_name, 'context': context or 'motor'})
    result['plan_mode'] = document.plan_mode
    if context:
        result['context'] = context
    if user is not None:
        from .motor_service import registration_data
        if document.plan_mode and sheet_name and context != 'listing':
            from .plan_workbook import selected_motor_document, known_sheet
            result['motor_registration'] = registration_data(selected_motor_document(document, sheet_name), inspect=True)
            if not result['motor_registration'].get('registered') and result['motor_registration'].get('has_data'):
                record = known_sheet(document.content, sheet_name)
                if record:
                    result['motor_registration'].update(registered=True, saved_motor_id=record.pk, name=record.name, can_register=False)
        else:
            result['motor_registration'] = registration_data(document, inspect=True)
    return result


def marker_data(marker):
    return {
        'id': marker.pk, 'page': marker.page, 'x': marker.x, 'y': marker.y, 'label': marker.label,
        'sheet_name': marker.sheet_name,
        'workbook': workbook_data(marker.workbook, sheets=False, sheet_name=marker.sheet_name) if marker.workbook_id and (marker.sheet_name or not marker.workbook.plan_mode) else None,
        'update_url': reverse('update_marker', args=[marker.pk]),
        'delete_url': reverse('delete_marker', args=[marker.pk]),
        'assign_url': reverse('assign_marker_workbook', args=[marker.pk]),
    }


def pdf_data(plan, *, detail=True):
    from .plan_workbook import ensure_workbook
    ensure_workbook(plan)
    result = {
        'id': plan.pk, 'name': plan.name, 'size': plan.size, 'date': plan.imported_at.isoformat(),
        'pages': plan.pages, 'page_count': len(plan.pages),
        'detail_url': reverse('detail_pdf', args=[plan.pk]),
        'source_url': reverse('source_pdf', args=[plan.pk]),
        'markers_url': reverse('create_marker', args=[plan.pk]),
        'update_url': reverse('update_pdf_plan', args=[plan.pk]),
        'delete_url': reverse('delete_pdf_plan', args=[plan.pk]),
    }
    if detail:
        result['markers'] = [marker_data(marker) for marker in plan.markers.select_related('workbook').defer('workbook__content').all()]
    from .project_views import project_plan_data
    result.update(project_plan_data('pdf', plan))
    return result


@require_GET
@api_permissions()
def list_pdf_plans(request):
    return JsonResponse({'plans': [pdf_data(plan, detail=False) for plan in PdfPlan.objects.order_by('-imported_at')]})


@require_POST
@api_permissions()
def import_pdf(request):
    upload = request.FILES.get('file')
    if not upload:
        return error_response('Seleccione un archivo .pdf.')
    try:
        if Path(upload.name).suffix.lower() != '.pdf':
            raise ValidationError('Solo se admiten archivos .pdf.')
        if not upload.size or upload.size > settings.PLAN_UPLOAD_MAX_BYTES:
            raise ValidationError('El PDF debe tener contenido y pesar como máximo 20 MB.')
        content = upload.read()
        if not content.startswith(b'%PDF-'):
            raise ValidationError('El archivo no es un PDF válido.')
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise ValidationError('Seleccione un PDF sin contraseña.')
        if not 1 <= len(reader.pages) <= 1000:
            raise ValidationError('El PDF debe contener entre 1 y 1.000 páginas.')
        pages = []
        for page in reader.pages:
            width, height = float(page.cropbox.width), float(page.cropbox.height)
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise ValidationError('El PDF contiene una página con dimensiones no válidas.')
            if int(page.rotation or 0) % 180:
                width, height = height, width
            pages.append({'width': width, 'height': height})
    except ValidationError as exc:
        return error_response(exc)
    except Exception:
        return error_response('El archivo no es un PDF válido o está dañado.')
    finally:
        upload.seek(0)
    plan = PdfPlan(name=upload.name, size=upload.size, imported_by=request.user, pages=pages)
    try:
        with transaction.atomic():
            plan.source.save(upload.name, upload, save=False)
            plan.save()
    except Exception:
        if plan.source:
            plan.source.delete(save=False)
        raise
    return JsonResponse(pdf_data(plan), status=201)


@require_GET
@api_permissions()
def detail_pdf(request, plan_id):
    return JsonResponse(pdf_data(get_object_or_404(PdfPlan, pk=plan_id)))


@require_GET
@api_permissions()
def source_pdf(request, plan_id):
    plan = get_object_or_404(PdfPlan, pk=plan_id)
    response = FileResponse(plan.source.open('rb'), content_type='application/pdf', filename=plan.name)
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def validated_marker(plan, data, *, partial=False):
    values = {}
    for key in ('page', 'x', 'y'):
        if key not in data and partial:
            continue
        value = data.get(key)
        if key == 'page':
            values[key] = positive_integer(value, 'Página', len(plan.pages))
        elif type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValidationError('Las coordenadas del botón deben estar entre 0 y 1.')
        else:
            values[key] = value
    if 'label' in data:
        if not isinstance(data['label'], str) or len(data['label']) > 120:
            raise ValidationError('El nombre del botón debe tener como máximo 120 caracteres.')
        values['label'] = data['label'].strip()
    return values


@require_POST
@api_permissions()
def create_marker(request, plan_id):
    plan = get_object_or_404(PdfPlan, pk=plan_id)
    try:
        data = validated_marker(plan, json_body(request))
    except ValidationError as exc:
        return error_response(exc)
    with transaction.atomic():
        marker = PdfMarker.objects.create(plan=plan, **data)
        from .plan_workbook import initialize_marker
        initialize_marker(marker, request.user)
    return JsonResponse({'marker': marker_data(marker)}, status=201)


@require_POST
@api_permissions()
def update_marker(request, marker_id):
    # Assigning a label or a workbook is open to registered users; moving an
    # existing button changes the saved drawing and requires modification rights.
    try:
        data = json_body(request)
    except ValidationError as exc:
        return error_response(exc)
    if any(key in data for key in ('page', 'x', 'y')) and not capabilities(request.user)['can_change_pdf_plan']:
        return error_response('No tiene permiso para modificar este plano.', status=403)
    marker = get_object_or_404(PdfMarker.objects.select_related('plan', 'workbook'), pk=marker_id)
    try:
        values = validated_marker(marker.plan, data, partial=True)
        if 'workbook_id' in data:
            from .plan_workbook import assign_marker
            assign_marker(marker, request.user, {'blank': True} if data['workbook_id'] is None else {'workbook_id': data['workbook_id']})
    except ValidationError as exc:
        return error_response(exc)
    for key, value in values.items():
        setattr(marker, key, value)
    if values:
        marker.save(update_fields=list(values))
    return JsonResponse({'marker': marker_data(marker)})


@require_POST
@api_permissions()
def delete_marker(request, marker_id):
    if not capabilities(request.user)['can_delete_pdf_marker']:
        return error_response('No tiene permiso para eliminar botones de este plano.', status=403)
    marker = get_object_or_404(PdfMarker, pk=marker_id)
    marker.delete()
    return JsonResponse({'deleted': True})


@require_GET
@api_permissions()
def list_workbooks(request):
    return JsonResponse({
        'workbooks': [workbook_data(document, sheets=False) for document in WorkbookDocument.objects.filter(is_motor=False, plan_mode='').defer('content').order_by('-updated_at')],
        'motors': [
            {'source_motor_id': motor.pk, 'name': motor.excel_name, 'sheets': motor.excel_sheets}
            for motor in Motor.objects.exclude(excel='').filter(workbook__isnull=True).order_by('id')
        ],
    })


def uploaded_document(upload, user):
    validate_excel(upload)
    content = upload.read()
    return WorkbookDocument.objects.create(name=upload.name, content=content, updated_by=user)


@require_POST
@api_permissions()
def upload_workbook(request):
    upload = request.FILES.get('file')
    if not upload:
        return error_response('Seleccione un archivo .xlsx.')
    try:
        document = uploaded_document(upload, request.user)
    except ValidationError as exc:
        return error_response(exc)
    return JsonResponse({'workbook': workbook_data(document)}, status=201)


@require_POST
@api_permissions()
def assign_marker_workbook(request, marker_id):
    from .plan_workbook import assign_marker, WorkbookConflict, workspace_data
    marker = get_object_or_404(PdfMarker.objects.select_related('plan'), pk=marker_id)
    try:
        upload = request.FILES.get('file')
        data = json_body(request) if upload is None else {}
        assign_marker(marker, request.user, data, upload)
        return JsonResponse({'marker': marker_data(marker), **workspace_data(marker.plan, request.user)})
    except ValidationError as exc:
        return error_response(exc, status=409 if isinstance(exc, WorkbookConflict) else 400)


@require_GET
@api_permissions()
def workbook_metadata(request, workbook_id):
    document = get_object_or_404(WorkbookDocument, pk=workbook_id)
    try:
        return JsonResponse(workbook_data(document, user=request.user, sheet_name=request.GET.get('sheet'), context=request.GET.get('context')))
    except ValidationError as exc:
        return error_response(exc)


@require_GET
@api_permissions()
def workbook_cells(request, workbook_id):
    document = get_object_or_404(WorkbookDocument, pk=workbook_id)
    try:
        numbers = {}
        for key, default in [('row', 1), ('col', 1), ('rows', 50), ('cols', 20)]:
            value = request.GET.get(key, default)
            try:
                numbers[key] = int(value)
            except (ValueError, TypeError):
                raise ValidationError('El rango de celdas no es válido.')
        result = read_cells(document.content, request.GET.get('sheet', ''), **numbers)
    except ValidationError as exc:
        return error_response(exc)
    result['revision'] = document.revision
    return JsonResponse(result)


@require_POST
@api_permissions()
def workbook_save(request, workbook_id):
    if not capabilities(request.user)['can_change_excel']:
        return error_response('No tiene permiso para modificar el contenido del Excel.', status=403)
    try:
        data = json_body(request)
        revision = positive_integer(data.get('revision'), 'Versión del Excel')
        # Read/transform outside the transaction; the conditional UPDATE prevents
        # stale writes even on SQLite, whose SELECT FOR UPDATE is a no-op.
        document = get_object_or_404(WorkbookDocument, pk=workbook_id)
        if SavedMotor.objects.filter(workbook_id=document.pk).exists():
            return error_response('Los motores guardados son registros originales. Asigne una copia a un botón para editar sus datos.', status=403)
        if document.revision != revision:
            return error_response('Otro usuario modificó este Excel. Recargue el libro antes de guardar.', status=409)
        content, sheets = edited_document(document.content, data.get('edits', []), data.get('operations', []))
        from .plan_workbook import MAX_WORKBOOK_BYTES
        limit = MAX_WORKBOOK_BYTES if document.plan_mode else settings.PLAN_UPLOAD_MAX_BYTES
        if len(content) > limit:
            return error_response('El Excel modificado supera el tamaño máximo permitido.')
        with transaction.atomic():
            changed = WorkbookDocument.objects.filter(pk=workbook_id, revision=revision).update(
                content=content, revision=revision + 1, updated_by=request.user, updated_at=timezone.now(),
            )
            if not changed:
                return error_response('Otro usuario modificó este Excel. Recargue el libro antes de guardar.', status=409)
        document.refresh_from_db()
        result = workbook_data(document, user=request.user, sheet_name=data.get('sheet'), context=data.get('context'))
        return JsonResponse(result)
    except ValidationError as exc:
        return error_response(exc)


@require_GET
@api_permissions()
def workbook_download(request, workbook_id):
    document = get_object_or_404(WorkbookDocument, pk=workbook_id)
    return FileResponse(
        BytesIO(bytes(document.content)), as_attachment=True, filename=document.name,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@require_POST
@api_permissions()
def update_pdf_plan(request, plan_id):
    if not capabilities(request.user)['can_change_pdf_plan']:
        return error_response('No tiene permiso para modificar este plano.', status=403)
    plan = get_object_or_404(PdfPlan, pk=plan_id)
    try:
        name = json_body(request).get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise ValidationError('Ingrese un nombre de hasta 255 caracteres.')
    except ValidationError as exc:
        return error_response(exc)
    plan.name = name.strip()
    plan.save(update_fields=['name'])
    return JsonResponse(pdf_data(plan))


@require_POST
@api_permissions()
def delete_pdf_plan(request, plan_id):
    if not capabilities(request.user)['can_delete_pdf_plan']:
        return error_response('No tiene permiso para eliminar este plano.', status=403)
    plan = get_object_or_404(PdfPlan, pk=plan_id)
    storage, name = plan.source.storage, plan.source.name
    with transaction.atomic():
        plan.delete()
        transaction.on_commit(lambda: storage.delete(name))
    return JsonResponse({'deleted': True})
