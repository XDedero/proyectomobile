import csv
from datetime import datetime
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from .models import ButtonCategory, Motor, PdfMarker, PdfPlan, Plan, SavedProject, WorkbookDocument
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
            imported = self.client.post(plan['workbook_url'], {'file': SimpleUploadedFile('Equipos.xlsx', self.download(book['download_url'])), 'first_as_listing': 'true'})
            self.assertEqual(imported.status_code, 200, imported.content[:400])
            if kind == 'pdf':
                for x in (.2, .6):
                    marker = self.post(plan['markers_url'], {'page': 2, 'x': x, 'y': .3}).json()['marker']
                    self.assertEqual(self.post(marker['assign_url'], {'workbook_id': book['id']}).status_code, 200)
            else:
                for motor in plan['motors']:
                    self.assertEqual(self.post(motor['assign_url'], {'workbook_id': book['id']}).status_code, 200)
        return self.client.get(plan['detail_url']).json(), self.download(plan['export_project_url'])

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

    def test_opening_or_deleting_cloud_copy_does_not_publish(self):
        from . import cloud_projects, terminal_monitor as monitor

        project = {'id': 'remote-pdf-1', 'name': 'Plano compartido.pdf', 'date': '2026-11-06T15:07:38Z',
                   'pdfVersion': '2026-11-06T15:07:35Z', 'excelId': 'remote-xlsx-1', 'excelName': 'Equipos.xlsx'}

        def drive(*args):
            if args[0] == '--list-projects':
                return {'ok': True, 'projects': [project]}
            self.assertEqual(args[:3], ('--read-project', '--drive-id', project['id']))
            directory = Path(args[-1])
            (directory / 'plan.pdf').write_bytes(pdf_upload().read())
            (directory / 'project.xlsx').write_bytes(excel_upload().read())
            return {'ok': True, 'project': project}

        category = ButtonCategory.objects.get(name='Motores')
        metadata = {'name': 'Plano compartido', 'plan_mode': 'imported', 'markers': [
            {'page': 1, 'x': .25, 'y': .4, 'label': 'Motor 1', 'sheet': 'Equipos',
             'category_id': str(category.pk), 'category_name': category.name},
        ]}
        self.client.force_login(self.admin)
        with (
            patch.object(cloud_projects, 'drive_command', side_effect=drive),
            patch.object(cloud_projects, 'supabase_metadata', return_value=metadata),
            patch.object(monitor, 'is_monitor_enabled', return_value=True),
            patch.object(monitor, '_log_line'),
            patch.object(monitor, 'enqueue_sync_task') as queue,
        ):
            self.assertEqual(Client().get(reverse('cloud_projects')).status_code, 401)
            self.assertEqual(self.client.get(reverse('saved_projects')).json()['plans'], [])
            cloud = self.client.get(reverse('cloud_projects')).json()['plans']
            self.assertEqual([row['name'] for row in cloud], ['Plano compartido.pdf'])
            response = self.post(cloud[0]['open_url'])
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()
            self.assertEqual(data['warning'], '')
            plan = data['plan']
            self.assertEqual(plan['markers'][0]['sheet_name'], 'Equipos')
            self.assertEqual(plan['markers'][0]['category_name'], 'Motores')
            self.assertEqual(self.post(cloud[0]['open_url']).json()['plan']['id'], plan['id'])
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.post(plan['delete_url']).status_code, 200)
            queue.assert_not_called()

    def test_saving_a_second_button_updates_the_existing_shared_excel_and_rejects_newer_versions(self):
        from . import cloud_projects, terminal_monitor as monitor

        self.client.force_login(self.admin)
        original = self.plan('pdf')
        self.post(original['assign_listing_url'], {'action': 'add'})
        self.post(original['markers_url'], {'page': 1, 'x': .2, 'y': .3})
        with ZipFile(BytesIO(self.download(original['export_project_url']))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            source = archive.read('plan/source.pdf')
            workbook = archive.read('workbooks/w1.xlsx')
        project = {'id': 'remote-pdf', 'name': 'Plano.pdf', 'date': '2026-11-06T21:00:00Z',
                   'pdfVersion': '2026-11-06T20:59:00Z', 'excelId': 'remote-xlsx', 'excelName': 'Plano.xlsx',
                   'projectKey': 'local_pdf_12'}
        updated_at = '2026-11-06T21:05:00Z'

        def drive(*args):
            if args[0] == '--list-projects':
                return {'ok': True, 'projects': [project.copy()]}
            directory = Path(args[-1])
            (directory / 'plan.pdf').write_bytes(source)
            (directory / 'project.xlsx').write_bytes(workbook)
            return {'ok': True, 'project': project.copy(), 'manifest': manifest}

        with (
            patch.object(cloud_projects, 'drive_command', side_effect=drive),
            patch.object(monitor, 'is_monitor_enabled', return_value=True),
            patch.object(monitor, '_log_line'),
            patch.object(monitor, 'enqueue_sync_task') as queue,
            patch.object(monitor, '_sync_pdf_plan_worker', return_value={'drive_file_id': project['id'], 'modified_time': updated_at, 'supabase_ok': True}) as pdf,
            patch.object(monitor, '_sync_workbook_worker', return_value={'drive_file_id': project['excelId'], 'modified_time': updated_at, 'supabase_ok': True}) as excel,
            patch.object(monitor, '_sync_marker_snapshot', return_value=True) as markers,
        ):
            plan = self.post(reverse('open_cloud_project', args=[project['id']])).json()['plan']
            # Simulate a copy downloaded before the workbook version field existed.
            PdfPlan.objects.filter(pk=plan['id']).update(cloud_workbook_version='')
            self.assertEqual(self.post(plan['markers_url'], {'page': 1, 'x': .5, 'y': .6}).status_code, 201)
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.post(plan['save_project_url']).json()['saved']['cloud_status'], 'pending')
            queue.call_args.args[0]()
            self.assertEqual(pdf.call_args.kwargs['drive_target']['id'], project['id'])
            self.assertEqual(excel.call_args.kwargs['drive_target'], {'id': project['excelId'], 'modifiedTime': project['date']})
            published = load_workbook(BytesIO(excel.call_args.args[4]), read_only=True)
            try:
                self.assertEqual(published.sheetnames, ['Listado', 'Motor 1', 'Motor 2'])
            finally:
                published.close()
            self.assertEqual([marker['sheet'] for marker in markers.call_args.args[1]['markers']], ['Motor 1', 'Motor 2'])
            self.assertEqual(SavedProject.objects.get(pdf_plan_id=plan['id']).cloud_status, 'synced')
            self.assertEqual(PdfPlan.objects.get(pk=plan['id']).cloud_workbook_version, f"{project['excelId']}:{updated_at}")

            project['date'] = '2026-11-06T21:06:00Z'
            with self.captureOnCommitCallbacks(execute=True):
                self.post(plan['save_project_url'])
            queue.call_args.args[0]()
            saved = SavedProject.objects.get(pdf_plan_id=plan['id'])
            self.assertEqual(saved.cloud_status, 'error')
            self.assertIn('versión distinta', saved.cloud_error)
            excel.assert_called_once()

    def test_drive_sync_waits_for_project_save_and_deletes_both_copies(self):
        from . import terminal_monitor as monitor

        self.client.force_login(self.admin)
        with (
            patch.object(monitor, 'is_monitor_enabled', return_value=True),
            patch.object(monitor, '_log_line'),
            patch.object(monitor, 'enqueue_sync_task') as queue,
            patch.object(monitor, '_sync_pdf_plan_worker', return_value={'drive_file_id': 'pdf1', 'modified_time': '2026-11-07T22:00:00Z', 'supabase_ok': True}) as upload_pdf,
            patch.object(monitor, '_sync_workbook_worker', return_value={'drive_file_id': 'xlsx1', 'modified_time': '2026-11-07T22:00:01Z', 'supabase_ok': True}) as upload_excel,
            patch.object(monitor, '_sync_marker_snapshot', return_value=True),
            patch.object(monitor, '_delete_drive_copy') as delete_copy,
        ):
            plan = self.plan('pdf')
            self.assertEqual(self.client.post(plan['workbook_url'], {'file': excel_upload()}).status_code, 200)
            queue.assert_not_called()
            document = PdfPlan.objects.get(pk=plan['id']).workbook
            workbook_bytes = bytes(document.content)

            with self.captureOnCommitCallbacks(execute=True):
                saved = self.post(plan['save_project_url'])
                self.assertEqual(saved.status_code, 200)
                queue.assert_not_called()
            self.assertEqual(queue.call_count, 1)
            self.assertEqual(saved.json()['saved']['cloud_status'], 'pending')
            tasks = [call.args[0] for call in queue.call_args_list]
            queue.reset_mock()
            document.content = b'Draft after saving'
            document.save(update_fields=['content'])
            queue.assert_not_called()
            for task in tasks:
                task()
            self.assertEqual(SavedProject.objects.get(pdf_plan_id=plan['id']).cloud_status, 'synced')
            self.assertEqual(upload_pdf.call_args.args[0], plan['id'])
            self.assertTrue(upload_pdf.call_args.args[2].startswith(b'%PDF-'))
            self.assertEqual(upload_excel.call_args.args[0], document.pk)
            self.assertEqual(upload_excel.call_args.args[4], workbook_bytes)
            timestamp = timezone.localtime(datetime.fromisoformat(saved.json()['saved_at'])).strftime('%Y-%m-%d_%H-%M-%S')
            self.assertEqual(upload_pdf.call_args.args[4], f'plano - {timestamp}.pdf')
            self.assertEqual(upload_excel.call_args.args[6], f'plano - {timestamp}.xlsx')

            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.post(plan['delete_url']).status_code, 200)
                queue.assert_not_called()
            for call in queue.call_args_list:
                call.args[0]()
            key = upload_pdf.call_args.args[5]
            self.assertTrue(key.startswith('project_'))
            self.assertEqual(
                [call.args for call in delete_copy.call_args_list],
                [('excel', 'workbook_documents', f'{key}_xlsx', key),
                 ('pdf', 'pdf_plans', key, key)],
            )

    def test_cloud_project_recovers_buttons_without_supabase_or_local_project(self):
        from . import cloud_projects
        original, bundle = self.bundle('pdf')
        with ZipFile(BytesIO(bundle)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            source = archive.read('plan/source.pdf')
            workbook = archive.read('workbooks/w1.xlsx')
        PdfPlan.objects.all().delete()
        WorkbookDocument.objects.all().delete()
        self.assertEqual(self.client.get(reverse('saved_projects')).json()['plans'], [])
        project = {'id': 'remote-pdf', 'name': 'Compartido.pdf', 'excelId': 'remote-book', 'excelName': 'Compartido.xlsx', 'date': '2026-11-06'}

        def drive(*args):
            if args[0] == '--list-projects':
                return {'ok': True, 'projects': [project]}
            directory = Path(args[-1])
            (directory / 'plan.pdf').write_bytes(source)
            (directory / 'project.xlsx').write_bytes(workbook)
            return {'ok': True, 'project': project, 'manifest': manifest}

        with patch.object(cloud_projects, 'drive_command', side_effect=drive), patch.object(cloud_projects, 'supabase_metadata') as database:
            listed = self.client.get(reverse('cloud_projects')).json()['plans']
            response = self.post(listed[0]['open_url'])
            self.assertEqual(response.status_code, 200, response.content)
            restored = response.json()['plan']
            self.assertEqual(response.json()['warning'], '')
            self.assertEqual(len(restored['markers']), len(manifest['markers']))
            self.assertEqual([m['sheet_name'] for m in restored['markers']], [m['sheet'] for m in manifest['markers']])
            self.assertEqual(self.download(restored['workbook']['download_url']), workbook)
            database.assert_not_called()
            manifest['markers'][0]['x'] = 'invalid'
            self.assertEqual(self.post(listed[0]['open_url']).status_code, 400)

    def test_cloud_status_reports_failures_and_does_not_overwrite_a_newer_save(self):
        from . import terminal_monitor as monitor
        self.client.force_login(self.admin)
        plan = self.plan('pdf')
        with (
            patch.object(monitor, 'is_monitor_enabled', return_value=True),
            patch.object(monitor, '_log_line'),
            patch.object(monitor, 'enqueue_sync_task') as queue,
            patch('mobileApp.cloud_projects.shared_publish_target', return_value={'id':'pdf1', 'excelId':'xlsx1', 'projectKey':'project_test', 'pdfVersion':'2026-11-07T22:00:00Z', 'date':'2026-11-07T22:00:01Z'}),
            patch.object(monitor, '_sync_pdf_plan_worker', return_value={'drive_file_id': 'pdf1', 'modified_time': '2026-11-07T22:00:00Z', 'supabase_ok': False}),
            patch.object(monitor, '_sync_workbook_worker', return_value={'drive_file_id': 'xlsx1', 'modified_time': '2026-11-07T22:00:01Z', 'supabase_ok': False}) as book,
        ):
            for _ in range(2):
                with self.captureOnCommitCallbacks(execute=True):
                    self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
            queue.call_args_list[0].args[0]()
            self.assertEqual(SavedProject.objects.get(pdf_plan_id=plan['id']).cloud_status, 'pending')
            queue.call_args_list[1].args[0]()
            self.assertEqual(SavedProject.objects.get(pdf_plan_id=plan['id']).cloud_status, 'partial')
            self.assertTrue(book.call_args.args[7]['sheets'])
            with self.captureOnCommitCallbacks(execute=True):
                self.post(plan['save_project_url'])
            book.return_value = None
            queue.call_args.args[0]()
            self.assertEqual(SavedProject.objects.get(pdf_plan_id=plan['id']).cloud_status, 'error')

    def test_button_changes_stay_local_until_save_and_new_projects_have_distinct_keys(self):
        from . import terminal_monitor as monitor
        self.client.force_login(self.admin)
        with patch.object(monitor, 'is_monitor_enabled', return_value=True), patch.object(monitor, '_log_line'), patch.object(monitor, 'enqueue_sync_task') as queue:
            first, second = self.plan('pdf'), self.plan('pdf')
            self.assertNotEqual(PdfPlan.objects.get(pk=first['id']).cloud_key, PdfPlan.objects.get(pk=second['id']).cloud_key)
            marker = self.post(first['markers_url'], {'page': 1, 'x': .2, 'y': .3}).json()['marker']
            PdfMarker.objects.filter(pk=marker['id']).delete()
            queue.assert_not_called()

    def test_clear_local_removes_working_files_without_sending_a_cloud_delete(self):
        from . import terminal_monitor as monitor
        self.client.force_login(self.admin)
        plan = self.plan('pdf')
        saved = self.post(plan['save_project_url']).json()['saved']
        model = PdfPlan.objects.get(pk=plan['id'])
        path, workbook_id = Path(model.source.path), model.workbook_id
        with patch.object(monitor, 'is_monitor_enabled', return_value=True), patch.object(monitor, '_log_line'), patch.object(monitor, 'enqueue_sync_task') as queue:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post(saved['clear_url'])
            self.assertEqual(response.status_code, 200, response.content)
            queue.assert_not_called()
        self.assertFalse(path.exists())
        self.assertFalse(SavedProject.objects.filter(pk=saved['id']).exists())
        self.assertFalse(WorkbookDocument.objects.filter(pk=workbook_id).exists())

    def test_absolute_cloud_delete_reverts_drive_when_supabase_cannot_delete(self):
        from . import cloud_projects as cloud
        self.client.force_login(self.admin)
        plan = self.plan('pdf')
        local = PdfPlan.objects.get(pk=plan['id'])
        pair = {'id': 'pdf-verified', 'excelId': 'xls-verified', 'name': 'Archivo.pdf', 'projectKey': local.cloud_key}
        url = reverse('delete_cloud_project', args=[pair['id']])
        with patch.object(cloud, 'list_cloud_projects', return_value=[pair]), patch.object(cloud, 'drive_command') as drive, patch.object(cloud, 'supabase_query') as sql:
            sql.side_effect = [{}, cloud.CloudUnavailable('Supabase no respondió')]
            self.assertEqual(self.post(url).status_code, 503)
            self.assertTrue(PdfPlan.objects.filter(pk=local.pk).exists())
            self.assertEqual([call.args[0] for call in drive.call_args_list], ['--delete-project', '--restore-project'])
            sql.side_effect = None
            self.assertEqual(self.post(url).json()['local_ids'], [local.pk])
        self.assertFalse(PdfPlan.objects.filter(pk=local.pk).exists())

    def test_button_imports_one_selected_sheet_and_preserves_independent_categories(self):
        plan = self.plan('pdf')
        first = self.post(plan['markers_url'], {'page': 1, 'x': .2, 'y': .3}).json()['marker']
        second = self.post(plan['markers_url'], {'page': 1, 'x': .6, 'y': .3}).json()['marker']
        document = PdfPlan.objects.get(pk=plan['id']).workbook
        preview = self.client.post(first['assign_url'], {'file': excel_upload(), 'action': 'inspect', 'source_sheet': 'Otra hoja'})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()['sheets'], ['Equipos', 'Otra hoja'])
        self.assertEqual(preview.json()['rows'][0][0], 'Conservar')
        self.assertEqual(self.client.post(first['assign_url'], {'file': excel_upload()}).status_code, 400)
        self.assertEqual(WorkbookDocument.objects.get(pk=document.pk).revision, document.revision)

        chosen = self.client.post(first['assign_url'], {'file': excel_upload(), 'source_sheet': 'Otra hoja'})
        self.assertEqual(chosen.status_code, 200)
        self.assertEqual(chosen.json()['marker']['sheet_name'], 'Otra hoja')
        categories = dict(ButtonCategory.objects.values_list('name', 'id'))
        for marker, category in ((first, 'Motores'), (second, 'Controladores')):
            response = self.post(marker['assign_url'], {'action': 'set_category', 'category_id': str(categories[category])})
            self.assertEqual(response.json()['marker']['category_name'], category)
        shared = self.post(second['assign_url'], {'sheet': 'Otra hoja'}).json()['marker']
        self.assertEqual(shared['category_name'], 'Controladores')
        self.assertEqual(PdfMarker.objects.get(pk=first['id']).category_id, categories['Motores'])

        imported = self.client.post(second['assign_url'], {
            'file': SimpleUploadedFile('Control.csv', b'ID;Nota\r\n001;=texto\r\n'),
            'source_sheet': 'Control',
        })
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()['marker']['category_name'], 'Controladores')
        saved = self.post(plan['save_project_url'])
        self.assertEqual(saved.status_code, 200)
        bundle = self.download(saved.json()['saved']['export_url'])
        with ZipFile(BytesIO(bundle)) as archive:
            workbook = load_workbook(BytesIO(archive.read('workbooks/w1.xlsx')))
            try:
                self.assertEqual(workbook.sheetnames[:2], ['Controladores', 'Motores'])
                self.assertNotIn('Listado', workbook.sheetnames)
                self.assertNotIn('Equipos', workbook.sheetnames)
                self.assertEqual(workbook['Controladores']['A2'].value, '001')
                self.assertEqual(workbook['Controladores']['B2'].data_type, 's')
            finally:
                workbook.close()
        restored = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('Proyecto.planproj', bundle)})
        self.assertEqual(restored.status_code, 201)
        self.assertEqual([marker['category_name'] for marker in restored.json()['plan']['markers']], ['Motores', 'Controladores'])

    def test_new_button_keeps_source_tabs_and_materializes_only_its_selected_tab_on_save(self):
        plan = self.plan('pdf')
        category = ButtonCategory.objects.get(name='Controladores')
        position = {'page': 1, 'x': .25, 'y': .5, 'origin': 'upload', 'category_id': str(category.pk)}
        created = self.client.post(plan['markers_url'], {'placement': json.dumps(position), 'file': excel_upload()})
        self.assertEqual(created.status_code, 201)
        button = created.json()['marker']
        self.assertEqual(button['category_name'], 'Controladores')
        self.assertEqual(button['source_sheet'], 'Equipos')
        source_url = button['workbook']['metadata_url']
        self.assertEqual([sheet['name'] for sheet in self.client.get(source_url).json()['sheets']], ['Equipos', 'Otra hoja'])

        def shared_sheets():
            document = PdfPlan.objects.get(pk=plan['id']).workbook
            workbook = load_workbook(BytesIO(document.content), read_only=True)
            try:
                return workbook.sheetnames
            finally:
                workbook.close()

        self.assertEqual(shared_sheets(), ['Sin fichas'])
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        self.assertEqual(shared_sheets(), ['Controladores'])
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        self.assertEqual(shared_sheets(), ['Controladores'])

        selected = self.post(button['assign_url'], {
            'action': 'select_source', 'source_sheet': 'Otra hoja', 'source_workbook_id': button['workbook']['id'],
        })
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(shared_sheets(), ['Controladores'])
        self.assertEqual(self.client.get(plan['detail_url']).json()['markers'][0]['workbook']['preferred_sheet'], 'Otra hoja')
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        self.assertEqual(shared_sheets(), ['Controladores'])
        self.assertEqual(len(self.client.get(source_url).json()['sheets']), 2)

        replacement = self.client.post(button['assign_url'], {
            'action': 'stage', 'source_sheet': 'Nuevo',
            'file': SimpleUploadedFile('Nuevo.csv', b'Equipo,ID\r\nTablero,001\r\n'),
        })
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(shared_sheets(), ['Controladores'])
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        self.assertEqual(shared_sheets(), ['Controladores'])
        self.assertEqual(replacement.json()['marker']['category_name'], 'Controladores')

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
        self.assertEqual(len(ids), 1)
        self.assertIn(restored['listing']['id'], ids)
        self.assertEqual(len({item['sheet_name'] for item in restored['markers']}), 1)
        for marker in restored['markers']:
            self.assertEqual(self.download(marker['workbook']['download_url']), xlsx)
        self.assertEqual(self.download(restored['listing']['download_url']), xlsx)

    def test_save_orders_optional_listings_then_category_names_and_keeps_button_links(self):
        plan = self.plan('pdf')
        self.assertEqual(plan['listings'], [])
        categories = dict(ButtonCategory.objects.values_list('name', 'id'))
        for x, name in ((.2, 'Motores'), (.3, 'Controladores'), (.4, 'Motores')):
            marker = self.post(plan['markers_url'], {'page': 1, 'x': x, 'y': .2,
                'origin': 'blank', 'category_id': str(categories[name])})
            self.assertEqual(marker.status_code, 201, marker.content)
        self.assertEqual(self.post(plan['assign_listing_url'], {'action': 'add'}).status_code, 200)
        preview = self.client.post(plan['assign_listing_url'], {'action': 'inspect', 'file': excel_upload()})
        self.assertEqual(preview.json()['sheets'], ['Equipos', 'Otra hoja'])
        added = self.client.post(plan['assign_listing_url'], {'action': 'import', 'source_sheet': 'Otra hoja', 'file': excel_upload()})
        self.assertEqual(added.status_code, 200, added.content)
        saved = self.post(plan['save_project_url'])
        self.assertEqual(saved.status_code, 200, saved.content)
        data = saved.json()['plan']
        self.assertEqual([button['sheet_name'] for button in data['markers']], ['Motores 1', 'Controladores', 'Motores 2'])
        with ZipFile(BytesIO(self.download(saved.json()['saved']['export_url']))) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            book = load_workbook(BytesIO(archive.read('workbooks/w1.xlsx')), read_only=True)
            try:
                self.assertEqual(book.sheetnames, ['Listado', 'Listado motores 2', 'Controladores', 'Motores 1', 'Motores 2'])
                self.assertEqual([sheet['name'] for sheet in manifest['sheets']], book.sheetnames)
                self.assertEqual(book['Listado motores 2']['A1'].value, 'Conservar')
                self.assertEqual([row['sheet'] for row in manifest['markers']], ['Motores 1', 'Controladores', 'Motores 2'])
            finally:
                book.close()

    def test_dxf_round_trip_preserves_motor_world_positions_and_attachments(self):
        original, content = self.bundle('dxf')
        restored_response = self.client.post(reverse('upload_project'), {'file': SimpleUploadedFile('cad.planproj', content)})
        self.assertEqual(restored_response.status_code, 201, restored_response.content[:400])
        restored = restored_response.json()['plan']
        self.assertEqual([(motor['name'], motor['x'], motor['y'], motor['z']) for motor in restored['motors']], [(motor['name'], motor['x'], motor['y'], motor['z']) for motor in original['motors']])
        ids = {motor['excel']['id'] for motor in restored['motors']}
        self.assertEqual(len(ids), 1)
        self.assertIn(restored['listing']['id'], ids)
        for motor in restored['motors']:
            self.assertEqual(self.download(motor['excel']['url']), self.download(restored['listing']['download_url']))
        self.assertEqual(self.download(restored['export_project_url'])[:2], b'PK')

    def test_saved_snapshot_contains_bytes_from_save_including_excel_updates(self):
        plan, _ = self.bundle('pdf')
        self.client.force_login(self.admin)
        document = PdfPlan.objects.get(pk=plan['id']).listing
        edit = {'revision': document.revision, 'edits': [{'sheet': 'Equipos', 'row': 2, 'col': 2, 'value': 123}]}
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
            self.assertEqual(book[manifest['markers'][0]['sheet']]['B2'].value, 15)
            book.close()

    def test_template_matches_list_capture_has_logo_and_grows_for_more_motors(self):
        plan = self.plan('pdf')
        for index in range(12):
            self.post(plan['markers_url'], {'page': 1, 'x': .1 + index*.01, 'y': .3})
        response = self.post(plan['assign_listing_url'], {'create_template': True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['listings'][-1]['name'], 'Listado')
        from .project_views import listing_template
        book = load_workbook(BytesIO(listing_template(12)))
        self.assertEqual(book.sheetnames, ['Listado'])
        sheet = book['Listado']
        self.assertIn('IDENTIFICACIÓN DE MOTORES', sheet['B2'].value)
        self.assertEqual([sheet.cell(7, col).value for col in (2, 3, 6)], ['Nº Motor', 'Especificaciones del Motor', 'Sector'])
        self.assertEqual([sheet.cell(7+index, 2).value for index in range(1, 13)], list(range(1, 13)))
        self.assertEqual(len(sheet._images), 1)
        self.assertIn('C19:E19', str(sheet.merged_cells))
        self.assertIn('F19:H19', str(sheet.merged_cells))
        book.close()

    def test_creator_can_assign_listing_save_and_edit_its_cells(self):
        plan = self.plan('dxf')
        response = self.post(plan['assign_listing_url'], {'create_template': True})
        listing = response.json()['listing']
        self.assertEqual(self.post(plan['save_project_url']).status_code, 200)
        metadata = self.client.get(listing['metadata_url']).json()
        self.assertTrue(metadata['can_edit'])
        self.assertEqual(self.post(listing['save_url'], {'revision': metadata['revision'], 'edits': [{'sheet': 'Listado', 'row': 8, 'col': 3, 'value': 'Permitido'}]}).status_code, 200)

    def test_adding_listing_columns_and_motors_keeps_logical_merged_columns_styles_and_logo(self):
        plan = self.plan('pdf')
        listing = self.post(plan['assign_listing_url'], {'create_template': True}).json()['listing']
        self.client.force_login(self.admin)
        metadata = self.client.get(listing['metadata_url']).json()
        self.assertEqual(metadata['sheets'][0]['layout']['content_bounds']['max_col'], 8)
        changed = self.post(listing['save_url'], {
            'revision': metadata['revision'],
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

    def test_cad_marker_drag_by_creator_keeps_excel(self):
        plan, _ = self.bundle('dxf')
        motor = Motor.objects.get(pk=plan['motors'][0]['id'])
        url = reverse('move_cad_marker', args=[motor.pk])
        target = {'svg_x': motor.svg_x + 50, 'svg_y': motor.svg_y + 50}
        self.assertEqual(self.post(url, target).status_code, 200)
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
