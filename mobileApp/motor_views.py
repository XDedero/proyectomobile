"""Named saved motors are snapshots; every button receives an editable copy."""

from io import BytesIO
import re
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .access import api_permissions
from .models import SavedMotor, WorkbookDocument
from .motor_service import inspect_motor, known_motor, motor_name, read_origin, stamp_origin
from .pdf_views import error_response, json_body, workbook_data
from .workbook_service import positive_integer


class RegistrationConflict(Exception):
    pass


def saved_motor_data(record):
    return {
        'id': record.pk, 'uuid': str(record.uuid), 'name': record.name,
        'date': record.created_at.isoformat(), 'workbook': workbook_data(record.workbook, sheets=False),
    }


@require_GET
@api_permissions()
def saved_motors(request):
    query = ' '.join(request.GET.get('q', '').split()).casefold()[:120]
    records = SavedMotor.objects.select_related('workbook').defer('workbook__content')
    if query:
        records = records.filter(name_key__contains=query)
    try:
        offset = int(request.GET.get('offset', 0))
        if offset < 0:
            raise ValueError
    except (ValueError, TypeError):
        return error_response('El rango de motores no es válido.')
    total = records.count()
    return JsonResponse({'motors': [saved_motor_data(record) for record in records[offset:offset+100]], 'total': total, 'next_offset': offset+100 if offset+100 < total else None})


@require_POST
@api_permissions()
def register_motor(request):
    from .views import validate_excel
    try:
        upload = request.FILES.get('file')
        if upload is not None:
            validate_excel(upload)
            content = upload.read()
            document = WorkbookDocument(name=upload.name, content=content, is_motor=True, origin_uuid=read_origin(content))
            data = request.POST
            revision = None
        else:
            data = json_body(request)
            document = get_object_or_404(WorkbookDocument, pk=positive_integer(data.get('workbook_id'), 'Ficha del motor'))
            revision = positive_integer(data.get('revision'), 'Versión de la ficha')
            if revision != document.revision:
                raise RegistrationConflict('La ficha cambió. Recárguela antes de registrar el motor.')
        if not document.is_motor:
            raise ValidationError('Seleccione una ficha de motor; el listado pertenece al plano.')
        fingerprint, filled = inspect_motor(document.content)
        existing = known_motor(document, fingerprint=fingerprint)
        if existing:
            return JsonResponse({'error': f'Esta ficha ya pertenece al motor guardado «{existing.name}».', 'existing': saved_motor_data(existing)}, status=409)
        name, key = motor_name(data.get('name'))
        if not filled:
            raise ValidationError('Rellene los datos de la ficha antes de guardar el motor.')
        if SavedMotor.objects.filter(name_key=key).exists():
            return error_response('Ya existe un motor guardado con ese nombre. Elija otro nombre.', status=409)
        identifier = document.origin_uuid or uuid4()
        content = stamp_origin(document.content, identifier)
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', name) + '.xlsx'
        with transaction.atomic():
            snapshot = WorkbookDocument.objects.create(name=filename, content=content, is_motor=True, origin_uuid=identifier, updated_by=request.user)
            record = SavedMotor.objects.create(uuid=identifier, name=name, name_key=key, fingerprint=fingerprint, workbook=snapshot, created_by=request.user)
            if document.pk:
                changed = WorkbookDocument.objects.filter(pk=document.pk, revision=revision).update(content=content, origin_uuid=identifier, revision=revision+1, updated_by=request.user, updated_at=timezone.now())
                if not changed:
                    raise RegistrationConflict('La ficha cambió. Recárguela antes de registrar el motor.')
        if document.pk:
            document.refresh_from_db()
        return JsonResponse({'motor': saved_motor_data(record), 'workbook': workbook_data(document, user=request.user) if document.pk else None}, status=201)
    except RegistrationConflict as exc:
        return error_response(exc, status=409)
    except IntegrityError:
        return error_response('Ese motor o esa ficha ya se guardó. Actualice la sección Motores guardados.', status=409)
    except ValidationError as exc:
        return error_response(exc)


@require_POST
@api_permissions()
def import_motor_document(request):
    from .pdf_views import uploaded_document
    from .motor_service import mark_motor
    upload = request.FILES.get('file')
    if upload is None:
        return error_response('Seleccione una ficha de motor .xlsx.')
    try:
        with transaction.atomic():
            document = mark_motor(uploaded_document(upload, request.user))
            result = workbook_data(document, user=request.user)
    except ValidationError as exc:
        return error_response(exc)
    return JsonResponse({'workbook': result}, status=201)
