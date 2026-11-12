"""Portable projects: original plan + marker positions + exact XLSX documents."""

from io import BytesIO
import json
import hashlib
import math
from pathlib import Path, PurePosixPath
from uuid import UUID
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
from zlib import error as ZlibError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from pypdf import PdfReader

from .dxf_service import import_dxf, MAX_MOTORS
from .models import ButtonCategory, Motor, PdfMarker, PdfPlan, Plan, WorkbookDocument, PlanWorkbookSheet, SavedProject

MAX_PROJECT_BYTES = 100 * 1024 * 1024
MAX_PROJECT_UNPACKED = 250 * 1024 * 1024


def plan_model(kind):
    if kind not in ('pdf', 'dxf'):
        raise ValidationError('El formato del plano debe ser PDF o DXF.')
    return PdfPlan if kind == 'pdf' else Plan


def open_plan_source(kind, plan):
    """Use the saved original when a deployment has lost the media file."""
    if plan.source:
        try:
            return plan.source.open('rb')
        except FileNotFoundError:
            pass
    record = SavedProject.objects.filter(**{'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': plan.draft_of_id or plan.pk}).only('content').first()
    try:
        saved = record.content if record else b''
    except OSError:
        saved = b''
    if saved and len(saved) <= MAX_PROJECT_BYTES:
        try:
            with ZipFile(BytesIO(bytes(saved))) as archive:
                if archive.getinfo('manifest.json').file_size > 8 * 1024 * 1024:
                    raise ValueError('Manifest too large')
                manifest = json.loads(archive.read('manifest.json'))
                path = f'plan/source.{kind}'
                if (not isinstance(manifest, dict) or manifest.get('format') != 'motor-plan-project'
                        or type(manifest.get('version')) is not int or manifest['version'] not in (1, 2, 3)
                        or manifest.get('kind') != kind or manifest.get('source') != path):
                    raise ValueError('Invalid source reference')
                info = archive.getinfo(path)
                if not 0 < info.file_size <= settings.PLAN_UPLOAD_MAX_BYTES:
                    raise ValueError('Invalid source size')
                # Read only the known member; never extract archive paths.
                with archive.open(info) as member:
                    source = member.read(settings.PLAN_UPLOAD_MAX_BYTES + 1)
                if len(source) != info.file_size or (kind == 'pdf' and not source.startswith(b'%PDF-')):
                    raise ValueError('Invalid original')
                return BytesIO(source)
        except (BadZipFile, KeyError, ValueError, TypeError, OSError, RuntimeError, NotImplementedError, EOFError, ZlibError):
            pass
    raise ValidationError('El archivo original del plano no está disponible y no hay una copia válida en el proyecto guardado. Vuelva a importar el plano.', code='plan_source_missing')


def export_project(kind, plan, *, source=None):
    from .display_settings import clean_display
    from .button_descriptions import TemplateReader, snapshot_details
    from .plan_workbook import ensure_workbook
    from .xlsx_package import sheet_names
    document = ensure_workbook(plan)
    indexed = {row.name: row for row in document.plan_sheets.all()}
    rows = [indexed[name] for name in sheet_names(document.content) if name in indexed]
    with TemplateReader(document) as reader:
        if kind == 'pdf':
            markers = [{'page': item.page, 'x': item.x, 'y': item.y, 'label': item.label,
                    'has_area': getattr(item, 'has_area', False),
                    'area_radius_x': getattr(item, 'area_radius_x', None),
                    'area_radius_y': getattr(item, 'area_radius_y', None),
                    'area_color': getattr(item, 'area_color', 'rgba(59, 130, 246, 0.25)'),
                    'workbook': 'w1' if item.sheet_name else None, 'sheet': item.sheet_name,
                    **category_data(item), **snapshot_details(item, reader)}
                       for item in plan.markers.select_related('category')]
        else:
            markers = [{'entity_key': item.entity_key, 'name': item.name, 'x': item.x, 'y': item.y, 'z': item.z,
                    'workbook': 'w1' if item.sheet_name else None, 'sheet': item.sheet_name,
                    **category_data(item), **snapshot_details(item, reader)}
                       for item in plan.motors.select_related('category')]
    manifest = {
        'format': 'motor-plan-project', 'version': 3, 'kind': kind, 'name': plan.name,
        'display_settings': clean_display(plan.display_settings),
        'sectors': plan.sectors,
        'owner_username': plan.owner_username or (plan.imported_by.get_username() if plan.imported_by_id else ''),
        'source': f'plan/source.{kind}', 'markers': markers, 'workbook': 'w1',
        'workbook_mode': document.plan_mode,
        'listing': 'w1' if any(row.is_listing for row in rows) else None,
        'listing_name': document.name,
        'sheets': [{'name': row.name, 'is_listing': row.is_listing, 'is_primary': row.is_primary, 'is_protected': row.is_protected,
                    'listing_order': row.listing_order, 'origin_uuid': str(row.origin_uuid) if row.origin_uuid else None} for row in rows],
        'workbooks': [{'key': 'w1', 'name': document.name, 'path': 'workbooks/w1.xlsx', 'origin_uuid': None}],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
    content = bytes(document.content)
    if source is None:
        with open_plan_source(kind, plan) as stream:
            source = stream.read()
    if len(markers) > MAX_MOTORS or len(manifest_bytes) > 8 * 1024 * 1024 or len(source) + len(content) + len(manifest_bytes) > MAX_PROJECT_UNPACKED:
        raise ValidationError('El proyecto supera el tamaño transportable permitido.')
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', manifest_bytes)
        archive.writestr(manifest['source'], source)
        archive.writestr('workbooks/w1.xlsx', content)
    result = output.getvalue()
    if len(result) > MAX_PROJECT_BYTES:
        raise ValidationError('El proyecto supera el tamaño máximo de 100 MB.')
    return result


def category_data(button):
    if not button.category_id:
        return {}
    return {'category_id': str(button.category_id), 'category_name': button.category.name}


def validate_pdf_source(content):
    try:
        if not content.startswith(b'%PDF-'):
            raise ValueError
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 1000:
            raise ValueError
        pages = []
        for page in reader.pages:
            width, height = float(page.cropbox.width), float(page.cropbox.height)
            if not all(math.isfinite(value) and value > 0 for value in (width, height)):
                raise ValueError
            if int(page.rotation or 0) % 180:
                width, height = height, width
            pages.append({'width': width, 'height': height})
        return pages
    except Exception as exc:
        raise ValidationError('El proyecto no contiene un PDF válido sin contraseña.') from exc


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e15


@transaction.atomic
def import_shared_project(plan, kind, source, svg, markers, books, manifest, user):
    from .sectors import clean_sectors
    plan.sectors = clean_sectors(manifest.get('sectors', []), pages=len(plan.pages) if kind == 'pdf' else 1)
    from .button_descriptions import imported_details
    from .display_settings import clean_display
    plan.display_settings = clean_display(manifest.get('display_settings', {}))
    key = manifest.get('workbook')
    if not isinstance(key, str) or key not in books or len(books) != 1:
        raise ValidationError('El proyecto debe contener un único Excel del plano.')
    info = books[key]
    specs = manifest.get('sheets')
    if not isinstance(specs, list) or not specs or len(specs) != len(info['sheets']):
        raise ValidationError('Las hojas del proyecto no coinciden con su Excel.')
    names = set()
    for row in specs:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str) or row['name'] in names or row['name'] not in info['sheets']:
            raise ValidationError('Una hoja del proyecto no existe o está repetida.')
        names.add(row['name'])
        if type(row.get('is_listing')) is not bool or type(row.get('is_primary')) is not bool or (row['is_primary'] and not row['is_listing']):
            raise ValidationError('El tipo de una hoja del proyecto no es válido.')
        if type(row.get('is_protected', row['is_primary'])) is not bool:
            raise ValidationError('La protección del listado no es válida.')
        if type(row.get('listing_order')) is not int or not 0 <= row['listing_order'] <= 5001:
            raise ValidationError('El orden de los listados no es válido.')
        if row.get('origin_uuid') is not None:
            try:
                if not isinstance(row['origin_uuid'], str):
                    raise ValueError
                UUID(row['origin_uuid'])
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValidationError('El origen de una hoja del motor no es válido.') from exc
    if sum(row['is_primary'] for row in specs) > 1 or manifest.get('workbook_mode') not in ('template', 'imported'):
        raise ValidationError('La configuración del Excel del plano no es válida.')
    motor_names = {row['name'] for row in specs if not row['is_listing']}
    categories = list(ButtonCategory.objects.values_list('id', 'name'))
    category_ids = {str(identifier): identifier for identifier, _ in categories}
    category_names = {name.casefold(): identifier for identifier, name in categories}
    def category_for(item):
        # Importing a project can use categories that an admin has already created.
        return category_ids.get(item.get('category_id')) or category_names.get((item.get('category_name') or '').casefold())

    details = []
    for item in markers:
        sheet = item.get('sheet')
        if not isinstance(sheet, str) or (sheet and (sheet not in motor_names or item.get('workbook') != key)) or (not sheet and item.get('workbook') is not None):
            raise ValidationError('Un botón apunta a una hoja de motor inexistente.')
        details.append(imported_details(item))
    document = WorkbookDocument.objects.create(name=info['name'], content=info['content'], plan_mode=manifest['workbook_mode'], updated_by=user)
    PlanWorkbookSheet.objects.bulk_create([PlanWorkbookSheet(workbook=document, name=row['name'],
        is_listing=row['is_listing'], is_primary=row['is_primary'], listing_order=row['listing_order'],
        is_protected=row.get('is_protected', row['is_primary']), origin_uuid=row.get('origin_uuid'), created_by=user) for row in specs])
    plan.workbook = plan.listing = document
    plan.source.save(f'plan.{kind}', SimpleUploadedFile(f'plan.{kind}', source), save=False)
    plan.save()
    if kind == 'pdf':
        PdfMarker.objects.bulk_create([PdfMarker(plan=plan, page=item['page'], x=item['x'], y=item['y'],
            label=item.get('label', ''),
            has_area=item.get('has_area', False),
            area_radius_x=item.get('area_radius_x'),
            area_radius_y=item.get('area_radius_y'),
            area_color=item.get('area_color', 'rgba(59, 130, 246, 0.25)'),
             workbook=document, sheet_name=item['sheet'], category_id=category_for(item), **detail) for item, detail in zip(markers, details)])
    else:
        a, b, c, d, e, f = map(float, ET.fromstring(svg).get('data-wcs-transform').split())
        Motor.objects.bulk_create([Motor(plan=plan, entity_key=item['entity_key'], name=item['name'],
            has_area=item.get('has_area', False), area_radius_x=item.get('area_radius_x'), area_radius_y=item.get('area_radius_y'),
            x=item['x'], y=item['y'], z=item['z'], svg_x=a*item['x']+c*item['y']+e,
            svg_y=b*item['x']+d*item['y']+f, svg_bounds=[a*item['x']+c*item['y']+e, b*item['x']+d*item['y']+f, 0, 0],
            workbook=document, sheet_name=item['sheet'], excel_sheets=[item['sheet']] if item['sheet'] else [],
             category_id=category_for(item), **detail) for item, detail in zip(markers, details)])
    return kind, plan


def import_project(upload, user):
    from .views import validate_excel
    if not upload.size or upload.size > MAX_PROJECT_BYTES or Path(upload.name).suffix.lower() != '.planproj':
        raise ValidationError('Seleccione un proyecto .planproj de hasta 100 MB.')
    try:
        with ZipFile(upload) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > MAX_MOTORS + 3 or len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > MAX_PROJECT_UNPACKED:
                raise ValidationError('El proyecto contiene demasiados archivos o es demasiado grande.')
            if any('\\' in name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts for name in names):
                raise ValidationError('El proyecto contiene rutas no válidas.')
            if archive.getinfo('manifest.json').file_size > 8 * 1024 * 1024:
                raise ValidationError('El índice del proyecto es demasiado grande.')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('format') != 'motor-plan-project' or type(manifest.get('version')) is not int or manifest['version'] not in (1, 2, 3):
                raise ValidationError('El archivo no es un proyecto compatible de esta aplicación.')
            kind = manifest.get('kind')
            model = plan_model(kind)
            name = manifest.get('name')
            if not isinstance(name, str) or not name.strip() or len(name) > 255:
                raise ValidationError('El nombre del proyecto no es válido.')
            if manifest.get('source') != f'plan/source.{kind}':
                raise ValidationError('La referencia al plano no es válida.')
            if archive.getinfo(manifest['source']).file_size > settings.PLAN_UPLOAD_MAX_BYTES:
                raise ValidationError('El plano del proyecto supera 20 MB.')
            source = archive.read(manifest['source'])
            if kind == 'pdf':
                pages, svg = validate_pdf_source(source), None
            else:
                svg, _ = import_dxf(SimpleUploadedFile('source.dxf', source))
                pages = None
            specifications = manifest.get('workbooks')
            if not isinstance(specifications, list) or len(specifications) > MAX_MOTORS + 1:
                raise ValidationError('La lista de Excel del proyecto no es válida.')
            books = {}
            allowed = {'manifest.json', manifest['source']}
            for spec in specifications:
                if not isinstance(spec, dict) or not isinstance(spec.get('key'), str) or not spec['key'].isalnum() or len(spec['key']) > 32 or spec['key'] in books:
                    raise ValidationError('Una referencia de Excel no es válida o está repetida.')
                path, filename = spec.get('path'), spec.get('name')
                if path != f"workbooks/{spec['key']}.xlsx" or not isinstance(filename, str) or not filename.lower().endswith('.xlsx') or len(filename) > 255:
                    raise ValidationError('El archivo Excel del proyecto no es válido.')
                if archive.getinfo(path).file_size > (MAX_PROJECT_BYTES if manifest['version'] == 3 else settings.PLAN_UPLOAD_MAX_BYTES):
                    raise ValidationError('Un Excel del proyecto supera 20 MB.')
                content = archive.read(path)
                sheets = validate_excel(SimpleUploadedFile(filename, content), max_bytes=MAX_PROJECT_BYTES if manifest['version'] == 3 else None)
                origin = UUID(str(spec['origin_uuid'])) if spec.get('origin_uuid') else None
                books[spec['key']] = {'name': filename, 'content': content, 'sheets': sheets, 'origin': origin}
                allowed.add(path)
            if set(names) != allowed:
                raise ValidationError('El proyecto contiene archivos ajenos a su índice.')
            markers = manifest.get('markers')
            if not isinstance(markers, list) or len(markers) > MAX_MOTORS:
                raise ValidationError('La lista de botones del proyecto no es válida.')
            if manifest.get('listing') is not None and manifest['listing'] not in books:
                raise ValidationError('No se encontró el listado asociado al plano.')
            for marker in markers:
                if not isinstance(marker, dict) or (marker.get('workbook') is not None and marker['workbook'] not in books):
                    raise ValidationError('Un botón hace referencia a un Excel inexistente.')
                if marker.get('workbook_name') is not None and (not isinstance(marker['workbook_name'], str) or len(marker['workbook_name']) > 255):
                    raise ValidationError('El nombre de una ficha no es válido.')
                if marker.get('category_id') is not None and not isinstance(marker['category_id'], str):
                    raise ValidationError('La categoría de un botón no es válida.')
                if marker.get('category_name') is not None and (not isinstance(marker['category_name'], str) or len(marker['category_name']) > 80):
                    raise ValidationError('El nombre de la categoría de un botón no es válido.')
                if kind == 'pdf':
                    if type(marker.get('page')) is not int or not 1 <= marker['page'] <= len(pages) or any(not finite(marker.get(key)) or not 0 <= marker[key] <= 1 for key in ('x', 'y')):
                        raise ValidationError('Un botón PDF tiene página o coordenadas no válidas.')
                    if not isinstance(marker.get('label', ''), str) or len(marker.get('label', '')) > 120:
                        raise ValidationError('El nombre de un botón no es válido.')
                else:
                    if any(not finite(marker.get(key)) for key in ('x', 'y', 'z')) or not isinstance(marker.get('name'), str) or len(marker['name']) > 255 or not isinstance(marker.get('entity_key'), str) or len(marker['entity_key']) > 2048:
                        raise ValidationError('Un botón DXF tiene datos no válidos.')
    except ValidationError:
        raise
    except (BadZipFile, KeyError, ValueError, TypeError, OSError, RuntimeError, UnicodeError, EOFError, ZlibError) as exc:
        raise ValidationError('El proyecto está dañado o incompleto.') from exc

    plan = model(name=name, size=len(source), imported_by=user)
    if kind == 'pdf':
        plan.pages = pages
    else:
        plan.svg = svg
    if manifest['version'] == 3:
        try:
            return import_shared_project(plan, kind, source, svg, markers, books, manifest, user)
        except Exception:
            if plan.source:
                plan.source.delete(save=False)
            raise
    try:
        with transaction.atomic():
            from .motor_service import resolve_origin, stamp_origin, read_origin, template_bytes
            listing_key = manifest.get('listing')
            if listing_key:
                info = books[listing_key]
                plan.listing = WorkbookDocument.objects.create(name=str(manifest.get('listing_name') or info['name'])[:255], content=info['content'], updated_by=user)
            for info in books.values():
                info['origin'] = resolve_origin(info['content'], info['origin'])
                if info['origin'] and read_origin(info['content']) != info['origin']:
                    info['content'] = stamp_origin(info['content'], info['origin'])
            documents = WorkbookDocument.objects.bulk_create([
                WorkbookDocument(name=item.get('workbook_name') or (books[item['workbook']]['name'] if item.get('workbook') else f'Motor {index+1}.xlsx'),
                    content=books[item['workbook']]['content'] if item.get('workbook') else template_bytes(),
                    origin_uuid=books[item['workbook']]['origin'] if item.get('workbook') else None, is_motor=True, updated_by=user)
                for index, item in enumerate(markers)
            ])
            plan.source.save(f'plan.{kind}', SimpleUploadedFile(f'plan.{kind}', source), save=False)
            plan.save()
            if kind == 'pdf':
                PdfMarker.objects.bulk_create([PdfMarker(plan=plan, page=item['page'], x=item['x'], y=item['y'], label=item.get('label', ''), workbook=documents[index]) for index, item in enumerate(markers)])
            else:
                a, b, c, d, e, f = map(float, ET.fromstring(svg).get('data-wcs-transform').split())
                motors = []
                for index, item in enumerate(markers):
                    sx, sy = a*item['x']+c*item['y']+e, b*item['x']+d*item['y']+f
                    key = item.get('workbook')
                    motors.append(Motor(plan=plan, entity_key=item['entity_key'], name=item['name'], x=item['x'], y=item['y'], z=item['z'], svg_x=sx, svg_y=sy, svg_bounds=[sx, sy, 0, 0], workbook=documents[index], excel_sheets=books[key]['sheets'] if key else ['1']))
                Motor.objects.bulk_create(motors)
    except Exception:
        if plan.source:
            plan.source.delete(save=False)
        raise
    return kind, plan
