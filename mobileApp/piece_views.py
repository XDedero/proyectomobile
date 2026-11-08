"""Named pieces are selected-sheet snapshots shared through piezas.xlsx."""

from io import BytesIO
import re
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .access import api_permissions, can_edit_workbook, capabilities
from .models import SavedPiece, PieceDeletion, WorkbookDocument
from .motor_service import inspect_motor, known_motor, motor_name, read_origin, stamp_origin
from .pdf_views import error_response, json_body, workbook_data
from .workbook_service import positive_integer


class RegistrationConflict(Exception):
    pass


@require_GET
@api_permissions()
def piece_categories(request):
    from .models import ButtonCategory
    return JsonResponse({'categories':list(ButtonCategory.objects.values('id','name'))})


def saved_piece_data(record):
    return {
        'category_id': str(record.category_id) if record.category_id else None,
        'category_name': record.category.name if record.category_id else '',
        'cloud_status': record.cloud_status, 'cloud_sheet': record.cloud_sheet,
        'id': record.pk, 'uuid': str(record.uuid), 'name': record.name,
        'date': record.created_at.isoformat(), 'workbook': workbook_data(record.workbook, sheets=False),
        'edit_url':reverse('edit_piece', args=[record.pk]), 'update_url':reverse('update_piece', args=[record.pk]),
        'delete_url':reverse('delete_piece', args=[record.pk]),
    }


@require_GET
@api_permissions()
def saved_pieces(request):
    from .terminal_monitor import is_monitor_enabled
    cloud_error = ''
    if is_monitor_enabled():
        from .piece_service import refresh_catalog, queue_catalog_sync
        try:
            refresh_catalog()
            if SavedPiece.objects.exclude(cloud_status='synced').exists() or PieceDeletion.objects.filter(synced=False).exists():
                queue_catalog_sync()
        except Exception:
            cloud_error = 'No se pudo actualizar el catálogo de Drive. Se muestran las piezas de este PC.'
    query = ' '.join(request.GET.get('q', '').split()).casefold()[:120]
    records = SavedPiece.objects.select_related('workbook', 'category').defer('workbook__content')
    if query:
        records = records.filter(name_key__contains=query)
    try:
        offset = int(request.GET.get('offset', 0))
        if offset < 0:
            raise ValueError
    except (ValueError, TypeError):
        return error_response('El rango de piezas no es válido.')
    total = records.count()
    from .models import ButtonCategory
    return JsonResponse({'motors' if request.path.startswith('/api/motor-library/') else 'pieces': [saved_piece_data(record) for record in records[offset:offset+100]], 'total': total, 'cloud_error':cloud_error,
        'pending_deletions':PieceDeletion.objects.filter(synced=False).count(),
        'can_manage':capabilities(request.user)['can_change_excel'],
        'categories':list(ButtonCategory.objects.values('id','name')), 'next_offset': offset+100 if offset+100 < total else None})


@require_POST
@api_permissions()
def register_piece(request):
    legacy = request.path.startswith('/api/motor-library/')
    from .views import validate_excel
    from .plan_workbook import selected_motor_document, known_sheet, sheet_identity
    from .xlsx_package import sheet_names
    shared = None
    selected_name = None
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
            document = get_object_or_404(WorkbookDocument, pk=positive_integer(data.get('workbook_id'), 'Ficha de la pieza'))
            if not can_edit_workbook(request.user, document):
                return error_response('Solo el creador del plano o un administrador puede registrar cambios en sus fichas.', 403)
            revision = positive_integer(data.get('revision'), 'Versión de la ficha')
            if revision != document.revision:
                raise RegistrationConflict('La ficha cambió. Recárguela antes de registrar la pieza.')
            if document.plan_mode in ('template', 'imported'):
                shared, selected_name = document, data.get('sheet')
                document = selected_motor_document(shared, selected_name)
        from .piece_service import piece_category, queue_catalog_sync
        from .motor_service import single_sheet
        from .models import ButtonCategory
        category = (ButtonCategory.objects.filter(name__iexact='Motores').first() if legacy and not data.get('category_id') else piece_category(data.get('category_id')))
        names = sheet_names(document.content)
        selected = data.get('sheet') or (names[0] if len(names) == 1 else None)
        if selected not in names:
            raise ValidationError('Seleccione la hoja que desea guardar como pieza.')
        if len(names) > 1 or document.plan_mode == 'source':
            shared, selected_name = document, selected
            from .xlsx_package import read_sheet_origins
            document = WorkbookDocument(name=document.name, content=single_sheet(document.content, selected), is_motor=True,
                origin_uuid=read_sheet_origins(document.content).get(selected))
        fingerprint, filled = inspect_motor(document.content)
        first_sheet = sheet_names(document.content)[0]
        existing = (known_motor(document, fingerprint=fingerprint) or known_sheet(document.content, first_sheet)) if legacy else (SavedPiece.objects.filter(uuid=document.origin_uuid).first() if document.origin_uuid else None)
        if existing:
            return JsonResponse({'error': f'Esta ficha ya pertenece a la pieza guardada «{existing.name}».', 'existing': saved_piece_data(existing)}, status=409)
        name, key = motor_name(data.get('name'))
        if legacy and not filled:
            raise ValidationError('Rellene los datos de la ficha antes de guardar la pieza.')
        key = key if legacy else f'{category.pk}:{key}'
        if SavedPiece.objects.filter(name_key=key).exists():
            return error_response('Ya existe una pieza con ese nombre en esta categoría. Elija otro nombre.', status=409)
        identifier = document.origin_uuid or uuid4()
        if PieceDeletion.objects.filter(pk=identifier).exists():
            identifier = uuid4()
        content = stamp_origin(document.content, identifier)
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', name) + '.xlsx'
        with transaction.atomic():
            snapshot = WorkbookDocument.objects.create(name=filename, content=content, is_motor=True, origin_uuid=identifier, updated_by=request.user)
            import hashlib
            piece_fingerprint = fingerprint if legacy else hashlib.sha256((key+fingerprint).encode()).hexdigest()
            record = SavedPiece.objects.create(uuid=identifier, name=name, name_key=key, fingerprint=piece_fingerprint,
                data_fingerprint=sheet_identity(document.content, first_sheet)[0] if legacy else '', workbook=snapshot, category=category, created_by=request.user)
            queue_catalog_sync()
            if shared:
                from .xlsx_package import read_sheet_origins, write_sheet_origins
                origins = read_sheet_origins(shared.content)
                origins[selected_name] = identifier
                combined = write_sheet_origins(shared.content, origins)
                changed = WorkbookDocument.objects.filter(pk=shared.pk, revision=revision).update(content=combined, revision=revision+1, updated_by=request.user, updated_at=timezone.now())
                if not changed:
                    raise RegistrationConflict('El libro cambió. Recárguelo antes de registrar la pieza.')
                shared.plan_sheets.filter(name=selected_name).update(origin_uuid=identifier)
            elif document.pk:
                changed = WorkbookDocument.objects.filter(pk=document.pk, revision=revision).update(content=content, origin_uuid=identifier, revision=revision+1, updated_by=request.user, updated_at=timezone.now())
                if not changed:
                    raise RegistrationConflict('La ficha cambió. Recárguela antes de registrar la pieza.')
        if shared:
            shared.refresh_from_db()
            result = workbook_data(shared, user=request.user, sheet_name=selected_name if shared.plan_mode in ('template','imported') else None, context='motor')
        elif document.pk:
            document.refresh_from_db()
            result = workbook_data(document, user=request.user)
        else:
            result = None
        return JsonResponse({'motor' if legacy else 'piece': saved_piece_data(record), 'workbook': result}, status=201)
    except RegistrationConflict as exc:
        return error_response(exc, status=409)
    except IntegrityError:
        return error_response('Esa pieza o esa ficha ya se guardó. Actualice la sección Piezas guardadas.', status=409)
    except ValidationError as exc:
        return error_response(exc)


@require_POST
@api_permissions()
def import_piece_document(request):
    from .pdf_views import uploaded_document
    from .motor_service import template_bytes
    from .piece_service import piece_category
    upload = request.FILES.get('file')
    try:
        data = request.POST if upload is not None else json_body(request)
        if request.path.startswith('/api/motor-library/') and not data.get('category_id'):
            from .models import ButtonCategory
            category = ButtonCategory.objects.filter(name__iexact='Motores').first()
        else:
            category = piece_category(data.get('category_id'))
        with transaction.atomic():
            if upload is not None:
                document = uploaded_document(upload, request.user)
                document.is_motor = True
                document.save(update_fields=['is_motor'])
            elif data.get('blank') is True:
                document = WorkbookDocument.objects.create(name='Nueva pieza.xlsx', content=template_bytes(), is_motor=True, updated_by=request.user)
            else:
                raise ValidationError('Seleccione un Excel o CSV, o un formato predeterminado.')
            result = workbook_data(document, user=request.user)
            result['piece_category_id'] = str(category.pk)
    except ValidationError as exc:
        return error_response(exc)
    return JsonResponse({'workbook': result}, status=201)


@require_POST
@api_permissions()
def edit_piece(request, piece_id):
    if not capabilities(request.user)['can_change_excel']:
        return error_response('No tiene permiso para modificar piezas.', 403)
    from .terminal_monitor import is_monitor_enabled
    if is_monitor_enabled():
        from .piece_service import refresh_catalog
        try:
            refresh_catalog()
        except Exception:
            return error_response('No se pudo actualizar el catálogo. Reintente antes de editar la pieza.', 503)
    record = get_object_or_404(SavedPiece.objects.select_related('workbook'), pk=piece_id)
    draft = WorkbookDocument.objects.create(name=record.workbook.name, content=record.workbook.content, is_motor=True, updated_by=request.user)
    result = workbook_data(draft, user=request.user)
    result.update(context='piece-edit', piece_category_id=str(record.category_id) if record.category_id else None,
        piece_name=record.name, piece_edit={'id':record.pk, 'revision':record.workbook.revision, 'update_url':reverse('update_piece',args=[record.pk])})
    return JsonResponse({'workbook':result})


@require_POST
@api_permissions()
def update_piece(request, piece_id):
    if not capabilities(request.user)['can_change_excel']:
        return error_response('No tiene permiso para modificar piezas.', 403)
    from .piece_service import piece_category, queue_catalog_sync
    from .motor_service import single_sheet
    from .xlsx_package import sheet_names
    import hashlib
    try:
        data = json_body(request)
        with transaction.atomic():
            record = get_object_or_404(SavedPiece.objects.select_for_update().select_related('workbook'), pk=piece_id)
            expected = positive_integer(data.get('piece_revision'), 'Versión de la pieza')
            if expected != record.workbook.revision:
                raise RegistrationConflict('La pieza cambió. Cierre esta ficha y vuelva a pulsar Modificar para cargar la versión actual.')
            draft = get_object_or_404(WorkbookDocument, pk=positive_integer(data.get('workbook_id'), 'Excel'))
            if not can_edit_workbook(request.user, draft):
                return error_response('No tiene permiso para usar este Excel.', 403)
            if positive_integer(data.get('revision'), 'Versión del Excel') != draft.revision:
                raise RegistrationConflict('El Excel cambió. Recárguelo antes de guardar la pieza.')
            selected = data.get('sheet')
            if selected not in sheet_names(draft.content):
                raise ValidationError('Seleccione la hoja que desea guardar como pieza.')
            content = stamp_origin(single_sheet(draft.content, selected), record.uuid)
            name, key = motor_name(data.get('name',record.name))
            category = piece_category(data.get('category_id') or record.category_id)
            key = f'{category.pk}:{key}'
            if SavedPiece.objects.exclude(pk=record.pk).filter(name_key=key).exists():
                raise RegistrationConflict('Ya existe otra pieza con ese nombre en esta categoría.')
            fingerprint, _ = inspect_motor(content)
            changed = WorkbookDocument.objects.filter(pk=record.workbook_id, revision=expected).update(content=content,
                revision=expected+1, name=name+'.xlsx', updated_by=request.user, updated_at=timezone.now())
            if not changed:
                raise RegistrationConflict('La pieza cambió. Vuelva a abrirla antes de guardar.')
            record.name, record.name_key, record.category = name, key, category
            record.fingerprint = hashlib.sha256((str(record.uuid)+fingerprint).encode()).hexdigest()
            record.data_fingerprint, record.cloud_status = '', 'local'
            record.save(update_fields=['name','name_key','category','fingerprint','data_fingerprint','cloud_status'])
            queue_catalog_sync()
        record.workbook.refresh_from_db()
        return JsonResponse({'piece':saved_piece_data(record)})
    except RegistrationConflict as exc:
        return error_response(exc,409)
    except IntegrityError:
        return error_response('Otra pieza ya utiliza ese nombre. Actualice el catálogo.',409)
    except ValidationError as exc:
        return error_response(exc)


@require_POST
@api_permissions()
def delete_piece(request, piece_id):
    if not capabilities(request.user)['can_change_excel']:
        return error_response('No tiene permiso para eliminar piezas.',403)
    from .piece_service import discard_piece, queue_catalog_sync
    try:
        data = json_body(request)
        with transaction.atomic():
            record = get_object_or_404(SavedPiece.objects.select_for_update().select_related('workbook'), pk=piece_id)
            if positive_integer(data.get('revision'), 'Versión de la pieza') != record.workbook.revision:
                raise RegistrationConflict('La pieza cambió. Actualice el catálogo antes de eliminarla.')
            PieceDeletion.objects.get_or_create(pk=record.uuid)
            discard_piece(record)
            queue_catalog_sync()
        return JsonResponse({'deleted':True,'cloud_status':'pending'})
    except RegistrationConflict as exc:
        return error_response(exc,409)
    except ValidationError as exc:
        return error_response(exc)
