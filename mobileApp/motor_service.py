"""Standard motor forms, independent copies, and named immutable motor records."""

from functools import lru_cache
import hashlib
from io import BytesIO
import json
import posixpath
from pathlib import Path
import unicodedata
from uuid import UUID
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from openpyxl.utils.cell import range_boundaries

from .models import SavedMotor, WorkbookDocument

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CP = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
VT = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
ORIGIN_PROPERTY = 'MotorRegistroUUID'
INPUT_RANGES = ('D10:E10', 'D11:H11', 'C18:G29', 'D30:G30', 'I18:I22', 'K19:K21', 'K23:K25', 'K27:K29', 'B39:E44', 'B54:H63', 'B65:K72')


def _relationships_path(part):
    return posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels') if part else '_rels/.rels'


def single_sheet(content, name):
    """Keep a sheet's original chart/image XML; discard other sheets' data."""
    from .workbook_service import _sheet_paths
    with ZipFile(BytesIO(bytes(content))) as archive:
        paths = _sheet_paths(archive)
        if name not in paths:
            raise ValidationError('La hoja de la ficha no existe.')
        replacements = {}
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = workbook.find('{' + S + '}sheets')
        original_index = next(index for index, item in enumerate(sheets) if item.get('name') == name)
        selected = list(sheets)[original_index]
        relationship_id = selected.get('{' + R + '}id')
        for item in list(sheets):
            if item is not selected:
                sheets.remove(item)
        selected.set('state', 'visible')
        for view in workbook.findall('.//{' + S + '}workbookView'):
            view.set('activeTab', '0')
            view.set('firstSheet', '0')
        definitions = workbook.find('{' + S + '}definedNames')
        if definitions is not None:
            for item in list(definitions):
                if item.get('localSheetId') is not None and int(item.get('localSheetId')) != original_index:
                    definitions.remove(item)
                elif item.get('localSheetId') is not None:
                    item.set('localSheetId', '0')
                elif '!' in (item.text or '') and name + '!' not in item.text and "'" + name + "'!" not in item.text:
                    definitions.remove(item)
        replacements['xl/workbook.xml'] = ET.tostring(workbook, encoding='utf-8', xml_declaration=True)
        rel_path = _relationships_path('xl/workbook.xml')
        relations = ET.fromstring(archive.read(rel_path))
        for item in list(relations):
            if item.get('Type', '').endswith('/calcChain') or (item.get('Type', '').endswith('/worksheet') and item.get('Id') != relationship_id):
                relations.remove(item)
        replacements[rel_path] = ET.tostring(relations, encoding='utf-8', xml_declaration=True)
        keep, pending = {'[Content_Types].xml'}, ['']
        while pending:
            part = pending.pop()
            if part:
                if part in keep:
                    continue
                keep.add(part)
            relation_path = _relationships_path(part)
            if relation_path not in archive.namelist():
                continue
            keep.add(relation_path)
            root = ET.fromstring(replacements.get(relation_path, archive.read(relation_path)))
            for relation in root:
                if relation.get('TargetMode') == 'External':
                    continue
                target = relation.get('Target', '')
                resolved = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
                if resolved in archive.namelist():
                    pending.append(resolved)
        types = ET.fromstring(archive.read('[Content_Types].xml'))
        for item in list(types):
            if item.tag.endswith('Override') and item.get('PartName', '').lstrip('/') not in keep:
                types.remove(item)
        replacements['[Content_Types].xml'] = ET.tostring(types, encoding='utf-8', xml_declaration=True)
        output = BytesIO()
        with ZipFile(output, 'w') as result:
            for item in archive.infolist():
                if item.filename in keep:
                    result.writestr(item, replacements.get(item.filename, archive.read(item.filename)))
        return output.getvalue()


def read_origin(content):
    try:
        with ZipFile(BytesIO(bytes(content))) as archive:
            if 'docProps/custom.xml' not in archive.namelist():
                return None
            for item in ET.fromstring(archive.read('docProps/custom.xml')):
                if item.get('name') == ORIGIN_PROPERTY and len(item):
                    return UUID(item[0].text)
    except (ValueError, TypeError, ET.ParseError):
        return None
    return None


def stamp_origin(content, identifier):
    """Record provenance without rewriting any worksheet, chart or image."""
    with ZipFile(BytesIO(bytes(content))) as archive:
        custom = ET.fromstring(archive.read('docProps/custom.xml')) if 'docProps/custom.xml' in archive.namelist() else ET.Element('{' + CP + '}Properties')
        for item in list(custom):
            if item.get('name') == ORIGIN_PROPERTY:
                custom.remove(item)
        next_pid = max([1] + [int(item.get('pid', '1')) for item in custom]) + 1
        prop = ET.SubElement(custom, '{' + CP + '}property', {'fmtid': '{D5CDD505-2E9C-101B-9397-08002B2CF9AE}', 'pid': str(next_pid), 'name': ORIGIN_PROPERTY})
        ET.SubElement(prop, '{' + VT + '}lpwstr').text = str(identifier)
        types = ET.fromstring(archive.read('[Content_Types].xml'))
        if not any(item.get('PartName') == '/docProps/custom.xml' for item in types):
            ET.SubElement(types, '{' + CT + '}Override', {'PartName': '/docProps/custom.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.custom-properties+xml'})
        relations = ET.fromstring(archive.read('_rels/.rels'))
        if not any(item.get('Type', '').endswith('/custom-properties') for item in relations):
            ids = {item.get('Id') for item in relations}
            number = 1
            while f'rId{number}' in ids:
                number += 1
            ET.SubElement(relations, '{' + REL + '}Relationship', {'Id': f'rId{number}', 'Type': R + '/custom-properties', 'Target': 'docProps/custom.xml'})
        replacements = {'docProps/custom.xml': ET.tostring(custom, encoding='utf-8', xml_declaration=True), '[Content_Types].xml': ET.tostring(types, encoding='utf-8', xml_declaration=True), '_rels/.rels': ET.tostring(relations, encoding='utf-8', xml_declaration=True)}
        output = BytesIO()
        with ZipFile(output, 'w') as result:
            for item in archive.infolist():
                result.writestr(item, replacements.pop(item.filename, archive.read(item.filename)))
            for path, payload in replacements.items():
                result.writestr(path, payload)
        return output.getvalue()


def motor_name(value):
    if not isinstance(value, str):
        raise ValidationError('Asigne un nombre al motor.')
    name = ' '.join(unicodedata.normalize('NFKC', value).split())
    key = name.casefold()
    if not name or len(name) > 120 or len(key) > 240:
        raise ValidationError('El nombre del motor debe tener entre 1 y 120 caracteres.')
    return name, key


def inspect_motor(content):
    """Fingerprint cell data, independent of ZIP dates and registry metadata."""
    from .workbook_service import cell_value
    from .workbook_service import load_document
    workbook = load_document(content, read_only=False)
    try:
        payload, forms, filled, generic = [], False, False, False
        for sheet in workbook:
            values = []
            for (row, col), cell in sorted(sheet._cells.items()):
                if cell.value is not None:
                    value = cell_value(cell.value)
                    if isinstance(value, float) and value.is_integer():
                        value = int(value)
                    values.append([row, col, value])
            payload.append([sheet.title, values])
            form = 'FICHA' in str(sheet['B6'].value or '').upper() and str(sheet['B18'].value or '').strip().casefold() == 'marca'
            if form:
                forms = True
                data = {}
                for address in INPUT_RANGES:
                    left, top, right, bottom = range_boundaries(address)
                    for row in sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
                        for cell in row:
                            if cell.value is not None and str(cell.value).strip():
                                data[cell.coordinate] = cell.value
                # The supplied prototype contains 4/8/12 as example currents.
                if data and data != {'C39': 4, 'D39': 8, 'E39': 12}:
                    filled = True
            elif sheet.title.casefold() != 'listado':
                generic |= any(row >= 2 and str(value).strip() for row, col, value in values)
        fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':')).encode('utf-8')).hexdigest()
        return fingerprint, bool(filled if forms else generic)
    finally:
        workbook.close()


@lru_cache(maxsize=1)
def template_bytes():
    return (Path(__file__).resolve().parent / 'data' / 'motor-template.xlsx').read_bytes()


def blank_motor(user, name='Ficha de motor.xlsx'):
    return WorkbookDocument.objects.create(name=name[:255], content=template_bytes(), is_motor=True, updated_by=user)


def known_motor(document, *, fingerprint=None):
    identifier = document.origin_uuid
    if identifier:
        saved = SavedMotor.objects.filter(uuid=identifier).first()
        if saved:
            return saved
    if fingerprint is None:
        fingerprint, _ = inspect_motor(document.content)
    return SavedMotor.objects.filter(fingerprint=fingerprint).first()


def copy_motor(document, user, *, name=None):
    content = bytes(document.content)
    identifier = resolve_origin(content, document.origin_uuid)
    if identifier and read_origin(content) != identifier:
        content = stamp_origin(content, identifier)
    return WorkbookDocument.objects.create(name=(name or document.name)[:255], content=content, is_motor=True, origin_uuid=identifier, updated_by=user)


def resolve_origin(content, hint=None):
    identifier = read_origin(content) or hint
    if not identifier and not SavedMotor.objects.exists():
        return None
    if not identifier or not SavedMotor.objects.filter(uuid=identifier).exists():
        fingerprint, _ = inspect_motor(content)
        saved = SavedMotor.objects.filter(fingerprint=fingerprint).first()
        if saved:
            identifier = saved.uuid
    return identifier


def mark_motor(document):
    content = bytes(document.content)
    identifier = resolve_origin(content, document.origin_uuid)
    document.is_motor = True
    document.origin_uuid = identifier
    if identifier and read_origin(content) != identifier:
        document.content = stamp_origin(content, identifier)
    document.save(update_fields=['content', 'is_motor', 'origin_uuid'])
    return document


def registration_data(document, *, inspect=False):
    if not document.is_motor:
        return {'is_motor': False, 'can_register': False}
    fingerprint, filled = inspect_motor(document.content) if inspect else (None, None)
    saved = SavedMotor.objects.filter(uuid=document.origin_uuid).first() if document.origin_uuid else None
    if saved is None and inspect:
        saved = SavedMotor.objects.filter(fingerprint=fingerprint).first()
    return {
        'is_motor': True, 'registered': bool(saved), 'saved_motor_id': saved.pk if saved else None,
        'name': saved.name if saved else None, 'has_data': filled,
        'can_register': bool(not saved and filled) if inspect else None,
    }
