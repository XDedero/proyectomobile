import csv
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from .models import Motor, PdfMarker, PdfPlan, Plan, SavedProject, WorkbookDocument
from .test_pdf import pdf_upload, excel_upload
from .tests import dxf_file


class ProjectTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('proyectos', password='clave-proyectos')
        cls.user.user_permissions.add(Permission.objects.get(content_type__app_label='mobileApp', codename='add_savedproject'))
        cls.admin = get_user_model().objects.create_superuser('admin_proyectos', password='clave')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='project-tests-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.user)

    def post(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type='application/json')

    def plan(self, kind):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'), {'file': pdf_upload() if kind == 'pdf' else dxf_file()})
        self.assertEqual(response.status_code, 201, response.content[:400])
        return response.json()

    def document(self):
        response = self.client.post(reverse('upload_workbook'), {'file': excel_upload()})
        self.assertEqual(response.status_code, 201)
        return response.json()['workbook']

    def download(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, getattr(response, 'content', b'')[:400] if not response.streaming else '')
        content = b''.join(response.streaming_content)
        response.close()
        return content

    def bundle(self, kind, *, linked=True):
        plan = self.plan(kind)
        if linked:
            book = self.document()
            self.assertEqual(self.post(plan['assign_listing_url'], {'workbook_id': book['id']}).status_code, 200)
            if kind == 'pdf':
                for x in (.2, .6):
                    marker = self.post(plan['markers_url'], {'page': 2, 'x': x, 'y': .3}).json()['marker']
                    self.assertEqual(self.post(marker['assign_url'], {'workbook_id': book['id']}).status_code, 200)
            else:
                for motor in plan['motors']:
                    self.assertEqual(self.post(motor['assign_url'], {'workbook_id': book['id']}).status_code, 200)
        return plan, self.download(plan['export_project_url'])

    def test_both_formats_are_explicitly_saved_in_one_library_without_duplicates(self):
        pdf, dxf = self.plan('pdf'), self.plan('dxf')
        self.assertEqual(self.client.get(reverse('saved_projects')).json()['plans'], [])
        for plan in (pdf, dxf):
            self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
            self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        saved = self.client.get(reverse('saved_projects')).json()['plans']
        self.assertEqual(len(saved), 2)
        self.assertEqual({plan['kind'] for plan in saved}, {'pdf', 'dxf'})
        for plan in saved:
            self.assertEqual(self.client.get(plan['detail_url']).status_code, 200)
            self.assertTrue(self.download(plan['export_url']).startswith(b'PK'))

    def test_pdf_project_round_trip_keeps_pages_positions_listing_and_shared_xlsx_bytes(self):
        original, content = self.bundle('pdf')
        with ZipFile(BytesIO(content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(len(manifest['workbooks']), 1)
            self.assertEqual(manifest['listing'], manifest['markers'][0]['workbook'])
            xlsx = archive.read(manifest['workbooks'][0]['path'])
        response = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('respaldo.planproj', content)})
        self.assertEqual(response.status_code, 201, response.content[:400])
        restored = response.json()['plan']
        self.assertNotEqual(restored['id'], original['id'])
        self.assertEqual(restored['pages'], original['pages'])
        self.assertEqual([(item['page'], item['x'], item['y']) for item in restored['markers']], [(2, .2, .3), (2, .6, .3)])
        ids = {item['workbook']['id'] for item in restored['markers']}
        self.assertEqual(len(ids), 2)
        self.assertNotIn(restored['listing']['id'], ids)
        for marker in restored['markers']:
            self.assertEqual(self.download(marker['workbook']['download_url']), xlsx)
        self.assertEqual(self.download(restored['listing']['download_url']), xlsx)

    def test_dxf_round_trip_preserves_motor_world_positions_and_attachments(self):
        original, content = self.bundle('dxf')
        restored_response = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('cad.planproj', content)})
        self.assertEqual(restored_response.status_code, 201, restored_response.content[:400])
        restored = restored_response.json()['plan']
        self.assertEqual([(motor['name'], motor['x'], motor['y'], motor['z']) for motor in restored['motors']], [(motor['name'], motor['x'], motor['y'], motor['z']) for motor in original['motors']])
        ids = {motor['excel']['id'] for motor in restored['motors']}
        self.assertEqual(len(ids), len(restored['motors']))
        self.assertNotIn(restored['listing']['id'], ids)
        for motor in restored['motors']:
            self.assertEqual(self.download(motor['excel']['url']), self.download(restored['listing']['download_url']))
        self.assertEqual(self.download(restored['export_project_url'])[:2], b'PK')

    def test_saved_snapshot_contains_bytes_from_save_including_excel_updates(self):
        plan, _ = self.bundle('pdf')
        self.client.force_login(self.admin)
        document = PdfPlan.objects.get(pk=plan['id']).listing
        edit = {'revision': 1, 'edits': [{'sheet': 'Equipos', 'row': 2, 'col': 2, 'value': 123}]}
        self.assertEqual(self.post(reverse('workbook_save', args=[document.pk]), edit).status_code, 200)
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        snapshot = SavedProject.objects.get(pdf_plan_id=plan['id'])
        with ZipFile(BytesIO(bytes(snapshot.content))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            listing = next(item for item in manifest['workbooks'] if item['key'] == manifest['listing'])
            book = load_workbook(BytesIO(archive.read(listing['path'])))
            self.assertEqual(book['Equipos']['B2'].value, 123)
            book.close()
            motor = next(item for item in manifest['workbooks'] if item['key'] == manifest['markers'][0]['workbook'])
            book = load_workbook(BytesIO(archive.read(motor['path'])))
            self.assertEqual(book['Equipos']['B2'].value, 15)
            book.close()

    def test_template_matches_list_capture_has_logo_and_grows_for_more_motors(self):
        plan = self.plan('pdf')
        for index in range(12):
            self.post(plan['markers_url'], {'page': 1, 'x': .1 + index*.01, 'y': .3})
        response = self.post(plan['assign_listing_url'], {'create_template': True})
        self.assertEqual(response.status_code, 200)
        document = response.json()['listing']
        book = load_workbook(BytesIO(self.download(document['download_url'])))
        self.assertEqual(book.sheetnames, ['Listado'])
        sheet = book['Listado']
        self.assertIn('IDENTIFICACIÓN DE MOTORES', sheet['B2'].value)
        self.assertEqual([sheet.cell(7, col).value for col in (2, 3, 6)], ['Nº Motor', 'Especificaciones del Motor', 'Sector'])
        self.assertEqual([sheet.cell(7+index, 2).value for index in range(1, 13)], list(range(1, 13)))
        self.assertEqual(len(sheet._images), 1)
        self.assertIn('C19:E19', str(sheet.merged_cells))
        self.assertIn('F19:H19', str(sheet.merged_cells))
        book.close()

    def test_registered_reader_can_assign_listing_and_save_but_cannot_edit_its_cells(self):
        plan = self.plan('dxf')
        response = self.post(plan['assign_listing_url'], {'create_template': True})
        listing = response.json()['listing']
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        metadata = self.client.get(listing['metadata_url']).json()
        self.assertFalse(metadata['can_edit'])
        self.assertEqual(self.post(listing['save_url'], {'revision': 1, 'edits': [{'sheet': 'Listado', 'row': 8, 'col': 3, 'value': 'No permitido'}]}).status_code, 403)

    def test_adding_listing_columns_and_motors_keeps_logical_merged_columns_styles_and_logo(self):
        plan = self.plan('pdf')
        listing = self.post(plan['assign_listing_url'], {'create_template': True}).json()['listing']
        self.client.force_login(self.admin)
        metadata = self.client.get(listing['metadata_url']).json()
        self.assertEqual(metadata['sheets'][0]['layout']['content_bounds']['max_col'], 8)
        changed = self.post(listing['save_url'], {
            'revision': 1,
            'operations': [
                {'sheet': 'Listado', 'type': 'insert_column', 'index': 9, 'copy_from': 8, 'extend_header_through_row': 6},
                {'sheet': 'Listado', 'type': 'insert_row', 'index': 17, 'copy_from': 16},
            ],
            'edits': [
                {'sheet': 'Listado', 'row': 7, 'col': 9, 'value': 'Dato adicional'},
                {'sheet': 'Listado', 'row': 17, 'col': 2, 'value': 10},
                {'sheet': 'Listado', 'row': 17, 'col': 3, 'value': 'Motor adicional'},
            ],
        })
        self.assertEqual(changed.status_code, 200, changed.content[:400])
        book = load_workbook(BytesIO(self.download(listing['download_url'])))
        sheet = book['Listado']
        self.assertIn('B2:I5', str(sheet.merged_cells))
        self.assertIn('C17:E17', str(sheet.merged_cells))
        self.assertIn('F17:H17', str(sheet.merged_cells))
        self.assertEqual(sheet['I7'].value, 'Dato adicional')
        self.assertTrue(sheet['I7'].font.bold)
        self.assertEqual(sheet['B17'].value, 10)
        self.assertEqual(sheet['C17'].value, 'Motor adicional')
        self.assertAlmostEqual(sheet.column_dimensions['I'].width, sum(sheet.column_dimensions[key].width for key in ('F', 'G', 'H')))
        self.assertEqual(len(sheet._images), 1)
        book.close()

    def test_listing_and_motor_books_export_selected_sheet_as_utf8_csv(self):
        plan, _ = self.bundle('pdf')
        document = PdfPlan.objects.get(pk=plan['id']).listing
        url = reverse('workbook_csv', args=[document.pk])
        content = self.download(url + '?sheet=Otra%20hoja')
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
        rows = list(csv.reader(StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(rows, [['Conservar']])
        self.assertEqual(self.client.get(url, {'sheet': 'No existe'}).status_code, 400)
        self.assertTrue(self.download(url + '?sheet=Equipos'))

    def test_cad_marker_drag_requires_modification_permission_and_keeps_excel(self):
        plan, _ = self.bundle('dxf')
        motor = Motor.objects.get(pk=plan['motors'][0]['id'])
        url = reverse('move_cad_marker', args=[motor.pk])
        target = {'svg_x': motor.svg_x + 50, 'svg_y': motor.svg_y + 50}
        self.assertEqual(self.post(url, target).status_code, 403)
        self.client.force_login(self.admin)
        response = self.post(url, target)
        self.assertEqual(response.status_code, 200, response.content[:400])
        moved = response.json()['motor']
        self.assertEqual(moved['excel']['id'], motor.workbook_id)
        self.assertEqual((moved['svg_x'], moved['svg_y']), (target['svg_x'], target['svg_y']))
        self.assertEqual(self.post(url, {'svg_x': True, 'svg_y': 0}).status_code, 400)

    def test_project_import_rejects_corrupt_versions_unknown_files_paths_and_missing_books_atomically(self):
        _, valid = self.bundle('pdf')
        before = (PdfPlan.objects.count(), WorkbookDocument.objects.count())
        for change in ('version', 'path', 'missing', 'unknown'):
            output = BytesIO()
            with ZipFile(BytesIO(valid)) as source, ZipFile(output, 'w', ZIP_DEFLATED) as target:
                manifest = json.loads(source.read('manifest.json'))
                if change == 'version': manifest['version'] = True
                if change == 'missing': manifest['markers'][0]['workbook'] = 'absent'
                for entry in source.infolist():
                    if entry.filename == 'manifest.json': target.writestr(entry, json.dumps(manifest))
                    else: target.writestr(entry, source.read(entry.filename))
                if change == 'path': target.writestr('../outside.txt', b'x')
                if change == 'unknown': target.writestr('extra.txt', b'x')
            response = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('bad.planproj', output.getvalue())})
            self.assertEqual(response.status_code, 400, change)
            self.assertEqual((PdfPlan.objects.count(), WorkbookDocument.objects.count()), before)
        for name, content in [('bad.zip', valid), ('bad.planproj', b'bad zip')]:
            self.assertEqual(self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile(name, content)}).status_code, 400)

    def test_project_and_csv_endpoints_require_login_and_mutations_require_csrf(self):
        plan = self.plan('pdf')
        book = self.document()
        anonymous = Client()
        for url in (reverse('saved_projects'), plan['export_project_url'], book['csv_url']):
            self.assertEqual(anonymous.get(url).status_code, 401)
        for url in (reverse('upload_project'), plan['save_project_url'], plan['assign_listing_url']):
            self.assertEqual(anonymous.post(url).status_code, 401)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        for url in (plan['save_project_url'], plan['assign_listing_url'], reverse('upload_project')):
            self.assertEqual(csrf.post(url, '{}', content_type='application/json').status_code, 403)
