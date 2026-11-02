from importlib import import_module
from io import BytesIO
import json
import tempfile
from zipfile import ZipFile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from .models import Motor, PdfMarker, PdfPlan, SavedMotor, WorkbookDocument
from .motor_service import inspect_motor, read_origin, template_bytes, single_sheet
from .test_pdf import pdf_upload
from .tests import dxf_file
from .workbook_service import edited_document


class SavedMotorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.editor = User.objects.create_user('motor_editor', password='clave')
        cls.editor.user_permissions.add(Permission.objects.get(content_type__app_label='mobileApp', codename='change_workbookdocument'))
        cls.reader = User.objects.create_user('motor_lector', password='clave')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='motor-library-')
        self.addCleanup(temporary.cleanup)
        setting = override_settings(MEDIA_ROOT=temporary.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.editor)

    def post(self, url, values):
        return self.client.post(url, json.dumps(values), content_type='application/json')

    def marker(self, plan=None):
        if plan is None:
            plan = self.client.post(reverse('import_pdf'), {'file': pdf_upload()}).json()
        result = self.post(plan['markers_url'], {'page': 1, 'x': .25, 'y': .5})
        self.assertEqual(result.status_code, 201)
        return plan, result.json()['marker']

    def fill(self, workbook, serial='SN-001'):
        metadata = self.client.get(workbook['metadata_url']).json()
        sheet = metadata.get('preferred_sheet') or metadata['sheets'][0]['name']
        response = self.post(metadata['save_url'], {'revision': metadata['revision'], 'sheet': sheet, 'context': 'motor', 'edits': [
            {'sheet': sheet, 'row': 18, 'col': 3, 'value': 'WEG'},
            {'sheet': sheet, 'row': 20, 'col': 3, 'value': serial},
        ]})
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()

    def register(self, workbook, name='Motor de prueba'):
        response = self.post(reverse('register_motor'), {'workbook_id': workbook['id'], 'revision': workbook['revision'], 'sheet': workbook.get('preferred_sheet'), 'name': name})
        self.assertEqual(response.status_code, 201, response.content[:400])
        return response.json()

    def file(self, url, client=None):
        response = (client or self.client).get(url)
        self.assertEqual(response.status_code, 200)
        content = b''.join(response.streaming_content)
        response.close()
        return content

    def cell(self, document_id, address='C18'):
        document = WorkbookDocument.objects.get(pk=document_id)
        workbook = load_workbook(BytesIO(bytes(document.content)))
        try:
            name = document.plan_sheets.filter(is_listing=False).values_list('name', flat=True).first() if document.plan_mode else workbook.sheetnames[0]
            return workbook[name][address].value
        finally:
            workbook.close()

    def test_pdf_buttons_get_independent_empty_sheets_in_the_same_document(self):
        plan, first = self.marker()
        _, second = self.marker(plan)
        self.assertEqual(first['workbook']['id'], second['workbook']['id'])
        self.assertNotEqual(first['sheet_name'], second['sheet_name'])
        first_bytes = self.file(first['workbook']['download_url'])
        self.assertEqual(first_bytes, self.file(second['workbook']['download_url']))
        self.assertFalse(inspect_motor(first_bytes)[1])
        metadata = self.client.get(first['workbook']['metadata_url']).json()
        self.assertEqual([sheet['name'] for sheet in metadata['sheets']], [first['sheet_name']])
        self.assertEqual(len(metadata['sheets'][0]['layout']['charts']), 2)
        self.assertEqual(len(metadata['sheets'][0]['layout']['images']), 1)
        self.assertEqual(metadata['sheets'][0]['layout']['presentation'], 'form')
        self.assertIsNone(self.cell(first['workbook']['id'], 'G10'))
        self.assertIsNone(self.cell(first['workbook']['id'], 'C39'))

    def test_detected_and_manually_added_dxf_motors_have_independent_empty_forms(self):
        plan = self.client.post(reverse('plan_import'), {'file': dxf_file()}).json()
        ids = [motor['excel']['id'] for motor in plan['motors']]
        self.assertEqual(len(set(ids)), 1)
        reference = plan['motors'][0]
        manual = self.post(plan['markers_url'], {'name': 'Motor nuevo', 'svg_x': reference['svg_x'], 'svg_y': reference['svg_y']}).json()['motor']
        self.assertIn(manual['excel']['id'], ids)
        self.assertNotIn(manual['sheet_name'], [motor['sheet_name'] for motor in plan['motors']])
        self.assertFalse(inspect_motor(single_sheet(self.file(manual['excel']['download_url']), manual['sheet_name']))[1])

    def test_empty_forms_and_missing_names_cannot_be_registered(self):
        _, marker = self.marker()
        book = marker['workbook']
        self.assertEqual(self.post(reverse('register_motor'), {'workbook_id': book['id'], 'revision': book['revision'], 'sheet': marker['sheet_name'], 'name': 'Vacío'}).status_code, 400)
        filled = self.fill(book)
        for name in ('', ' ', None, 'x'*121):
            self.assertEqual(self.post(reverse('register_motor'), {'workbook_id': book['id'], 'revision': filled['revision'], 'sheet': marker['sheet_name'], 'name': name}).status_code, 400)
        self.assertEqual(SavedMotor.objects.count(), 0)

    def test_named_motor_keeps_filled_original_snapshot_and_provenance(self):
        _, marker = self.marker()
        filled = self.fill(marker['workbook'])
        before = single_sheet(self.file(filled['download_url']), filled['preferred_sheet'])
        registered = self.register(filled, 'Motor Bomba 01')
        snapshot = registered['motor']['workbook']
        self.assertNotEqual(snapshot['id'], filled['id'])
        self.assertEqual(self.cell(snapshot['id']), 'WEG')
        self.assertEqual(self.cell(snapshot['id'], 'C20'), 'SN-001')
        self.assertTrue(registered['workbook']['motor_registration']['registered'])
        self.assertFalse(registered['workbook']['motor_registration']['can_register'])
        saved = SavedMotor.objects.get()
        self.assertEqual(read_origin(self.file(snapshot['download_url'])), saved.uuid)
        with ZipFile(BytesIO(before)) as original, ZipFile(BytesIO(self.file(snapshot['download_url']))) as after:
            for name in original.namelist():
                if name not in ('[Content_Types].xml', '_rels/.rels', 'docProps/custom.xml'):
                    self.assertEqual(original.read(name), after.read(name), name)

    def test_saved_original_is_read_only_even_for_an_excel_editor(self):
        _, marker = self.marker()
        registered = self.register(self.fill(marker['workbook']))
        snapshot = registered['motor']['workbook']
        metadata = self.client.get(snapshot['metadata_url']).json()
        self.assertFalse(metadata['can_edit'])
        response = self.post(metadata['save_url'], {'revision': 1, 'edits': [{'sheet': '1', 'row': 18, 'col': 3, 'value': 'Prohibido'}]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.cell(snapshot['id']), 'WEG')

    def test_two_buttons_reusing_saved_motor_share_changes_but_keep_catalog_original(self):
        plan, original = self.marker()
        saved = self.register(self.fill(original['workbook']))['motor']
        _, first = self.marker(plan)
        _, second = self.marker(plan)
        copies = []
        for marker in (first, second):
            copies.append(self.post(marker['assign_url'], {'saved_motor_id': saved['id']}).json()['marker']['workbook'])
        self.assertEqual(len({saved['workbook']['id'], original['workbook']['id'], copies[0]['id'], copies[1]['id']}), 2)
        metadata = self.client.get(copies[0]['metadata_url']).json()
        self.assertTrue(metadata['can_edit'])
        self.assertTrue(metadata['motor_registration']['registered'])
        changed = self.post(metadata['save_url'], {'revision': metadata['revision'], 'sheet': metadata['preferred_sheet'], 'edits': [{'sheet': metadata['preferred_sheet'], 'row': 18, 'col': 3, 'value': 'ABB'}]})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.cell(copies[0]['id']), 'ABB')
        self.assertEqual(self.cell(copies[1]['id']), 'ABB')
        self.assertEqual(self.cell(saved['workbook']['id']), 'WEG')
        self.assertEqual(self.file(copies[0]['download_url']), self.file(copies[1]['download_url']))
        self.assertIn('ABB', self.file(copies[0]['csv_url'] + '?sheet=' + copies[0]['preferred_sheet']).decode('utf-8-sig'))
        self.assertIn('ABB', self.file(copies[1]['csv_url'] + '?sheet=' + copies[1]['preferred_sheet']).decode('utf-8-sig'))

    def test_program_copy_cannot_be_registered_again_even_after_editing_its_data(self):
        plan, first = self.marker()
        saved = self.register(self.fill(first['workbook']))['motor']
        _, second = self.marker(plan)
        copy = self.post(second['assign_url'], {'saved_motor_id': saved['id']}).json()['marker']['workbook']
        updated = self.fill(copy, 'SERIE-CAMBIADA')
        self.assertTrue(updated['motor_registration']['registered'])
        response = self.post(reverse('register_motor'), {'workbook_id': updated['id'], 'revision': updated['revision'], 'sheet': updated['preferred_sheet'], 'name': 'Otro nombre'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SavedMotor.objects.count(), 1)
        self.assertEqual(self.cell(saved['workbook']['id'], 'C20'), 'SN-001')

    def test_renamed_device_export_is_recognized_as_an_existing_motor(self):
        _, first = self.marker()
        saved = self.register(self.fill(first['workbook']))['motor']
        content = self.file(saved['workbook']['download_url'])
        response = self.client.post(reverse('import_motor_document'), {'file': SimpleUploadedFile('nombre_distinto.xlsx', content)})
        self.assertEqual(response.status_code, 201)
        book = response.json()['workbook']
        self.assertTrue(book['motor_registration']['registered'])
        self.assertEqual(self.post(reverse('register_motor'), {'workbook_id': book['id'], 'revision': book['revision'], 'name': 'Duplicado'}).status_code, 409)

    def test_unmarked_device_original_is_recognized_by_data_after_registration(self):
        _, marker = self.marker()
        filled = self.fill(marker['workbook'])
        unmarked = single_sheet(self.file(filled['download_url']), filled['preferred_sheet'])
        self.assertIsNone(read_origin(unmarked))
        saved = self.register(filled)['motor']
        result = self.client.post(reverse('import_motor_document'), {'file': SimpleUploadedFile('original.xlsx', unmarked)}).json()['workbook']
        self.assertEqual(result['motor_registration']['saved_motor_id'], saved['id'])
        self.assertEqual(self.post(reverse('register_motor'), {'workbook_id': result['id'], 'revision': result['revision'], 'name': 'Otro'}).status_code, 409)

    def test_new_device_motor_can_be_registered_by_an_active_reader(self):
        content, _ = edited_document(template_bytes(), [{'sheet': '1', 'row': 18, 'col': 3, 'value': 'ABB'}, {'sheet': '1', 'row': 20, 'col': 3, 'value': 'SN-LECTOR'}], [])
        self.client.force_login(self.reader)
        response = self.client.post(reverse('import_motor_document'), {'file': SimpleUploadedFile('motor_lector.xlsx', content)})
        book = response.json()['workbook']
        self.assertFalse(book['can_edit'])
        self.assertTrue(book['motor_registration']['can_register'])
        saved = self.register(book, 'Motor del lector')['motor']
        self.assertEqual(SavedMotor.objects.get(pk=saved['id']).created_by_id, self.reader.pk)

    def test_motor_names_are_unique_ignoring_case_and_repeated_spaces(self):
        plan, first = self.marker()
        self.register(self.fill(first['workbook']), 'Motor Norte')
        _, second = self.marker(plan)
        book = self.fill(second['workbook'], 'SN-OTRO')
        self.assertEqual(self.post(reverse('register_motor'), {'workbook_id': book['id'], 'revision': book['revision'], 'sheet': book['preferred_sheet'], 'name': '  motor   NORTE  '}).status_code, 409)
        self.assertEqual(SavedMotor.objects.count(), 1)
        self.assertIsNone(WorkbookDocument.objects.get(pk=book['id']).origin_uuid)

    def test_stale_registration_does_not_capture_or_overwrite_newer_data(self):
        _, marker = self.marker()
        original = self.fill(marker['workbook'])
        latest = self.fill(original, 'SERIE-NUEVA')
        response = self.post(reverse('register_motor'), {
            'workbook_id': original['id'], 'revision': original['revision'], 'name': 'Ficha antigua',
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SavedMotor.objects.count(), 0)
        document = WorkbookDocument.objects.get(pk=latest['id'])
        self.assertEqual(document.revision, latest['revision'])
        self.assertIsNone(document.origin_uuid)
        self.assertEqual(self.cell(document.pk, 'C20'), 'SERIE-NUEVA')

    def test_direct_device_registration_keeps_named_filled_snapshot_and_detects_duplicates(self):
        _, marker = self.marker()
        filled = self.fill(marker['workbook'])
        content = single_sheet(self.file(filled['download_url']), filled['preferred_sheet'])
        response = self.client.post(reverse('register_motor'), {
            'file': SimpleUploadedFile('externo.xlsx', content), 'name': 'Motor externo',
        })
        self.assertEqual(response.status_code, 201, response.content[:400])
        snapshot = response.json()['motor']['workbook']
        self.assertEqual(self.cell(snapshot['id'], 'C20'), 'SN-001')
        self.assertIsNotNone(read_origin(self.file(snapshot['download_url'])))
        self.assertEqual(self.client.post(reverse('register_motor'), {
            'file': SimpleUploadedFile('otro_nombre.xlsx', content), 'name': 'Duplicado',
        }).status_code, 409)
        self.assertEqual(SavedMotor.objects.count(), 1)

    def test_library_can_search_by_name_and_requires_a_session(self):
        _, first = self.marker()
        saved = self.register(self.fill(first['workbook']), 'Motor ÁREA NORTE')['motor']
        result = self.client.get(reverse('saved_motors'), {'q': 'área norte'}).json()
        self.assertEqual([item['id'] for item in result['motors']], [saved['id']])
        self.assertEqual(self.client.get(reverse('saved_motors'), {'q': 'inexistente'}).json()['motors'], [])
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse('saved_motors')).status_code, 401)
        self.assertEqual(anonymous.post(reverse('register_motor')).status_code, 401)
        self.assertEqual(anonymous.post(reverse('import_motor_document')).status_code, 401)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.editor)
        self.assertEqual(csrf.post(reverse('register_motor'), '{}', content_type='application/json').status_code, 403)

    def test_project_round_trip_keeps_independent_documents_and_known_motor_origin(self):
        plan, first = self.marker()
        saved = self.register(self.fill(first['workbook']))['motor']
        _, second = self.marker(plan)
        self.post(second['assign_url'], {'saved_motor_id': saved['id']})
        content = self.file(plan['export_project_url'])
        response = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('motores.planproj', content)})
        self.assertEqual(response.status_code, 201, response.content[:400])
        restored = response.json()['plan']['markers']
        ids = [marker['workbook']['id'] for marker in restored]
        self.assertEqual(len(set(ids)), 1)
        for marker in restored:
            metadata = self.client.get(marker['workbook']['metadata_url']).json()
            self.assertEqual(metadata['motor_registration']['saved_motor_id'], saved['id'])
        self.assertEqual(SavedMotor.objects.count(), 1)

    def test_identical_blank_forms_are_deduplicated_in_project_file_but_not_in_imported_buttons(self):
        plan, first = self.marker()
        self.marker(plan)
        content = self.file(plan['export_project_url'])
        with ZipFile(BytesIO(content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['version'], 3)
            self.assertEqual(len(manifest['workbooks']), 1)
        result = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('vacios.planproj', content)}).json()['plan']
        self.assertEqual(result['markers'][0]['workbook']['id'], result['markers'][1]['workbook']['id'])
        self.assertNotEqual(result['markers'][0]['sheet_name'], result['markers'][1]['sheet_name'])

    def test_migration_separates_existing_shared_motor_books_from_each_other_and_plan_listing(self):
        plan, first = self.marker()
        _, second = self.marker(plan)
        document = WorkbookDocument.objects.get(pk=first['workbook']['id'])
        PdfPlan.objects.filter(pk=plan['id']).update(listing=document)
        PdfMarker.objects.filter(pk=second['id']).update(workbook=document)
        module = import_module('mobileApp.migrations.0007_workbookdocument_is_motor_and_more')
        module.independent_existing_motors(apps, None)
        ids = list(PdfMarker.objects.filter(plan_id=plan['id']).values_list('workbook_id', flat=True))
        self.assertEqual(len(set(ids+[document.pk])), 3)
        self.assertEqual([bytes(WorkbookDocument.objects.get(pk=pk).content) for pk in ids], [bytes(document.content)]*2)

    def test_legacy_v1_project_with_shared_book_gets_independent_motor_copies(self):
        plan, first = self.marker()
        self.fill(first['workbook'])
        self.marker(plan)
        original = self.file(plan['export_project_url'])
        output = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(output, 'w') as target:
            manifest = json.loads(source.read('manifest.json'))
            manifest['version'] = 1
            selected = single_sheet(source.read(manifest['workbooks'][0]['path']), first['sheet_name'])
            manifest['listing'] = None
            shared = manifest['workbooks'][0]
            shared.pop('origin_uuid', None)
            manifest['workbooks'] = [shared]
            for marker in manifest['markers']:
                marker['workbook'] = shared['key']
                marker.pop('workbook_name', None)
            paths = {'manifest.json', shared['path'], manifest['source']}
            for item in source.infolist():
                if item.filename in paths:
                    target.writestr(item, json.dumps(manifest) if item.filename == 'manifest.json' else selected if item.filename == shared['path'] else source.read(item.filename))
        response = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('anterior.planproj', output.getvalue())})
        self.assertEqual(response.status_code, 201, response.content[:400])
        markers = response.json()['plan']['markers']
        self.assertEqual(markers[0]['workbook']['id'], markers[1]['workbook']['id'])
        for marker in markers:
            self.assertEqual(self.cell(marker['workbook']['id'], 'C20'), 'SN-001')
