import io
import tempfile
from pathlib import Path
from xml.etree import ElementTree

import ezdxf
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from io import BytesIO

from .dxf_service import find_motors
from .models import Motor, Plan


def sample_drawing():
    doc = ezdxf.new('R2010')
    motor = doc.blocks.new('MOTOR_BOMBA')
    motor.add_circle((0, 0), 4)
    motor.add_line((-4, 0), (4, 0))
    other = doc.blocks.new('VALVULA')
    other.add_circle((0, 0), 2)
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    doc.modelspace().add_blockref('MOTOR_BOMBA', (10, 20))
    doc.modelspace().add_blockref('MOTOR_BOMBA', (70, 80))
    doc.modelspace().add_blockref('VALVULA', (50, 50))
    return doc


def dxf_file(doc=None, name='plano.dxf', binary=False):
    doc = doc if doc is not None else sample_drawing()
    stream = io.BytesIO() if binary else io.StringIO()
    doc.write(stream, fmt='bin' if binary else 'asc')
    data = stream.getvalue()
    return SimpleUploadedFile(name, data if binary else data.encode('utf-8'))


def xlsx_file(name='mantenimiento.xlsx'):
    stream = io.BytesIO()
    workbook = Workbook()
    workbook.active.title = 'Motor'
    workbook.active.append(['Potencia (kW)', 'Fecha'])
    workbook.active.append([15, '2026-10-29'])
    workbook.save(stream)
    workbook.close()
    return SimpleUploadedFile(name, stream.getvalue())


class MotorCoordinatesTests(SimpleTestCase):
    def test_insert_names_and_text_labels_identify_equipment(self):
        doc = sample_drawing()
        doc.modelspace().add_text('MOTOR TEXTO')
        doc.blocks.new('MOTOR_SIN_INSERTAR').add_circle((0, 0), 2)
        motors = find_motors(doc)
        self.assertEqual([(motor['x'], motor['y']) for motor in motors], [(10, 20), (70, 80), (0, 0)])
        self.assertEqual(len({motor['entity_key'] for motor in motors}), 3)

    def test_nested_rotation_scale_and_block_basepoint(self):
        doc = ezdxf.new()
        doc.blocks.new('motor_pequeno').add_circle((0, 0), 1)
        assembly = doc.blocks.new('CONJUNTO', base_point=(2, 3))
        assembly.add_blockref('motor_pequeno', (5, 7))
        doc.modelspace().add_blockref('CONJUNTO', (100, 200), dxfattribs={
            'rotation': 90, 'xscale': 2, 'yscale': 3,
        })
        motor, = find_motors(doc)
        self.assertAlmostEqual(motor['x'], 88)
        self.assertAlmostEqual(motor['y'], 206)

    def test_multiple_nesting_levels_and_mirrored_scale(self):
        doc = ezdxf.new()
        doc.blocks.new('MOTOR').add_circle((0, 0), 1)
        inner = doc.blocks.new('INTERIOR')
        inner.add_blockref('MOTOR', (2, 3))
        outer = doc.blocks.new('EXTERIOR')
        outer.add_blockref('INTERIOR', (10, 20), dxfattribs={'xscale': -2, 'yscale': 3})
        doc.modelspace().add_blockref('EXTERIOR', (100, 200), dxfattribs={'rotation': 90})
        motor, = find_motors(doc)
        self.assertAlmostEqual(motor['x'], 71)
        self.assertAlmostEqual(motor['y'], 206)

    def test_insert_ocs_is_converted_to_world_coordinates(self):
        doc = ezdxf.new()
        doc.blocks.new('MOTOR').add_circle((0, 0), 1)
        doc.modelspace().add_blockref('MOTOR', (2, 3, 4), dxfattribs={'extrusion': (0, 0, -1)})
        motor, = find_motors(doc)
        self.assertEqual((motor['x'], motor['y'], motor['z']), (-2, 3, -4))

    def test_multi_insert_array_has_one_motor_per_position(self):
        doc = ezdxf.new()
        doc.blocks.new('MOTOR').add_circle((0, 0), 1)
        doc.modelspace().add_blockref('MOTOR', (10, 20), dxfattribs={
            'row_count': 2, 'column_count': 2, 'row_spacing': 5, 'column_spacing': 8,
        })
        motors = find_motors(doc)
        self.assertEqual({(motor['x'], motor['y']) for motor in motors}, {(10, 20), (18, 20), (10, 25), (18, 25)})
        self.assertEqual(len({motor['entity_key'] for motor in motors}), 4)

    @override_settings(DXF_MOTOR_KEYWORD='BOMBA')
    def test_configurable_naming_convention(self):
        doc = ezdxf.new()
        doc.blocks.new('bomba_01').add_circle((0, 0), 1)
        doc.modelspace().add_blockref('bomba_01', (1, 2))
        self.assertEqual(len(find_motors(doc)), 1)

    def test_circular_block_references_are_rejected(self):
        doc = ezdxf.new()
        a = doc.blocks.new('A')
        b = doc.blocks.new('B')
        a.add_blockref('B', (0, 0))
        b.add_blockref('A', (0, 0))
        doc.modelspace().add_blockref('A', (0, 0))
        with self.assertRaisesMessage(ValidationError, 'circulares'):
            find_motors(doc)


class PlanWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('test_admin', password='test-password')

    def setUp(self):
        self.client.force_login(self.user)
        directory = tempfile.TemporaryDirectory(prefix='dxf-test-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def import_plan(self, upload=None):
        response = self.client.post(reverse('plan_import'), {'file': upload or dxf_file()})
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()

    def test_import_renders_plan_and_extracts_real_motor_coordinates(self):
        data = self.import_plan()
        self.assertEqual(data['motor_count'], 2)
        self.assertEqual([(m['x'], m['y']) for m in data['motors']], [(10, 20), (70, 80)])
        self.assertTrue(Path(Plan.objects.get(pk=data['id']).source.path).is_file())
        response = self.client.get(data['svg_url'])
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.content)
        self.assertEqual(root.tag.split('}')[-1], 'svg')
        box_x, box_y, width, height = map(float, root.attrib['viewBox'].split())
        for motor in data['motors']:
            self.assertTrue(box_x <= motor['svg_x'] <= box_x + width)
            self.assertTrue(box_y <= motor['svg_y'] <= box_y + height)
            self.assertEqual(len(motor['svg_bounds']), 4)
        # DXF Y grows upward; SVG Y grows downward.
        self.assertGreater(data['motors'][0]['svg_y'], data['motors'][1]['svg_y'])

    def test_binary_dxf_and_uppercase_extension(self):
        self.assertEqual(self.import_plan(dxf_file(name='PLANO.DXF', binary=True))['motor_count'], 2)

    def test_dwg_file_import_is_rejected_even_with_dxf_content(self):
        doc = sample_drawing()
        response = self.client.post(reverse('plan_import'), {'file': dxf_file(doc, name='plano_sala.dwg')})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Solo se admiten archivos .dxf.', response.json()['error'])
        self.assertFalse(Plan.objects.exists())

    def test_long_original_filenames_are_preserved_without_long_storage_paths(self):
        plan_name = 'plano_' + 'a' * 220 + '.dxf'
        excel_name = 'motor_' + 'b' * 220 + '.xlsx'
        plan = self.import_plan(dxf_file(name=plan_name))
        self.assertEqual(plan['name'], plan_name)
        response = self.client.post(plan['motors'][0]['assign_url'], {'file': xlsx_file(excel_name)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['motor']['sheet_name'], 'Motor')
        self.assertLessEqual(len(response.json()['motor']['excel']['name']), 255)

    def test_plan_without_motors_can_still_be_viewed(self):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (10, 0))
        data = self.import_plan(dxf_file(doc))
        self.assertEqual(data['motors'], [])
        self.assertEqual(self.client.get(data['svg_url']).status_code, 200)

    def test_rejects_wrong_extensions_and_files_renamed_dxf(self):
        for name in ['plano.bin', 'plano.pdf', 'falso.dxf']:
            with self.subTest(name=name):
                response = self.client.post(reverse('plan_import'), {'file': SimpleUploadedFile(name, b'not a DXF')})
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.json())
        self.assertEqual(Plan.objects.count(), 0)

    def test_empty_dxf_is_rejected(self):
        response = self.client.post(reverse('plan_import'), {'file': dxf_file(ezdxf.new())})
        self.assertEqual(response.status_code, 400)

    @override_settings(PLAN_UPLOAD_MAX_BYTES=100)
    def test_upload_size_limit_is_checked_on_server(self):
        response = self.client.post(reverse('plan_import'), {'file': dxf_file()})
        self.assertEqual(response.status_code, 400)

    def test_xlsx_is_persisted_to_selected_instance_and_can_be_downloaded(self):
        plan = self.import_plan()
        first, second = plan['motors']
        upload = xlsx_file()
        original_bytes = upload.read()
        upload.seek(0)
        response = self.client.post(first['assign_url'], {'file': upload})
        self.assertEqual(response.status_code, 200, response.content[:500])
        motor = response.json()['motor']
        self.assertEqual(motor['excel']['sheets'], ['Motor'])
        detail = self.client.get(plan['detail_url']).json()
        self.assertEqual(detail['motors'][0]['sheet_name'], 'Motor')
        self.assertIsNotNone(detail['motors'][1]['excel'])
        self.assertEqual(detail['motors'][1]['excel']['id'], motor['excel']['id'])
        self.assertNotEqual(detail['motors'][1]['sheet_name'], motor['sheet_name'])
        download = self.client.get(motor['excel']['url'])
        exported = load_workbook(BytesIO(b''.join(download.streaming_content)))
        try:
            self.assertEqual(exported[motor['sheet_name']]['A2'].value, 15)
            self.assertIsNone(exported[detail['motors'][1]['sheet_name']]['C18'].value)
        finally:
            exported.close()
        download.close()
        self.assertIsNone(Motor.objects.get(pk=second['id']).excel_name or None)
        self.assertEqual(self.client.get(reverse('plans')).json()['plans'][0]['id'], plan['id'])

    def test_invalid_excel_preserves_existing_assignment(self):
        plan = self.import_plan()
        motor = plan['motors'][0]
        self.assertEqual(self.client.post(motor['assign_url'], {'file': xlsx_file()}).status_code, 200)
        previous = Motor.objects.get(pk=motor['id']).excel.name
        for name in ['datos.xls', 'datos.csv', 'falso.xlsx']:
            with self.subTest(name=name):
                response = self.client.post(motor['assign_url'], {'file': SimpleUploadedFile(name, b'bad\x00workbook')})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Motor.objects.get(pk=motor['id']).excel.name, previous)

    def test_reimporting_identical_motor_data_reuses_the_workbook_and_sheet(self):
        plan = self.import_plan()
        motor = plan['motors'][0]
        first = self.client.post(motor['assign_url'], {'file': xlsx_file()}).json()['motor']
        previous = Motor.objects.get(pk=motor['id']).workbook_id
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(motor['assign_url'], {'file': xlsx_file('nuevo.xlsx')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['motor']['excel']['id'], previous)
        self.assertEqual(response.json()['motor']['sheet_name'], first['sheet_name'])
        self.assertFalse(Motor.objects.get(pk=motor['id']).excel)

    def test_anonymous_browser_cannot_access_plans_or_motor_files(self):
        plan = self.import_plan()
        other = Client()
        self.assertEqual(other.get(reverse('plans')).status_code, 401)
        self.assertEqual(other.get(plan['detail_url']).status_code, 401)
        self.assertEqual(other.get(plan['svg_url']).status_code, 401)
        motor = plan['motors'][0]
        self.assertEqual(other.post(motor['assign_url'], {'file': xlsx_file()}).status_code, 401)

    def test_csrf_is_required_for_uploads(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(reverse('index'))
        self.assertEqual(client.post(reverse('plan_import'), {'file': dxf_file()}).status_code, 403)
        token = client.cookies['csrftoken'].value
        response = client.post(reverse('plan_import'), {'file': dxf_file()}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)
