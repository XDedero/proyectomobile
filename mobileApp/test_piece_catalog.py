from io import BytesIO
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from .models import ButtonCategory, PdfPlan, SavedPiece, SavedProject, WorkbookDocument, PieceDeletion
from .test_pdf import pdf_upload


def source_book():
    book = Workbook()
    book.active.title = 'No corresponde'
    book.active['A2'] = 'Primera'
    book.create_sheet('Elegida')['A2'] = 'Información de la segunda'
    book.create_sheet('Tercera')['A2'] = 'Tercera'
    stream = BytesIO(); book.save(stream); book.close()
    return stream.getvalue()


class PieceCatalogTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        setting = override_settings(MEDIA_ROOT=temporary.name)
        setting.enable(); self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_superuser('piece-admin','', 'secret')
        self.client.force_login(self.user)
        self.category = ButtonCategory.objects.get(name='Motores')

    def post(self, name, data, args=None):
        return self.client.post(reverse(name,args=args), json.dumps(data), content_type='application/json')

    def register_selected(self, name='Bomba'):
        imported = self.client.post(reverse('import_piece_document'), {'file':SimpleUploadedFile('origen.xlsx', source_book()), 'category_id':str(self.category.pk)})
        self.assertEqual(imported.status_code,201,imported.content)
        doc = imported.json()['workbook']
        response = self.post('register_piece', {'workbook_id':doc['id'], 'revision':doc['revision'], 'sheet':'Elegida', 'name':name, 'category_id':str(self.category.pk)})
        self.assertEqual(response.status_code,201,response.content)
        return SavedPiece.objects.get(pk=response.json()['piece']['id'])

    def test_piece_uses_only_selected_sheet_and_preserves_source_tabs(self):
        record = self.register_selected()
        book = load_workbook(BytesIO(bytes(record.workbook.content)))
        self.assertEqual(book.sheetnames,['Elegida'])
        self.assertEqual(book.active['A2'].value,'Información de la segunda')
        book.close()
        original = WorkbookDocument.objects.exclude(pk=record.workbook_id).get()
        book = load_workbook(BytesIO(bytes(original.content)))
        self.assertEqual(len(book.sheetnames),3); book.close()
        again=self.post('register_piece', {'workbook_id':original.pk,'revision':original.revision,'sheet':'Elegida','name':'Duplicada','category_id':str(self.category.pk)})
        self.assertEqual(again.status_code,409)
        other=self.post('register_piece', {'workbook_id':original.pk,'revision':original.revision,'sheet':'Tercera','name':'Otra pieza','category_id':str(self.category.pk)})
        self.assertEqual(other.status_code,201,other.content)

    def test_catalog_combines_selected_sheets_and_retries_without_duplicates(self):
        from . import piece_service
        first = self.register_selected()
        second = self.register_selected('Ventilador')
        result = {}
        def publish(*args):
            self.assertEqual(args[0],'--write-pieces')
            result['content'] = Path(args[args.index('--file')+1]).read_bytes()
            result['index'] = json.loads(Path(args[args.index('--index')+1]).read_text())
            return {'library':{'id':'catalog', 'modifiedTime':'v1'}}
        with patch.object(piece_service,'read_catalog',return_value=(None,[],None)), patch('mobileApp.cloud_projects.drive_command',side_effect=publish), patch('mobileApp.cloud_projects.supabase_query',return_value={'rows':[{'singleton':True}]}):
            piece_service.sync_catalog()
        book=load_workbook(BytesIO(result['content']))
        self.assertEqual(book.sheetnames,['Motores-Bomba','Motores-Ventilador'])
        self.assertEqual([sheet['A2'].value for sheet in book],['Información de la segunda']*2); book.close()
        first.refresh_from_db(); self.assertEqual(first.cloud_status,'synced')
        with patch.object(piece_service,'read_catalog',return_value=({'id':'catalog','modifiedTime':'v1'},result['index'],result['content'])), patch('mobileApp.cloud_projects.drive_command',side_effect=publish), patch('mobileApp.cloud_projects.supabase_query',return_value={'rows':[{'singleton':True}]}):
            piece_service.sync_catalog()
        self.assertEqual(len(result['index']),2)

    def test_only_rectangular_area_survives_portable_save(self):
        plan=self.client.post(reverse('import_pdf'),{'file':pdf_upload()}).json()
        marker=self.client.post(plan['markers_url'],json.dumps({'page':1,'x':.3,'y':.4}),content_type='application/json').json()['marker']
        response=self.post('project_display',{'display_settings':{'opacity':0,'legend':True,'hover':True},'button_id':marker['id'],'has_area':True,'area_shape':'rectangle','area_radius_x':.2,'area_radius_y':.1},args=['pdf',plan['id']])
        self.assertEqual(response.status_code,200,response.content)
        from .project_service import export_project, import_project
        source=PdfPlan.objects.get(pk=plan['id'])
        archive=export_project('pdf',source)
        kind, restored=import_project(SimpleUploadedFile('copia.planproj',archive),self.user)
        self.assertEqual(restored.display_settings,{})
        self.assertEqual(restored.markers.get().area_shape,'rectangle')
        bad=self.post('project_display',{'display_settings':{'opacity':2}},args=['pdf',plan['id']])
        self.assertEqual(bad.status_code,400)

    def test_uploaded_receipt_retries_only_database_and_ignores_old_saves(self):
        from . import terminal_monitor as monitor
        plan=self.client.post(reverse('import_pdf'),{'file':pdf_upload()}).json()
        saved=SavedProject.objects.create(pdf_plan_id=plan['id'],name='Proyecto',cloud_status='uploaded',cloud_receipt={'sql':'SELECT 1;'})
        monitor._REGISTRATION_RETRIES.clear()
        with patch.object(monitor,'is_monitor_enabled',return_value=True), patch.object(monitor,'enqueue_sync_task') as queue, patch.object(monitor,'run_supabase_sql_sync',return_value=True) as db, patch.object(monitor,'_upload_drive_content') as upload:
            monitor.retry_cloud_registrations(); queue.call_args.args[0]()
            saved.refresh_from_db(); self.assertEqual(saved.cloud_status,'synced'); db.assert_called_once(); upload.assert_not_called()

    def test_edit_and_replace_use_only_selected_sheet_and_keep_identity(self):
        piece=self.register_selected()
        before=bytes(piece.workbook.content)
        edit=self.post('edit_piece',{},[piece.pk]).json()['workbook']
        replacement=self.client.post(reverse('import_piece_document'),{'file':SimpleUploadedFile('replacement.xlsx',source_book()),'category_id':str(self.category.pk)}).json()['workbook']
        piece.workbook.refresh_from_db();self.assertEqual(bytes(piece.workbook.content),before)
        payload={'piece_revision':edit['piece_edit']['revision'],'workbook_id':replacement['id'],'revision':replacement['revision'],
            'sheet':'Tercera','name':'Bomba editada','category_id':str(self.category.pk)}
        response=self.post('update_piece',payload,[piece.pk])
        self.assertEqual(response.status_code,200,response.content)
        piece.refresh_from_db();piece.workbook.refresh_from_db()
        book=load_workbook(BytesIO(bytes(piece.workbook.content)))
        self.assertEqual(book.sheetnames,['Tercera']);self.assertEqual(book.active['A2'].value,'Tercera');book.close()
        self.assertEqual(piece.cloud_status,'local')
        self.assertEqual(SavedPiece.objects.count(),1)
        self.assertEqual(self.post('update_piece',payload,[piece.pk]).status_code,409)

    def test_new_button_can_switch_piece_sheets_and_edits_are_independent(self):
        first=self.register_selected('Bomba')
        second=self.register_selected('Otra')
        plan=self.client.post(reverse('import_pdf'),{'file':pdf_upload()}).json()
        response=self.client.post(plan['markers_url'],json.dumps({'page':1,'x':.3,'y':.4,'origin':'piece',
            'category_id':str(self.category.pk),'saved_piece_id':first.pk}),content_type='application/json')
        self.assertEqual(response.status_code,201,response.content)
        marker=response.json()['marker']
        source=WorkbookDocument.objects.get(pk=marker['workbook']['id'])
        self.assertEqual(len(source.source_pieces),2)
        selected=next(name for name,item in source.source_pieces.items() if item['id']==str(second.uuid))
        change=self.client.post(marker['assign_url'],json.dumps({'action':'select_source','source_sheet':selected,'source_workbook_id':source.pk}),content_type='application/json')
        self.assertEqual(change.status_code,200,change.content)
        save=self.client.post(reverse('workbook_save',args=[source.pk]),json.dumps({'revision':source.revision,'sheet':selected,
            'edits':[{'sheet':selected,'row':2,'col':1,'value':'Solo para este botón'}]}),content_type='application/json')
        self.assertEqual(save.status_code,200,save.content)
        result=self.client.post(plan['save_project_url'], '{}',content_type='application/json')
        self.assertEqual(result.status_code,200,result.content)
        restored=PdfPlan.objects.get(pk=plan['id']);button=restored.markers.get()
        self.assertEqual(button.sheet_name,selected)
        book=load_workbook(BytesIO(bytes(restored.workbook.content)))
        self.assertEqual(book.sheetnames,[selected]);self.assertEqual(book.active['A2'].value,'Solo para este botón');book.close()
        second.workbook.refresh_from_db()
        book=load_workbook(BytesIO(bytes(second.workbook.content)))
        self.assertEqual(book.active['A2'].value,'Información de la segunda');book.close()
        # Deleting the catalog entry leaves this saved project sheet intact.
        deleted=self.post('delete_piece',{'revision':second.workbook.revision},[second.pk])
        self.assertEqual(deleted.status_code,200,deleted.content)
        self.assertTrue(PieceDeletion.objects.filter(pk=second.uuid,synced=False).exists())
        self.assertFalse(SavedPiece.objects.filter(pk=second.pk).exists())
        restored.workbook.refresh_from_db();self.assertIn(selected,load_workbook(BytesIO(bytes(restored.workbook.content))).sheetnames)

    def test_catalog_replaces_a_sheet_and_removes_deleted_piece_without_resurrection(self):
        from . import piece_service
        first=self.register_selected('Bomba');second=self.register_selected('Otra')
        cloud={'library':None,'index':[],'content':None};queries=[]
        def read(): return cloud['library'],json.loads(json.dumps(cloud['index'])),cloud['content']
        def publish(*args):
            cloud['content']=Path(args[args.index('--file')+1]).read_bytes()
            cloud['index']=json.loads(Path(args[args.index('--index')+1]).read_text())
            cloud['library']={'id':'catalog','modifiedTime':'v1'}
            return {'library':cloud['library']}
        def query(sql,**kwargs):
            queries.append(sql)
            return {'rows':[{'singleton':True}]} if 'RETURNING singleton' in sql else {'rows':[]}
        with patch.object(piece_service,'read_catalog',side_effect=read),patch('mobileApp.cloud_projects.drive_command',side_effect=publish),patch('mobileApp.cloud_projects.supabase_query',side_effect=query):
            piece_service.sync_catalog()
            draft=self.post('edit_piece',{},[first.pk]).json()['workbook']
            self.client.post(draft['save_url'],json.dumps({'revision':draft['revision'],'edits':[{'sheet':'Elegida','row':2,'col':1,'value':'Actualizada'}]}),content_type='application/json')
            doc=WorkbookDocument.objects.get(pk=draft['id'])
            response=self.post('update_piece',{'piece_revision':1,'workbook_id':doc.pk,'revision':doc.revision,'sheet':'Elegida'},[first.pk])
            self.assertEqual(response.status_code,200,response.content)
            piece_service.sync_catalog()
            book=load_workbook(BytesIO(cloud['content']))
            self.assertEqual(len(book.sheetnames),2);self.assertEqual(book['Motores-Bomba']['A2'].value,'Actualizada');book.close()
            first.workbook.refresh_from_db()
            stale_index=json.loads(json.dumps(cloud['index']));stale_content=cloud['content']
            self.post('delete_piece',{'revision':first.workbook.revision},[first.pk])
            piece_service.sync_catalog()
            self.assertEqual([item['id'] for item in cloud['index']],[str(second.uuid)])
            self.assertEqual(load_workbook(BytesIO(cloud['content'])).sheetnames,['Motores-Otra'])
            self.assertTrue(PieceDeletion.objects.get(pk=first.uuid).synced)
            piece_service.import_catalog(stale_index,stale_content)
            self.assertFalse(SavedPiece.objects.filter(uuid=first.uuid).exists())
            self.post('delete_piece',{'revision':second.workbook.revision},[second.pk]);piece_service.sync_catalog()
            self.assertEqual(cloud['index'],[])
            self.assertEqual(load_workbook(BytesIO(cloud['content'])).sheetnames,['Sin piezas'])
        self.assertTrue(any('DELETE FROM public.saved_pieces' in sql for sql in queries))

    def test_refresh_updates_a_clean_cached_piece_and_remote_deletion_removes_it(self):
        from .piece_service import import_catalog
        from .motor_service import single_sheet
        from .xlsx_package import rename_sheet
        piece=self.register_selected()
        SavedPiece.objects.filter(pk=piece.pk).update(cloud_status='synced',cloud_version='old')
        content=rename_sheet(single_sheet(source_book(),'Tercera'),'Tercera','Motores-Bomba')[0]
        index=[{'id':str(piece.uuid),'name':piece.name,'category_id':str(self.category.pk),
            'category_name':self.category.name,'sheet':'Motores-Bomba','version':'new'}]
        import_catalog(index,content)
        piece.refresh_from_db();piece.workbook.refresh_from_db()
        self.assertEqual(piece.cloud_version,'new')
        self.assertEqual(piece.workbook.revision,2)
        book=load_workbook(BytesIO(bytes(piece.workbook.content)))
        self.assertEqual(book.active['A2'].value,'Tercera');book.close()
        import_catalog(index,content,{str(piece.uuid)})
        self.assertFalse(SavedPiece.objects.filter(pk=piece.pk).exists())
        self.assertTrue(PieceDeletion.objects.filter(pk=piece.uuid,synced=True).exists())
