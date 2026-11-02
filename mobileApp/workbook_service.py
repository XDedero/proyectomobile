"""Validated, lossless XLSX access for the configurable PDF module."""

from datetime import date, datetime, time, timedelta
from copy import copy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import base64
import colorsys
from fractions import Fraction
from functools import lru_cache
from io import BytesIO
import math
import posixpath
import re
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.styles.numbers import is_date_format
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from openpyxl.utils.datetime import from_excel

MAX_ROWS = 1_048_576
MAX_COLUMNS = 16_384
MAX_WINDOW_ROWS = 100
MAX_WINDOW_COLUMNS = 50


def positive_integer(value, name, maximum=None):
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        raise ValidationError(f'{name} debe ser un entero válido mayor que cero.')
    return value


def load_document(content, *, read_only=True, data_only=False):
    try:
        return load_workbook(BytesIO(bytes(content)), read_only=read_only, data_only=data_only, keep_links=True)
    except Exception as exc:
        raise ValidationError('No se pudo leer el libro Excel guardado.') from exc


def sheet_dimensions(sheet):
    rows = max(sheet.max_row or 1, max(sheet.row_dimensions, default=1) if hasattr(sheet, 'row_dimensions') else 1)
    columns = sheet.max_column or 1
    if hasattr(sheet, 'column_dimensions'):
        from openpyxl.utils.cell import column_index_from_string
        for key, dimension in sheet.column_dimensions.items():
            columns = max(columns, dimension.max or column_index_from_string(key))
    return {'name': sheet.title, 'rows': min(rows, MAX_ROWS), 'columns': min(columns, MAX_COLUMNS)}


def document_sheets(content):
    workbook = load_document(content, read_only=False)
    try:
        sheets = [sheet_metadata(sheet, workbook) for sheet in workbook.worksheets]
        from .workbook_charts import add_chart_layouts
        add_chart_layouts(content, workbook, sheets)
        return sheets
    finally:
        workbook.close()


def _theme_colors(workbook):
    if hasattr(workbook, '_mobile_theme_colors'):
        return workbook._mobile_theme_colors
    if not workbook.loaded_theme:
        return []
    try:
        root = ET.fromstring(workbook.loaded_theme)
        scheme = root.find('.//{http://schemas.openxmlformats.org/drawingml/2006/main}clrScheme')
        colors = {entry.tag.rsplit('}', 1)[-1]: next(iter(entry)).get('lastClr') or next(iter(entry)).get('val') for entry in scheme}
        # Spreadsheet theme indices differ from clrScheme element order.
        workbook._mobile_theme_colors = [colors.get(name) for name in ('lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink')]
        return workbook._mobile_theme_colors
    except (ET.ParseError, AttributeError, TypeError, StopIteration):
        return []


def _color(color, workbook, fallback=None):
    if color is None:
        return fallback
    value = None
    if color.type == 'rgb':
        value = color.rgb[-6:]
    elif color.type == 'indexed' and 0 <= color.indexed < len(COLOR_INDEX):
        palette = workbook._colors or COLOR_INDEX
        value = palette[color.indexed][-6:] if color.indexed < len(palette) else COLOR_INDEX[color.indexed][-6:]
    elif color.type == 'theme':
        theme = _theme_colors(workbook)
        if 0 <= color.theme < len(theme):
            value = theme[color.theme]
    if not value or not re.fullmatch(r'[0-9a-fA-F]{6}', value):
        return fallback
    if color.tint:
        red, green, blue = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        hue, light, saturation = colorsys.rgb_to_hls(red, green, blue)
        light = light * (1 + color.tint) if color.tint < 0 else light * (1 - color.tint) + color.tint
        value = ''.join(f'{round(channel * 255):02x}' for channel in colorsys.hls_to_rgb(hue, light, saturation))
    return '#' + value


def _font_style(font, workbook):
    result = {}
    if font.name:
        result['fontFamily'] = font.name
    if font.sz:
        result['fontSize'] = f'{font.sz * 96 / 72:g}px'
    if font.bold:
        result['fontWeight'] = 'bold'
    if font.italic:
        result['fontStyle'] = 'italic'
    decorations = []
    if font.underline:
        decorations.append('underline')
    if font.strike:
        decorations.append('line-through')
    if decorations:
        result['textDecoration'] = ' '.join(decorations)
    color = _color(font.color, workbook)
    if color:
        result['color'] = color
    return result


def cell_style(cell, workbook):
    result = _font_style(cell.font, workbook)
    if cell.fill.patternType == 'solid':
        color = _color(cell.fill.fgColor, workbook)
        if color:
            result['backgroundColor'] = color
    alignment = cell.alignment
    if alignment.horizontal in ('left', 'center', 'right', 'justify'):
        result['textAlign'] = alignment.horizontal
    elif alignment.horizontal in ('centerContinuous', 'distributed'):
        result['textAlign'] = 'center'
    if alignment.vertical:
        result['verticalAlign'] = {'center': 'middle', 'distributed': 'middle', 'justify': 'middle'}.get(alignment.vertical, alignment.vertical)
    if alignment.wrap_text:
        result.update(whiteSpace='pre-wrap', overflowWrap='break-word')
    else:
        result['whiteSpace'] = 'pre'
    if alignment.indent:
        result['textIndent'] = f'{alignment.indent * 3}ch'
    if alignment.shrink_to_fit:
        result['shrinkToFit'] = True
    if alignment.textRotation:
        result['textRotation'] = alignment.textRotation
    border_styles = {
        'hair': ('1px', 'solid'), 'thin': ('1px', 'solid'), 'medium': ('2px', 'solid'),
        'thick': ('3px', 'solid'), 'double': ('3px', 'double'), 'dotted': ('1px', 'dotted'),
        'dashed': ('1px', 'dashed'), 'dashDot': ('1px', 'dashed'),
        'dashDotDot': ('1px', 'dashed'), 'mediumDashed': ('2px', 'dashed'),
        'mediumDashDot': ('2px', 'dashed'), 'mediumDashDotDot': ('2px', 'dashed'),
        'slantDashDot': ('2px', 'dashed'),
    }
    for side in ('top', 'right', 'bottom', 'left'):
        edge = getattr(cell.border, side)
        if edge and edge.style:
            width, style = border_styles.get(edge.style, ('1px', 'solid'))
            result['border' + side.title()] = f'{width} {style} {_color(edge.color, workbook, "#000000")}'
    return result


@lru_cache(maxsize=64)
def _digit_width(name, size, bold=False, italic=False):
    """Measure the workbook's Normal font, rather than assuming Calibri 11."""
    from PIL import ImageFont
    families = {'arial': 'arial', 'calibri': 'calibri', 'times new roman': 'times', 'courier new': 'cour', 'tahoma': 'tahoma', 'verdana': 'verdana'}
    filename = families.get((name or '').casefold(), (name or 'calibri').replace(' ', ''))
    suffix = 'bi' if bold and italic else 'bd' if bold else 'i' if italic else ''
    try:
        font = ImageFont.truetype(filename + suffix + '.ttf', max(1, round(size * 96 / 72)))
        return max(1, round(max(font.getlength(digit) for digit in '0123456789')))
    except OSError:
        return max(1, round(7 * size / (10 if filename == 'arial' else 11)))


def _column_pixels(width, digit_width=7):
    # ISO/IEC 29500 col@width already includes the five-pixel padding. Adding
    # it again shifts every section and each anchored drawing in a form.
    # https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.column
    return max(1, int(((256 * width + int(128 / digit_width)) / 256) * digit_width))


def sheet_metadata(sheet, workbook):
    metadata = sheet_dimensions(sheet)
    normal = workbook._fonts[0]
    digit_width = _digit_width(normal.name, normal.sz or 11, normal.bold, normal.italic)
    default_row = (sheet.sheet_format.defaultRowHeight or 15) * 96 / 72
    # With no explicit defaultColWidth, 8.43 is the UI character count, not
    # col@width. It needs padding once (explicit XML widths already have it).
    default_width = sheet.sheet_format.defaultColWidth
    default_col = _column_pixels(default_width if default_width is not None else 8.43 + 5 / digit_width, digit_width)
    row_heights = {str(index): dimension.height * 96 / 72 for index, dimension in sheet.row_dimensions.items() if dimension.height is not None}
    column_widths = {}
    hidden_columns = []
    for key, dimension in sheet.column_dimensions.items():
        first = dimension.min or column_index_from_string(key)
        last = dimension.max or first
        for index in range(first, min(last, MAX_COLUMNS) + 1):
            if dimension.width is not None:
                column_widths[str(index)] = _column_pixels(dimension.width, digit_width)
            if dimension.hidden:
                hidden_columns.append(index)
    hidden_rows = [index for index, dimension in sheet.row_dimensions.items() if dimension.hidden]
    merges = [dict(min_row=merged.min_row, max_row=merged.max_row, min_col=merged.min_col, max_col=merged.max_col) for merged in sheet.merged_cells.ranges]
    layout = {
        'default_row_height': default_row, 'default_column_width': default_col,
        'row_heights': row_heights, 'column_widths': column_widths,
        'hidden_rows': hidden_rows, 'hidden_columns': hidden_columns,
        'merges': merges, 'show_gridlines': sheet.sheet_view.showGridLines is not False,
        'default_style': _font_style(workbook._fonts[0], workbook), 'images': [],
        'charts': [], 'zoom_scale': (sheet.sheet_view.zoomScale or 100) / 100,
    }
    bordered = [cell for cell in sheet._cells.values() if any(getattr(cell.border, side) and getattr(cell.border, side).style for side in ('left', 'right', 'top', 'bottom'))]
    form = bool(re.fullmatch(r'(?:(?:motor|ficha)[ _-]*)?\d+', sheet.title.strip(), re.I) and len(merges) >= 5 and len(bordered) >= 20)
    meaningful = bordered if form else [cell for cell in sheet._cells.values() if cell.value is not None or cell.fill.patternType == 'solid']
    if meaningful:
        layout['content_bounds'] = {
            'min_row': min(cell.row for cell in meaningful), 'max_row': max(cell.row for cell in meaningful),
            'min_col': min(cell.column for cell in meaningful), 'max_col': max(cell.column for cell in meaningful),
        }
        # A logical table column may span several physical Excel columns.
        # Include the full merged extent, especially Sector (F:H) in Listado.
        for merge in merges:
            bounds = layout['content_bounds']
            bounds['min_row'] = min(bounds['min_row'], merge['min_row'])
            bounds['max_row'] = max(bounds['max_row'], merge['max_row'])
            bounds['min_col'] = min(bounds['min_col'], merge['min_col'])
            bounds['max_col'] = max(bounds['max_col'], merge['max_col'])
    layout['presentation'] = 'form' if form else 'grid'
    if any(cell.data_type == 'f' for cell in sheet._cells.values()):
        layout['has_formulas'] = True
        layout['calculation_notice'] = (
            'Las fórmulas muestran el último resultado guardado en Excel y no se recalculan aquí. '
            'Después de editar, ese resultado puede estar desactualizado; descargue y recalcule el libro en Excel.'
        )
    for image in sheet._images:
        try:
            anchor = image.anchor
            marker = anchor._from
            x = sum(0 if index in hidden_columns else column_widths.get(str(index), default_col) for index in range(1, marker.col + 1)) + marker.colOff / 9525
            y = sum(0 if index in hidden_rows else row_heights.get(str(index), default_row) for index in range(1, marker.row + 1)) + marker.rowOff / 9525
            width, height = image.width, image.height
            if hasattr(anchor, 'ext'):
                width, height = anchor.ext.cx / 9525, anchor.ext.cy / 9525
            elif hasattr(anchor, 'to'):
                end = anchor.to
                width = sum(0 if index in hidden_columns else column_widths.get(str(index), default_col) for index in range(1, end.col + 1)) + end.colOff / 9525 - x
                height = sum(0 if index in hidden_rows else row_heights.get(str(index), default_row) for index in range(1, end.row + 1)) + end.rowOff / 9525 - y
            payload = image._data()
            layout['images'].append({'src': f'data:image/{image.format};base64,' + base64.b64encode(payload).decode('ascii'), 'x': x, 'y': y, 'width': width, 'height': height})
        except (AttributeError, OSError, ValueError, TypeError):
            # Unsupported drawing types remain intact in the original XLSX.
            continue
    metadata['layout'] = layout
    if sheet.sheet_state != 'visible':
        metadata['state'] = sheet.sheet_state
    return metadata


def cell_value(value):
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is not None and type(value) not in (str, int, float, bool):
        return getattr(value, 'text', None) or str(value)
    return value


def _format_date(value, number_format):
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        if '[h]' in number_format.lower():
            return f'{total // 3600}:{abs(total) % 3600 // 60:02}:{abs(total) % 60:02}'
        return str(value)
    if not isinstance(value, (date, datetime, time)):
        return str(value)
    pattern = re.sub(r'\[([^hms\]]|[^\]]{2,})\]', '', number_format, flags=re.I)
    tokens = re.findall(r'"[^"]*"|\\.|AM/PM|A/P|\[[hms]+\]|[ymdhs]+|.', pattern, flags=re.I)
    output = []
    twelve_hour = bool(re.search(r'AM/PM|A/P', pattern, flags=re.I))
    for index, token in enumerate(tokens):
        lower = token.lower()
        if token.startswith('"'):
            output.append(token[1:-1])
        elif token.startswith('\\'):
            output.append(token[1:])
        elif lower in ('am/pm', 'a/p'):
            mark = 'AM' if getattr(value, 'hour', 0) < 12 else 'PM'
            output.append(mark if lower == 'am/pm' else mark[0])
        elif lower.startswith('y'):
            year = getattr(value, 'year', 1900)
            output.append(f'{year % 100:02}' if len(token) <= 2 else f'{year:04}')
        elif lower.startswith('d'):
            day = getattr(value, 'day', 1)
            output.append(value.strftime('%A' if len(token) >= 4 else '%a') if len(token) >= 3 else (f'{day:02}' if len(token) == 2 else str(day)))
        elif lower.startswith('m'):
            neighbors = ''.join(tokens[max(0, index - 2):index] + tokens[index + 1:index + 3]).lower()
            minute = 'h' in neighbors or 's' in neighbors
            number = getattr(value, 'minute' if minute else 'month', 0 if minute else 1)
            if not minute and len(token) >= 3:
                output.append(value.strftime('%B' if len(token) >= 4 else '%b')[:1] if len(token) >= 5 else value.strftime('%B' if len(token) >= 4 else '%b'))
            else:
                output.append(f'{number:02}' if len(token) >= 2 else str(number))
        elif lower.startswith('h'):
            hour = getattr(value, 'hour', 0)
            hour = (hour % 12 or 12) if twelve_hour else hour
            output.append(f'{hour:02}' if len(token) >= 2 else str(hour))
        elif lower.startswith('s'):
            second = getattr(value, 'second', 0)
            output.append(f'{second:02}' if len(token) >= 2 else str(second))
        else:
            output.append(token)
    return ''.join(output)


def formatted_value(value, number_format='General', epoch=None):
    """Render the common Excel date, decimal, currency and percentage formats.

    The raw value is sent separately for editing. Formula displays use the last
    result stored by Excel, rather than replacing that result with formula text.
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (date, datetime, time, timedelta)):
        return _format_date(value, number_format if number_format != 'General' else 'yyyy-mm-dd hh:mm:ss')
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return str(value)
    if is_date_format(number_format):
        try:
            return _format_date(from_excel(value, epoch=epoch), number_format)
        except (ValueError, OverflowError, TypeError):
            return str(value)
    sections = re.split(r';(?=(?:[^"]*"[^"]*")*[^"]*$)', number_format)
    section = sections[1] if value < 0 and len(sections) > 1 else sections[2] if value == 0 and len(sections) > 2 else sections[0]
    if not section:
        return ''
    # Remove color/condition tokens and expose locale-tagged currency symbols.
    section = re.sub(r'\[\$([^\]-]*)[^\]]*\]', lambda match: '"' + match[1] + '"', section)
    section = re.sub(r'\[[^\]]*\]', '', section)
    section = re.sub(r'_.|\*.', '', section)
    if section.lower() == 'general' or not re.search(r'[0#?]', section):
        if section.lower() == 'general':
            return f'{value:g}' if isinstance(value, float) else str(value)
        return re.sub(r'"([^"]*)"|\\(.)', lambda match: match[1] if match[1] is not None else match[2], section).replace('@', str(value))
    # Quoted strings are literals, never numeric placeholders.
    literal_parts = []
    def literal(match):
        literal_parts.append(match[1] if match[1] is not None else match[2])
        return chr(0xE000 + len(literal_parts) - 1)
    pattern = re.sub(r'"([^"]*)"|\\(.)', literal, section)
    match = re.search(r'[0#?][0#?,]*(?:\.[0#?]+)?(?:[Ee][+-]?0+)?(?:\s+[0#?]+/[0#?]+)?', pattern)
    if not match:
        return str(value)
    prefix, token, suffix = pattern[:match.start()], match[0], pattern[match.end():]
    amount = abs(value) if value < 0 and len(sections) > 1 else value
    amount *= 100 ** pattern.count('%')
    scale = len(re.match(r',*', suffix)[0])
    if scale:
        amount /= 1000 ** scale
        suffix = suffix[scale:]
    if '/' in token:
        denominator_length = len(token.split('/')[-1])
        fraction = Fraction(abs(amount) % 1).limit_denominator(10 ** denominator_length - 1)
        whole = int(abs(amount))
        rendered = f'{whole} {fraction.numerator}/{fraction.denominator}' if whole else f'{fraction.numerator}/{fraction.denominator}'
        if amount < 0:
            rendered = '-' + rendered
    else:
        decimals = len(token.split('.')[1].split('E')[0].split('e')[0]) if '.' in token else 0
        if 'e' in token.lower():
            rendered = f'{amount:.{decimals}E}'
        else:
            try:
                rounded = Decimal(str(amount)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
            except InvalidOperation:
                rounded = amount
            rendered = format(rounded, f',.{decimals}f' if ',' in token else f'.{decimals}f')
            if '.' in token:
                optional = len(token.split('.')[1]) - len(token.split('.')[1].rstrip('#?'))
                while optional and rendered.endswith('0'):
                    rendered = rendered[:-1]
                    optional -= 1
                rendered = rendered.rstrip('.')
            zeros = len(token.split('.')[0].replace(',', '').replace('#', '').replace('?', ''))
            if zeros > 1 and ',' not in token:
                sign = '-' if rendered.startswith('-') else ''
                rendered = sign + rendered.lstrip('-').zfill(zeros + (decimals + 1 if decimals else 0))
    result = prefix + rendered + suffix
    for index, text in enumerate(literal_parts):
        result = result.replace(chr(0xE000 + index), text)
    return result


def read_cells(content, sheet_name, row, col, rows, cols):
    positive_integer(row, 'Fila', MAX_ROWS)
    positive_integer(col, 'Columna', MAX_COLUMNS)
    positive_integer(rows, 'Cantidad de filas', MAX_WINDOW_ROWS)
    positive_integer(cols, 'Cantidad de columnas', MAX_WINDOW_COLUMNS)
    if row + rows - 1 > MAX_ROWS or col + cols - 1 > MAX_COLUMNS:
        raise ValidationError('El rango solicitado supera las dimensiones de Excel.')
    workbook = load_document(content, read_only=False)
    cached = None
    cached_values = {}
    try:
        sheet_name = sheet_name or workbook.sheetnames[0]
        if sheet_name not in workbook.sheetnames:
            raise ValidationError('La hoja solicitada no existe.')
        sheet = workbook[sheet_name]
        cells = []
        for row_index, values in enumerate(sheet.iter_rows(
            min_row=row, max_row=row + rows - 1,
            min_col=col, max_col=col + cols - 1,
        ), start=row):
            for column_index, cell in enumerate(values, start=col):
                value = cell.value
                if value is not None or cell.has_style:
                    display_value = value
                    if cell.data_type == 'f':
                        if cached is None:
                            cached = load_document(content, data_only=True)
                            cached_values = {
                                (cached_row, cached_col): cached_value
                                for cached_row, cached_cells in enumerate(cached[sheet_name].iter_rows(
                                    min_row=row, max_row=row + rows - 1,
                                    min_col=col, max_col=col + cols - 1, values_only=True,
                                ), start=row)
                                for cached_col, cached_value in enumerate(cached_cells, start=col)
                            }
                        display_value = cached_values.get((row_index, column_index))
                    item = {'row': row_index, 'col': column_index, 'value': cell_value(value), 'display': formatted_value(display_value, cell.number_format, workbook.epoch)}
                    if cell.data_type == 'f':
                        item['formula_cached'] = display_value is not None
                        if display_value is None:
                            item['display'] = cell_value(value)
                    if cell.has_style:
                        item['style'] = cell_style(cell, workbook)
                    if isinstance(display_value, (int, float)) and not isinstance(display_value, bool):
                        item.setdefault('style', {})['textAlign'] = item.get('style', {}).get('textAlign', 'right')
                    cells.append(item)
        return {'sheet': sheet_name, 'row': row, 'col': col, 'rows': rows, 'cols': cols, 'cells': cells}
    finally:
        workbook.close()
        if cached is not None:
            cached.close()


_XML_PREFIX = r'(?:[A-Za-z_][\w.-]*:)?'
_CELL_XML = re.compile(r'<(?P<tag>' + _XML_PREFIX + r'c)\b(?P<attrs>[^>]*?)(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)', re.S)
_ROW_XML = re.compile(r'<(?P<tag>' + _XML_PREFIX + r'row)\b(?P<attrs>[^>]*?)(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)', re.S)
_FORMULA_XML = re.compile(r'<(?P<tag>' + _XML_PREFIX + r'f)\b(?P<attrs>[^>]*?)(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)', re.S)


def _attribute(attributes, name):
    found = re.search(r'\b' + re.escape(name) + r'\s*=\s*([\'"])(.*?)\1', attributes)
    return found[2] if found else None


def _value_xml(tag, attributes, old_body, value):
    prefix = tag[:-1]
    attributes = re.sub(r'\s+t\s*=\s*([\'"]).*?\1', '', attributes)
    # Retain extensions and metadata children, replacing only the cell value.
    body = re.sub(r'<' + _XML_PREFIX + r'(?:f|v|is)\b[^>]*(?:/>|>.*?</' + _XML_PREFIX + r'(?:f|v|is)\s*>)', '', old_body or '', flags=re.S)
    if value is None:
        payload = ''
    elif isinstance(value, str) and value.startswith('='):
        payload = f'<{prefix}f>{escape(value[1:])}</{prefix}f>'
    elif isinstance(value, str):
        attributes += ' t="inlineStr"'
        payload = f'<{prefix}is><{prefix}t xml:space="preserve">{escape(value)}</{prefix}t></{prefix}is>'
    elif isinstance(value, bool):
        attributes += ' t="b"'
        payload = f'<{prefix}v>{int(value)}</{prefix}v>'
    else:
        payload = f'<{prefix}v>{value}</{prefix}v>'
    if not payload and not body:
        return f'<{tag}{attributes}/>'
    return f'<{tag}{attributes}>{payload}{body}</{tag}>'


def _patch_row(match, edits, row_index, prefix):
    tag = match['tag'] if match else prefix + 'row'
    attributes = match['attrs'] if match else f' r="{row_index}"'
    body = match['body'] or '' if match else ''
    existing = {column_index_from_string(re.match(r'[A-Z]+', _attribute(cell['attrs'], 'r'))[0]): cell for cell in _CELL_XML.finditer(body) if _attribute(cell['attrs'], 'r')}
    replacements = []
    for edit in edits:
        col = edit['col']
        cell = existing.get(col)
        if cell:
            replacement = _value_xml(cell['tag'], cell['attrs'], cell['body'], edit['value'])
            replacements.append((cell.start(), cell.end(), col, replacement))
        else:
            following = [other for index, other in existing.items() if index > col]
            position = min((other.start() for other in following), default=len(body))
            replacement = _value_xml(prefix + 'c', f' r="{get_column_letter(col)}{row_index}"', '', edit['value'])
            replacements.append((position, position, col, replacement))
    # Descending column order makes multiple insertions at one offset sorted.
    for start, end, col, replacement in sorted(replacements, key=lambda item: (item[0], item[1], item[2]), reverse=True):
        body = body[:start] + replacement + body[end:]
    return f'<{tag}{attributes}>{body}</{tag}>'


def _expand_edited_shared_formulas(xml, edits, sheet):
    """Keep shared-formula dependents valid when their master is overwritten."""
    coordinates = {f'{get_column_letter(edit["col"])}{edit["row"]}' for edit in edits}
    affected = set()
    for cell in _CELL_XML.finditer(xml):
        if _attribute(cell['attrs'], 'r') not in coordinates:
            continue
        formula = _FORMULA_XML.search(cell['body'] or '')
        if formula and _attribute(formula['attrs'], 't') == 'shared' and formula['body']:
            affected.add(_attribute(formula['attrs'], 'si'))
    if not affected:
        return xml
    def expand(cell):
        body = cell['body'] or ''
        formula = _FORMULA_XML.search(body)
        if not formula or _attribute(formula['attrs'], 't') != 'shared' or _attribute(formula['attrs'], 'si') not in affected:
            return cell[0]
        value = sheet[_attribute(cell['attrs'], 'r')].value
        if not isinstance(value, str) or not value.startswith('='):
            # The overwritten master's new value is patched below.
            return cell[0]
        replacement = f'<{formula["tag"]}>{escape(value[1:])}</{formula["tag"]}>'
        body = body[:formula.start()] + replacement + body[formula.end():]
        return f'<{cell["tag"]}{cell["attrs"]}>{body}</{cell["tag"]}>'
    return _CELL_XML.sub(expand, xml)


def _patch_sheet_xml(payload, edits, sheet):
    xml = payload.decode('utf-8')
    xml = _expand_edited_shared_formulas(xml, edits, sheet)
    match = re.search(r'<(?P<tag>' + _XML_PREFIX + r'sheetData)\b[^>]*(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)', xml, re.S)
    if match is None:
        raise ValidationError('El Excel no contiene una tabla de celdas válida.')
    prefix = match['tag'][:-9]
    body = match['body'] or ''
    existing = {int(_attribute(item['attrs'], 'r')): item for item in _ROW_XML.finditer(body) if _attribute(item['attrs'], 'r')}
    by_row = {}
    for edit in edits:
        by_row.setdefault(edit['row'], []).append(edit)
    replacements = []
    for index, row_edits in by_row.items():
        row = existing.get(index)
        replacement = _patch_row(row, row_edits, index, prefix)
        if row:
            replacements.append((row.start(), row.end(), index, replacement))
        else:
            position = min((other.start() for number, other in existing.items() if number > index), default=len(body))
            replacements.append((position, position, index, replacement))
    for start, end, index, replacement in sorted(replacements, key=lambda item: (item[0], item[1], item[2]), reverse=True):
        body = body[:start] + replacement + body[end:]
    xml = xml[:match.start()] + f'<{match["tag"]}>{body}</{match["tag"]}>' + xml[match.end():]
    # Update the used range so new cells remain visible after reopening.
    dimension = sheet.calculate_dimension()
    xml = re.sub(r'(<' + _XML_PREFIX + r'dimension\b[^>]*\bref\s*=\s*)([\'"])(.*?)\2', lambda item: item[1] + quoteattr(dimension), xml, count=1)
    return xml.encode('utf-8')


def _lossless_values(content, edits, workbook):
    """Patch cell XML without resaving drawings, styles, charts or extensions.

    openpyxl cannot round-trip every OOXML drawing/extension. Copying every
    untouched ZIP member protects these original parts during ordinary edits.
    """
    output = BytesIO()
    with ZipFile(BytesIO(bytes(content))) as original:
        paths = _sheet_paths(original)
        grouped = {}
        for edit in edits:
            grouped.setdefault(edit['sheet'], {})[(edit['row'], edit['col'])] = edit
        changed = {paths[name]: _patch_sheet_xml(original.read(paths[name]), list(items.values()), workbook[name]) for name, items in grouped.items()}
        with ZipFile(output, 'w') as result:
            result.comment = original.comment
            for info in original.infolist():
                payload = changed.get(info.filename)
                result.writestr(info, original.read(info.filename) if payload is None else payload)
    return output.getvalue()


def _sheet_paths(archive):
    namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    rel_namespace = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
    book = ET.fromstring(archive.read('xl/workbook.xml'))
    targets = {item.get('Id'): item.get('Target') for item in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
    paths = {}
    for item in book.find(namespace + 'sheets'):
        target = targets[item.get(rel_namespace + 'id')]
        paths[item.get('name')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
    return paths


def _extend_styles(original, generated):
    """Append any styles materialized by merged cells, keeping original styles."""
    original_root, generated_root = ET.fromstring(original), ET.fromstring(generated)
    xml = original.decode('utf-8')
    for name in ('numFmts', 'fonts', 'fills', 'borders', 'cellStyleXfs', 'cellXfs', 'cellStyles', 'dxfs'):
        namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        before, after = original_root.find(namespace + name), generated_root.find(namespace + name)
        old_count = len(before) if before is not None else 0
        if after is None or len(after) <= old_count:
            continue
        extra = ''.join(ET.tostring(item, encoding='unicode') for item in list(after)[old_count:])
        match = re.search(r'<(?P<tag>' + _XML_PREFIX + name + r')\b(?P<attrs>[^>]*?)(?:\s*/>|>(?P<body>.*?)</(?P=tag)\s*>)', xml, re.S)
        if match:
            attributes = re.sub(r'\bcount\s*=\s*([\'"]).*?\1', f'count="{len(after)}"', match['attrs'])
            replacement = f'<{match["tag"]}{attributes}>{match["body"] or ""}{extra}</{match["tag"]}>'
            xml = xml[:match.start()] + replacement + xml[match.end():]
        else:
            # A newly needed numFmts collection belongs before fonts.
            replacement = f'<{name} xmlns="{namespace[1:-1]}" count="{len(after)}">{extra}</{name}>'
            position = re.search(r'<' + _XML_PREFIX + r'fonts\b', xml) if name == 'numFmts' else re.search(r'</' + _XML_PREFIX + r'styleSheet\s*>', xml)
            xml = xml[:position.start()] + replacement + xml[position.start():]
    return xml.encode('utf-8')


def _lossless_structural(content, operations, edits, workbook):
    """Keep the original package even for existing interior insertion actions.

    Only the affected cell tables and any newly materialized style records come
    from the temporary writer. Original drawing/extension relationships remain.
    As with openpyxl insertion, formulas and drawing anchors are not translated.
    """
    stream = BytesIO()
    workbook.save(stream)
    output = BytesIO()
    with ZipFile(BytesIO(bytes(content))) as original, ZipFile(stream) as generated:
        old_paths, new_paths = _sheet_paths(original), _sheet_paths(generated)
        changed_names = {item['sheet'] for item in operations + edits}
        changed = {}
        for name in changed_names:
            old_xml = original.read(old_paths[name]).decode('utf-8')
            new_xml = generated.read(new_paths[name]).decode('utf-8')
            clone_layout = any(item.get('sheet') == name and item.get('copy_from') is not None for item in operations)
            for tag in (('sheetData', 'dimension', 'cols', 'mergeCells') if clone_layout else ('sheetData', 'dimension')):
                pattern = r'<(?P<tag>' + _XML_PREFIX + tag + r')\b[^>]*(?:\s*/>|>.*?</(?P=tag)\s*>)'
                replacement = re.search(pattern, new_xml, re.S)
                if replacement:
                    # A source workbook may use namespace prefixes without a
                    # default namespace. The replacement declares its own.
                    fragment = re.sub(r'^(<[^\s>/]+)', r'\1 xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"', replacement[0], count=1)
                    if re.search(pattern, old_xml, re.S):
                        old_xml = re.sub(pattern, lambda match: fragment, old_xml, count=1, flags=re.S)
                    elif tag == 'cols':
                        point = re.search(r'<' + _XML_PREFIX + r'sheetData\b', old_xml)
                        if point:
                            old_xml = old_xml[:point.start()] + fragment + old_xml[point.start():]
            changed[old_paths[name]] = old_xml.encode('utf-8')
        changed['xl/styles.xml'] = _extend_styles(original.read('xl/styles.xml'), generated.read('xl/styles.xml'))
        with ZipFile(output, 'w') as target:
            target.comment = original.comment
            for info in original.infolist():
                target.writestr(info, changed.get(info.filename, original.read(info.filename)))
    return output.getvalue()


def edited_document(content, edits, operations):
    if not isinstance(edits, list) or len(edits) > 5000:
        raise ValidationError('Guarde como máximo 5.000 celdas por operación.')
    if not isinstance(operations, list) or len(operations) > 100:
        raise ValidationError('Guarde como máximo 100 inserciones de filas o columnas por operación.')
    workbook = load_document(content, read_only=False)
    try:
        can_patch = True
        appended_cells = []
        for operation in operations:
            if not isinstance(operation, dict) or operation.get('sheet') not in workbook.sheetnames:
                raise ValidationError('La operación debe indicar una hoja existente.')
            sheet = workbook[operation['sheet']]
            kind = operation.get('type')
            clone = operation.get('copy_from')
            if clone is not None:
                positive_integer(clone, 'Fila o columna de origen', MAX_ROWS if kind == 'insert_row' else MAX_COLUMNS)
                can_patch = False
            if kind == 'insert_row':
                index = positive_integer(operation.get('index'), 'Fila', MAX_ROWS)
                if (sheet.max_row or 1) >= MAX_ROWS:
                    raise ValidationError('No se pueden insertar más filas en esta hoja.')
                can_patch = can_patch and index > (sheet.max_row or 1)
                styles = [copy(sheet.cell(clone, col)._style) for col in range(1, sheet.max_column + 1)] if clone is not None else []
                merges = [(item.min_col, item.max_col) for item in sheet.merged_cells.ranges if item.min_row == clone and item.max_row == clone] if clone is not None else []
                height = sheet.row_dimensions[clone].height if clone is not None else None
                sheet.insert_rows(index)
                for col, style in enumerate(styles, 1):
                    sheet.cell(index, col)._style = style
                for first, last in merges:
                    sheet.merge_cells(start_row=index, end_row=index, start_column=first, end_column=last)
                if height is not None:
                    sheet.row_dimensions[index].height = height
                sheet.cell(index, 1, '')
                appended_cells.append({'sheet': operation['sheet'], 'row': index, 'col': 1, 'value': ''})
            elif kind == 'insert_column':
                index = positive_integer(operation.get('index'), 'Columna', MAX_COLUMNS)
                if (sheet.max_column or 1) >= MAX_COLUMNS:
                    raise ValidationError('No se pueden insertar más columnas en esta hoja.')
                can_patch = can_patch and index > (sheet.max_column or 1)
                source_cells = {}
                source_width = None
                header_merges = []
                if clone is not None:
                    through = operation.get('extend_header_through_row', 0)
                    if type(through) is not int or not 0 <= through <= MAX_ROWS:
                        raise ValidationError('La fila del encabezado no es válida.')
                    rows = {row for row, col in sheet._cells if col == clone}
                    source_start = clone
                    for row in rows:
                        merge = next((item for item in sheet.merged_cells.ranges if item.min_row <= row <= item.max_row and item.min_col <= clone <= item.max_col), None)
                        source_col = merge.min_col if merge else clone
                        if row > through:
                            source_start = min(source_start, source_col)
                        source_cells[row] = copy(sheet.cell(row, source_col)._style)
                    widths = []
                    for col in range(source_start, clone + 1):
                        dimension = next((value for key, value in sheet.column_dimensions.items() if (value.min or column_index_from_string(key)) <= col <= (value.max or value.min or column_index_from_string(key))), None)
                        widths.append(dimension.width if dimension is not None else sheet.sheet_format.defaultColWidth or 9.14)
                    source_width = sum(widths)
                    header_merges = [str(item) for item in sheet.merged_cells.ranges if item.max_col == index-1 and item.max_row <= through]
                sheet.insert_cols(index)
                for row, style in source_cells.items():
                    sheet.cell(row, index)._style = style
                if source_width is not None:
                    dimension = sheet.column_dimensions[get_column_letter(index)]
                    dimension.min = dimension.max = index
                    dimension.width = source_width
                for address in header_merges:
                    from openpyxl.worksheet.cell_range import CellRange
                    merged = CellRange(address)
                    sheet.unmerge_cells(address)
                    sheet.merge_cells(start_row=merged.min_row, end_row=merged.max_row, start_column=merged.min_col, end_column=index)
                sheet.cell(1, index, '')
                appended_cells.append({'sheet': operation['sheet'], 'row': 1, 'col': index, 'value': ''})
            else:
                raise ValidationError('La operación de filas o columnas no es válida.')
        for edit in edits:
            if not isinstance(edit, dict) or edit.get('sheet') not in workbook.sheetnames:
                raise ValidationError('Cada celda debe indicar una hoja existente.')
            if 'value' not in edit:
                raise ValidationError('Cada celda debe indicar el valor que desea guardar.')
            row = positive_integer(edit.get('row'), 'Fila', MAX_ROWS)
            col = positive_integer(edit.get('col'), 'Columna', MAX_COLUMNS)
            value = edit.get('value')
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValidationError('El valor de una celda debe ser texto, un número, un booleano o vacío.')
            if isinstance(value, float) and not math.isfinite(value):
                raise ValidationError('El número de la celda no es válido.')
            if isinstance(value, str) and (len(value) > 32767 or ILLEGAL_CHARACTERS_RE.search(value)):
                raise ValidationError('El texto contiene caracteres no válidos o supera 32.767 caracteres.')
            cell = workbook[edit['sheet']].cell(row, col)
            if cell.__class__.__name__ == 'MergedCell':
                raise ValidationError('Edite la celda superior izquierda de la combinación de celdas.')
            cell.value = value
        if can_patch:
            all_edits = appended_cells + edits
            updated = _lossless_values(content, all_edits, workbook) if all_edits else bytes(content)
        else:
            updated = _lossless_structural(content, operations, edits, workbook)
        return updated, document_sheets(updated)
    except ValidationError:
        raise
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValidationError('No se pudieron aplicar los cambios al Excel.') from exc
    finally:
        workbook.close()
