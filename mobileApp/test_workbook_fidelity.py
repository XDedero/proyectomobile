from datetime import date
from io import BytesIO
import re
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from PIL import Image as PillowImage

from .workbook_service import document_sheets, edited_document, formatted_value, read_cells


def styled_workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = 'Ficha'
    sheet.merge_cells('A1:D1')
    sheet['A1'] = 'FICHA DEL MOTOR'
    sheet['A1'].font = Font(name='Arial', size=18, bold=True, color='FFFFFF')
    sheet['A1'].fill = PatternFill('solid', fgColor='107C41')
    sheet['A1'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    sheet['A1'].border = Border(bottom=Side(style='double', color='112233'))
    sheet.row_dimensions[1].height = 36
    sheet.column_dimensions['A'].width = 28
    sheet.row_dimensions[7].hidden = True
    sheet.column_dimensions['D'].hidden = True
    sheet.sheet_view.showGridLines = False
    sheet['B2'] = 1234.5
    sheet['B2'].number_format = '#,##0.00'
    sheet['C2'] = '=B2*2'
    sheet['B3'] = .125
    sheet['B3'].number_format = '0.0%'
    sheet['B4'] = date(2026, 10, 31)
    sheet['B4'].number_format = 'dd/mm/yyyy'
    sheet['C5'].fill = PatternFill('solid', fgColor='FFFF00')
    pixels = BytesIO()
    PillowImage.new('RGB', (18, 12), 'green').save(pixels, format='PNG')
    pixels.seek(0)
    sheet.add_image(Image(pixels), 'F2')
    book.create_sheet('Otra')['A1'] = 'Conservar'
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class WorkbookFidelityTests(SimpleTestCase):
    def test_layout_includes_original_sizes_merges_hidden_dimensions_and_image(self):
        sheet = document_sheets(styled_workbook())[0]
        layout = sheet['layout']
        self.assertEqual(layout['row_heights']['1'], 48)
        self.assertGreater(layout['column_widths']['1'], layout['default_column_width'])
        self.assertEqual(layout['hidden_rows'], [7])
        self.assertEqual(layout['hidden_columns'], [4])
        self.assertEqual(layout['merges'], [{'min_row': 1, 'max_row': 1, 'min_col': 1, 'max_col': 4}])
        self.assertFalse(layout['show_gridlines'])
        self.assertEqual(layout['default_style']['color'], '#000000')
        self.assertEqual(len(layout['images']), 1)
        image = layout['images'][0]
        self.assertTrue(image['src'].startswith('data:image/png;base64,'))
        self.assertEqual((image['width'], image['height']), (18, 12))

    def test_cells_include_original_font_fill_border_alignment_and_formatted_values(self):
        result = read_cells(styled_workbook(), 'Ficha', 1, 1, 5, 4)
        cells = {(cell['row'], cell['col']): cell for cell in result['cells']}
        title = cells[1, 1]['style']
        self.assertEqual(title['fontFamily'], 'Arial')
        self.assertEqual(title['fontSize'], '24px')
        self.assertEqual(title['fontWeight'], 'bold')
        self.assertEqual(title['color'], '#FFFFFF')
        self.assertEqual(title['backgroundColor'], '#107C41')
        self.assertEqual(title['textAlign'], 'center')
        self.assertEqual(title['borderBottom'], '3px double #112233')
        self.assertEqual(cells[2, 2]['display'], '1,234.50')
        self.assertEqual(cells[3, 2]['display'], '12.5%')
        self.assertEqual(cells[4, 2]['display'], '31/10/2026')
        self.assertIsNone(cells[5, 3]['value'])
        self.assertEqual(cells[5, 3]['style']['backgroundColor'], '#FFFF00')

    def test_formula_display_uses_excel_cached_result_with_raw_formula_for_editing(self):
        original = styled_workbook()
        stream = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(stream, 'w') as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == 'xl/worksheets/sheet1.xml':
                    payload = re.sub(rb'<f>B2\*2</f><v\s*/>', b'<f>B2*2</f><v>2469</v>', payload)
                target.writestr(info, payload)
        cell = read_cells(stream.getvalue(), 'Ficha', 2, 3, 1, 1)['cells'][0]
        self.assertEqual(cell['value'], '=B2*2')
        self.assertEqual(cell['display'], '2469')
        self.assertTrue(cell['formula_cached'])

    def test_formula_without_cached_result_shows_formula_and_calculation_notice(self):
        content = styled_workbook()
        cell = read_cells(content, 'Ficha', 2, 3, 1, 1)['cells'][0]
        self.assertEqual(cell['display'], '=B2*2')
        self.assertFalse(cell['formula_cached'])
        layout = document_sheets(content)[0]['layout']
        self.assertTrue(layout['has_formulas'])
        self.assertIn('no se recalculan aquí', layout['calculation_notice'])

    def test_value_save_preserves_every_unmodified_zip_part_and_other_cells(self):
        original = styled_workbook()
        # Include a part unknown to openpyxl; ordinary saves must retain it.
        stream = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(stream, 'w') as target:
            for info in source.infolist():
                target.writestr(info, source.read(info.filename))
            target.writestr('xl/drawings/original-custom-shape.xml', b'<shape preserve="exactly"/>')
        original = stream.getvalue()
        updated, metadata = edited_document(original, [{'sheet': 'Ficha', 'row': 2, 'col': 2, 'value': 2222.75}], [])
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(updated)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != 'xl/worksheets/sheet1.xml':
                    self.assertEqual(before.read(name), after.read(name), name)
            old_cell = re.search(rb'<c r="A1".*?</c>', before.read('xl/worksheets/sheet1.xml'))[0]
            self.assertIn(old_cell, after.read('xl/worksheets/sheet1.xml'))
        workbook = load_workbook(BytesIO(updated))
        self.assertEqual(workbook['Ficha']['B2'].value, 2222.75)
        self.assertEqual(workbook['Ficha']['B2'].number_format, '#,##0.00')
        self.assertEqual(workbook['Ficha']['C2'].value, '=B2*2')
        self.assertEqual(workbook['Otra']['A1'].value, 'Conservar')
        self.assertEqual(len(metadata[0]['layout']['images']), 1)
        workbook.close()

    def test_lossless_save_can_create_typed_cells_and_rows_without_losing_layout(self):
        updated, metadata = edited_document(styled_workbook(), [
            {'sheet': 'Ficha', 'row': 400, 'col': 31, 'value': True},
            {'sheet': 'Ficha', 'row': 400, 'col': 2, 'value': '<Motor & bomba>'},
            {'sheet': 'Ficha', 'row': 300, 'col': 1, 'value': '=B2+5'},
            {'sheet': 'Ficha', 'row': 2, 'col': 2, 'value': None},
        ], [])
        workbook = load_workbook(BytesIO(updated))
        self.assertTrue(workbook['Ficha']['AE400'].value)
        self.assertEqual(workbook['Ficha']['B400'].value, '<Motor & bomba>')
        self.assertEqual(workbook['Ficha']['A300'].value, '=B2+5')
        self.assertIsNone(workbook['Ficha']['B2'].value)
        self.assertEqual(workbook['Ficha']['B2'].number_format, '#,##0.00')
        self.assertEqual(metadata[0]['rows'], 400)
        self.assertEqual(metadata[0]['columns'], 31)
        workbook.close()

    def test_merged_cell_only_allows_editing_its_anchor(self):
        with self.assertRaises(ValidationError):
            edited_document(styled_workbook(), [{'sheet': 'Ficha', 'row': 1, 'col': 2, 'value': 'x'}], [])

    def test_number_formats_and_text_literals(self):
        for value, pattern, expected in [
            (12.5, '"$"#,##0.00', '$12.50'),
            (-12.5, '#,##0.00;[Red](#,##0.00)', '(12.50)'),
            (4, '00000', '00004'),
            (.5, '0.##', '0.5'),
            (0, '0;-0;"Sin datos"', 'Sin datos'),
            (1250000, '0.0,," M"', '1.3 M'),
        ]:
            with self.subTest(pattern=pattern):
                self.assertEqual(formatted_value(value, pattern), expected)

    def test_append_and_interior_insert_keep_original_drawings_and_parts(self):
        original = styled_workbook()
        for operations in [
            [{'sheet': 'Ficha', 'type': 'insert_row', 'index': 8}, {'sheet': 'Ficha', 'type': 'insert_column', 'index': 5}],
            [{'sheet': 'Ficha', 'type': 'insert_row', 'index': 2}, {'sheet': 'Ficha', 'type': 'insert_column', 'index': 2}],
        ]:
            with self.subTest(operations=operations):
                updated, metadata = edited_document(original, [], operations)
                with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(updated)) as after:
                    for name in before.namelist():
                        if name not in ('xl/worksheets/sheet1.xml', 'xl/styles.xml'):
                            self.assertEqual(before.read(name), after.read(name), name)
                workbook = load_workbook(BytesIO(updated))
                self.assertEqual(len(workbook['Ficha']._images), 1)
                self.assertEqual(len(metadata[0]['layout']['images']), 1)
                workbook.close()

    def test_hidden_sheet_state_survives_editing_and_is_exposed_in_metadata(self):
        workbook = Workbook()
        workbook.active.title = 'Visible'
        hidden = workbook.create_sheet('Reservada')
        hidden.sheet_state = 'veryHidden'
        hidden['A1'] = 'Oculto en el original'
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        updated, sheets = edited_document(stream.getvalue(), [{'sheet': 'Visible', 'row': 1, 'col': 1, 'value': 'Cambio'}], [])
        self.assertEqual(sheets[1]['state'], 'veryHidden')
        reopened = load_workbook(BytesIO(updated))
        self.assertEqual(reopened['Reservada'].sheet_state, 'veryHidden')
        reopened.close()

    def test_editing_shared_formula_master_keeps_other_shared_formulas_valid(self):
        workbook = Workbook()
        workbook.active['A1'] = 3
        workbook.active['B1'] = '=A1*2'
        workbook.active['B2'] = '=A2*2'
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        shared = BytesIO()
        with ZipFile(BytesIO(stream.getvalue())) as source, ZipFile(shared, 'w') as target:
            for info in source.infolist():
                payload = source.read(info.filename)
                if info.filename == 'xl/worksheets/sheet1.xml':
                    payload = payload.replace(b'<f>A1*2</f>', b'<f t="shared" ref="B1:B2" si="0">A1*2</f>')
                    payload = payload.replace(b'<f>A2*2</f>', b'<f t="shared" si="0"/>')
                target.writestr(info, payload)
        updated, sheets = edited_document(shared.getvalue(), [{'sheet': 'Sheet', 'row': 1, 'col': 2, 'value': 99}], [])
        reopened = load_workbook(BytesIO(updated))
        self.assertEqual(reopened.active['B1'].value, 99)
        self.assertEqual(reopened.active['B2'].value, '=A2*2')
        reopened.close()
