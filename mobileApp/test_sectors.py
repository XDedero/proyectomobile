"""Plan-owned sectors, portable persistence, authorization and atomic button setup."""
from io import BytesIO
import json
import tempfile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import ButtonCategory, Motor, PdfMarker, PdfPlan, Plan, PlanRole
from .project_service import export_project, import_project
from .sectors import clean_sectors
from .test_pdf import pdf_upload
from .tests import dxf_file


class SectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = get_user_model().objects.create_user('sector-creator')
        cls.admin = get_user_model().objects.create_superuser('sector-admin', password='test')

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='sectors-')
        self.addCleanup(directory.cleanup)
        setting = override_settings(MEDIA_ROOT=directory.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.client.force_login(self.reader)

    def post(self, url, data=None, client=None):
        return (client or self.client).post(url, json.dumps(data or {}), content_type='application/json')

    def import_plan(self, kind):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'), {
            'file': pdf_upload() if kind == 'pdf' else dxf_file(),
        })
        self.assertEqual(response.status_code, 201, response.content[:300])
        return response.json()

    def sector(self, **values):
        return dict(id=str(uuid4()), name='Sala de bombas', shape='rectangle', color='#22c55e',
                    page=1, x=200, y=200, width=200, height=140, rotation=30, **values)

    def test_sector_persistence_edit_conflict_and_delete_for_both_formats(self):
        for kind, model in [('pdf', PdfPlan), ('dxf', Plan)]:
            with self.subTest(kind=kind):
                plan = self.import_plan(kind)
                self.assertEqual(plan['sectors'], [])
                url = plan['sectors_url']
                first = self.sector()
                response = self.post(url, {'action': 'save', 'sector': first})
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(self.client.get(plan['detail_url']).json()['sectors'], [first])
                second = self.sector()
                self.assertEqual(self.post(url, {'action': 'save', 'sector': second}).status_code, 200)
                changed = dict(first, rotation=85, name='Zona ajustada')
                self.assertEqual(self.post(url, {'action': 'save', 'sector': changed, 'previous': first}).status_code, 200)
                self.assertEqual(self.post(url, {'action': 'save', 'sector': first, 'previous': first}).status_code, 409)
                self.assertEqual(self.post(url, {'action': 'delete', 'id': first['id'], 'previous': first}).status_code, 409)
                self.assertEqual(self.post(url, {'action': 'delete', 'id': first['id'], 'previous': changed}).status_code, 200)
                self.assertEqual(model.objects.get(pk=plan['id']).sectors, [second])
                self.assertEqual(self.post(plan['delete_url']).status_code, 200)
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_creators_without_extra_permissions_and_readers_with_plan_scope(self):
        plan = self.import_plan('pdf')
        sector = self.sector()
        self.assertEqual(self.post(plan['sectors_url'], {'action': 'save', 'sector': sector}).status_code, 200)
        other = get_user_model().objects.create_user('sector-other')
        client = Client()
        client.force_login(other)
        self.assertEqual(client.get(plan['sectors_url']).status_code, 200)
        self.assertEqual(self.post(plan['sectors_url'], {'action': 'delete', 'id': sector['id']}, client).status_code, 403)
        restricted = PlanRole.objects.create(name='Sin acceso', all_plans=False, can_create=True)
        restricted.users.add(other)
        self.assertEqual(client.get(plan['sectors_url']).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.post(plan['sectors_url'], {'action': 'delete', 'id': sector['id'], 'previous': sector}).status_code, 200)

    def test_project_roundtrip_includes_sectors_and_accepts_older_projects(self):
        for kind, model in [('pdf', PdfPlan), ('dxf', Plan)]:
            with self.subTest(kind=kind):
                data = self.import_plan(kind)
                plan = model.objects.get(pk=data['id'])
                plan.sectors = [self.sector()]
                plan.save(update_fields=['sectors'])
                content = export_project(kind, plan)
                _, restored = import_project(SimpleUploadedFile('zona.planproj', content), self.reader)
                self.assertEqual(restored.sectors, plan.sectors)
                output = BytesIO()
                with ZipFile(BytesIO(content)) as source, ZipFile(output, 'w', ZIP_DEFLATED) as dest:
                    for name in source.namelist():
                        payload = source.read(name)
                        if name == 'manifest.json':
                            manifest = json.loads(payload)
                            manifest.pop('sectors')
                            payload = json.dumps(manifest).encode()
                        dest.writestr(name, payload)
                _, old = import_project(SimpleUploadedFile('anterior.planproj', output.getvalue()), self.reader)
                self.assertEqual(old.sectors, [])

    def test_rejects_invalid_geometry_strokes_page_and_limits(self):
        for changes in [dict(x=float('nan')), dict(width=-1), dict(rotation=float('inf')),
                        dict(shape='unknown'), dict(color='javascript:bad'), dict(name=''),
                        dict(page=3), dict(shape='circle'), dict(strokes='bad', shape='brush')]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                clean_sectors([dict(self.sector(), **changes)], pages=2)
        valid = dict(self.sector(), shape='brush', brush_size=.1, strokes=[[[-.5, 0], [.5, 0]]])
        self.assertEqual(clean_sectors([valid], pages=1), [valid])
        for strokes in [[[[float('nan'), 0]]], [[[0, 1]]], [[[0, 0]]] * 501]:
            with self.assertRaises(ValidationError):
                clean_sectors([dict(valid, strokes=strokes)])
        with self.assertRaises(ValidationError):
            clean_sectors([self.sector()] * 201)

    def test_new_button_saves_all_options_atomically_for_pdf_and_dxf(self):
        category = ButtonCategory.objects.create(name='Bombas de sector')
        for kind, model in [('pdf', PdfMarker), ('dxf', Motor)]:
            plan = self.import_plan(kind)
            values = dict(category_id=str(category.pk), origin='blank', show_description=True,
                          button_color='#a855f7', descriptions=[{'key': 'Ubicación', 'value': 'Sala norte'}])
            values.update(page=1, x=.3, y=.4, label='Bomba 1') if kind == 'pdf' else values.update(svg_x=0, svg_y=0, name='Bomba 1')
            response = self.post(plan['markers_url'], values)
            self.assertEqual(response.status_code, 201, response.content)
            result = response.json()['marker' if kind == 'pdf' else 'motor']
            button = model.objects.get(pk=result['id'])
            self.assertEqual(button.button_color, '#a855f7')
            self.assertEqual(button.category, category)
            self.assertEqual(button.descriptions[0]['value'], 'Sala norte')
            count = model.objects.count()
            invalid = dict(values, button_color='invalid')
            self.assertEqual(self.post(plan['markers_url'], invalid).status_code, 400)
            self.assertEqual(model.objects.count(), count)
            # Selecting an existing worksheet shares its data with the new button.
            linked = self.post(plan['markers_url'], dict(values, origin='sheet', sheet=button.sheet_name))
            self.assertEqual(linked.status_code, 201, linked.content)
            self.assertEqual(model.objects.get(pk=linked.json()['marker' if kind == 'pdf' else 'motor']['id']).sheet_name, button.sheet_name)
