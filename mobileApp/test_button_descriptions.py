import json
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from .models import ButtonCategory, PdfMarker
from .motor_service import template_bytes
from .test_pdf import excel_upload, pdf_upload
from .workbook_service import edited_document


class ButtonDescriptionTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = override_settings(MEDIA_ROOT=directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(get_user_model().objects.create_superuser('button-admin', password='test-only'))
        self.plan = self.client.post(reverse('import_pdf'), {'file': pdf_upload()}).json()

    def post(self, url, data):
        return self.client.post(url, json.dumps(data), content_type='application/json')

    def button(self, *, enabled=True, upload=None):
        data = {'page': 1, 'x': .4, 'y': .5, 'show_description': enabled,
                'category_id': str(ButtonCategory.objects.get(name='Motores').pk),
                'origin': 'upload' if upload else 'blank'}
        response = (self.client.post(self.plan['markers_url'], {'file': upload, 'placement': json.dumps(data)})
                    if upload else self.post(self.plan['markers_url'], data))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['marker']

    def details(self, button):
        return self.client.get(button['details_url']).json()

    def edit_excel(self, button, row, col, value):
        marker = PdfMarker.objects.select_related('workbook').get(pk=button['id'])
        response = self.post(button['workbook']['save_url'], {'revision': marker.workbook.revision,
            'edits': [{'sheet': marker.sheet_name, 'row': row, 'col': col, 'value': value}]})
        self.assertEqual(response.status_code, 200, response.content[:400])

    def test_optional_template_descriptions_follow_latest_cell_change_and_keep_excel_unchanged(self):
        button = self.button(enabled=False)
        details = self.details(button)
        self.assertFalse(details['show_description'])
        self.assertEqual(len(details['descriptions']), 10)
        self.assertTrue(all(row['value'] == '' for row in details['descriptions']))
        self.edit_excel(button, 18, 3, 'ABB')
        details = self.details(button)
        row = next(row for row in details['descriptions'] if row['id'] == 'marca')
        row['value'] = 'Revisar placa'
        details['descriptions'].append({'key': 'Observación', 'value': 'Revisión pendiente'})
        saved = self.post(button['details_url'], {'name': 'Bomba 1', 'color': '#3979CB', 'show_description': True,
                                                 'descriptions': details['descriptions']})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()['button']['button_color'], '#3979cb')
        self.assertTrue(saved.json()['button']['show_description'])
        doc = PdfMarker.objects.get(pk=button['id']).workbook
        book = load_workbook(BytesIO(doc.content), read_only=True)
        self.assertEqual(book[button['sheet_name']]['C18'].value, 'ABB')
        book.close()
        self.edit_excel(button, 19, 3, 'Modelo A')
        self.assertEqual(next(row['value'] for row in self.details(button)['descriptions'] if row['id'] == 'marca'), 'Revisar placa')
        self.edit_excel(button, 18, 3, 'Siemens')
        self.edit_excel(button, 18, 3, 'ABB')
        self.assertEqual(next(row['value'] for row in self.details(button)['descriptions'] if row['id'] == 'marca'), 'ABB')
        self.post(button['details_url'], {'name': '', 'color': '', 'show_description': False})
        self.assertFalse(self.details(button)['show_description'])
        self.assertEqual(len(self.details(button)['descriptions']), 11)
        self.post(button['details_url'], {'name': '', 'color': '', 'show_description': True, 'descriptions': []})
        self.assertEqual(self.details(button)['descriptions'], [])

    def test_import_detects_a_filled_standard_template_and_portably_keeps_its_details(self):
        from .xlsx_package import sheet_names
        plain = self.button(upload=excel_upload())
        self.assertEqual(self.details(plain)['descriptions'], [])
        template = template_bytes()
        sheet = sheet_names(template)[0]
        filled, _ = edited_document(template, [{'sheet': sheet, 'row': 18, 'col': 3, 'value': 'Motor importado'}], [])
        button = self.button(upload=SimpleUploadedFile('Original.xlsx', filled))
        self.assertEqual(next(row['value'] for row in self.details(button)['descriptions'] if row['id'] == 'marca'), 'Motor importado')
        self.post(button['details_url'], {'name': 'Prueba portátil', 'color': '#248852'})
        saved = self.post(self.plan['save_project_url'], {})
        self.assertEqual(saved.status_code, 200, saved.content)
        download = self.client.get(saved.json()['saved']['export_url'])
        bundle = b''.join(download.streaming_content)
        download.close()
        with ZipFile(BytesIO(bundle)) as archive:
            data = json.loads(archive.read('manifest.json'))
            self.assertEqual(data['markers'][1]['button_color'], '#248852')
            self.assertTrue(data['markers'][1]['show_description'])
            self.assertEqual(len(data['markers'][1]['descriptions']), 10)
        restored = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('Restaurado.planproj', bundle)})
        self.assertEqual(restored.status_code, 201, restored.content[:400])
        marker = restored.json()['plan']['markers'][1]
        self.assertEqual(marker['label'], 'Prueba portátil')
        self.assertEqual(marker['button_color'], '#248852')
        self.assertTrue(marker['show_description'])
        self.assertEqual(next(row['value'] for row in self.details(marker)['descriptions'] if row['id'] == 'marca'), 'Motor importado')

    def test_supabase_publication_contains_reference_without_description_text(self):
        from . import terminal_monitor as monitor

        manifest = {'kind': 'pdf', 'listing': None, 'sheets': [], 'markers': [
            {'descriptions': [{'key': 'Texto privado de prueba', 'value': 'Contenido de prueba en Drive'}]}, {}]}
        with patch.object(monitor, '_upload_drive_content', return_value={'drive_file_id': 'xlsx-original'}) as drive, \
                patch.object(monitor, 'run_supabase_sql_sync', return_value=True) as database:
            monitor._sync_workbook_worker(1, 1, 'template', False, b'xls', 'local_pdf_1', 'Plano.xlsx', manifest, b'pdf')
        self.assertEqual(drive.call_args.args[4], manifest)
        sql = database.call_args.args[1]
        self.assertIn("jsonb_array_elements_text('[0]'::jsonb)", sql)
        self.assertIn("'#markers/'", sql)
        self.assertNotIn('Contenido de prueba en Drive', sql)
