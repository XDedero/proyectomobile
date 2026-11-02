import io
from xml.etree import ElementTree

import ezdxf
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from ezdxf.enums import TextEntityAlignment

from .dxf_service import find_motors, import_dxf


def drawing_upload(doc, name='plano.dxf'):
    stream = io.StringIO()
    doc.write(stream)
    return SimpleUploadedFile(name, stream.getvalue().encode('utf-8'))


class MotorLabelDetectionTests(SimpleTestCase):
    def test_text_and_formatted_mtext_create_distinct_motors(self):
        doc = ezdxf.new()
        doc.modelspace().add_text('Motor 01', dxfattribs={'insert': (10, 20, 3)})
        doc.modelspace().add_mtext(r'{\C1;motor_02}', dxfattribs={'insert': (70, 80, 5)})
        motors = find_motors(doc)
        self.assertEqual([m['name'] for m in motors], ['Motor 01', 'motor_02'])
        self.assertEqual([(m['x'], m['y'], m['z']) for m in motors], [(10, 20, 3), (70, 80, 5)])
        self.assertEqual(len({m['entity_key'] for m in motors}), 2)

    def test_non_motor_words_and_uninserted_block_labels_are_ignored(self):
        doc = ezdxf.new()
        doc.modelspace().add_text('automotor')
        doc.modelspace().add_text('motorizado')
        doc.blocks.new('SIN_USAR').add_text('MOTOR 99')
        self.assertEqual(find_motors(doc), [])

    def test_aligned_text_uses_real_alignment_point_and_ocs(self):
        doc = ezdxf.new()
        text = doc.modelspace().add_text('MOTOR 1', dxfattribs={'extrusion': (0, 0, -1)})
        text.set_placement((2, 3, 4), align=TextEntityAlignment.MIDDLE_CENTER)
        motor, = find_motors(doc)
        self.assertEqual((motor['x'], motor['y'], motor['z']), (-2, 3, -4))

    def test_nested_label_respects_rotation_scale_and_block_basepoint(self):
        doc = ezdxf.new()
        assembly = doc.blocks.new('CONJUNTO', base_point=(2, 3))
        assembly.add_text('MOTOR 1', dxfattribs={'insert': (5, 7)})
        doc.modelspace().add_blockref('CONJUNTO', (100, 200), dxfattribs={
            'rotation': 90, 'xscale': 2, 'yscale': 3,
        })
        motor, = find_motors(doc)
        self.assertAlmostEqual(motor['x'], 88)
        self.assertAlmostEqual(motor['y'], 206)

    def test_generic_block_attributes_identify_motor_at_insertion(self):
        doc = ezdxf.new()
        doc.blocks.new('EQUIPO').add_circle((0, 0), 2)
        assembly = doc.blocks.new('CONJUNTO')
        insert = assembly.add_blockref('EQUIPO', (10, 20))
        insert.add_attrib('DESCRIPCION', 'Motor bomba', (13, 23))
        doc.modelspace().add_blockref('CONJUNTO', (100, 200), dxfattribs={'rotation': 90})
        motor, = find_motors(doc)
        self.assertEqual(motor['name'], 'Motor bomba')
        self.assertAlmostEqual(motor['x'], 80)
        self.assertAlmostEqual(motor['y'], 210)

    def test_attribute_tag_can_identify_equipment(self):
        doc = ezdxf.new()
        doc.blocks.new('EQUIPO').add_circle((0, 0), 2)
        insert = doc.modelspace().add_blockref('EQUIPO', (10, 20))
        insert.add_attrib('MOTOR', 'M-01', (13, 23))
        motor, = find_motors(doc)
        self.assertEqual((motor['x'], motor['y']), (10, 20))
        self.assertEqual(motor['name'], 'M-01')

    def test_motor_block_own_labels_do_not_duplicate_marker(self):
        doc = ezdxf.new()
        block = doc.blocks.new('MOTOR_BOMBA')
        block.add_circle((0, 0), 2)
        block.add_text('MOTOR 01', dxfattribs={'insert': (3, 0)})
        insert = doc.modelspace().add_blockref('MOTOR_BOMBA', (10, 20))
        insert.add_attrib('DESCRIPCION', 'Motor bomba', (13, 20))
        motor, = find_motors(doc)
        self.assertEqual((motor['x'], motor['y']), (10, 20))

    def test_generic_block_with_several_labels_and_array_gets_every_motor(self):
        doc = ezdxf.new()
        block = doc.blocks.new('CONJUNTO')
        block.add_text('MOTOR 01', dxfattribs={'insert': (2, 3)})
        block.add_mtext('MOTOR 02', dxfattribs={'insert': (5, 7)})
        doc.modelspace().add_blockref('CONJUNTO', (10, 20), dxfattribs={
            'row_count': 2, 'row_spacing': 50,
        })
        motors = find_motors(doc)
        self.assertEqual({(m['x'], m['y']) for m in motors}, {(12, 23), (15, 27), (12, 73), (15, 77)})
        self.assertEqual(len({m['entity_key'] for m in motors}), 4)

    def test_nested_named_motor_retains_its_own_marker(self):
        doc = ezdxf.new()
        doc.blocks.new('MOTOR_HIJO').add_circle((0, 0), 1)
        block = doc.blocks.new('MOTOR_CONJUNTO')
        block.add_text('MOTOR conjunto')
        block.add_blockref('MOTOR_HIJO', (5, 7))
        doc.modelspace().add_blockref('MOTOR_CONJUNTO', (10, 20))
        self.assertEqual([(m['x'], m['y']) for m in find_motors(doc)], [(10, 20), (15, 27)])


class DxfOnlyImportTests(SimpleTestCase):
    def test_dwg_is_rejected_even_when_it_contains_valid_dxf(self):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (10, 20))
        for name in ('plano.dwg', 'PLANO.DWG'):
            with self.subTest(name=name), self.assertRaisesMessage(ValidationError, 'Solo se admiten archivos .dxf.'):
                import_dxf(drawing_upload(doc, name))

    def test_dwg_binary_renamed_to_dxf_is_rejected(self):
        upload = SimpleUploadedFile('falso.dxf', b'AC1032' + b'\x00' * 100)
        with self.assertRaisesMessage(ValidationError, 'DXF válido'):
            import_dxf(upload)

    def test_text_markers_and_world_transform_match_the_rendered_svg(self):
        doc = ezdxf.new()
        doc.modelspace().add_text('MOTOR 01', dxfattribs={'insert': (10, 20), 'height': 2})
        doc.modelspace().add_mtext('Motor 02', dxfattribs={'insert': (40, 50), 'char_height': 2})
        svg, motors = import_dxf(drawing_upload(doc, 'PLANO.DXF'))
        root = ElementTree.fromstring(svg)
        a, b, c, d, e, f = map(float, root.attrib['data-wcs-transform'].split())
        self.assertEqual(len(motors), 2)
        for motor in motors:
            self.assertAlmostEqual(motor['svg_x'], a * motor['x'] + c * motor['y'] + e)
            self.assertAlmostEqual(motor['svg_y'], b * motor['x'] + d * motor['y'] + f)
            self.assertGreater(motor['svg_bounds'][2], 0)
            self.assertGreater(motor['svg_bounds'][3], 0)
