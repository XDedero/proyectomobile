"""Optional button notes. Template values follow the last edit of the linked cell or note."""
from io import BytesIO
import re
import unicodedata
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db.models import Q
from openpyxl import load_workbook

from .models import Motor, PdfMarker, WorkbookDocument
from .workbook_service import formatted_value


# The location is relative to the matching label on the standard motor form.
FIELDS = {
    'ficha': ('Nº de ficha', 'no ficha', 1),
    'fecha': ('Fecha', 'fecha', 2),
    'sector': ('Sector', 'sector', 2),
    'marca': ('Marca', 'marca', 1),
    'modelo': ('Modelo', 'modelo', 1),
    'serie': ('Nº de serie', 'no de serie', 1),
    'potencia': ('Potencia', 'potencia', 1),
    'corriente': ('Corriente', 'corriente', 1),
    'voltaje': ('Voltaje', 'voltaje', 1),
    'velocidad': ('Velocidad', 'velocidad', 1),
}
MAX_ITEMS = 64
MAX_KEY = 100
MAX_VALUE = 2000


def label_key(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold()).replace('°', 'o')
    return ' '.join(re.sub(r'[^a-z0-9 ]', '', text).split())


class TemplateReader:
    """Reuse one Excel reader when several buttons share a workbook."""

    def __init__(self, document=None):
        self.documents = {document.pk: document} if document is not None else {}
        self.books = {}
        self.values = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for book in self.books.values():
            book.close()

    def document(self, identifier):
        if identifier not in self.documents:
            self.documents[identifier] = WorkbookDocument.objects.get(pk=identifier)
        return self.documents[identifier]

    def read(self, identifier, name):
        if not identifier or not name:
            return {}
        key = (identifier, name)
        if key not in self.values:
            if identifier not in self.books:
                self.books[identifier] = load_workbook(BytesIO(self.document(identifier).content), read_only=True, data_only=True)
            book = self.books[identifier]
            self.values[key] = template_values(book[name], book.epoch) if name in book.sheetnames else {}
        return self.values[key]


def template_values(sheet, epoch):
    rows = list(sheet.iter_rows(min_row=1, max_row=100, min_col=1, max_col=24))
    keys = [[label_key(cell.value) if isinstance(cell.value, str) else '' for cell in row] for row in rows]
    signature = next(((r, c) for r, row in enumerate(keys) for c, value in enumerate(row)
                      if value == 'datos tecnicos electricos'), None)
    if not signature or not any('especificaciones de motores' in row for row in keys):
        return {}
    header, left = signature
    locations = {}
    for field, (_, label, offset) in FIELDS.items():
        if field == 'ficha':
            candidates = ((r, c) for r in range(header) for c in range(left + 1, len(keys[r])))
        elif field in ('fecha', 'sector'):
            candidates = ((r, left) for r in range(header))
        else:
            candidates = ((r, left) for r in range(header + 1, min(header + 25, len(keys))))
        location = next(((r, c + offset) for r, c in candidates if keys[r][c] == label and c + offset < len(rows[r])), None)
        if location is None:
            return {}
        locations[field] = location
    values = {}
    for field, (r, c) in locations.items():
        cell = rows[r][c]
        values[field] = formatted_value(cell.value, getattr(cell, 'number_format', None) or 'General', epoch)[:MAX_VALUE]
    return values


def effective_descriptions(button, reader, *, final=False):
    stored = button.descriptions
    if stored is not None and not any(item.get('field') for item in stored):
        return stored
    workbook, sheet = button.workbook_id, button.sheet_name
    if not final and button.source_workbook_id and button.source_sheet:
        source = reader.document(button.source_workbook_id)
        if source.revision != button.source_revision or not sheet:
            workbook, sheet = source.pk, button.source_sheet
    values = reader.read(workbook, sheet)
    if stored is None:
        if not values:
            return None
        return [{'id': field, 'key': label, 'value': values[field], 'field': field, 'excel_value': values[field]}
                for field, (label, _, _) in FIELDS.items()]
    return [{**row, 'value': values[row['field']], 'excel_value': values[row['field']]}
            if row.get('field') in values and values[row['field']] != row.get('excel_value') else row.copy()
            for row in stored]


def refresh_linked_descriptions(document):
    # Record every changed cell while saving Excel, including a later change back
    # to an earlier value. A manual note remains until its own cell changes.
    with TemplateReader(document) as reader:
        for model in (PdfMarker, Motor):
            buttons = model.objects.select_for_update().filter(descriptions__isnull=False).filter(
                Q(workbook_id=document.pk) | Q(source_workbook_id=document.pk))
            changed = []
            for button in buttons:
                rows = effective_descriptions(button, reader)
                if rows != button.descriptions:
                    button.descriptions = rows
                    changed.append(button)
            model.objects.bulk_update(changed, ['descriptions'])


def clean_color(value):
    if not isinstance(value, str) or (value and not re.fullmatch(r'#[0-9a-fA-F]{6}', value)):
        raise ValidationError('Selecciona un color válido para el botón.')
    return value.lower()


def clean_rows(rows, previous=None, *, snapshot=False):
    if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        raise ValidationError(f'Puedes guardar hasta {MAX_ITEMS} descripciones por botón.')
    by_id = {row['id']: row for row in (previous or [])}
    result, ids, keys = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValidationError('La descripción no es válida.')
        key, value, identifier = row.get('key'), row.get('value'), row.get('id') or ''
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY or not isinstance(value, str) or len(value) > MAX_VALUE:
            raise ValidationError(f'Usa una clave de hasta {MAX_KEY} caracteres y un valor de hasta {MAX_VALUE}.')
        if not isinstance(identifier, str) or (identifier and not re.fullmatch(r'[a-z0-9_-]{1,64}', identifier)):
            raise ValidationError('El identificador de la descripción no es válido.')
        if identifier and identifier in ids or key.strip().casefold() in keys:
            raise ValidationError('Cada descripción debe tener una clave distinta.')
        stored = row if snapshot else by_id.get(identifier, {})
        item = {'id': identifier or uuid4().hex, 'key': key.strip(), 'value': value}
        field = stored.get('field')
        if field is not None:
            excel_value = stored.get('excel_value')
            if not isinstance(field, str) or field not in FIELDS or not isinstance(excel_value, str) or len(excel_value) > MAX_VALUE:
                raise ValidationError('El vínculo de la descripción con el Excel no es válido.')
            item.update(field=field, excel_value=excel_value)
        result.append(item)
        ids.add(item['id'])
        keys.add(item['key'].casefold())
    return result


def snapshot_details(button, reader):
    rows = effective_descriptions(button, reader, final=True) if button.show_description or button.descriptions is not None else None
    data = {'descriptions': rows} if rows is not None else {}
    if button.button_color:
        data['button_color'] = button.button_color
    if button.show_description:
        data['show_description'] = True
    return data


def imported_details(item):
    rows = item.get('descriptions')
    enabled = item.get('show_description', False)
    if type(enabled) is not bool:
        raise ValidationError('La configuración de la ficha de descripción no es válida.')
    return {'descriptions': None if rows is None else clean_rows(rows, snapshot=True),
            'button_color': clean_color(item.get('button_color', '')), 'show_description': enabled}
