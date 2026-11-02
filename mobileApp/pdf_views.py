"""Authenticated PDF plans, manually placed markers and editable XLSX library."""

from io import BytesIO
import json
import math
from pathlib import Path

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


def workbook_data(document, *, sheets=True, user=None):
    snapshot = bool(user is not None and SavedMotor.objects.filter(workbook_id=document.pk).exists())
    result = {
        'id': document.pk, 'name': document.name, 'revision': document.revision,
        'metadata_url': reverse('workbook_metadata', args=[document.pk]),
        'cells_url': reverse('workbook_cells', args=[document.pk]),
        'save_url': reverse('workbook_save', args=[document.pk]),
        'download_url': reverse('workbook_download', args=[document.pk]),
        'csv_url': reverse('workbook_csv', args=[document.pk]),
        'can_edit': bool(user is not None and not snapshot and capabilities(user)['can_change_excel']),
        'is_motor': document.is_motor,
    }
    if sheets:
        result['sheets'] = document_sheets(document.content)
    if user is not None:
        from .motor_service import registration_data
        result['motor_registration'] = registration_data(document, inspect=True)
    return result


def marker_data(marker):
    return {
        'id': marker.pk, 'page': marker.page, 'x': marker.x, 'y': marker.y, 'label': marker.label,
        'workbook': workbook_data(marker.workbook, sheets=False) if marker.workbook_id else None,
        'update_url': reverse('update_marker', args=[marker.pk]),
        'delete_url': reverse('delete_marker', args=[marker.pk]),
        'assign_url': reverse('assign_marker_workbook', args=[marker.pk]),
    }


def pdf_data(plan, *, detail=True):
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
    from .motor_service import blank_motor
    with transaction.atomic():
        marker = PdfMarker.objects.create(plan=plan, **data)
        marker.workbook = blank_motor(request.user, f'Motor {marker.pk}.xlsx')
        marker.save(update_fields=['workbook'])
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
            from .motor_service import copy_motor, blank_motor
            values['workbook'] = blank_motor(request.user, f'Motor {marker.pk}.xlsx') if data['workbook_id'] is None else copy_motor(get_object_or_404(WorkbookDocument, pk=positive_integer(data['workbook_id'], 'Excel')), request.user)
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
        'workbooks': [workbook_data(document, sheets=False) for document in WorkbookDocument.objects.filter(is_motor=False).defer('content').order_by('-updated_at')],
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
    marker = get_object_or_404(PdfMarker.objects.select_related('workbook'), pk=marker_id)
    try:
        from .motor_service import copy_motor, mark_motor
        upload = request.FILES.get('file')
        if upload is not None:
            document = uploaded_document(upload, request.user)
            mark_motor(document)
        else:
            data = json_body(request)
            if 'saved_motor_id' in data:
                saved = get_object_or_404(SavedMotor, pk=positive_integer(data['saved_motor_id'], 'Motor guardado'))
                document = copy_motor(saved.workbook, request.user)
                if not marker.label:
                    marker.label = saved.name
            elif 'workbook_id' in data:
                document = copy_motor(get_object_or_404(WorkbookDocument, pk=positive_integer(data['workbook_id'], 'Excel')), request.user)
            elif 'source_motor_id' in data:
                motor = get_object_or_404(Motor, pk=positive_integer(data['source_motor_id'], 'Motor'))
                if motor.workbook_id:
                    document = copy_motor(motor.workbook, request.user)
                elif not motor.excel:
                    return error_response('El motor seleccionado no tiene un Excel guardado.')
                else:
                    with motor.excel.open('rb') as source:
                        upload = SimpleUploadedFile(motor.excel_name or 'motor.xlsx', source.read())
                    document = uploaded_document(upload, request.user)
                    mark_motor(document)
            else:
                return error_response('Seleccione un Excel de la biblioteca o del dispositivo.')
    except ValidationError as exc:
        return error_response(exc)
    marker.workbook = document
    marker.save(update_fields=['workbook', 'label'])
    return JsonResponse({'marker': marker_data(marker)})


@require_GET
@api_permissions()
def workbook_metadata(request, workbook_id):
    document = get_object_or_404(WorkbookDocument, pk=workbook_id)
    return JsonResponse(workbook_data(document, user=request.user))


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
        if len(content) > settings.PLAN_UPLOAD_MAX_BYTES:
            return error_response('El Excel modificado supera el tamaño máximo de 20 MB.')
        with transaction.atomic():
            changed = WorkbookDocument.objects.filter(pk=workbook_id, revision=revision).update(
                content=content, revision=revision + 1, updated_by=request.user, updated_at=timezone.now(),
            )
            if not changed:
                return error_response('Otro usuario modificó este Excel. Recargue el libro antes de guardar.', status=409)
        document.refresh_from_db()
        result = workbook_data(document, sheets=False, user=request.user)
        result['sheets'] = sheets
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
