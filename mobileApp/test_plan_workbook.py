"""The shared plan workbook, worksheet bindings and listing ownership."""
from io import BytesIO
import json
import tempfile
from zipfile import ZipFile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo

from .models import ButtonCategory, Motor, PdfMarker, PdfPlan, Plan, PlanWorkbookSheet, SavedMotor, SavedProject, WorkbookDocument
from .test_pdf import pdf_upload
from .tests import dxf_file


def source_workbook():
    workbook = Workbook()
    listing = workbook.active
    listing.title = 'Inventario'
    listing.append(['Número', 'Motor', 'Sector'])
    listing.append([1, 'Compresor', 'Taller'])
    for title, value in [('Motor A', 'Compresor'), ('Motor B', 'Bomba')]:
        sheet = workbook.create_sheet(title)
        sheet.append(['Equipo', 'Tensión'])
        sheet.append([value, 380])
        sheet['A2'].font = Font(name='Arial', bold=True, color='0044AA')
        sheet['A2'].fill = PatternFill('solid', fgColor='DDEEFF')
        sheet.merge_cells('C3:D3')
        sheet.column_dimensions['A'].width = 29
    result = BytesIO()
    workbook.save(result)
    workbook.close()
    return result.getvalue()


class SharedPlanWorkbookTests(TestCase):
    def test_two_users_share_saved_plans_and_current_excel_in_both_formats(self):
        reader = Client()
        reader.force_login(self.other)
        for kind, model in [('pdf', PdfPlan), ('dxf', Plan)]:
            with self.subTest(kind=kind):
                plan = self.plan(kind)
                self.imported(plan, as_listing=True)
                self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
                library = reader.get(reverse('saved_projects'))
                self.assertEqual(library.status_code, 200)
                entry = next(row for row in library.json()['plans'] if row['kind'] == kind and row['plan_id'] == plan['id'])
                self.assertEqual(reader.get(entry['detail_url']).status_code, 200)
                document = model.objects.get(pk=plan['id']).workbook
                download_url = reverse('workbook_download', args=[document.pk])
                response = reader.get(download_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b''.join(response.streaming_content), document.content)
                response.close()
                revision = document.revision
                self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'add'}).status_code, 200)
                self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
                document.refresh_from_db()
                self.assertGreater(document.revision, revision)
                response = reader.get(download_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(b''.join(response.streaming_content), document.content)
                self.assertEqual(int(response['X-Reloncavi-Workbook-Revision']), document.revision)
                response.close()
                self.assertEqual(reader.post(plan['save_project_url'], '{}', content_type='application/json').status_code, 403)

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('shared-admin', password='test')
        cls.author = get_user_model().objects.create_user('listing-author')
        cls.other = get_user_model().objects.create_user('another-reader')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='shared-plan-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.admin)

    def post(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type='application/json')

    def plan(self, kind='pdf'):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'), {
            'file': pdf_upload() if kind == 'pdf' else dxf_file(),
        })
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()

    def marker(self, plan):
        response = self.post(plan['markers_url'], {'page': 1, 'x': .3, 'y': .4})
        self.assertEqual(response.status_code, 201, response.content[:500])
        return response.json()['marker']

    def imported(self, plan, *, as_listing=False):
        content = source_workbook()
        response = self.client.post(plan['workbook_url'], {'file': SimpleUploadedFile('Equipos.xlsx', content), 'first_as_listing': str(as_listing).lower()})
        self.assertEqual(response.status_code, 200, response.content[:500])
        return response.json(), content

    def test_resaving_removes_deleted_and_replaced_button_sheets_in_both_plan_formats(self):
        for kind, model in [('pdf', PdfPlan), ('dxf', Plan)]:
            with self.subTest(kind=kind):
                plan = self.plan(kind)
                if kind == 'pdf':
                    self.marker(plan)
                    self.marker(plan)
                state = self.client.get(plan['detail_url']).json()
                buttons = state['markers' if kind == 'pdf' else 'motors']
                category = ButtonCategory.objects.get(name='Motores')
                for button in buttons:
                    self.assertEqual(self.post(button['assign_url'], {'action': 'set_category', 'category_id': str(category.pk)}).status_code, 200)
                self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'add'}).status_code, 200)
                self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'add'}).status_code, 200)
                self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
                for button in buttons[:-1]:
                    self.assertEqual(self.post(button['delete_url']).status_code, 200)
                survivor = buttons[-1]
                self.assertEqual(self.post(survivor['assign_url'], {'blank': True}).status_code, 200)
                document = model.objects.get(pk=plan['id']).workbook
                before = document.plan_sheets.count()
                with patch('mobileApp.terminal_monitor.queue_saved_project_sync') as publish:
                    response = self.post(plan['save_project_url'])
                self.assertEqual(response.status_code, 200, response.content[:500])
                self.assertGreater(before, 3)
                document.refresh_from_db()
                self.assertEqual(set(document.plan_sheets.values_list('name', flat=True)), {'Listado', 'Listado motores 2', 'Motores'})
                marker_model = PdfMarker if kind == 'pdf' else Motor
                current = marker_model.objects.get(pk=survivor['id'])
                self.assertEqual(current.sheet_name, 'Motores')
                if kind == 'dxf':
                    self.assertEqual(current.excel_sheets, ['Motores'])
                snapshot = SavedProject.objects.get(**{'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': plan['id']})
                self.assertEqual(publish.call_args.args[2], bytes(snapshot.content))
                with ZipFile(BytesIO(bytes(snapshot.content))) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                    self.assertEqual([button['sheet'] for button in manifest['markers']], ['Motores'])
                    book = load_workbook(BytesIO(archive.read('workbooks/w1.xlsx')))
                    try:
                        self.assertEqual(book.sheetnames, ['Listado', 'Listado motores 2', 'Motores'])
                        self.assertEqual(len(book['Motores']._images), 1)
                        self.assertEqual(len(book['Motores']._charts), 2)
                    finally:
                        book.close()

    def test_shared_sheet_survives_until_its_last_button_is_deleted(self):
        from .plan_workbook import EMPTY_SHEET
        from .xlsx_package import sheet_names

        plan = self.plan()
        self.imported(plan)
        first, second = self.marker(plan), self.marker(plan)
        for button in (first, second):
            self.assertEqual(self.post(button['assign_url'], {'sheet': 'Motor A'}).status_code, 200)
        document = PdfPlan.objects.get(pk=plan['id']).workbook
        self.assertEqual(self.post(first['delete_url']).status_code, 200)
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        document.refresh_from_db()
        self.assertEqual(sheet_names(document.content), ['Motor A'])
        self.assertEqual(list(document.plan_sheets.values_list('name', flat=True)), ['Motor A'])
        book = load_workbook(BytesIO(bytes(document.content)))
        try:
            self.assertEqual(book['Motor A']['A2'].value, 'Compresor')
        finally:
            book.close()
        self.assertEqual(self.post(second['delete_url']).status_code, 200)
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        document.refresh_from_db()
        self.assertEqual(sheet_names(document.content), [EMPTY_SHEET])
        revision = document.revision
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.revision, revision)

    def test_imported_button_source_replaces_default_sheet_without_exporting_other_source_tabs(self):
        from .xlsx_package import sheet_names

        plan = self.plan()
        marker = self.marker(plan)
        response = self.client.post(marker['assign_url'], {
            'action': 'stage', 'source_sheet': 'Motor B',
            'file': SimpleUploadedFile('Importado.xlsx', source_workbook()),
        })
        self.assertEqual(response.status_code, 200, response.content[:400])
        response = self.post(plan['save_project_url'])
        self.assertEqual(response.status_code, 200, response.content[:400])
        button = PdfMarker.objects.get(pk=marker['id'])
        self.assertEqual(sheet_names(button.workbook.content), ['Motor B'])
        self.assertEqual(sheet_names(button.source_workbook.content), ['Inventario', 'Motor A', 'Motor B'])
        self.assertEqual(button.source_output, button.sheet_name)
        book = load_workbook(BytesIO(bytes(button.workbook.content)))
        try:
            self.assertEqual(book['Motor B']['A2'].value, 'Bomba')
        finally:
            book.close()
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        button.workbook.refresh_from_db()
        self.assertEqual(sheet_names(button.workbook.content), ['Motor B'])

    def test_failed_save_rolls_back_sheet_cleanup_and_catalog_changes(self):
        from django.core.exceptions import ValidationError

        plan = self.plan()
        self.marker(plan)
        marker = self.marker(plan)
        self.assertEqual(self.post(marker['delete_url']).status_code, 200)
        document = PdfPlan.objects.get(pk=plan['id']).workbook
        original = bytes(document.content)
        rows = list(document.plan_sheets.values_list('id', 'name'))
        with patch('mobileApp.project_views.export_project', side_effect=ValidationError('No se pudo exportar.')):
            response = self.post(plan['save_project_url'])
        self.assertEqual(response.status_code, 400)
        document.refresh_from_db()
        self.assertEqual(bytes(document.content), original)
        self.assertEqual(list(document.plan_sheets.values_list('id', 'name')), rows)

    def test_default_drawings_use_one_book_and_independent_empty_motor_sheets(self):
        cad = self.plan('dxf')
        self.assertEqual(WorkbookDocument.objects.count(), 1)
        self.assertEqual(len({motor['excel']['id'] for motor in cad['motors']}), 1)
        self.assertEqual(len({motor['sheet_name'] for motor in cad['motors']}), len(cad['motors']))
        workbook = load_workbook(BytesIO(bytes(WorkbookDocument.objects.get().content)))
        try:
            self.assertIsNone(cad['listing'])
            self.assertNotIn('Listado', workbook.sheetnames)
            for name in workbook.sheetnames:
                self.assertEqual(len(workbook[name]._charts), 2)
                self.assertEqual(len(workbook[name]._images), 1)
                self.assertIsNone(workbook[name]['C18'].value)
        finally:
            workbook.close()

    def test_import_preserves_original_book_and_leaves_listing_optional(self):
        plan = self.plan()
        data, content = self.imported(plan)
        document = WorkbookDocument.objects.get(pk=data['workbook']['id'])
        self.assertEqual(bytes(document.content), content)
        self.assertIsNone(data['listing'])
        self.assertEqual(len(data['sheet_options']), 3)
        self.assertEqual(WorkbookDocument.objects.count(), 1)
        marker = self.marker(plan)
        self.assertIsNone(marker['workbook'])
        self.assertEqual(marker['sheet_name'], '')

    def test_two_buttons_share_the_same_sheet_and_see_the_same_edited_data(self):
        plan = self.plan()
        self.imported(plan)
        first, second = self.marker(plan), self.marker(plan)
        for marker in (first, second):
            response = self.post(marker['assign_url'], {'sheet': 'Motor A'})
            self.assertEqual(response.status_code, 200, response.content[:500])
        records = list(PdfMarker.objects.all())
        self.assertEqual(records[0].workbook_id, records[1].workbook_id)
        self.assertEqual(records[0].sheet_name, records[1].sheet_name)
        document = records[0].workbook
        response = self.post(reverse('workbook_save', args=[document.pk]), {
            'revision': document.revision, 'sheet': 'Motor A', 'context': 'motor',
            'edits': [{'sheet': 'Motor A', 'row': 2, 'col': 2, 'value': 440}],
        })
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertEqual([sheet['name'] for sheet in response.json()['sheets']], ['Motor A'])
        document.refresh_from_db()
        workbook = load_workbook(BytesIO(bytes(document.content)))
        try:
            self.assertEqual(workbook['Motor A']['B2'].value, 440)
            self.assertEqual(workbook['Motor B']['B2'].value, 380)
            self.assertTrue(workbook['Motor A']['A2'].font.bold)
            self.assertEqual(workbook['Motor A'].column_dimensions['A'].width, 29)
        finally:
            workbook.close()
        self.assertEqual(WorkbookDocument.objects.count(), 1)

    def test_saved_motor_is_one_sheet_and_reassignment_reuses_it_in_the_plan(self):
        plan = self.plan()
        marker = self.marker(plan)
        sheet = marker['sheet_name']
        document = PdfMarker.objects.get(pk=marker['id']).workbook
        edited = self.post(reverse('workbook_save', args=[document.pk]), {
            'revision': document.revision, 'sheet': sheet,
            'edits': [{'sheet': sheet, 'row': 18, 'col': 3, 'value': 'WEG'}],
        })
        self.assertEqual(edited.status_code, 200)
        response = self.post(reverse('register_motor'), {'workbook_id': document.pk,
            'revision': edited.json()['revision'], 'sheet': sheet, 'name': 'Compresor WEG'})
        self.assertEqual(response.status_code, 201, response.content[:500])
        saved = SavedMotor.objects.get()
        workbook = load_workbook(BytesIO(bytes(saved.workbook.content)))
        try:
            self.assertEqual(workbook.sheetnames, [sheet])
            self.assertEqual(len(workbook.active._charts), 2)
        finally:
            workbook.close()
        second = self.marker(plan)
        count = document.plan_sheets.count()
        assigned = self.post(second['assign_url'], {'saved_motor_id': saved.pk})
        self.assertEqual(assigned.status_code, 200, assigned.content[:500])
        self.assertEqual(assigned.json()['marker']['sheet_name'], sheet)
        self.assertEqual(document.plan_sheets.count(), count)
        self.assertEqual(WorkbookDocument.objects.count(), 2)  # plan + reusable original

    def test_edited_saved_motor_identity_survives_full_xlsx_export_and_import(self):
        plan = self.plan()
        self.imported(plan)
        marker = self.marker(plan)
        assigned = self.post(marker['assign_url'], {'sheet': 'Motor A'}).json()['marker']
        document = PdfMarker.objects.get(pk=marker['id']).workbook
        registered = self.post(reverse('register_motor'), {'workbook_id': document.pk,
            'revision': document.revision, 'sheet': 'Motor A', 'name': 'Motor con identidad'})
        self.assertEqual(registered.status_code, 201, registered.content[:400])
        document.refresh_from_db()
        changed = self.post(reverse('workbook_save', args=[document.pk]), {
            'revision': document.revision, 'sheet': 'Motor A',
            'edits': [{'sheet': 'Motor A', 'row': 2, 'col': 1, 'value': 'Datos actualizados'}],
        })
        self.assertEqual(changed.status_code, 200)
        document.refresh_from_db()
        second = self.plan()
        response = self.client.post(second['workbook_url'], {'file': SimpleUploadedFile('Exportado.xlsx', bytes(document.content))})
        self.assertEqual(response.status_code, 200, response.content[:400])
        new_marker = self.marker(second)
        bound = self.post(new_marker['assign_url'], {'sheet': 'Motor A'}).json()['marker']
        metadata = self.client.get(bound['workbook']['metadata_url']).json()
        self.assertTrue(metadata['motor_registration']['registered'])
        self.assertEqual(metadata['motor_registration']['saved_motor_id'], registered.json()['motor']['id'])

    def test_identical_motor_data_cannot_be_registered_under_different_sheet_names(self):
        plan = self.plan()
        self.imported(plan)
        document = PdfPlan.objects.get(pk=plan['id']).workbook
        first = self.post(reverse('register_motor'), {'workbook_id': document.pk,
            'revision': document.revision, 'sheet': 'Motor A', 'name': 'Motor original'})
        self.assertEqual(first.status_code, 201, first.content[:400])
        document.refresh_from_db()
        changed = self.post(reverse('workbook_save', args=[document.pk]), {
            'revision': document.revision, 'sheet': 'Motor B',
            'edits': [{'sheet': 'Motor B', 'row': 2, 'col': 1, 'value': 'Compresor'}],
        })
        self.assertEqual(changed.status_code, 200)
        response = self.post(reverse('register_motor'), {'workbook_id': document.pk,
            'revision': changed.json()['revision'], 'sheet': 'Motor B', 'name': 'Otro nombre'})
        self.assertEqual(response.status_code, 409, response.content[:400])
        self.assertEqual(SavedMotor.objects.count(), 1)

    def test_import_preserves_a_divergent_edited_motor_as_a_previous_sheet(self):
        from .xlsx_package import write_sheet_origins
        plan = self.plan()
        self.imported(plan)
        marker = self.marker(plan)
        self.post(marker['assign_url'], {'sheet': 'Motor A'})
        document = PdfMarker.objects.get(pk=marker['id']).workbook
        registered = self.post(reverse('register_motor'), {'workbook_id': document.pk,
            'revision': document.revision, 'sheet': 'Motor A', 'name': 'Motor con cambios previos'})
        self.assertEqual(registered.status_code, 201)
        document.refresh_from_db()
        self.assertEqual(self.post(reverse('workbook_save', args=[document.pk]), {
            'revision': document.revision, 'sheet': 'Motor A',
            'edits': [{'sheet': 'Motor A', 'row': 2, 'col': 1, 'value': 'Edición anterior que debe conservarse'}],
        }).status_code, 200)
        imported = write_sheet_origins(source_workbook(), {'Motor A': registered.json()['motor']['uuid']})
        response = self.client.post(plan['workbook_url'], {'file': SimpleUploadedFile('Actualizado.xlsx', imported)})
        self.assertEqual(response.status_code, 200, response.content[:400])
        document.refresh_from_db()
        workbook = load_workbook(BytesIO(bytes(document.content)))
        try:
            self.assertEqual(workbook['Motor A']['A2'].value, 'Compresor')
            self.assertTrue(any(sheet['A2'].value == 'Edición anterior que debe conservarse' for sheet in workbook))
        finally:
            workbook.close()

    def test_additional_listings_are_numbered_and_only_creator_or_admin_can_remove(self):
        self.client.force_login(self.author)
        plan = self.plan()
        self.imported(plan, as_listing=True)
        original = self.client.get(plan['workbook_url']).json()['listings'][0]
        added = self.post(plan['assign_listing_url'], {'action': 'add'})
        self.assertEqual(added.status_code, 200, added.content[:500])
        extra = added.json()['listings'][-1]
        self.assertEqual(extra['name'], 'Listado motores 2')
        self.assertTrue(extra['can_delete'])
        self.client.force_login(self.other)
        self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'delete', 'listing_id': extra['id']}).status_code, 403)
        self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'add'}).status_code, 403)
        self.client.force_login(self.author)
        self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'delete', 'listing_id': extra['id']}).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'delete', 'listing_id': original['id']}).status_code, 200)

    def test_view_listing_exposes_all_listing_sheets_and_no_motor_sheets(self):
        plan = self.plan()
        self.marker(plan)
        self.post(plan['assign_listing_url'], {'action': 'add'})
        result = self.post(plan['assign_listing_url'], {'action': 'add'}).json()
        response = self.client.get(result['listing']['metadata_url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([sheet['name'] for sheet in response.json()['sheets']], ['Listado', 'Listado motores 2'])
        self.assertEqual(self.post(plan['assign_listing_url'], {'workbook_id': result['workbook']['id']}).status_code, 400)

    def test_primary_listing_can_change_to_another_unassigned_imported_sheet(self):
        plan = self.plan()
        self.imported(plan)
        changed = self.post(plan['assign_listing_url'], {'action': 'set_primary', 'sheet': 'Motor B'})
        self.assertEqual(changed.status_code, 200, changed.content[:500])
        self.assertEqual(changed.json()['listings'][0]['name'], 'Motor B')
        self.assertTrue(changed.json()['listings'][0]['is_primary'])

    def test_existing_free_sheet_can_become_a_numbered_additional_listing(self):
        plan = self.plan()
        self.imported(plan, as_listing=True)
        result = self.post(plan['assign_listing_url'], {'action': 'add_sheet', 'sheet': 'Motor B'})
        self.assertEqual(result.status_code, 200, result.content[:500])
        self.assertEqual(result.json()['listings'][-1]['name'], 'Listado motores 2')
        document = WorkbookDocument.objects.get(pk=result.json()['workbook']['id'])
        workbook = load_workbook(BytesIO(bytes(document.content)))
        try:
            self.assertNotIn('Motor B', workbook.sheetnames)
            self.assertEqual(workbook['Listado motores 2']['A2'].value, 'Bomba')
        finally:
            workbook.close()

    def test_importing_another_book_cannot_remove_protection_from_previous_original(self):
        self.client.force_login(self.author)
        plan = self.plan()
        self.imported(plan, as_listing=True)
        workbook = load_workbook(BytesIO(source_workbook()))
        workbook['Inventario']['B2'] = 'Datos de otro listado'
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        response = self.client.post(plan['workbook_url'], {'file': SimpleUploadedFile('Otro.xlsx', output.getvalue()), 'first_as_listing': 'true'})
        self.assertEqual(response.status_code, 200, response.content[:400])
        result = response.json()
        previous = result['listings'][-1]
        self.assertTrue(previous['is_protected'])
        self.assertTrue(previous['can_delete'])
        self.client.force_login(self.other)
        self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'delete', 'listing_id': previous['id']}).status_code, 403)

    def test_tab_order_and_category_rename_keep_cross_sheet_formulas_and_print_areas(self):
        from .xlsx_package import arrange_sheets

        book = Workbook()
        original = book.active
        original.title = 'Nombre original'
        original['A1'] = 27
        original.print_area = 'A1:B2'
        listing = book.create_sheet('Listado')
        listing['A1'] = "='Nombre original'!A1"
        output = BytesIO()
        book.save(output)
        book.close()
        result = load_workbook(BytesIO(arrange_sheets(output.getvalue(), {'Nombre original': 'Motores'}, ['Listado', 'Nombre original'])))
        try:
            self.assertEqual(result.sheetnames, ['Listado', 'Motores'])
            self.assertEqual(result['Listado']['A1'].value, "='Motores'!A1")
            self.assertIn('$A$1:$B$2', result['Motores'].print_area)
            self.assertEqual(result['Motores']['A1'].value, 27)
        finally:
            result.close()

    def test_sheet_composition_preserves_tables_and_compatibility_namespaces(self):
        from .project_views import listing_template
        from .xlsx_package import append_sheet, package, packed
        source = Workbook()
        source.active.title = 'Ficha'
        source.active.append(['Equipo', 'Tensión'])
        source.active.append(['Motor', 380])
        table = Table(displayName='Equipos', ref='A1:B2')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
        source.active.add_table(table)
        output = BytesIO()
        source.save(output)
        source.close()
        parts = package(output.getvalue())
        parts['xl/worksheets/sheet1.xml'] = parts['xl/worksheets/sheet1.xml'].replace(b'<worksheet ',
            b'<worksheet xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" mc:Ignorable="xr" ', 1)
        addition = packed(parts)
        combined, first = append_sheet(listing_template(9), addition, 'Ficha', 'Motor 1')
        combined, second = append_sheet(combined, addition, 'Ficha', 'Motor 2')
        workbook = load_workbook(BytesIO(combined))
        try:
            self.assertEqual(workbook[first]['B2'].value, 380)
            self.assertEqual(workbook[second]['B2'].value, 380)
            self.assertNotEqual(list(workbook[first].tables), list(workbook[second].tables))
        finally:
            workbook.close()
        parts = package(combined)
        sheets = [data for name, data in parts.items() if name.startswith('xl/worksheets/') and name.endswith('.xml') and b'Ignorable' in data]
        self.assertEqual(len(sheets), 2)
        self.assertTrue(all(b'xmlns:xr=' in data for data in sheets))

    def test_standard_form_presentation_survives_custom_motor_sheet_names(self):
        from .project_views import listing_template
        from .motor_service import template_bytes
        from .xlsx_package import append_sheet
        from .workbook_service import document_sheets
        content, name = append_sheet(listing_template(9), template_bytes(), '1', 'Bomba principal')
        sheet = next(item for item in document_sheets(content) if item['name'] == name)
        self.assertEqual(sheet['layout']['presentation'], 'form')
        self.assertEqual(len(sheet['layout']['charts']), 2)
        self.assertEqual(len(sheet['layout']['images']), 1)

    def test_project_roundtrip_preserves_single_book_all_listings_and_shared_bindings(self):
        plan = self.plan()
        self.imported(plan, as_listing=True)
        for _ in range(2):
            marker = self.marker(plan)
            self.assertEqual(self.post(marker['assign_url'], {'sheet': 'Motor A'}).status_code, 200)
        self.post(plan['assign_listing_url'], {'action': 'add'})
        response = self.client.get(plan['export_project_url'])
        archive_bytes = b''.join(response.streaming_content)
        response.close()
        with ZipFile(BytesIO(archive_bytes)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['version'], 3)
            self.assertEqual(len(manifest['workbooks']), 1)
            self.assertEqual(len([name for name in archive.namelist() if name.endswith('.xlsx')]), 1)
        restored = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('Libro.planproj', archive_bytes)})
        self.assertEqual(restored.status_code, 201, restored.content[:500])
        result = restored.json()['plan']
        self.assertEqual(len(result['listings']), 2)
        self.assertEqual({marker['sheet_name'] for marker in result['markers']}, {'Motor A'})
        self.assertEqual(len({marker['workbook']['id'] for marker in result['markers']}), 1)

    def test_import_replacement_preserves_filled_previous_motor_sheets(self):
        plan = self.plan()
        marker = self.marker(plan)
        document = PdfMarker.objects.get(pk=marker['id']).workbook
        response = self.post(reverse('workbook_save', args=[document.pk]), {
            'revision': document.revision, 'sheet': marker['sheet_name'],
            'edits': [{'sheet': marker['sheet_name'], 'row': 18, 'col': 3, 'value': 'Datos anteriores'}],
        })
        self.assertEqual(response.status_code, 200)
        result, _ = self.imported(plan)
        binding = PdfMarker.objects.get(pk=marker['id'])
        self.assertTrue(binding.sheet_name)
        workbook = load_workbook(BytesIO(bytes(binding.workbook.content)))
        try:
            self.assertEqual(workbook[binding.sheet_name]['C18'].value, 'Datos anteriores')
            self.assertEqual(workbook['Motor A']['A2'].value, 'Compresor')
        finally:
            workbook.close()
