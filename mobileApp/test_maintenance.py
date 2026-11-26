from datetime import date
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .cloud_projects import open_cloud_project
from .maintenance import next_date
from .maintenance_alerts import generate_alerts
from .models import ButtonCategory, MaintenanceNotification, PdfMarker, UserProfile
from .project_service import import_project
from .test_pdf import pdf_upload
from .tests import dxf_file


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MaintenanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user('plan-owner', first_name='Dueño')
        cls.creator = users.create_user('button-creator', first_name='María', last_name='Soto')
        UserProfile.objects.create(user=cls.creator, rut='11111111-1', role=UserProfile.Role.WORKER)
        cls.admin = users.create_superuser('admin', first_name='Ana', last_name='Rojas')
        cls.other_admin = users.create_superuser('other-admin')
        cls.category = ButtonCategory.objects.create(name='Bombas')
        cls.schedule = {'last_date': '2026-09-26', 'interval': 2, 'unit': 'months'}

    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=directory))
        self.today = self.enterContext(patch('django.utils.timezone.localdate', return_value=date(2026, 11, 26)))
        self.client.force_login(self.owner)

    def post(self, url, data=None, client=None):
        return (client or self.client).post(url, data or {}, content_type='application/json')

    def plan(self, kind):
        response = self.client.post(reverse('import_pdf' if kind == 'pdf' else 'plan_import'),
                                    {'file': pdf_upload() if kind == 'pdf' else dxf_file()})
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def add_button(self, kind, plan, client=None, maintenance=None):
        position = {'page': 1, 'x': .25, 'y': .35, 'label': 'Bomba de agua'} if kind == 'pdf' else {
            'svg_x': 30, 'svg_y': 30, 'name': 'Motor de agua',
        }
        response = self.post(plan['markers_url'], {
            **position, 'origin': 'blank', 'category_id': str(self.category.pk), 'maintenance': maintenance,
        }, client=client)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['marker' if kind == 'pdf' else 'motor']

    def test_daily_alerts_and_reply_for_the_actual_button_creator(self):
        plan = self.plan('pdf')
        self.assertEqual(self.post(plan['permissions_url'], {'user_ids': [self.creator.pk]}).status_code, 200)
        creator_client = Client()
        creator_client.force_login(self.creator)
        draft = self.post(plan['draft_url'], client=creator_client).json()['plan']
        marker = self.add_button('pdf', draft, creator_client, self.schedule)
        self.assertEqual(marker['maintenance']['next_date'], '2026-11-26')
        self.assertEqual(creator_client.get(marker['details_url']).json()['maintenance'], marker['maintenance'])

        generate_alerts(today=date(2026, 11, 28))
        self.assertFalse(MaintenanceNotification.objects.exists())
        self.assertEqual(self.post(draft['save_project_url'], client=creator_client).status_code, 200)
        call_command('generate_maintenance_alerts')
        self.assertFalse(MaintenanceNotification.objects.exists())
        self.today.return_value = date(2026, 11, 28)
        call_command('generate_maintenance_alerts')
        call_command('generate_maintenance_alerts')
        alerts = MaintenanceNotification.objects.order_by('day', 'id')
        self.assertEqual(alerts.count(), 6)
        for user in (self.creator, self.admin, self.other_admin):
            self.assertEqual(list(alerts.filter(recipient=user).values_list('day', flat=True)),
                             [date(2026, 11, 27), date(2026, 11, 28)])
        self.assertFalse(alerts.filter(recipient=self.owner).exists())

        inbox = reverse('notifications')
        admin_client = Client(enforce_csrf_checks=True)
        admin_client.force_login(self.admin)
        page = admin_client.get(inbox)
        self.assertContains(page, 'Bomba de agua')
        self.assertContains(page, 'Bombas')
        self.assertContains(page, plan['name'])
        self.assertContains(page, 'María Soto')
        alert = alerts.filter(recipient=self.admin).last()
        reply = reverse('reply_to_notification', args=[alert.pk])
        self.assertEqual(creator_client.post(reply, {'message': 'Intento'}).status_code, 403)
        self.assertEqual(admin_client.post(reply, {'message': 'Falta token'}).status_code, 403)
        token = admin_client.cookies['csrftoken'].value
        other_alert = alerts.filter(recipient=self.other_admin).first()
        self.assertEqual(admin_client.post(reverse('reply_to_notification', args=[other_alert.pk]),
                                          {'message': 'Privado', 'csrfmiddlewaretoken': token}).status_code, 404)
        message = 'Confirma la revisión de la bomba, por favor.'
        self.assertRedirects(admin_client.post(reply, {'message': message, 'csrfmiddlewaretoken': token}), inbox)
        self.assertContains(creator_client.get(inbox), message)
        self.assertNotContains(self.client.get(inbox), message)
        self.assertContains(creator_client.get(inbox), 'Ana Rojas')

    def test_dxf_edit_after_overdue_preserves_elapsed_notifications_and_restarts_the_cycle(self):
        plan = self.plan('dxf')
        draft = self.post(plan['draft_url']).json()['plan']
        marker = self.add_button('dxf', draft, maintenance=self.schedule)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        self.today.return_value = date(2026, 11, 28)
        change = self.post(marker['details_url'], {
            'maintenance': {'last_date': '2026-11-28', 'interval': 2, 'unit': 'months'},
        })
        self.assertEqual(change.status_code, 200, change.content)
        self.assertEqual(change.json()['maintenance']['next_date'], '2027-01-28')
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        self.assertEqual(set(MaintenanceNotification.objects.values_list('day', flat=True)),
                         {date(2026, 11, 27), date(2026, 11, 28)})
        generate_alerts(today=date(2027, 1, 28))
        self.assertEqual(MaintenanceNotification.objects.count(), 6)
        generate_alerts(today=date(2027, 1, 29))
        self.assertEqual(MaintenanceNotification.objects.filter(day=date(2027, 1, 29)).count(), 3)

    def test_validation_and_calendar_frequencies(self):
        self.assertEqual(next_date(date(2026, 1, 31), 1, 'months'), date(2026, 2, 28))
        self.assertEqual(next_date(date(2024, 2, 29), 1, 'years'), date(2025, 2, 28))
        self.assertEqual(next_date(date(2026, 11, 26), 1, 'weeks'), date(2026, 12, 3))
        self.assertEqual(next_date(date(2026, 11, 26), 2, 'days'), date(2026, 11, 28))
        plan = self.plan('pdf')
        marker = self.add_button('pdf', plan)
        for changes in ({'interval': 0}, {'last_date': '2026-11-27'}, {'interval': True}, {'unit': 'hours'}):
            response = self.post(marker['details_url'], {'maintenance': {**self.schedule, **changes}})
            self.assertEqual(response.status_code, 400, response.content)
        self.assertIsNone(PdfMarker.objects.get(pk=marker['id']).next_maintenance)
        self.assertEqual(self.post(marker['details_url'], {'maintenance': self.schedule}).status_code, 200)
        self.assertIsNone(self.post(marker['details_url'], {'maintenance': None}).json()['maintenance'])

    def test_cloud_restore_preserves_responsibility_but_independent_import_gets_its_own_schedule(self):
        plan = self.plan('pdf')
        marker = self.add_button('pdf', plan, maintenance=self.schedule)
        result = self.client.get(plan['export_project_url'])
        self.assertEqual(result.status_code, 200)
        content = b''.join(result.streaming_content)
        with ZipFile(BytesIO(content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            source = archive.read('plan/source.pdf')
            workbook = archive.read('workbooks/w1.xlsx')
        project = {'name': 'Plano.pdf', 'excelId': 'cloud-workbook', 'excelName': 'Plano.xlsx', 'date': '2026-11-26'}
        metadata = {'name': manifest['name'], 'markers': manifest['markers']}
        with patch('mobileApp.cloud_projects.read_cloud_snapshot',
                   return_value=(project, source, workbook, metadata, manifest, '')):
            restored, _ = open_cloud_project('cloud-plan', self.admin)
        original = PdfMarker.objects.get(pk=marker['id'])
        from_cloud = restored.markers.get()
        self.assertEqual(from_cloud.next_maintenance, original.next_maintenance)
        self.assertEqual(from_cloud.maintenance_key, original.maintenance_key)
        self.assertEqual(from_cloud.button_creator, self.owner.username)
        generate_alerts(today=date(2026, 11, 27))
        self.assertEqual(MaintenanceNotification.objects.count(), 3)

        _, copy = import_project(SimpleUploadedFile('copia.planproj', content), self.creator)
        independent = copy.markers.get()
        self.assertEqual(independent.next_maintenance, original.next_maintenance)
        self.assertNotEqual(independent.maintenance_key, original.maintenance_key)
        self.assertEqual(independent.button_creator, self.creator.username)
