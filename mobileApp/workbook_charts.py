"""Render worksheet line charts from their original DrawingML, without saving it.

Reading the original XML matters: openpyxl's chart reader discards plot-area
fills and some manual layout information in older equipment workbooks.
"""

import base64
from io import BytesIO
import math
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl.utils.cell import range_to_tuple

NS = {
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'xdr': 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
}
SVG = 'http://www.w3.org/2000/svg'
PALETTE = ['#000080', '#FF00FF', '#FFFF00', '#00FFFF', '#800080', '#800000']


def relationships(archive, part):
    path = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    if path not in archive.namelist():
        return {}
    result = {}
    for relation in ET.fromstring(archive.read(path)):
        if relation.get('TargetMode') == 'External':
            continue
        target = relation.get('Target', '')
        result[relation.get('Id')] = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
    return result


def number(element, path, default=0, attribute='val'):
    node = element.find(path, NS) if element is not None else None
    try:
        value = float(node.get(attribute) if attribute else node.text)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError, AttributeError):
        return default


def color(element, default='#000000'):
    if element is None:
        return default
    for tag in ('srgbClr', 'sysClr'):
        node = element.find('.//a:' + tag, NS)
        if node is not None:
            value = node.get('lastClr') or node.get('val', '')
            if re.fullmatch(r'[0-9a-fA-F]{6}', value):
                return '#' + value
    return default


def axis_offset(layout, axis, index):
    default = layout['default_column_width' if axis == 'col' else 'default_row_height']
    sizes = layout['column_widths' if axis == 'col' else 'row_heights']
    hidden = set(layout['hidden_columns' if axis == 'col' else 'hidden_rows'])
    # Drawing markers are zero based. Sparse dimensions also work for distant
    # anchors without iterating a million rows.
    total = default * index
    for key, size in sizes.items():
        if 1 <= int(key) <= index:
            total += (0 if int(key) in hidden else size) - default
    for key in hidden:
        if 1 <= key <= index and str(key) not in sizes:
            total -= default
    return total


def anchor_rect(anchor, layout):
    start = anchor.find('xdr:from', NS)
    if start is None:
        position = anchor.find('xdr:pos', NS)
        if position is None:
            return None
        x, y = float(position.get('x', 0)) / 9525, float(position.get('y', 0)) / 9525
    else:
        x = axis_offset(layout, 'col', int(number(start, 'xdr:col', attribute=None))) + number(start, 'xdr:colOff', attribute=None) / 9525
        y = axis_offset(layout, 'row', int(number(start, 'xdr:row', attribute=None))) + number(start, 'xdr:rowOff', attribute=None) / 9525
    end = anchor.find('xdr:to', NS)
    extent = anchor.find('xdr:ext', NS)
    if end is not None:
        width = axis_offset(layout, 'col', int(number(end, 'xdr:col', attribute=None))) + number(end, 'xdr:colOff', attribute=None) / 9525 - x
        height = axis_offset(layout, 'row', int(number(end, 'xdr:row', attribute=None))) + number(end, 'xdr:rowOff', attribute=None) / 9525 - y
    elif extent is not None:
        width, height = float(extent.get('cx', 0)) / 9525, float(extent.get('cy', 0)) / 9525
    else:
        return None
    if not all(math.isfinite(v) and 0 <= v <= 1e9 for v in (x, y, width, height)) or width == 0 or height == 0:
        return None
    return {'x': x, 'y': y, 'width': width, 'height': height}


def reference_values(source, workbook, *, numeric=False):
    if source is None:
        return []
    cache = source.find('.//c:numCache', NS)
    if cache is None:
        cache = source.find('.//c:strCache', NS)
    if cache is None:
        cache = source.find('c:numLit', NS)
    count = min(10000, max(0, int(number(cache, 'c:ptCount'))))
    values = [None] * count
    if cache is not None:
        for point in cache.findall('c:pt', NS):
            index = int(point.get('idx', '-1'))
            if 0 <= index < count:
                values[index] = point.findtext('c:v', default='', namespaces=NS)
    formula = source.findtext('.//c:f', default='', namespaces=NS)
    if formula:
        try:
            sheet_name, (left, top, right, bottom) = range_to_tuple(formula)
            if sheet_name in workbook.sheetnames and (right - left + 1) * (bottom - top + 1) <= 10000:
                source_values = [cell.value for row in workbook[sheet_name].iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right) for cell in row]
                if len(source_values) > len(values):
                    values.extend([None] * (len(source_values) - len(values)))
                for index, value in enumerate(source_values):
                    if not (isinstance(value, str) and value.startswith('=')):
                        values[index] = value
        except (ValueError, TypeError, KeyError):
            pass
    if numeric:
        for index, value in enumerate(values):
            try:
                converted = float(value) if value is not None and value != '' and not isinstance(value, bool) else None
                values[index] = converted if converted is not None and math.isfinite(converted) else None
            except (TypeError, ValueError):
                values[index] = None
    return values


def svg_node(parent, tag, text=None, **attributes):
    node = ET.SubElement(parent, '{' + SVG + '}' + tag, {key.replace('_', '-'): f'{value:g}' if isinstance(value, float) else str(value) for key, value in attributes.items()})
    if text is not None:
        node.text = str(text)
    return node


def paint(root, shape, identifier, default='#FFFFFF'):
    if shape is None:
        return default
    if shape.find('a:noFill', NS) is not None:
        return 'none'
    gradient = shape.find('a:gradFill', NS)
    if gradient is None:
        return color(shape.find('a:solidFill', NS), default)
    defs = svg_node(root, 'defs')
    fill = svg_node(defs, 'linearGradient', id=identifier, x1='0%', y1='0%', x2='0%', y2='100%')
    for stop in gradient.findall('a:gsLst/a:gs', NS):
        svg_node(fill, 'stop', offset=f'{float(stop.get("pos", 0)) / 1000:g}%', stop_color=color(stop, default))
    return f'url(#{identifier})'


def line_style(shape, default='#000000', width=.5):
    line = shape.find('a:ln', NS) if shape is not None else None
    if line is None:
        return {'stroke': default, 'stroke_width': width}
    return {'stroke': 'none' if line.find('a:noFill', NS) is not None else color(line, default), 'stroke_width': float(line.get('w', width * 9525)) / 9525}


def manual_rect(element, width, height, fallback):
    manual = element.find('c:layout/c:manualLayout', NS) if element is not None else None
    if manual is None:
        return fallback
    return tuple(number(manual, 'c:' + key, default) * extent for key, default, extent in zip(('x', 'y', 'w', 'h'), (fallback[0]/width, fallback[1]/height, fallback[2]/width, fallback[3]/height), (width, height, width, height)))


def axis_limits(axis, values):
    if values:
        low, high = min(0, min(values)), max(0, max(values))
        span = max(high - low, abs(high) * .2, .001)
        rough = span / 6
        power = 10 ** math.floor(math.log10(rough))
        step = next((factor * power for factor in (1, 2, 2.5, 5, 10) if factor * power >= rough), power * 10)
        low = math.floor(low / step) * step
        high = (math.floor(high / step) + 1) * step
    else:
        low, high, step = 0, 1.2, .2
    low = number(axis, 'c:scaling/c:min', low)
    high = number(axis, 'c:scaling/c:max', high)
    step = number(axis, 'c:majorUnit', step)
    if high <= low or step <= 0:
        return 0, 1.2, .2
    return low, high, max(step, (high-low) / 100)


def render_line_chart(chart_root, workbook, width, height):
    plot = chart_root.find('c:chart/c:plotArea', NS)
    chart = plot.find('c:lineChart', NS) if plot is not None else None
    if chart is None:
        return None
    root = ET.Element('{' + SVG + '}svg', {'viewBox': f'0 0 {width:g} {height:g}', 'width': f'{width:g}', 'height': f'{height:g}'})
    svg_node(root, 'title', 'Gráfico del libro Excel')
    outer_style = chart_root.find('c:spPr', NS)
    svg_node(root, 'rect', x=.5, y=.5, width=max(0, width-1), height=max(0, height-1), fill=paint(root, outer_style, 'outer-fill'), **line_style(outer_style))
    series = []
    categories = []
    for index, source in enumerate(chart.findall('c:ser', NS)):
        values = reference_values(source.find('c:val', NS), workbook, numeric=True)
        cat_values = reference_values(source.find('c:cat', NS), workbook)
        if len(cat_values) > len(categories):
            categories = cat_values
        title = source.findtext('c:tx/c:v', namespaces=NS)
        if not title:
            titles = reference_values(source.find('c:tx', NS), workbook)
            title = next((str(item) for item in titles if item is not None), f'Series{index + 1}')
        style = line_style(source.find('c:spPr', NS), PALETTE[index % len(PALETTE)], 1.3)
        marker = source.find('c:marker/c:symbol', NS)
        series.append({'name': title, 'values': values, 'style': style, 'marker': marker.get('val') if marker is not None else 'none'})
    count = max([len(categories), 1] + [len(item['values']) for item in series])
    if not categories:
        categories = list(range(1, count+1))
    left, top, plot_width, plot_height = manual_rect(plot, width, height, (width*.12, height*.14, width*.62, height*.62))
    plot_style = plot.find('c:spPr', NS)
    svg_node(root, 'rect', x=left, y=top, width=plot_width, height=plot_height, fill=paint(root, plot_style, 'plot-fill', '#C0C0C0'), **line_style(plot_style, '#808080', 1))
    axis = plot.find('c:valAx', NS)
    low, high, step = axis_limits(axis, [value for item in series for value in item['values'] if value is not None])
    font_size = number(axis, 'c:txPr/a:p/a:pPr/a:defRPr', 800, 'sz') / 100 * 96 / 72
    font = 'Arial'
    axis_font = axis.find('c:txPr/a:p/a:pPr/a:defRPr/a:latin', NS) if axis is not None else None
    if axis_font is not None:
        font = axis_font.get('typeface', font)
    grid_style = line_style(axis.find('c:majorGridlines/c:spPr', NS) if axis is not None else None)
    for index in range(min(101, int(round((high-low) / step)) + 1)):
        value = low + index * step
        y = top + plot_height * (1-(value-low)/(high-low))
        svg_node(root, 'line', x1=left, y1=y, x2=left+plot_width, y2=y, **grid_style)
        svg_node(root, 'text', f'{value:.10g}'.replace('.', ','), x=left-6, y=y+font_size*.32, text_anchor='end', font_family=font, font_size=font_size, fill='#000000')
    for index, category in enumerate(categories):
        x = left + plot_width * (index+.5) / count
        svg_node(root, 'text', category if category is not None else '', x=x, y=top+plot_height+font_size*1.6, text_anchor='middle', font_family=font, font_size=font_size, fill='#000000')
    # Gaps remain gaps. A series with one value and marker="none" is invisible
    # in Excel too, rather than a fabricated line to a zero-valued blank cell.
    defs = svg_node(root, 'defs')
    clip = svg_node(defs, 'clipPath', id='plot-clip')
    svg_node(clip, 'rect', x=left, y=top, width=plot_width, height=plot_height)
    data = svg_node(root, 'g', clip_path='url(#plot-clip)')
    for item in series:
        segment = []
        for index, value in enumerate(item['values'] + [None]):
            if value is None:
                if len(segment) >= 2:
                    svg_node(data, 'polyline', points=' '.join(f'{x:g},{y:g}' for x, y in segment), fill='none', **item['style'])
                segment = []
                continue
            x = left + plot_width * (index+.5) / count
            y = top + plot_height * (1-(value-low)/(high-low))
            segment.append((x, y))
            if item['marker'] != 'none':
                svg_node(data, 'circle', cx=x, cy=y, r=2.5, fill=item['style']['stroke'])
    legend = chart_root.find('c:chart/c:legend', NS)
    if legend is not None and series:
        x, y, w, h = manual_rect(legend, width, height, (width*.78, height*.25, width*.21, height*.5))
        legend_style = legend.find('c:spPr', NS)
        svg_node(root, 'rect', x=x, y=y, width=w, height=h, fill=paint(root, legend_style, 'legend-fill'), **line_style(legend_style))
        size = number(legend, 'c:txPr/a:p/a:pPr/a:defRPr', 800, 'sz') / 100 * 96 / 72
        line_height = min(size*1.5, h / len(series))
        padding = max(0, (h-line_height*len(series))/2)
        for index, item in enumerate(series):
            center_y = y + padding + line_height * (index+.5)
            svg_node(root, 'line', x1=x+4, y1=center_y, x2=x+min(20, w*.27), y2=center_y, **item['style'])
            svg_node(root, 'text', item['name'], x=x+min(24, w*.32), y=center_y+size*.32, font_family=font, font_size=size, fill='#000000')
    return ET.tostring(root, encoding='utf-8')


def add_chart_layouts(content, workbook, sheets):
    with ZipFile(BytesIO(bytes(content))) as archive:
        parts = relationships(archive, 'xl/workbook.xml')
        metadata = {sheet['name']: sheet['layout'] for sheet in sheets}
        workbook_xml = ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in workbook_xml.findall('s:sheets/s:sheet', NS):
            layout = metadata.get(sheet.get('name'))
            part = parts.get(sheet.get('{' + NS['r'] + '}id'))
            if layout is None or not part:
                continue
            targets = relationships(archive, part)
            for drawing in ET.fromstring(archive.read(part)).findall('s:drawing', NS):
                path = targets.get(drawing.get('{' + NS['r'] + '}id'))
                if not path:
                    continue
                charts = relationships(archive, path)
                for anchor in ET.fromstring(archive.read(path)):
                    reference = anchor.find('.//c:chart', NS)
                    if reference is None:
                        continue
                    chart_part = charts.get(reference.get('{' + NS['r'] + '}id'))
                    rect = anchor_rect(anchor, layout)
                    if not chart_part or not rect:
                        continue
                    rendered = render_line_chart(ET.fromstring(archive.read(chart_part)), workbook, rect['width'], rect['height'])
                    if rendered:
                        layout['charts'].append({**rect, 'src': 'data:image/svg+xml;base64,' + base64.b64encode(rendered).decode('ascii'), 'description': 'Gráfico de mediciones del Excel original'})
