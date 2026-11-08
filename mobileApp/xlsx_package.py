"""Compose XLSX sheets at the package level, preserving drawings and charts."""
from copy import deepcopy
from io import BytesIO
import posixpath
import re
import json
from uuid import UUID, uuid4
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr
from zipfile import ZipFile, ZIP_DEFLATED

from django.core.exceptions import ValidationError

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
CP = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
VT = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'


def xml(root, original=None):
    result = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if original:
        # ElementTree keeps expanded tag names but drops namespace aliases used
        # only in values such as mc:Ignorable="x14ac xr". Retain these aliases
        # so Excel can read extension data without repairing the package.
        namespaces = {}
        for _, (prefix, uri) in ET.iterparse(BytesIO(original), events=('start-ns',)):
            if prefix and prefix != 'xml':
                namespaces.setdefault(prefix, uri)
        text = result.decode('utf-8')
        start = text.index('<', text.index('?>') + 2) if '<?xml' in text else 0
        end = text.index('>', start)
        additions = ''.join(f' xmlns:{prefix}={quoteattr(uri)}' for prefix, uri in namespaces.items()
            if not re.search(r'\bxmlns:' + re.escape(prefix) + r'=', text[start:end]))
        if additions:
            position = end - 1 if text[end - 1] == '/' else end
            text = text[:position] + additions + text[position:]
        result = text.encode('utf-8')
    return result


def package(content):
    with ZipFile(BytesIO(bytes(content))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def packed(parts):
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


def relpath(part):
    return posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')


def resolve(part, target):
    return target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


def sheet_names(content):
    with ZipFile(BytesIO(bytes(content))) as archive:
        return [item.get('name') for item in ET.fromstring(archive.read('xl/workbook.xml')).find('{' + S + '}sheets')]


def read_sheet_origins(content):
    with ZipFile(BytesIO(bytes(content))) as archive:
        if 'docProps/custom.xml' not in archive.namelist():
            return {}
        for item in ET.fromstring(archive.read('docProps/custom.xml')):
            if item.get('name') == 'PlanMotorSheets' and len(item):
                try:
                    data = json.loads(item[0].text)
                    return {name: UUID(value) for name, value in data.items() if isinstance(name, str)}
                except (ValueError, TypeError, AttributeError):
                    return {}
    return {}


def write_sheet_origins(content, origins):
    if not origins and not read_sheet_origins(content):
        return bytes(content)
    parts = package(content)
    original = parts.get('docProps/custom.xml')
    root = ET.fromstring(original) if original else ET.Element('{' + CP + '}Properties')
    for item in list(root):
        if item.get('name') == 'PlanMotorSheets':
            root.remove(item)
    if origins:
        pid = max([1] + [int(item.get('pid', '1')) for item in root]) + 1
        prop = ET.SubElement(root, '{' + CP + '}property', {'name': 'PlanMotorSheets', 'pid': str(pid), 'fmtid': '{D5CDD505-2E9C-101B-9397-08002B2CF9AE}'})
        ET.SubElement(prop, '{' + VT + '}lpwstr').text = json.dumps({name: str(value) for name, value in origins.items()}, ensure_ascii=False)
    parts['docProps/custom.xml'] = xml(root, original)
    types = ET.fromstring(parts['[Content_Types].xml'])
    if not any(item.get('PartName') == '/docProps/custom.xml' for item in types):
        ET.SubElement(types, '{' + CT + '}Override', {'PartName': '/docProps/custom.xml', 'ContentType': 'application/vnd.openxmlformats-officedocument.custom-properties+xml'})
    parts['[Content_Types].xml'] = xml(types)
    relations = ET.fromstring(parts['_rels/.rels'])
    if not any(item.get('Type', '').endswith('/custom-properties') for item in relations):
        ET.SubElement(relations, '{' + P + '}Relationship', {'Id': 'planMotorOrigins' + uuid4().hex[:8], 'Type': R + '/custom-properties', 'Target': 'docProps/custom.xml'})
    parts['_rels/.rels'] = xml(relations)
    return packed(parts)


def unique_name(names, requested):
    base = re.sub(r'[\\/*?:\[\]]', '-', str(requested)).strip().strip("'")[:31] or 'Motor'
    existing = {name.casefold() for name in names}
    name, index = base, 2
    while name.casefold() in existing:
        suffix = f' ({index})'
        name = base[:31-len(suffix)] + suffix
        index += 1
    return name


def recalculate(parts):
    path = 'xl/_rels/workbook.xml.rels'
    relations = ET.fromstring(parts[path])
    for item in list(relations):
        if item.get('Type', '').endswith('/calcChain'):
            relations.remove(item)
            parts.pop(resolve('xl/workbook.xml', item.get('Target')), None)
    parts[path] = xml(relations)
    book = ET.fromstring(parts['xl/workbook.xml'])
    calc = book.find('{' + S + '}calcPr')
    if calc is None:
        calc = ET.Element('{' + S + '}calcPr')
        extension = book.find('{' + S + '}extLst')
        book.insert(list(book).index(extension) if extension is not None else len(book), calc)
    calc.set('fullCalcOnLoad', '1')
    calc.set('forceFullCalc', '1')
    parts['xl/workbook.xml'] = xml(book, parts['xl/workbook.xml'])
    types = ET.fromstring(parts['[Content_Types].xml'])
    for item in list(types):
        if item.tag.endswith('Override') and item.get('PartName').lstrip('/') not in parts:
            types.remove(item)
    parts['[Content_Types].xml'] = xml(types)


def rename_sheet(content, old, requested):
    names = sheet_names(content)
    if old not in names:
        raise ValidationError('La hoja seleccionada no existe.')
    name = unique_name([value for value in names if value != old], requested)
    return arrange_sheets(content, {old: name}, names), name


def arrange_sheets(content, renames, order):
    """Change tab names and positions without rebuilding individual sheets."""
    parts = package(content)
    book_path = 'xl/workbook.xml'
    book = ET.fromstring(parts[book_path])
    sheets = book.find('{' + S + '}sheets')
    original_order = [item.get('name') for item in sheets]
    nodes = dict(zip(original_order, list(sheets)))
    if len(order) != len(nodes) or set(order) != set(nodes):
        raise ValidationError('El orden del Excel no coincide con sus hojas.')
    if not renames and order == original_order:
        return bytes(content)
    quotes = {"'" + old.replace("'", "''") + "'" : new for old, new in renames.items()}
    unquoted = {old: new for old, new in renames.items() if re.fullmatch(r'[\w.]+', old)}
    references = {value.casefold(): name for value, name in {**quotes, **unquoted}.items()}
    pattern = (re.compile(r'"(?:[^"]|"")*"|(?<![\w.\]])(' + '|'.join(map(re.escape, references)) + r')!', re.IGNORECASE)
               if references else None)

    def rewrite(value):
        if not pattern or not value:
            return value
        def substitute(match):
            name = references.get(match[1].casefold()) if match[1] else None
            return "'" + name.replace("'", "''") + "'!" if name else match[0]
        return pattern.sub(substitute, value)

    for part, original in list(parts.items()):
        if not part.endswith('.xml') or not (part == book_path or (renames and part.startswith(('xl/worksheets/', 'xl/charts/', 'xl/tables/')))):
            continue
        root, changed = (book if part == book_path else ET.fromstring(original)), False
        for item in root.iter():
            local = item.tag.split('}')[-1]
            if local in ('f', 'formula', 'formula1', 'formula2', 'definedName', 'calculatedColumnFormula', 'totalsRowFormula') and item.text:
                text = rewrite(item.text)
                if text != item.text:
                    item.text, changed = text, True
            if local == 'hyperlink' and item.get('location'):
                location = rewrite(item.get('location'))
                if location != item.get('location'):
                    item.set('location', location)
                    changed = True
        if changed and part != book_path:
            parts[part] = xml(root, original)
    for old in order:
        nodes[old].set('name', renames.get(old, old))
    sheets[:] = [nodes[old] for old in order]
    positions = {name: index for index, name in enumerate(order)}
    definitions = book.find('{' + S + '}definedNames')
    if definitions is not None:
        for definition in definitions:
            if definition.get('localSheetId') is not None:
                index = int(definition.get('localSheetId'))
                if 0 <= index < len(original_order):
                    definition.set('localSheetId', str(positions[original_order[index]]))
    first = next((i for i, sheet in enumerate(sheets) if sheet.get('state', 'visible') == 'visible'), 0)
    for view in book.findall('.//{' + S + '}workbookView'):
        view.set('activeTab', str(first))
        view.set('firstSheet', str(first))
    parts[book_path] = xml(book, parts[book_path])
    recalculate(parts)
    result = packed(parts)
    origins = read_sheet_origins(content) if renames else {}
    return write_sheet_origins(result, {renames.get(name, name): origin for name, origin in origins.items()}) if origins else result


def _styles(target, source):
    """Deduplicate style definitions, then remap all style indices."""
    target_root = ET.fromstring(target['xl/styles.xml'])
    source_root = ET.fromstring(source['xl/styles.xml'])
    maps = {}
    def group(root, name):
        element = root.find('{' + S + '}' + name)
        if element is None:
            element = ET.SubElement(root, '{' + S + '}' + name)
        return element
    def append_unique(name, transform=lambda value: None):
        dest, src = group(target_root, name), group(source_root, name)
        lookup = {ET.tostring(item): index for index, item in enumerate(dest)}
        mapping = {}
        for index, item in enumerate(src):
            item = deepcopy(item)
            transform(item)
            key = ET.tostring(item)
            if key not in lookup:
                lookup[key] = len(dest)
                dest.append(item)
            mapping[index] = lookup[key]
        dest.set('count', str(len(dest)))
        return mapping
    dest_formats = group(target_root, 'numFmts')
    format_codes = {item.get('formatCode'): int(item.get('numFmtId')) for item in dest_formats}
    next_id = max([163] + list(format_codes.values())) + 1
    maps['numFmtId'] = {}
    for item in group(source_root, 'numFmts'):
        code = item.get('formatCode')
        if code not in format_codes:
            copy = deepcopy(item)
            copy.set('numFmtId', str(next_id))
            dest_formats.append(copy)
            format_codes[code] = next_id
            next_id += 1
        maps['numFmtId'][int(item.get('numFmtId'))] = format_codes[code]
    dest_formats.set('count', str(len(dest_formats)))
    # Imported themes can differ. Resolve their theme colors before combining
    # styles, leaving the original target workbook's theme untouched.
    colors = []
    if 'xl/theme/theme1.xml' in source:
        theme = ET.fromstring(source['xl/theme/theme1.xml'])
        ns = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
        scheme = theme.find('.//' + ns + 'clrScheme')
        if scheme is not None:
            values = {item.tag.split('}')[-1]: next(iter(item)).get('lastClr') or next(iter(item)).get('val') for item in scheme if len(item)}
            colors = [values.get(key) for key in ('lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink')]
    def theme_colors(element):
        for color in element.iter():
            if 'theme' in color.attrib:
                index = int(color.get('theme'))
                if index < len(colors) and colors[index]:
                    color.attrib.pop('theme')
                    color.set('rgb', 'FF' + colors[index])
    for name, attribute in [('fonts', 'fontId'), ('fills', 'fillId'), ('borders', 'borderId')]:
        maps[attribute] = append_unique(name, theme_colors)
    def remap_style(item):
        for attribute in ('fontId', 'fillId', 'borderId', 'numFmtId', 'xfId'):
            if attribute in item.attrib and attribute in maps:
                value = int(item.get(attribute))
                item.set(attribute, str(maps[attribute].get(value, value)))
    maps['xfId'] = append_unique('cellStyleXfs', remap_style)
    maps['cell'] = append_unique('cellXfs', remap_style)
    maps['dxf'] = append_unique('dxfs', theme_colors)
    maps['table_style'] = {}
    styles = group(target_root, 'tableStyles')
    names = {item.get('name') for item in styles}
    for item in group(source_root, 'tableStyles'):
        copy = deepcopy(item)
        for child in copy.iter():
            if child.get('dxfId') is not None:
                child.set('dxfId', str(maps['dxf'][int(child.get('dxfId'))]))
        old = copy.get('name')
        name, index = old, 2
        while name in names:
            name = f'{old}_{index}'
            index += 1
        copy.set('name', name)
        names.add(name)
        styles.append(copy)
        maps['table_style'][old] = name
    styles.set('count', str(len(styles)))
    # Standard element order is significant to spreadsheet applications.
    order = ['numFmts', 'fonts', 'fills', 'borders', 'cellStyleXfs', 'cellXfs', 'cellStyles', 'dxfs', 'tableStyles', 'colors', 'extLst']
    target_root[:] = sorted(target_root, key=lambda item: order.index(item.tag.split('}')[-1]) if item.tag.split('}')[-1] in order else len(order))
    target['xl/styles.xml'] = xml(target_root, target['xl/styles.xml'])
    return maps


def append_sheet(content, addition, source_name, requested=None):
    target, source = package(content), package(addition)
    book = ET.fromstring(target['xl/workbook.xml'])
    sheets = book.find('{' + S + '}sheets')
    title = unique_name([item.get('name') for item in sheets], requested or source_name)
    source_book = ET.fromstring(source['xl/workbook.xml'])
    source_sheet = next((item for item in source_book.find('{' + S + '}sheets') if item.get('name') == source_name), None)
    if source_sheet is None:
        raise ValidationError('La hoja seleccionada no existe en el Excel.')
    source_rels = ET.fromstring(source['xl/_rels/workbook.xml.rels'])
    relation = next(item for item in source_rels if item.get('Id') == source_sheet.get('{' + R + '}id'))
    source_part = resolve('xl/workbook.xml', relation.get('Target'))
    style_map = _styles(target, source)
    shared = []
    if 'xl/sharedStrings.xml' in source:
        shared = list(ET.fromstring(source['xl/sharedStrings.xml']))
    source_types = ET.fromstring(source['[Content_Types].xml'])
    types = ET.fromstring(target['[Content_Types].xml'])
    overrides = {item.get('PartName').lstrip('/'): item.get('ContentType') for item in source_types if item.tag.endswith('Override')}
    defaults = {item.get('Extension'): item.get('ContentType') for item in types if item.tag.endswith('Default')}
    for item in source_types:
        if item.tag.endswith('Default') and item.get('Extension') not in defaults:
            types.append(deepcopy(item))
            defaults[item.get('Extension')] = item.get('ContentType')
    token = uuid4().hex[:12]
    existing_tables = [ET.fromstring(payload) for name, payload in target.items() if name.startswith('xl/tables/') and name.endswith('.xml')]
    table_id = max([0] + [int(item.get('id', '0')) for item in existing_tables]) + 1
    table_names = {item.get('displayName', '').casefold() for item in existing_tables}
    table_map = {}
    for name, payload in source.items():
        if name.startswith('xl/tables/') and name.endswith('.xml'):
            table = ET.fromstring(payload)
            old = table.get('displayName')
            new = old if old.casefold() not in table_names else old + '_' + token
            table_names.add(new.casefold())
            table_map[old] = (new, table_id)
            table_id += 1
    mapping = {}
    old_formula = "'" + source_name.replace("'", "''") + "'!"
    new_formula = "'" + title.replace("'", "''") + "'!"
    def clone(part):
        if part in mapping:
            return mapping[part]
        stem, ext = posixpath.splitext(posixpath.basename(part))
        new = posixpath.join(posixpath.dirname(part), f'{stem}_{token}{ext}')
        mapping[part] = new
        data = source[part]
        if ext.lower() == '.xml':
            root = ET.fromstring(data)
            if root.tag == '{' + S + '}table':
                name, identifier = table_map[root.get('displayName')]
                root.set('displayName', name)
                root.set('name', name)
                root.set('id', str(identifier))
            for item in root.iter():
                if item.tag == '{' + S + '}c':
                    if item.get('s') is not None:
                        item.set('s', str(style_map['cell'][int(item.get('s'))]))
                    if item.get('t') == 's':
                        value = item.find('{' + S + '}v')
                        string = deepcopy(shared[int(value.text)])
                        string.tag = '{' + S + '}is'
                        item.remove(value)
                        item.append(string)
                        item.set('t', 'inlineStr')
                if item.tag == '{' + S + '}row' and item.get('s') is not None:
                    item.set('s', str(style_map['cell'][int(item.get('s'))]))
                if item.tag == '{' + S + '}col' and item.get('style') is not None:
                    item.set('style', str(style_map['cell'][int(item.get('style'))]))
                for attribute, value in list(item.attrib.items()):
                    if attribute.lower().endswith('dxfid'):
                        item.set(attribute, str(style_map['dxf'][int(value)]))
                if item.tag == '{' + S + '}tableStyleInfo' and item.get('name') in style_map['table_style']:
                    item.set('name', style_map['table_style'][item.get('name')])
                if item.tag.split('}')[-1] in ('f', 'formula', 'formula1', 'formula2') and item.text:
                    item.text = item.text.replace(old_formula, new_formula)
                    item.text = re.sub(r'(?<![\w\]])' + re.escape(source_name) + r'!', lambda _: new_formula, item.text)
                    for old, (new, _) in table_map.items():
                        if old != new:
                            item.text = re.sub(r'(?<![\w.])' + re.escape(old) + r'(?=\[)', lambda _: new, item.text)
            data = xml(root, source[part])
        target[new] = data
        if part in overrides:
            ET.SubElement(types, '{' + CT + '}Override', {'PartName': '/' + new, 'ContentType': overrides[part]})
        relationships = relpath(part)
        if relationships in source:
            relations = ET.fromstring(source[relationships])
            for child in relations:
                if child.get('TargetMode') == 'External':
                    continue
                destination = resolve(part, child.get('Target'))
                if destination not in source:
                    raise ValidationError('El Excel contiene una referencia de archivo incompleta.')
                # Drawings, images, chart styles, printer settings, etc. travel
                # with the sheet. Styles/shared strings are handled separately.
                child.set('Target', posixpath.relpath(clone(destination), posixpath.dirname(new)))
            target[relpath(new)] = xml(relations)
        return new
    new_part = clone(source_part)
    relations = ET.fromstring(target['xl/_rels/workbook.xml.rels'])
    ids = {item.get('Id') for item in relations}
    number = 1
    while f'rId{number}' in ids:
        number += 1
    relation_id = f'rId{number}'
    ET.SubElement(relations, '{' + P + '}Relationship', {'Id': relation_id, 'Type': R + '/worksheet', 'Target': posixpath.relpath(new_part, 'xl')})
    next_id = max([0] + [int(item.get('sheetId')) for item in sheets]) + 1
    ET.SubElement(sheets, '{' + S + '}sheet', {'name': title, 'sheetId': str(next_id), '{' + R + '}id': relation_id})
    # Print areas and titles are worksheet-local defined names.
    definitions = source_book.find('{' + S + '}definedNames')
    source_index = list(source_book.find('{' + S + '}sheets')).index(source_sheet)
    if definitions is not None:
        dest = book.find('{' + S + '}definedNames')
        if dest is None:
            dest = ET.Element('{' + S + '}definedNames')
            index = list(book).index(book.find('{' + S + '}calcPr')) if book.find('{' + S + '}calcPr') is not None else len(book)
            book.insert(index, dest)
        for item in definitions:
            if item.get('localSheetId') == str(source_index):
                item = deepcopy(item)
                item.set('localSheetId', str(len(sheets) - 1))
                if item.text:
                    item.text = item.text.replace(old_formula, new_formula)
                dest.append(item)
    target['xl/workbook.xml'] = xml(book, target['xl/workbook.xml'])
    target['xl/_rels/workbook.xml.rels'] = xml(relations)
    target['[Content_Types].xml'] = xml(types)
    recalculate(target)
    return packed(target), title


def remove_sheet(content, name):
    parts = package(content)
    book = ET.fromstring(parts['xl/workbook.xml'])
    sheets = book.find('{' + S + '}sheets')
    selected = next((item for item in sheets if item.get('name') == name), None)
    if selected is None or len(sheets) == 1:
        raise ValidationError('El libro debe conservar al menos una hoja.')
    index = list(sheets).index(selected)
    relation_id = selected.get('{' + R + '}id')
    relations = ET.fromstring(parts['xl/_rels/workbook.xml.rels'])
    sheets.remove(selected)
    for item in list(relations):
        if item.get('Id') == relation_id:
            relations.remove(item)
    names = book.find('{' + S + '}definedNames')
    if names is not None:
        for item in list(names):
            local = item.get('localSheetId')
            if local == str(index):
                names.remove(item)
            elif local is not None and int(local) > index:
                item.set('localSheetId', str(int(local) - 1))
    for view in book.findall('.//{' + S + '}workbookView'):
        view.set('activeTab', '0')
        view.set('firstSheet', '0')
    sheets[0].set('state', 'visible')
    parts['xl/workbook.xml'] = xml(book, parts['xl/workbook.xml'])
    parts['xl/_rels/workbook.xml.rels'] = xml(relations)
    recalculate(parts)
    # Remove parts no longer reachable from the package root; this includes
    # the removed sheet's image/chart data and stale content-type entries.
    keep, pending = {'[Content_Types].xml'}, ['']
    while pending:
        part = pending.pop()
        if part:
            if part in keep:
                continue
            keep.add(part)
        path = relpath(part) if part else '_rels/.rels'
        if path in parts:
            keep.add(path)
            for item in ET.fromstring(parts[path]):
                if item.get('TargetMode') != 'External':
                    dest = resolve(part, item.get('Target'))
                    if dest in parts:
                        pending.append(dest)
    types = ET.fromstring(parts['[Content_Types].xml'])
    for item in list(types):
        if item.tag.endswith('Override') and item.get('PartName').lstrip('/') not in keep:
            types.remove(item)
    parts['[Content_Types].xml'] = xml(types)
    return packed({name: payload for name, payload in parts.items() if name in keep})
