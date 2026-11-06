"""Inspect a device file without changing the project until a sheet is chosen."""

import csv
from io import BytesIO, StringIO
from pathlib import Path
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from openpyxl import Workbook, load_workbook
from openpyxl.utils.exceptions import IllegalCharacterError

MAX_CSV_CELLS = 500_000


def csv_workbook(upload):
    if not 0 < upload.size <= settings.PLAN_UPLOAD_MAX_BYTES:
        raise ValidationError('El CSV debe tener contenido y pesar como máximo 20 MB.')
    payload = upload.read()
    try:
        text = payload.decode('utf-16' if payload.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = payload.decode('cp1252')
        except UnicodeDecodeError as exc:
            raise ValidationError('Guarde el CSV con codificación UTF-8 y vuelva a importarlo.') from exc
    if not text.strip() or '\x00' in text:
        raise ValidationError('El archivo no es un CSV de texto válido.')
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=',;\t').delimiter
    except csv.Error:
        delimiter = ';' if ';' in text.splitlines()[0] and ',' not in text.splitlines()[0] else ','

    name = re.sub(r'[\\/*?:\[\]]', ' ', Path(upload.name).stem).strip(" '")[:31] or 'Datos'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = name
    rows = 0
    cells = 0
    try:
        for values in csv.reader(StringIO(text, newline=''), delimiter=delimiter, strict=True):
            rows += 1
            cells += max(1, len(values))
            if rows > 1_048_576 or len(values) > 16_384 or cells > MAX_CSV_CELLS:
                raise ValidationError('El CSV supera el límite de 500.000 celdas.')
            if any(len(value) > 32767 for value in values):
                raise ValidationError('Una celda del CSV tiene más de 32.767 caracteres.')
            for column, value in enumerate(values, 1):
                if not value:
                    continue
                cell = sheet.cell(rows, column, value)
                # CSV has no types. Preserve IDs with leading zeroes and treat
                # text that begins with an equals sign as text in the workbook.
                cell.data_type = 's'
        if not rows:
            raise ValidationError('El CSV está vacío.')
    except (csv.Error, ValueError, IllegalCharacterError) as exc:
        raise ValidationError('No se pudo leer el CSV. Revise sus separadores y comillas.') from exc
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def inspect_button_file(upload, selected=None, *, preview=False, allow_default=False):
    from .views import validate_excel

    if Path(upload.name).suffix.lower() == '.csv':
        content = csv_workbook(upload)
    else:
        validate_excel(upload)
        content = upload.read()
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        sheets = [sheet.title for sheet in workbook.worksheets if sheet.sheet_state == 'visible']
        if not sheets:
            raise ValidationError('El archivo debe tener al menos una hoja visible con celdas.')
        if selected and selected not in sheets:
            raise ValidationError('La hoja elegida no está disponible en ese archivo.')
        if not preview:
            if not selected and len(sheets) != 1 and not allow_default:
                raise ValidationError('Elija la hoja que quiere incorporar al proyecto.')
            return content, selected or sheets[0]
        selected = selected or sheets[0]
        rows = [
            [str(value)[:120] if value is not None else '' for value in row]
            for row in workbook[selected].iter_rows(max_row=20, max_col=8, values_only=True)
        ]
        return {'sheets': sheets, 'source_sheet': selected, 'rows': rows}
    finally:
        workbook.close()
