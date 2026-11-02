"""Create a real two-page PDF floor plan and an XLSX with distant cells."""

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    }))
    for number in (1, 2):
        page = writer.add_blank_page(width=900, height=600)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        drawing = DecodedStreamObject()
        drawing.set_data((
            '0.15 0.25 0.4 RG 2 w 35 35 830 530 re S '
            '35 480 m 865 480 l S 300 35 m 300 480 l S 600 35 m 600 480 l S '
            '0.2 0.55 0.75 RG 3 w 65 190 m 180 190 l 180 360 l 460 360 l 460 190 l 800 190 l S '
            '0.1 0.25 0.2 rg 145 160 70 60 re f 425 330 70 60 re f 700 160 90 60 re f '
            '0.12 0.15 0.22 rg BT /F1 23 Tf 60 520 Td '
            f'(PLANO PDF CONFIGURABLE - PAGINA {number}) Tj ET '
            'BT /F1 15 Tf 130 130 Td (EQUIPO A) Tj ET '
            'BT /F1 15 Tf 410 410 Td (EQUIPO B) Tj ET '
            'BT /F1 15 Tf 690 130 Td (EQUIPO C) Tj ET '
        ).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(drawing)
    stream = BytesIO()
    writer.write(stream)
    (directory / 'plano_pdf_configurable.pdf').write_bytes(stream.getvalue())

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Equipos'
    sheet.append(['Equipo', 'Potencia kW', 'Estado'])
    sheet.append(['BOMBA PDF 01', 15, 'Operativo'])
    sheet['B2'].font = Font(bold=True)
    sheet['D2'] = '=B2*2'
    sheet['AE350'] = 'Dato lejano de la hoja completa'
    workbook.create_sheet('Mantenimiento').append(['Fecha', 'Observacion'])
    workbook['Mantenimiento'].append(['2026-10-30', 'Inspeccion inicial'])
    workbook.save(directory / 'equipos_pdf.xlsx')
    workbook.close()


if __name__ == '__main__':
    generate(Path(__file__).resolve().parent.parent / 'examples')
    print('Created examples/plano_pdf_configurable.pdf and examples/equipos_pdf.xlsx')
