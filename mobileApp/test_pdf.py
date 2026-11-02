from datetime import date
from io import BytesIO
import json
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from pypdf import PdfWriter

from .models import Motor, PdfMarker, PdfPlan, Plan, WorkbookDocument


def pdf_upload(name='plano.pdf', pages=2, encrypted=False):
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=600 + index * 100, height=400)
    if encrypted:
        writer.encrypt('secreto')
    stream = BytesIO()
    writer.write(stream)
    return SimpleUploadedFile(name, stream.getvalue(), content_type='application/pdf')


def excel_upload(name='equipo.xlsx'):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Equipos'
    sheet['A1'] = 'Motor'
    sheet['B2'] = 15
    sheet['B2'].font = Font(bold=True, color='FF0000')
    sheet['C2'] = '=B2*2'
    sheet['D2'] = date(2026, 10, 30)
    sheet['AE350'] = 'Lejos del recorte anterior'
    second = workbook.create_sheet('Otra hoja')
    second['A1'] = 'Conservar'
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return SimpleUploadedFile(name, stream.getvalue())


class PdfModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Importing/assigning is open to registered users; editing is explicit.
        cls.user = get_user_model().objects.create_user('usuario_pdf', password='clave-pdf')
        cls.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='mobileApp', codename__in=('change_workbookdocument', 'change_pdfplan'),
        ))
        cls.other = get_user_model().objects.create_user('otro_usuario_pdf', password='clave-pdf')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='pdf-test-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.user)

    def post_json(self, name, data, args=()):
        return self.client.post(reverse(name, args=args), json.dumps(data), content_type='application/json')

    def import_plan(self):
        response = self.client.post(reverse('import_pdf'), {'file': pdf_upload()})
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()

    def create_marker(self, plan):
        response = self.client.post(plan['markers_url'], json.dumps({'page': 2, 'x': .25, 'y': .75}), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()['marker']

    def upload_excel(self):
        response = self.client.post(reverse('upload_workbook'), {'file': excel_upload()})
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()['workbook']

    def test_user_without_cad_permissions_can_import_real_multipage_pdf(self):
        self.assertFalse(self.user.has_perm('mobileApp.view_plan'))
        plan = self.import_plan()
        self.assertEqual(plan['pages'], [{'width': 600, 'height': 400}, {'width': 700, 'height': 400}])
        self.assertEqual(plan['markers'], [])
        self.assertEqual(PdfPlan.objects.get(pk=plan['id']).imported_by_id, self.user.pk)
        self.assertTrue(Path(PdfPlan.objects.get(pk=plan['id']).source.path).exists())
        source = self.client.get(plan['source_url'])
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source['Content-Type'], 'application/pdf')
        self.assertTrue(b''.join(source.streaming_content).startswith(b'%PDF-'))
        source.close()

    def test_rejects_non_pdf_renamed_corrupt_empty_and_encrypted_pdf(self):
        uploads = [
            SimpleUploadedFile('plano.png', b'%PDF-'),
            SimpleUploadedFile('falso.pdf', b'not a pdf'),
            SimpleUploadedFile('roto.pdf', b'%PDF-1.7\nnot a valid document'),
            SimpleUploadedFile('vacio.pdf', b''),
            pdf_upload(encrypted=True),
        ]
        for upload in uploads:
            with self.subTest(name=upload.name):
                response = self.client.post(reverse('import_pdf'), {'file': upload})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(PdfPlan.objects.count(), 0)

    @override_settings(PLAN_UPLOAD_MAX_BYTES=50)
    def test_pdf_upload_size_limit(self):
        response = self.client.post(reverse('import_pdf'), {'file': pdf_upload()})
        self.assertEqual(response.status_code, 400)

    def test_marker_creation_starts_empty_and_is_saved_at_normalized_position(self):
        plan = self.import_plan()
        marker = self.create_marker(plan)
        self.assertIsNotNone(marker['workbook'])
        metadata = self.client.get(marker['workbook']['metadata_url']).json()
        self.assertTrue(metadata['is_motor'])
        self.assertFalse(metadata['motor_registration']['has_data'])
        self.assertEqual(marker['label'], '')
        reloaded = self.client.get(plan['detail_url']).json()
        self.assertEqual(reloaded['markers'][0]['x'], .25)
        self.assertEqual(reloaded['markers'][0]['y'], .75)
        self.assertEqual(reloaded['markers'][0]['page'], 2)

    def test_marker_rename_move_and_delete(self):
        marker = self.create_marker(self.import_plan())
        response = self.client.post(marker['update_url'], json.dumps({'page': 1, 'x': .8, 'y': .2, 'label': 'Bomba principal'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['marker']['label'], 'Bomba principal')
        self.assertEqual(response.json()['marker']['x'], .8)
        self.assertEqual(self.client.post(marker['delete_url']).status_code, 200)
        self.assertFalse(PdfMarker.objects.filter(pk=marker['id']).exists())

    def test_marker_invalid_pages_and_positions_are_rejected(self):
        plan = self.import_plan()
        for data in [
            {'page': 0, 'x': .1, 'y': .1}, {'page': 3, 'x': .1, 'y': .1},
            {'page': True, 'x': .1, 'y': .1}, {'page': 1, 'x': -1, 'y': .1},
            {'page': 1, 'x': .1, 'y': 1.1}, {'page': 1, 'x': '0.2', 'y': .1},
            {'page': 1, 'x': .1, 'y': .1, 'label': ['invalid']},
        ]:
            with self.subTest(data=data):
                self.assertEqual(self.client.post(plan['markers_url'], json.dumps(data), content_type='application/json').status_code, 400)
        self.assertEqual(PdfMarker.objects.count(), 0)

    def test_workbook_upload_stores_actual_file_in_database_and_full_dimensions(self):
        document = self.upload_excel()
        stored = WorkbookDocument.objects.get(pk=document['id'])
        self.assertTrue(bytes(stored.content).startswith(b'PK'))
        self.assertEqual({key: document['sheets'][0][key] for key in ('name', 'rows', 'columns')},
                         {'name': 'Equipos', 'rows': 350, 'columns': 31})
        self.assertEqual(document['sheets'][1]['name'], 'Otra hoja')
        self.assertEqual(stored.updated_by_id, self.user.pk)

    def test_marker_assignment_from_device_and_existing_library(self):
        plan = self.import_plan()
        first = self.create_marker(plan)
        assigned = self.client.post(first['assign_url'], {'file': excel_upload()})
        self.assertEqual(assigned.status_code, 200)
        document = assigned.json()['marker']['workbook']
        second = self.create_marker(plan)
        response = self.client.post(second['assign_url'], json.dumps({'workbook_id': document['id']}), content_type='application/json')
        copied_id = response.json()['marker']['workbook']['id']
        self.assertNotEqual(copied_id, document['id'])
        self.assertEqual(bytes(WorkbookDocument.objects.get(pk=copied_id).content),
                         bytes(WorkbookDocument.objects.get(pk=document['id']).content))
        library = self.client.get(reverse('list_workbooks')).json()
        self.assertEqual(library['workbooks'], [])

    def test_legacy_motor_excel_is_copied_without_modifying_cad_document(self):
        plan = Plan.objects.create(name='plano.dxf', size=1, svg='<svg/>', imported_by=self.user)
        motor = Motor.objects.create(plan=plan, entity_key='m1', name='MOTOR', x=1, y=2, svg_x=1, svg_y=2)
        upload = excel_upload()
        original = upload.read()
        upload.seek(0)
        motor.excel.save(upload.name, upload, save=False)
        motor.excel_name = upload.name
        motor.excel_sheets = ['Equipos', 'Otra hoja']
        motor.save()
        marker = self.create_marker(self.import_plan())
        library = self.client.get(reverse('list_workbooks')).json()
        self.assertEqual(library['motors'][0]['source_motor_id'], motor.pk)
        response = self.client.post(marker['assign_url'], json.dumps({'source_motor_id': motor.pk}), content_type='application/json')
        document = response.json()['marker']['workbook']
        self.assertEqual(self.client.post(document['save_url'], json.dumps({
            'revision': document['revision'], 'edits': [{'sheet': 'Equipos', 'row': 2, 'col': 2, 'value': 99}],
        }), content_type='application/json').status_code, 200)
        with motor.excel.open('rb') as source:
            self.assertEqual(source.read(), original)

    def test_cell_windows_can_reach_beyond_100_rows_and_20_columns(self):
        document = self.upload_excel()
        response = self.client.get(document['cells_url'], {'sheet': 'Equipos', 'row': 345, 'col': 28, 'rows': 10, 'cols': 6})
        self.assertEqual(response.status_code, 200)
        cell = next(cell for cell in response.json()['cells'] if cell['row'] == 350 and cell['col'] == 31)
        self.assertEqual(cell['value'], 'Lejos del recorte anterior')
        self.assertEqual(cell['display'], 'Lejos del recorte anterior')

    def test_window_preserves_raw_formulas_numbers_and_dates(self):
        document = self.upload_excel()
        cells = self.client.get(document['cells_url'], {'sheet': 'Equipos', 'row': 2, 'col': 2, 'rows': 1, 'cols': 3}).json()['cells']
        self.assertEqual(cells[0]['value'], 15)
        self.assertEqual(cells[1]['value'], '=B2*2')
        self.assertTrue(cells[2]['value'].startswith('2026-10-30'))

    def test_saving_typed_values_preserves_styles_other_sheets_and_formulas(self):
        document = self.upload_excel()
        response = self.client.post(document['save_url'], json.dumps({
            'revision': 1, 'edits': [
                {'sheet': 'Equipos', 'row': 2, 'col': 2, 'value': 25},
                {'sheet': 'Equipos', 'row': 700, 'col': 40, 'value': True},
                {'sheet': 'Equipos', 'row': 701, 'col': 40, 'value': '=B2+1'},
                {'sheet': 'Otra hoja', 'row': 2, 'col': 1, 'value': 'Nuevo dato'},
            ],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content[:500])
        updated = response.json()
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(updated['sheets'][0]['rows'], 701)
        self.assertEqual(updated['sheets'][0]['columns'], 40)
        download = self.client.get(updated['download_url'])
        data = b''.join(download.streaming_content)
        download.close()
        workbook = load_workbook(BytesIO(data))
        self.assertEqual(workbook['Equipos']['B2'].value, 25)
        self.assertTrue(workbook['Equipos']['B2'].font.bold)
        self.assertEqual(workbook['Equipos']['C2'].value, '=B2*2')
        self.assertIs(workbook['Equipos'].cell(700, 40).value, True)
        self.assertEqual(workbook['Equipos'].cell(701, 40).value, '=B2+1')
        self.assertEqual(workbook['Otra hoja']['A1'].value, 'Conservar')
        self.assertEqual(workbook['Otra hoja']['A2'].value, 'Nuevo dato')
        workbook.close()

    def test_stale_revision_returns_conflict_without_overwriting(self):
        document = self.upload_excel()
        first = {'revision': 1, 'edits': [{'sheet': 'Equipos', 'row': 2, 'col': 2, 'value': 20}]}
        self.assertEqual(self.client.post(document['save_url'], json.dumps(first), content_type='application/json').status_code, 200)
        first['edits'][0]['value'] = 999
        self.assertEqual(self.client.post(document['save_url'], json.dumps(first), content_type='application/json').status_code, 409)
        cells = self.client.get(document['cells_url'], {'sheet': 'Equipos', 'row': 2, 'col': 2, 'rows': 1, 'cols': 1}).json()['cells']
        self.assertEqual(cells[0]['value'], 20)

    def test_insert_rows_and_columns_then_edit(self):
        document = self.upload_excel()
        response = self.client.post(document['save_url'], json.dumps({
            'revision': 1,
            'operations': [
                {'type': 'insert_row', 'sheet': 'Equipos', 'index': 2},
                {'type': 'insert_column', 'sheet': 'Equipos', 'index': 2},
            ],
            'edits': [{'sheet': 'Equipos', 'row': 2, 'col': 2, 'value': 'Nueva fila y columna'}],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        content = bytes(WorkbookDocument.objects.get(pk=document['id']).content)
        workbook = load_workbook(BytesIO(content))
        self.assertEqual(workbook['Equipos']['B2'].value, 'Nueva fila y columna')
        self.assertEqual(workbook['Equipos']['C3'].value, 15)
        workbook.close()

    def test_blank_appended_rows_and_columns_persist_after_reopening(self):
        document = self.upload_excel()
        response = self.client.post(document['save_url'], json.dumps({
            'revision': 1, 'edits': [],
            'operations': [
                {'type': 'insert_row', 'sheet': 'Equipos', 'index': 351},
                {'type': 'insert_column', 'sheet': 'Equipos', 'index': 32},
            ],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sheets'][0]['rows'], 351)
        self.assertEqual(response.json()['sheets'][0]['columns'], 32)
        metadata = self.client.get(document['metadata_url']).json()
        self.assertEqual({key: metadata['sheets'][0][key] for key in ('name', 'rows', 'columns')},
                         {'name': 'Equipos', 'rows': 351, 'columns': 32})

    def test_invalid_edits_ranges_and_json_are_rejected_without_change(self):
        document = self.upload_excel()
        for edit in [
            {'sheet': 'No existe', 'row': 1, 'col': 1, 'value': 1},
            {'sheet': 'Equipos', 'row': 0, 'col': 1, 'value': 1},
            {'sheet': 'Equipos', 'row': 1, 'col': 16385, 'value': 1},
            {'sheet': 'Equipos', 'row': 1, 'col': 1, 'value': ['invalid']},
        ]:
            response = self.client.post(document['save_url'], json.dumps({'revision': 1, 'edits': [edit]}), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(WorkbookDocument.objects.get(pk=document['id']).revision, 1)
        for params in [{'row': 0}, {'rows': 101}, {'cols': 51}, {'sheet': 'No existe'}, {'col': 'bad'}]:
            self.assertEqual(self.client.get(document['cells_url'], params).status_code, 400)
        self.assertEqual(self.client.post(document['save_url'], '[', content_type='application/json').status_code, 400)

    def test_ordinary_authenticated_user_can_view_but_cannot_edit_shared_workbook(self):
        document = self.upload_excel()
        original = bytes(WorkbookDocument.objects.get(pk=document['id']).content)
        other_client = Client()
        other_client.force_login(self.other)
        metadata = other_client.get(document['metadata_url'])
        self.assertEqual(metadata.status_code, 200)
        self.assertFalse(metadata.json()['can_edit'])
        self.assertEqual(other_client.get(document['cells_url']).status_code, 200)
        response = other_client.post(document['save_url'], json.dumps({
            'revision': 1, 'edits': [{'sheet': 'Equipos', 'row': 1, 'col': 1, 'value': 'Editado por otro usuario'}],
        }), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        saved = WorkbookDocument.objects.get(pk=document['id'])
        self.assertEqual(saved.updated_by_id, self.user.pk)
        self.assertEqual(saved.revision, 1)
        self.assertEqual(bytes(saved.content), original)

    def test_reader_can_import_place_and_assign_but_cannot_move_or_delete_saved_buttons(self):
        self.client.force_login(self.other)
        plan = self.import_plan()
        marker = self.create_marker(plan)
        document = self.upload_excel()
        assigned = self.client.post(marker['assign_url'], {
            'workbook_id': document['id'],
        }, content_type='application/json')
        self.assertEqual(assigned.status_code, 200)
        copied_id = assigned.json()['marker']['workbook']['id']
        self.assertNotEqual(copied_id, document['id'])
        self.assertEqual(bytes(WorkbookDocument.objects.get(pk=copied_id).content),
                         bytes(WorkbookDocument.objects.get(pk=document['id']).content))
        renamed = self.client.post(marker['update_url'], {'label': 'Motor asignado'}, content_type='application/json')
        self.assertEqual(renamed.status_code, 200)
        for data in ({'x': .8}, {'y': .2}, {'page': 1}):
            self.assertEqual(self.client.post(marker['update_url'], data, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(marker['delete_url']).status_code, 403)
        saved = PdfMarker.objects.get(pk=marker['id'])
        self.assertEqual((saved.page, saved.x, saved.y), (2, .25, .75))
        self.assertEqual(saved.label, 'Motor asignado')
        self.assertEqual(saved.workbook_id, copied_id)

    def test_pdf_rename_and_delete_require_independent_permissions_even_for_importer(self):
        self.client.force_login(self.other)
        plan = self.import_plan()
        marker = self.create_marker(plan)
        assigned = self.client.post(marker['assign_url'], {'file': excel_upload()})
        document_id = assigned.json()['marker']['workbook']['id']
        source_path = Path(PdfPlan.objects.get(pk=plan['id']).source.path)
        self.assertEqual(self.client.post(plan['update_url'], {'name': 'Sala A'}, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(plan['delete_url']).status_code, 403)
        self.other.user_permissions.add(Permission.objects.get(
            content_type__app_label='mobileApp', codename='change_pdfplan',
        ))
        renamed = self.client.post(plan['update_url'], {'name': 'Sala A'}, content_type='application/json')
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(self.client.get(plan['detail_url']).json()['name'], 'Sala A')
        self.assertEqual(self.client.post(plan['delete_url']).status_code, 403)
        self.other.user_permissions.clear()
        self.other.user_permissions.add(Permission.objects.get(
            content_type__app_label='mobileApp', codename='delete_pdfplan',
        ))
        self.assertEqual(self.client.post(plan['update_url'], {'name': 'Sala B'}, content_type='application/json').status_code, 403)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(plan['delete_url']).status_code, 200)
        self.assertFalse(PdfPlan.objects.filter(pk=plan['id']).exists())
        self.assertFalse(PdfMarker.objects.filter(pk=marker['id']).exists())
        self.assertFalse(source_path.exists())
        self.assertTrue(WorkbookDocument.objects.filter(pk=document_id).exists())

    def test_anonymous_users_cannot_access_pdf_marker_or_workbook_endpoints(self):
        plan = self.import_plan()
        marker = self.create_marker(plan)
        document = self.upload_excel()
        anonymous = Client()
        for url in [reverse('list_pdf_plans'), plan['detail_url'], plan['source_url'], reverse('list_workbooks'), document['metadata_url'], document['cells_url'], document['download_url']]:
            self.assertEqual(anonymous.get(url).status_code, 401)
        for url in [reverse('import_pdf'), plan['update_url'], plan['delete_url'], plan['markers_url'], marker['update_url'], marker['delete_url'], marker['assign_url'], reverse('upload_workbook'), document['save_url']]:
            self.assertEqual(anonymous.post(url, '{}', content_type='application/json').status_code, 401)

    def test_pdf_mutations_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(reverse('index'))
        self.assertEqual(client.post(reverse('import_pdf'), {'file': pdf_upload()}).status_code, 403)
        token = client.cookies['csrftoken'].value
        response = client.post(reverse('import_pdf'), {'file': pdf_upload()}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)
