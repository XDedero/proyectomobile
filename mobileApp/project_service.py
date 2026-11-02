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
from .models import Motor, PdfMarker, PdfPlan, Plan, WorkbookDocument

MAX_PROJECT_BYTES = 100 * 1024 * 1024
MAX_PROJECT_UNPACKED = 250 * 1024 * 1024


def plan_model(kind):
    if kind not in ('pdf', 'dxf'):
        raise ValidationError('El formato del plano debe ser PDF o DXF.')
    return PdfPlan if kind == 'pdf' else Plan


def export_project(kind, plan):
    documents, keys = [], {}

    def document_key(document, legacy=None):
        if document is None and not legacy:
            return None
        if document is not None:
            content, name = bytes(document.content), document.name
            origin = str(document.origin_uuid) if document.origin_uuid else None
        else:
            with legacy.excel.open('rb') as source:
                content = source.read()
            name, origin = legacy.excel_name, None
        # Store identical blank forms only once in the file. Import still
        # creates a separate editable document for every motor occurrence.
        identity = (hashlib.sha256(content).hexdigest(), origin)
        if identity not in keys:
            key = f'w{len(keys) + 1}'
            keys[identity] = key
            documents.append({'key': key, 'name': name, 'path': f'workbooks/{key}.xlsx', 'content': content, 'origin_uuid': origin})
        return keys[identity]

    if kind == 'pdf':
        markers = [{'page': item.page, 'x': item.x, 'y': item.y, 'label': item.label,
                    'workbook': document_key(item.workbook), 'workbook_name': item.workbook.name if item.workbook_id else None} for item in plan.markers.select_related('workbook')]
    else:
        markers = [{'entity_key': item.entity_key, 'name': item.name, 'x': item.x, 'y': item.y, 'z': item.z,
                    'workbook': document_key(item.workbook, item if item.excel else None), 'workbook_name': item.workbook.name if item.workbook_id else item.excel_name}
                   for item in plan.motors.select_related('workbook')]
    manifest = {
        'format': 'motor-plan-project', 'version': 2, 'kind': kind, 'name': plan.name,
        'source': f'plan/source.{kind}', 'markers': markers,
        'listing': document_key(plan.listing),
        'listing_name': plan.listing.name if plan.listing_id else None,
        'workbooks': [{key: value for key, value in document.items() if key != 'content'} for document in documents],
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode('utf-8')
    if len(documents) > MAX_MOTORS + 1 or len(markers) > MAX_MOTORS or len(manifest_bytes) > 8 * 1024 * 1024:
        raise ValidationError('El proyecto supera el límite de botones o archivos transportables.')
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', manifest_bytes)
        with plan.source.open('rb') as source:
            source_content = source.read()
        if len(source_content) > settings.PLAN_UPLOAD_MAX_BYTES or len(manifest_bytes) + len(source_content) + sum(len(document['content']) for document in documents) > MAX_PROJECT_UNPACKED:
            raise ValidationError('El contenido del proyecto supera el tamaño transportable permitido.')
        archive.writestr(manifest['source'], source_content)
        for document in documents:
            archive.writestr(document['path'], document['content'])
    result = output.getvalue()
    if len(result) > MAX_PROJECT_BYTES:
        raise ValidationError('El proyecto supera el tamaño máximo de 100 MB.')
    return result


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
            if not isinstance(manifest, dict) or manifest.get('format') != 'motor-plan-project' or type(manifest.get('version')) is not int or manifest['version'] not in (1, 2):
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
                if archive.getinfo(path).file_size > settings.PLAN_UPLOAD_MAX_BYTES:
                    raise ValidationError('Un Excel del proyecto supera 20 MB.')
                content = archive.read(path)
                sheets = validate_excel(SimpleUploadedFile(filename, content))
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
