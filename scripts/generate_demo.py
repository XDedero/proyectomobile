"""Create a small, real DXF with three MOTOR INSERTs and two sample workbooks."""

from pathlib import Path

import ezdxf
from openpyxl import Workbook


def generate(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new('R2010')
    doc.units = ezdxf.units.MM
    doc.layers.new('EQUIPOS', dxfattribs={'color': 3})
    doc.layers.new('TUBERIAS', dxfattribs={'color': 4})
    doc.layers.new('TEXTOS', dxfattribs={'color': 7})
    for name in ['MOTOR_BOMBA_01', 'MOTOR_BOMBA_02', 'MOTOR_VENTILADOR']:
        block = doc.blocks.new(name)
        block.add_circle((0, 0), 7)
        block.add_line((-10, 0), (-7, 0))
        block.add_line((7, 0), (10, 0))
        block.add_lwpolyline([(-3, -3), (-3, 3), (0, -1), (3, 3), (3, -3)])
        block.add_lwpolyline([(-5, -8), (5, -8), (5, -9), (-5, -9)], close=True)
    assembly = doc.blocks.new('CONJUNTO_VENTILACION')
    assembly.add_blockref('MOTOR_VENTILADOR', (5, 5), dxfattribs={'layer': 'EQUIPOS'})
    model = doc.modelspace()
    model.add_lwpolyline([(0, 0), (220, 0), (220, 120), (0, 120)], close=True)
    model.add_text('SALA DE MOTORES - PLANO DEMO', dxfattribs={
        'height': 4, 'insert': (8, 108), 'layer': 'TEXTOS',
    })
    for x in [75, 145]:
        model.add_line((x, 0), (x, 96))
    model.add_line((0, 96), (220, 96))
    model.add_lwpolyline([(8, 30), (30, 30), (30, 70), (110, 70), (175, 70), (175, 30), (210, 30)], dxfattribs={'layer': 'TUBERIAS'})
    model.add_blockref('MOTOR_BOMBA_01', (30, 30), dxfattribs={'layer': 'EQUIPOS'})
    model.add_blockref('MOTOR_BOMBA_02', (110, 70), dxfattribs={'layer': 'EQUIPOS', 'rotation': 30})
    model.add_blockref('CONJUNTO_VENTILACION', (170, 25))
    for label, position in [('BOMBA 01', (15, 13)), ('BOMBA 02', (93, 53)), ('VENTILADOR', (155, 13))]:
        model.add_text(label, dxfattribs={'height': 3, 'insert': position, 'layer': 'TEXTOS'})
    doc.saveas(directory / 'motores_demo.dxf')
    for index, name in enumerate(['motor_01.xlsx', 'motor_02.xlsx'], start=1):
        workbook = Workbook()
        workbook.active.title = 'Mantenimiento'
        workbook.active.append(['Motor', 'Potencia kW', 'Estado'])
        workbook.active.append([f'MOTOR_BOMBA_0{index}', 15 * index, 'Operativo'])
        workbook.save(directory / name)
        workbook.close()


if __name__ == '__main__':
    generate(Path(__file__).resolve().parent.parent / 'examples')
    print('Created examples/motores_demo.dxf, motor_01.xlsx and motor_02.xlsx')
