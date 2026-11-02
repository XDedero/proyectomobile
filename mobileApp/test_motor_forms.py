"""Motor forms exercise geometry and DrawingML omitted by simple table tests."""

import base64
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from django.test import SimpleTestCase
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.drawing.image import Image
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from PIL import Image as PillowImage

from .workbook_service import _column_pixels, document_sheets, edited_document, read_cells


def motor_form_workbook():
    book = Workbook()
    book._fonts[0] = Font(name='Arial', size=10)
    book.active.title = 'Listado'
    book.active['B2'] = 'Listado de motores'
    for number in range(1, 5):
        sheet = book.create_sheet(str(number))
        sheet.sheet_format.defaultRowHeight = 13.2
        widths = [2.33203125, 14.5546875, 10.6640625, 11.6640625, 12.6640625, 11, 8.88671875, 13.5546875, 16, 18.88671875, 18.5546875]
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[chr(64+index)].width = width
        sheet.column_dimensions['L'].min = 12
        sheet.column_dimensions['L'].max = 256
        sheet.column_dimensions['L'].width = 11.5546875
        for row in range(6, 75):
            for column in range(2, 12):
                sheet.cell(row, column).fill = PatternFill('solid', fgColor='FFFFFF')
            sheet.cell(row, 2).border = Border(left=Side(style='medium'))
            sheet.cell(row, 11).border = Border(right=Side(style='medium'))
        for column in range(2, 12):
            sheet.cell(74, column).border = Border(bottom=Side(style='medium'))
        for bounds, label in [('B6:K9', 'FICHA DE IDENTIFICACIÓN DE EQUIPOS'), ('B13:K14', 'ESPECIFICACIONES DE MOTORES'), ('B15:G16', 'DATOS TÉCNICOS ELÉCTRICOS'), ('H15:I16', 'DATOS TÉCNICOS MECÁNICOS'), ('J15:K16', 'ACCIONAMIENTOS'), ('B33:K34', 'MEDICIONES'), ('B73:K73', 'Ficha del motor')]:
            sheet.merge_cells(bounds)
            cell = sheet[bounds.split(':')[0]]
            cell.value = label
            cell.font = Font(name='Arial', size=10, bold=True, color='0000FF')
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = Border(left=Side(style='medium'), right=Side(style='medium'), top=Side(style='medium'), bottom=Side(style='medium'))
        sheet['B18'] = 'Marca'
        sheet['B64'] = 'OBSERVACIONES'
        sheet.row_dimensions[9].height = 24.75
        for row in (67, 68):
            sheet.row_dimensions[row].height = 12.75
            sheet.row_dimensions[row].hidden = True
        for index, value in enumerate((4, 8, 12), 3):
            sheet.cell(39, index, value)
        for data_row, end_column, start, end in [
            (39, 5, AnchorMarker(col=5, row=35, colOff=381000), AnchorMarker(col=9, row=44, colOff=419100)),
            (62, 8, AnchorMarker(col=8, row=50, colOff=266700, rowOff=91440), AnchorMarker(col=10, row=59, colOff=998220)),
        ]:
            chart = LineChart()
            chart.add_data(Reference(sheet, min_col=3, max_col=end_column, min_row=data_row, max_row=data_row + (5 if data_row == 39 else 1)))
            chart.layout = Layout(manualLayout=ManualLayout(x=.1, y=.14, w=.66, h=.62))
            chart.anchor = TwoCellAnchor(_from=start, to=end)
            sheet.add_chart(chart)
        picture = BytesIO()
        PillowImage.new('RGB', (24, 14), '#003366').save(picture, format='PNG')
        picture.seek(0)
        sheet.add_image(Image(picture), 'B6')
    stream = BytesIO()
    book.save(stream)
    book.close()
    # The legacy plot gradient is intentionally read from the original package,
    # rather than round-tripping it through openpyxl's lossy chart reader.
    output = BytesIO()
    with ZipFile(BytesIO(stream.getvalue())) as source, ZipFile(output, 'w') as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename.startswith('xl/charts/chart'):
                payload = payload.replace(b'</plotArea>', b'<spPr><a:gradFill xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:gsLst><a:gs pos="0"><a:srgbClr val="C0C0C0"/></a:gs><a:gs pos="100000"><a:srgbClr val="FFFFFF"/></a:gs></a:gsLst><a:lin ang="5400000"/></a:gradFill></spPr></plotArea>')
            target.writestr(item, payload)
    return output.getvalue()


class MotorFormTests(SimpleTestCase):
    def test_ooxml_width_padding_is_not_added_twice(self):
        self.assertEqual(_column_pixels(8.7109375), 61)
        self.assertEqual(_column_pixels(14.5546875), 102)
        self.assertEqual(_column_pixels(10), 70)
        workbook = Workbook()
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        self.assertEqual(document_sheets(stream.getvalue())[0]['layout']['default_column_width'], 64)

    def test_numbered_forms_have_real_bounds_independent_of_unused_column_styles(self):
        sheets = document_sheets(motor_form_workbook())
        self.assertEqual(sheets[0]['layout']['presentation'], 'grid')
        for sheet in sheets[1:]:
            self.assertEqual(sheet['layout']['presentation'], 'form')
            self.assertEqual(sheet['layout']['content_bounds'], {'min_row': 6, 'max_row': 74, 'min_col': 2, 'max_col': 11})
            self.assertEqual(len(sheet['layout']['images']), 1)
            self.assertEqual(len(sheet['layout']['charts']), 2)

    def test_original_chart_gradient_manual_layout_legend_and_anchors_are_visible(self):
        layout = document_sheets(motor_form_workbook())[1]['layout']
        chart = layout['charts'][0]
        root = ET.fromstring(base64.b64decode(chart['src'].split(',')[1]))
        namespace = {'s': 'http://www.w3.org/2000/svg'}
        self.assertEqual(len(root.findall('.//s:linearGradient', namespace)), 1)
        self.assertEqual({stop.get('stop-color') for stop in root.findall('.//s:stop', namespace)}, {'#C0C0C0', '#FFFFFF'})
        text = ''.join(root.itertext())
        self.assertIn('Series1', text)
        self.assertIn('Series3', text)
        self.assertEqual(chart['x'], sum(layout['column_widths'][str(index)] for index in range(1, 6)) + 40)
        self.assertGreater(chart['width'], 300)
        # Single measurements with no markers do not fabricate lines through
        # empty measurements, matching the two charts in the reference form.
        self.assertEqual(len(root.findall('.//s:polyline', namespace)), 0)

    def test_editing_measurements_updates_chart_and_preserves_original_drawing_xml(self):
        original = motor_form_workbook()
        updated, sheets = edited_document(original, [{'sheet': '1', 'row': 40, 'col': 3, 'value': 7}], [])
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(updated)) as after:
            for part in before.namelist():
                if not part.startswith('xl/worksheets/sheet'):
                    self.assertEqual(before.read(part), after.read(part), part)
        chart = sheets[1]['layout']['charts'][0]
        root = ET.fromstring(base64.b64decode(chart['src'].split(',')[1]))
        self.assertEqual(len(root.findall('.//{http://www.w3.org/2000/svg}polyline')), 1)

    def test_shrink_to_fit_and_custom_palette_are_preserved_for_the_renderer(self):
        from openpyxl.styles.colors import Color
        book = Workbook()
        book.active['A1'] = 'Motor con texto largo'
        book.active['A1'].alignment = Alignment(shrink_to_fit=True)
        book.active['A1'].font = Font(color=Color(indexed=8))
        colors = list(book._colors)
        colors[8] = '00123456'
        book._colors = colors
        stream = BytesIO()
        book.save(stream)
        book.close()
        cell = read_cells(stream.getvalue(), '', 1, 1, 1, 1)['cells'][0]
        self.assertTrue(cell['style']['shrinkToFit'])
        self.assertEqual(cell['style']['color'], '#123456')
