"""One XLSX per drawing; markers reference sheets rather than copied files."""
from io import BytesIO
import hashlib
import json
import unicodedata
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.shortcuts import get_object_or_404
from openpyxl import Workbook, load_workbook

from .models import ButtonCategory, Motor, PdfMarker, PlanWorkbookSheet, SavedMotor, WorkbookDocument
from .motor_service import inspect_motor, single_sheet, template_bytes, read_origin
from .xlsx_package import (append_sheet, arrange_sheets, remove_sheet, rename_sheet, sheet_names,
                           read_sheet_origins, unique_name, write_sheet_origins)

MAX_WORKBOOK_BYTES = 100 * 1024 * 1024
EMPTY_SHEET = 'Sin fichas'


class WorkbookConflict(ValidationError):
    pass


def markers(plan):
    return plan.markers if plan._meta.model_name == 'pdfplan' else plan.motors


def empty_workbook():
    book = Workbook()
    book.active.title = EMPTY_SHEET
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def discard_empty_sheet(document, content):
    if sheet_names(document.content) != [EMPTY_SHEET]:
        return content
    row = document.plan_sheets.filter(name=EMPTY_SHEET, is_listing=False).first()
    plan = plan_for_workbook(document)
    if not row or (plan and markers(plan).filter(sheet_name=EMPTY_SHEET).exists()):
        return content
    workbook = load_workbook(BytesIO(document.content), read_only=True)
    try:
        if any(value is not None for values in workbook.active.values for value in values):
            return content
    finally:
        workbook.close()
    row.delete()
    return remove_sheet(content, EMPTY_SHEET)


def store(document, content, user):
    if len(content) > MAX_WORKBOOK_BYTES:
        raise ValidationError('El libro del plano supera el límite de 100 MB.')
    changed = WorkbookDocument.objects.filter(pk=document.pk, revision=document.revision).update(
        content=content, revision=document.revision + 1, updated_by=user, updated_at=timezone.now())
    if not changed:
        raise WorkbookConflict('Otro usuario modificó el libro. Actualice el plano e intente nuevamente.')
    document.refresh_from_db()
    try:
        from .terminal_monitor import on_workbook_saved
        on_workbook_saved(WorkbookDocument, document, created=False)
    except Exception:
        pass


def sheet_identity(content, name):
    """Compare actual motor values, independently of a sheet's display name."""
    selected = single_sheet(content, name)
    _, filled = inspect_motor(selected)
    workbook = load_workbook(BytesIO(selected), read_only=False, data_only=False)
    try:
        cells = [[row, col, cell.value] for (row, col), cell in sorted(workbook.active._cells.items()) if cell.value is not None]
        key = hashlib.sha256(json.dumps(cells, ensure_ascii=False, default=str, separators=(',', ':')).encode()).hexdigest()
        return key, filled
    finally:
        workbook.close()


def _append(document, content, source_name, user, *, title=None, origin=None, deduplicate=True):
    rows = list(document.plan_sheets.filter(is_listing=False))
    if origin:
        existing = next((row for row in rows if row.origin_uuid == origin), None)
        if existing:
            return existing
    fingerprint, filled = sheet_identity(content, source_name)
    if deduplicate and filled:
        for row in rows:
            identity, has_data = sheet_identity(document.content, row.name)
            if has_data and identity == fingerprint:
                if origin and not row.origin_uuid:
                    origins = read_sheet_origins(document.content)
                    origins[row.name] = origin
                    store(document, write_sheet_origins(document.content, origins), user)
                    row.origin_uuid = origin
                    row.save(update_fields=['origin_uuid'])
                return row
    result, name = append_sheet(document.content, content, source_name, title or source_name)
    result = discard_empty_sheet(document, result)
    if origin:
        origins = read_sheet_origins(result)
        origins[name] = origin
        result = write_sheet_origins(result, origins)
    store(document, result, user)
    return PlanWorkbookSheet.objects.create(workbook=document, name=name, origin_uuid=origin, created_by=user)


def preserve_variant(document, content, name, user, origin, title):
    """A former independent copy can contain edits absent in the shared one."""
    if not origin:
        return
    current = document.plan_sheets.filter(origin_uuid=origin, is_listing=False).first()
    if current and sheet_identity(document.content, current.name)[0] != sheet_identity(content, name)[0]:
        _append(document, content, name, user, title=f'{title} (anterior)', deduplicate=True)


@transaction.atomic
def ensure_workbook(plan, user=None):
    """Upgrade an older drawing lazily, retaining all original documents."""
    locked = type(plan).objects.select_for_update().get(pk=plan.pk)
    if locked.workbook_id:
        plan.workbook_id = locked.workbook_id
        return WorkbookDocument.objects.get(pk=locked.workbook_id)
    owner = user or plan.imported_by
    legacy_listing = locked.listing
    content = bytes(legacy_listing.content) if legacy_listing else empty_workbook()
    document = WorkbookDocument.objects.create(name='Excel del plano.xlsx', content=content, plan_mode='template', updated_by=owner)
    for index, name in enumerate(sheet_names(content)):
        is_listing = bool(legacy_listing and index == 0)
        PlanWorkbookSheet.objects.create(workbook=document, name=name, is_listing=is_listing,
            is_primary=is_listing, is_protected=is_listing, listing_order=int(is_listing), created_by=owner)
    copied = {}
    known_listings = {sheet_identity(document.content, row.name)[0]: row.name for row in document.plan_sheets.filter(is_listing=True)}
    for index, marker in enumerate(markers(locked).select_related('workbook')):
        original = marker.workbook
        if original:
            source = bytes(original.content)
            key = ('document', original.pk)
        elif isinstance(marker, Motor) and marker.excel:
            with marker.excel.open('rb') as stream:
                source = stream.read()
            key = ('legacy', marker.pk)
        else:
            source, key = template_bytes(), ('blank', marker.pk)
        if key not in copied:
            names = sheet_names(source)
            copied[key] = []
            for number, name in enumerate(names):
                if len(names) > 1:
                    existing_listing = known_listings.get(sheet_identity(source, name)[0])
                    if existing_listing:
                        copied[key].append(existing_listing)
                        continue
                origin = (original.origin_uuid if original else read_origin(source)) if len(names) == 1 else None
                title = f'Motor {index+1}' if len(names) == 1 else name
                preserve_variant(document, source, name, owner, origin, title)
                record = _append(document, source, name, owner,
                    title=title, origin=origin,
                    deduplicate=True)
                copied[key].append(record.name)
        marker.workbook = document
        names = sheet_names(source)
        listing_names = set(document.plan_sheets.filter(is_listing=True).values_list('name', flat=True))
        first = next((position for position, name in enumerate(names)
            if copied[key][position] not in listing_names and not name.casefold().startswith('listado')), None)
        if first is None:
            first = next((position for position, name in enumerate(copied[key]) if name not in listing_names), None)
        marker.sheet_name = copied[key][first] if first is not None else ''
        fields = ['workbook', 'sheet_name']
        if isinstance(marker, Motor):
            marker.excel_sheets = [marker.sheet_name] if marker.sheet_name else []
            fields.append('excel_sheets')
        marker.save(update_fields=fields)
    locked.workbook = locked.listing = document
    locked.save(update_fields=['workbook', 'listing'])
    plan.workbook = plan.listing = document
    return document


def plan_for_workbook(document):
    return document.cad_workspaces.first() or document.pdf_workspaces.first()


def can_delete_listing(record, user, plan=None):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    from .access import capabilities
    plan = plan or plan_for_workbook(record.workbook)
    if not plan:
        return False
    permission = 'can_delete_pdf_plan' if plan._meta.model_name == 'pdfplan' else 'can_delete_plan'
    return capabilities(user, plan)[permission]


def workspace_data(plan, user=None):
    from .pdf_views import workbook_data
    document = ensure_workbook(plan, user)
    by_name = {row.name: row for row in document.plan_sheets.all()}
    rows = [by_name[name] for name in sheet_names(document.content) if name in by_name]
    listing_rows = sorted((row for row in rows if row.is_listing), key=lambda row: (not row.is_primary, row.listing_order, row.pk))
    listings = [{
        'id': row.pk, 'name': row.name, 'sheet': row.name,
        'is_primary': row.is_primary, 'is_protected': row.is_protected, 'can_delete': can_delete_listing(row, user, plan),
    } for row in listing_rows]
    listing = workbook_data(document, sheets=False, sheet_name=listings[0]['name'], context='listing') if listings else None
    return {'workbook': workbook_data(document, sheets=False), 'workbook_mode': document.plan_mode,
        'sheet_options': [{'name': row.name, 'is_listing': row.is_listing} for row in rows
                          if not (row.name == EMPTY_SHEET and not row.is_listing and document.plan_mode == 'template')],
        'button_categories': [{'id': category.pk, 'name': category.name, 'templates': [
            {'id': item.pk, 'name': item.name, 'sheet': item.sheet} for item in category.templates.all()]}
            for category in ButtonCategory.objects.prefetch_related('templates')],
        'listings': listings, 'listing': listing,
        'binding_counts': {item['sheet_name']: item['count'] for item in markers(plan).values('sheet_name').annotate(count=Count('pk'))}}


def bind(marker, document, name):
    marker.workbook = document
    marker.sheet_name = name
    fields = ['workbook', 'sheet_name']
    if isinstance(marker, Motor):
        marker.excel_sheets = [name] if name else []
        fields.append('excel_sheets')
    marker.save(update_fields=fields)


def marker_assignment(request, marker):
    from .button_import import inspect_button_file
    from .pdf_views import json_body

    upload = request.FILES.get('file')
    if upload is not None:
        data = request.POST
        if data.get('action') == 'inspect':
            return inspect_button_file(upload, data.get('source_sheet'), preview=True)
    else:
        data = json_body(request)
    assign_marker(marker, request.user, data, upload)


def button_category(value):
    try:
        return ButtonCategory.objects.get(pk=UUID(str(value)))
    except (ValueError, ButtonCategory.DoesNotExist) as exc:
        raise ValidationError('Seleccione una categoría disponible.') from exc


def new_button_options(request):
    from .pdf_views import json_body

    upload = request.FILES.get('file')
    if upload is None:
        data = json_body(request)
    else:
        try:
            data = json.loads(request.POST.get('placement', ''))
        except ValueError as exc:
            raise ValidationError('La ubicación del botón no es válida.') from exc
        if not isinstance(data, dict):
            raise ValidationError('La ubicación del botón no es válida.')
    origin = data.get('origin')
    if 'show_description' in data and type(data['show_description']) is not bool:
        raise ValidationError('Indica si el botón mostrará su descripción.')
    if origin is None and upload is None and 'category_id' not in data:
        return data, None, None, None
    if origin not in ('blank', 'upload', 'category') or (origin == 'upload') != (upload is not None):
        raise ValidationError('Elija una ficha predeterminada o un archivo para importar.')
    category = button_category(data.get('category_id'))
    if origin == 'category':
        from .models import CategoryWorkbook
        from django.core.files.uploadedfile import SimpleUploadedFile
        template = CategoryWorkbook.objects.filter(pk=data.get('template_id'), category=category).first()
        if template is None:
            raise ValidationError('Elija un Excel permanente de esta categoría.')
        upload = SimpleUploadedFile(template.name, bytes(template.content))
        origin = ('category', template.sheet)
    return data, category, origin, upload


def initialize_button(marker, user, origin, upload):
    if isinstance(origin, tuple) and origin[0] == 'category':
        stage_button_source(marker, user, upload, selected=origin[1])
    elif origin == 'upload':
        stage_button_source(marker, user, upload)
    elif origin == 'blank':
        assign_marker(marker, user, {'blank': True})
    else:
        initialize_marker(marker, user)


def stage_button_source(marker, user, upload, selected=None):
    from pathlib import Path
    from .button_import import inspect_button_file

    content, sheet = inspect_button_file(upload, selected, allow_default=True)
    name = upload.name if upload.name.lower().endswith('.xlsx') else Path(upload.name).stem + '.xlsx'
    previous = marker.source_workbook_id
    marker.source_workbook = WorkbookDocument.objects.create(
        name=name[:255], content=content, plan_mode='source', updated_by=user,
    )
    marker.source_sheet = sheet
    marker.source_revision = None
    marker.save(update_fields=['source_workbook', 'source_sheet', 'source_revision'])
    if previous:
        WorkbookDocument.objects.filter(pk=previous, plan_mode='source').delete()


def source_workbook_data(marker, assign_url):
    from .pdf_views import workbook_data

    return {
        **workbook_data(marker.source_workbook, sheets=False, context='button-source'),
        'preferred_sheet': marker.source_sheet,
        'select_source_url': assign_url,
    }


def materialize_button_sources(plan, user):
    """Update only the selected sheet of each source in the shared workbook."""
    document = ensure_workbook(plan, user)
    sources = markers(plan).select_for_update().filter(source_workbook__isnull=False).order_by('pk')
    changed = False
    for marker in sources.iterator(chunk_size=32):
        source = marker.source_workbook
        if marker.source_revision == source.revision and marker.sheet_name == marker.source_output:
            continue
        name = marker.source_sheet
        content = bytes(document.content)
        old = marker.source_output
        previous = None
        if old and old == marker.sheet_name and not markers(plan).exclude(pk=marker.pk).filter(sheet_name=old).exists():
            previous = document.plan_sheets.filter(name=old, is_listing=False).first()
        remove_first = previous and document.plan_sheets.count() > 1
        if remove_first:
            content = remove_sheet(content, old)
        content, title = append_sheet(content, source.content, name, old if remove_first else name)
        content = discard_empty_sheet(document, content)
        if previous and not remove_first:
            content = remove_sheet(content, old)
        store(document, content, user)
        if previous:
            previous.delete()
        PlanWorkbookSheet.objects.create(workbook=document, name=title, created_by=user)
        marker.source_output = title
        marker.source_revision = source.revision
        marker.save(update_fields=['source_output', 'source_revision'])
        bind(marker, document, title)
        changed = True
    return changed


def organize_project_sheets(plan, user):
    """Use the category and marker order as the authoritative exported tab names."""
    document = ensure_workbook(plan, user)
    rows = {row.name: row for row in document.plan_sheets.all()}
    buttons = list(markers(plan).select_for_update().select_related('category').order_by('pk'))
    groups = {}
    for button in buttons:
        row = rows.get(button.sheet_name)
        if row and not row.is_listing and button.category_id:
            groups.setdefault(row.name, {}).setdefault(button.category.name, []).append(button)

    content = bytes(document.content)
    targets = {}
    categories = {}
    for name, by_category in groups.items():
        for index, (category, assigned) in enumerate(by_category.items()):
            target = name
            if index:
                # Existing links within a category still share their sheet.
                # A sheet used by another category needs its own named tab.
                content, target = append_sheet(content, content, name, category)
                if len(content) > MAX_WORKBOOK_BYTES:
                    raise ValidationError('El libro del plano supera el límite de 100 MB.')
                rows[target] = PlanWorkbookSheet.objects.create(workbook=document, name=target, created_by=user)
            categories.setdefault(category, []).append((assigned[0].pk, target))
            targets.update((button.pk, target) for button in assigned)

    def alphabetic(value):
        return ''.join(char for char in unicodedata.normalize('NFKD', value.casefold()) if not unicodedata.combining(char))

    categorized = {name for entries in categories.values() for _, name in entries}
    occupied = {name.casefold() for name in rows if name not in categorized}
    ordered = [row.name for row in sorted(rows.values(), key=lambda row: (not row.is_primary, row.listing_order, row.pk)) if row.is_listing]
    renamed = {}
    for category in sorted(categories, key=lambda value: (alphabetic(value), value.casefold())):
        entries = sorted(categories[category])
        base = unique_name([], category)
        next_number = 1
        for _, name in entries:
            title = base
            if len(entries) > 1 or title.casefold() in occupied:
                while True:
                    suffix = f' {next_number}'
                    title = base[:31 - len(suffix)].rstrip() + suffix
                    next_number += 1
                    if title.casefold() not in occupied:
                        break
            occupied.add(title.casefold())
            ordered.append(name)
            if title != name:
                renamed[name] = title
    names = sheet_names(content)
    ordered.extend(name for name in names if name not in rows or (name not in categorized and not rows[name].is_listing))
    if not renamed and ordered == names and content == bytes(document.content):
        return False
    result = arrange_sheets(content, renamed, ordered)
    store(document, result, user)
    changing = [rows[name] for name in renamed]
    for row in changing:
        PlanWorkbookSheet.objects.filter(pk=row.pk).update(name=uuid4().hex[:31])
        row.name = renamed[row.name]
    PlanWorkbookSheet.objects.bulk_update(changing, ['name'])
    fields = ['sheet_name', 'source_output']
    if plan._meta.model_name == 'plan':
        fields.append('excel_sheets')
    for button in buttons:
        original = button.sheet_name
        target = targets.get(button.pk, original)
        button.sheet_name = renamed.get(target, target)
        if button.source_output == original:
            button.source_output = button.sheet_name
        if isinstance(button, Motor) and button.sheet_name:
            button.excel_sheets = [button.sheet_name]
    markers(plan).bulk_update(buttons, fields)
    return True


@transaction.atomic
def assign_marker(marker, user, data=None, upload=None):
    data = data or {}
    if data.get('action') == 'set_category' and upload is None:
        value = data.get('category_id')
        marker.category = button_category(value) if value else None
        marker.save(update_fields=['category'])
        return None
    if data.get('action') == 'stage' and upload is not None:
        marker.refresh_from_db(from_queryset=type(marker).objects.select_for_update())
        stage_button_source(marker, user, upload, data.get('source_sheet'))
        return None
    if data.get('action') == 'select_source' and upload is None:
        marker.refresh_from_db(from_queryset=type(marker).objects.select_for_update())
        name = data.get('source_sheet')
        if not marker.source_workbook_id or data.get('source_workbook_id') != marker.source_workbook_id:
            raise WorkbookConflict('El archivo asociado cambió. Vuelva a abrir la ficha.')
        if name not in sheet_names(marker.source_workbook.content):
            raise ValidationError('Elija una hoja del archivo asociado al botón.')
        if name != marker.source_sheet:
            marker.source_sheet = name
            marker.source_revision = None
            marker.save(update_fields=['source_sheet', 'source_revision'])
        return None
    document = ensure_workbook(marker.plan, user)
    if upload:
        from .button_import import inspect_button_file

        selected = data.get('source_sheet')
        source, name = inspect_button_file(upload, selected)
        record = _append(document, source, name, user, origin=None if selected else read_origin(source),
                         deduplicate=not selected)
    elif data.get('sheet'):
        record = document.plan_sheets.filter(name=data['sheet'], is_listing=False).first()
        if not record:
            raise ValidationError('Seleccione una hoja de motor del Excel de este plano.')
    elif data.get('saved_motor_id'):
        from .workbook_service import positive_integer
        record_motor = get_object_or_404(SavedMotor, pk=positive_integer(data['saved_motor_id'], 'Motor guardado'))
        names = sheet_names(record_motor.workbook.content)
        source_name = next((name for name in names if not name.casefold().startswith('listado')), names[0])
        record = _append(document, record_motor.workbook.content, source_name, user,
            title=record_motor.name, origin=record_motor.uuid)
    elif data.get('workbook_id'):
        from .workbook_service import positive_integer
        source = get_object_or_404(WorkbookDocument, pk=positive_integer(data['workbook_id'], 'Excel'))
        names = sheet_names(source.content)
        candidate = source.plan_sheets.filter(is_listing=False).first() if source.plan_mode else None
        name = data.get('source_sheet') or (candidate.name if candidate else names[0])
        if source.pk == document.pk:
            record = document.plan_sheets.filter(name=name, is_listing=False).first()
            if not record:
                raise ValidationError('Elija una hoja de motor.')
        else:
            record = _append(document, source.content, name, user, origin=source.origin_uuid)
    elif data.get('source_motor_id'):
        from .workbook_service import positive_integer
        motor = get_object_or_404(Motor.objects.select_related('workbook'), pk=positive_integer(data['source_motor_id'], 'Motor'))
        if motor.workbook_id:
            source, names = motor.workbook.content, sheet_names(motor.workbook.content)
            name = motor.sheet_name or names[0]
            origin = motor.workbook.plan_sheets.filter(name=name).values_list('origin_uuid', flat=True).first() if motor.workbook.plan_mode else motor.workbook.origin_uuid
        elif motor.excel:
            with motor.excel.open('rb') as stream:
                source = stream.read()
            name, origin = sheet_names(source)[0], read_origin(source)
        else:
            raise ValidationError('El motor seleccionado no tiene una ficha.')
        record = _append(document, source, name, user, origin=origin)
    elif data.get('blank'):
        source = template_bytes()
        title = marker.category.name if marker.category_id else f'Motor {document.plan_sheets.exclude(name=EMPTY_SHEET).filter(is_listing=False).count()+1}'
        record = _append(document, source, sheet_names(source)[0], user,
            title=title, deduplicate=False)
    else:
        raise ValidationError('Elija una hoja, una ficha vacía o un motor guardado.')
    bind(marker, document, record.name)
    if marker.source_workbook_id:
        source_id = marker.source_workbook_id
        marker.source_workbook = None
        marker.source_sheet = marker.source_output = ''
        marker.source_revision = None
        marker.save(update_fields=['source_workbook', 'source_sheet', 'source_output', 'source_revision'])
        WorkbookDocument.objects.filter(pk=source_id, plan_mode='source').delete()
    return record


@transaction.atomic
def initialize_marker(marker, user):
    document = ensure_workbook(marker.plan, user)
    if document.plan_mode == 'template':
        # ensure_workbook may have already upgraded a newly created marker.
        marker.refresh_from_db()
        if not marker.sheet_name:
            assign_marker(marker, user, {'blank': True})
    else:
        bind(marker, document, '')


@transaction.atomic
def import_workbook(plan, filename, content, user, *, first_as_listing=False):
    document = ensure_workbook(plan, user)
    # Build first; conditional store prevents replacing a concurrent edit.
    previous = bytes(document.content)
    old_records = {row.name: row for row in document.plan_sheets.all()}
    old_mode = document.plan_mode
    old_markers = list(markers(plan).all())
    names = sheet_names(content)
    origins = read_sheet_origins(content)
    if not names:
        raise ValidationError('El Excel debe contener hojas.')
    preserved = {}
    for record in old_records.values():
        if not record.is_listing:
            _, filled = sheet_identity(previous, record.name)
            preserved[record.name] = filled
    store(document, content, user)
    document.name, document.plan_mode = filename[:255], 'imported'
    document.save(update_fields=['name', 'plan_mode'])
    document.plan_sheets.all().delete()
    for index, name in enumerate(names):
        is_listing = first_as_listing and index == 0
        PlanWorkbookSheet.objects.create(workbook=document, name=name, is_listing=is_listing,
            is_primary=is_listing, is_protected=is_listing, listing_order=int(is_listing),
            origin_uuid=None if is_listing else origins.get(name), created_by=user)
    remap = {}
    for old_name, filled in preserved.items():
        if filled:
            origin = old_records[old_name].origin_uuid if old_name in old_records else None
            preserve_variant(document, previous, old_name, user, origin, old_name)
            remap[old_name] = _append(document, previous, old_name, user, title=old_name, origin=origin).name
    # Preserve user-created and filled previous listings as additional sheets.
    from .project_views import listing_template
    baseline = listing_template(markers(plan).count())
    baseline_key, _ = sheet_identity(baseline, sheet_names(baseline)[0])
    new_primary_key = sheet_identity(content, names[0])[0] if first_as_listing else None
    number = 2 if first_as_listing else 1
    for row in old_records.values():
        if not row.is_listing:
            continue
        identity, _ = sheet_identity(previous, row.name)
        if first_as_listing and row.is_primary and row.is_protected and old_mode == 'template' and identity == baseline_key:
            continue
        if (row.is_primary or row.is_protected) and identity == new_primary_key:
            continue
        result, title = append_sheet(document.content, previous, row.name, f'Listado motores {number}' if first_as_listing else row.name)
        result = discard_empty_sheet(document, result)
        store(document, result, user)
        PlanWorkbookSheet.objects.create(workbook=document, name=title, is_listing=True,
            is_primary=number == 1, listing_order=number, is_protected=row.is_protected, created_by=row.created_by)
        number += 1
    for marker in old_markers:
        bind(marker, document, remap.get(marker.sheet_name, ''))
    plan.workbook = plan.listing = document
    plan.save(update_fields=['workbook', 'listing'])
    return document


@transaction.atomic
def add_listing(plan, user, upload=None, selected=None):
    from .project_views import listing_template
    from .button_import import inspect_button_file
    document = ensure_workbook(plan, user)
    if upload:
        content, source = inspect_button_file(upload, selected, allow_default=True)
    else:
        content = listing_template(markers(plan).count())
        source = sheet_names(content)[0]
    number = max([0] + list(document.plan_sheets.filter(is_listing=True).values_list('listing_order', flat=True))) + 1
    content, name = append_sheet(document.content, content, source, 'Listado' if number == 1 else f'Listado motores {number}')
    content = discard_empty_sheet(document, content)
    store(document, content, user)
    return PlanWorkbookSheet.objects.create(workbook=document, name=name, is_listing=True,
        is_primary=number == 1, listing_order=number, created_by=user)


@transaction.atomic
def change_primary_listing(plan, name, user):
    document = ensure_workbook(plan, user)
    record = document.plan_sheets.filter(name=name).first()
    if not record:
        raise ValidationError('La hoja seleccionada no existe.')
    if markers(plan).filter(sheet_name=name).exists():
        raise ValidationError('Esta hoja está asignada a un botón. Elija otra hoja para el listado.')
    if record.is_listing and not record.is_primary:
        raise ValidationError('Esta hoja ya es un listado adicional.')
    document.plan_sheets.filter(is_primary=True).exclude(pk=record.pk).update(is_primary=False, is_listing=False, listing_order=0)
    record.is_listing, record.is_primary, record.is_protected, record.listing_order = True, True, True, 1
    record.save(update_fields=['is_listing', 'is_primary', 'is_protected', 'listing_order'])
    return record


@transaction.atomic
def add_existing_listing(plan, name, user):
    document = ensure_workbook(plan, user)
    record = document.plan_sheets.filter(name=name, is_listing=False).first()
    if not record or markers(plan).filter(sheet_name=name).exists():
        raise ValidationError('Elija una hoja libre que no esté asignada a un botón.')
    number = max([0] + list(document.plan_sheets.filter(is_listing=True).values_list('listing_order', flat=True))) + 1
    content, title = rename_sheet(document.content, record.name, 'Listado' if number == 1 else f'Listado motores {number}')
    store(document, content, user)
    record.name, record.is_listing, record.listing_order = title, True, number
    record.is_primary = number == 1
    if not record.is_protected:
        record.created_by = user
    record.save(update_fields=['name', 'is_listing', 'is_primary', 'listing_order', 'created_by'])
    return record


@transaction.atomic
def delete_listing(plan, record_id, user):
    document = ensure_workbook(plan, user)
    record = document.plan_sheets.filter(pk=record_id, is_listing=True).first()
    if not record:
        raise ValidationError('El listado ya no existe en este plano.')
    if not can_delete_listing(record, user, plan):
        raise PermissionError('Solo el creador puede eliminar este listado adicional. El listado original requiere permiso de eliminar.')
    if markers(plan).filter(sheet_name=record.name).exists():
        raise ValidationError('La hoja está asignada a un botón y no puede eliminarse.')
    if len(sheet_names(document.content)) == 1:
        store(document, empty_workbook(), user)
        record.delete()
        PlanWorkbookSheet.objects.create(workbook=document, name=EMPTY_SHEET, created_by=user)
        return
    store(document, remove_sheet(document.content, record.name), user)
    record.delete()
    if record.is_primary:
        first = document.plan_sheets.filter(is_listing=True).first()
        if first:
            first.is_primary = True
            first.save(update_fields=['is_primary'])


def selected_motor_document(document, name):
    row = document.plan_sheets.filter(name=name, is_listing=False).first()
    if not row:
        raise ValidationError('Seleccione una hoja de motor del plano.')
    content = write_sheet_origins(single_sheet(document.content, name), {name: row.origin_uuid} if row.origin_uuid else {})
    return WorkbookDocument(name=f'{name}.xlsx', content=content,
        is_motor=True, origin_uuid=row.origin_uuid)


def known_sheet(content, name):
    fingerprint, filled = sheet_identity(content, name)
    return SavedMotor.objects.filter(data_fingerprint=fingerprint).first() if filled else None
