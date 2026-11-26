from datetime import date, timedelta
from io import BytesIO
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .cloud_projects import open_cloud_project
from .maintenance import next_date
from .maintenance_alerts import generate_alerts
from .models import ButtonCategory, MaintenanceNotification, PdfMarker, SavedPiece, UserProfile, WorkbookDocument
from .motor_service import template_bytes
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
        cls.optional = ButtonCategory.objects.create(name='Sensores', maintenance_required=False)
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

    def button_payload(self, kind, maintenance=None, category=None):
        position = {'page': 1, 'x': .25, 'y': .35, 'label': 'Bomba de agua'} if kind == 'pdf' else {
            'svg_x': 30, 'svg_y': 30, 'name': 'Motor de agua',
        }
        return {**position, 'origin': 'blank', 'category_id': str((category or self.category).pk),
                'maintenance': maintenance}

    def add_button(self, kind, plan, client=None, maintenance=None, category=None):
        response = self.post(plan['markers_url'], self.button_payload(kind, maintenance, category), client=client)
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
        self.assertFalse(MaintenanceNotification.objects.filter(kind=MaintenanceNotification.Kind.ALERT).exists())
        self.today.return_value = date(2026, 11, 27)
        self.assertTrue(creator_client.get(marker['details_url']).json()['maintenance']['overdue'])
        call_command('generate_maintenance_alerts')
        alerts = MaintenanceNotification.objects.filter(kind=MaintenanceNotification.Kind.ALERT).order_by('day', 'id')
        ids = set(alerts.values_list('pk', flat=True))
        self.assertEqual(alerts.count(), 3)
        self.assertFalse(alerts.exclude(day=date(2026, 11, 27)).exists())
        self.today.return_value = date(2026, 11, 28)
        call_command('generate_maintenance_alerts')
        call_command('generate_maintenance_alerts')
        self.assertEqual(set(alerts.values_list('pk', flat=True)), ids)
        for user in (self.creator, self.admin, self.other_admin):
            self.assertEqual(alerts.get(recipient=user).day, date(2026, 11, 28))
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
        self.today.return_value = date(2026, 11, 29)
        page = creator_client.get(inbox)
        self.assertContains(page, 'Mantención vencida', count=1)
        self.assertContains(page, 'datetime="2026-11-29"')
        self.assertContains(page, message)
        self.assertEqual(MaintenanceNotification.objects.get(kind=MaintenanceNotification.Kind.REPLY).reply_to_id, alert.pk)

    def test_monthly_reminder_appears_two_days_before_due_for_creator_and_administrators(self):
        self.today.return_value = date(2026, 11, 25)
        plan = self.plan('pdf')
        draft = self.post(plan['draft_url']).json()['plan']
        self.add_button('pdf', draft, maintenance={'last_date': '2026-10-28', 'interval': 1, 'unit': 'months'})
        reminder_day = date(2026, 11, 26)
        generate_alerts(today=reminder_day)
        self.assertFalse(MaintenanceNotification.objects.exists())
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        inbox = reverse('notifications')
        self.assertContains(self.client.get(inbox), 'Aún no tienes notificaciones.')

        self.today.return_value = reminder_day
        page = self.client.get(inbox)
        self.assertContains(page, 'Mantención próxima')
        self.assertContains(page, 'Bomba de agua')
        self.assertContains(page, plan['name'])
        self.assertContains(page, '28-11-2026')
        self.assertNotContains(page, 'Mantención vencida')
        reminders = MaintenanceNotification.objects.filter(kind=MaintenanceNotification.Kind.REMINDER)
        self.assertEqual(set(reminders.values_list('recipient_id', flat=True)),
                         {self.owner.pk, self.admin.pk, self.other_admin.pk})
        admin = Client()
        admin.force_login(self.admin)
        self.assertContains(admin.get(inbox), 'Mantención próxima')
        other_user = Client()
        other_user.force_login(self.creator)
        self.assertContains(other_user.get(inbox), 'Aún no tienes notificaciones.')

        for day in (26, 27, 28, 29, 30):
            generate_alerts(today=date(2026, 11, day))
        self.assertEqual(reminders.count(), 3)
        self.assertFalse(reminders.exclude(day=reminder_day, due_date=date(2026, 11, 28)).exists())
        for recipient in (self.owner, self.admin, self.other_admin):
            alert = MaintenanceNotification.objects.get(kind=MaintenanceNotification.Kind.ALERT, recipient=recipient)
            self.assertEqual(alert.day, date(2026, 11, 30))

    def test_dxf_publishing_maintenance_clears_only_the_overdue_alert_and_restarts_the_cycle(self):
        plan = self.plan('dxf')
        draft = self.post(plan['draft_url']).json()['plan']
        marker = self.add_button('dxf', draft, maintenance=self.schedule)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        self.today.return_value = date(2026, 11, 28)
        generate_alerts()
        alerts = MaintenanceNotification.objects.filter(kind=MaintenanceNotification.Kind.ALERT)
        self.assertEqual(alerts.count(), 3)
        admin = Client()
        admin.force_login(self.admin)
        reply_url = reverse('reply_to_notification', args=[alerts.get(recipient=self.admin).pk])
        message = 'Se requiere revisar el motor.'
        self.assertRedirects(admin.post(reply_url, {'message': message}), reverse('notifications'))
        change = self.post(marker['details_url'], {
            'maintenance': {'last_date': '2026-11-28', 'interval': 2, 'unit': 'months'},
        })
        self.assertEqual(change.status_code, 200, change.content)
        self.assertEqual(change.json()['maintenance']['next_date'], '2027-01-28')
        self.assertEqual(alerts.count(), 3)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        self.assertFalse(alerts.exists())
        page = self.client.get(reverse('notifications'))
        self.assertContains(page, message)
        self.assertNotContains(page, 'Mantención vencida')
        generate_alerts(today=date(2027, 1, 28))
        self.assertFalse(alerts.exists())
        generate_alerts(today=date(2027, 1, 29))
        self.assertEqual(alerts.count(), 3)
        self.assertFalse(alerts.exclude(day=date(2027, 1, 29), due_date=date(2027, 1, 28)).exists())

    def test_validation_and_calendar_frequencies(self):
        self.assertEqual(next_date(date(2026, 1, 31), 1, 'months'), date(2026, 2, 28))
        self.assertEqual(next_date(date(2024, 2, 29), 1, 'years'), date(2025, 2, 28))
        self.assertEqual(next_date(date(2026, 11, 26), 1, 'weeks'), date(2026, 12, 3))
        self.assertEqual(next_date(date(2026, 11, 26), 2, 'days'), date(2026, 11, 28))
        plan = self.plan('pdf')
        marker = self.add_button('pdf', plan, category=self.optional)
        for changes in ({'interval': 0}, {'last_date': '2026-11-27'}, {'interval': True}, {'unit': 'hours'}):
            response = self.post(marker['details_url'], {'maintenance': {**self.schedule, **changes}})
            self.assertEqual(response.status_code, 400, response.content)
        self.assertIsNone(PdfMarker.objects.get(pk=marker['id']).next_maintenance)
        self.assertEqual(self.post(marker['details_url'], {'maintenance': self.schedule}).status_code, 200)
        self.assertIsNone(self.post(marker['details_url'], {'maintenance': None}).json()['maintenance'])

    def test_backdated_monthly_schedule_shows_overdue_and_keeps_actual_due_date_for_inbox(self):
        today, due = date(2026, 11, 26), date(2026, 8, 26)
        plan = self.plan('pdf')
        marker = self.add_button('pdf', plan, maintenance={'last_date': '2026-07-26', 'interval': 1, 'unit': 'months'})
        self.assertEqual(marker['maintenance'], {
            'last_date': '2026-07-26', 'interval': 1, 'unit': 'months', 'overdue': True, 'next_date': None,
        })
        self.assertEqual(PdfMarker.objects.get(pk=marker['id']).next_maintenance, due)
        generate_alerts()
        for recipient in (self.owner, self.admin, self.other_admin):
            alert = MaintenanceNotification.objects.get(recipient=recipient)
            self.assertEqual((alert.day, alert.due_date), (today, due))
        self.today.return_value = today + timedelta(days=7)
        page = self.client.get(reverse('notifications'))
        self.assertContains(page, 'Mantención vencida', count=1)
        self.assertContains(page, '26-08-2026')
        self.assertContains(page, 'datetime="2026-12-03"')
        self.assertEqual(MaintenanceNotification.objects.count(), 3)

    def test_admin_controls_category_requirement_and_buttons_enforce_it(self):
        admin_client = Client()
        admin_client.force_login(self.admin)
        url = reverse('manage_category')
        self.assertTrue(self.post(url, {'name': 'Compresores'}, admin_client).json()['maintenance_required'])
        result = self.post(url, {'id': str(self.optional.pk), 'name': self.optional.name, 'maintenance_required': False},
                           admin_client)
        self.assertIs(result.json()['maintenance_required'], False)
        result = self.post(url, {'name': 'Regla inválida', 'maintenance_required': 'false'}, admin_client)
        self.assertEqual(result.status_code, 400)
        for kind in ('pdf', 'dxf'):
            with self.subTest(kind=kind):
                plan = self.plan(kind)
                self.assertEqual(self.post(plan['markers_url'], self.button_payload(kind)).status_code, 400)
                marker = self.add_button(kind, plan, category=self.optional)
                self.assertFalse(marker['maintenance_required'])
                self.assertIsNone(marker['maintenance'])
                category = {'action': 'set_category', 'category_id': str(self.category.pk)}
                self.assertEqual(self.post(marker['assign_url'], category).status_code, 400)
                self.assertEqual(self.post(marker['assign_url'], {**category, 'maintenance': self.schedule}).status_code, 200)
                details = self.client.get(marker['details_url']).json()
                self.assertTrue(details['maintenance_required'])
                self.assertEqual(details['maintenance']['next_date'], '2026-11-26')
                self.assertEqual(self.post(marker['details_url'], {'maintenance': None}).status_code, 400)

                copy = {**self.button_payload(kind), 'origin': 'copy', 'copy_button_id': marker['id']}
                self.assertEqual(self.post(plan['markers_url'], copy).status_code, 400)
                result = self.post(plan['markers_url'], {**copy, 'maintenance': self.schedule})
                self.assertEqual(result.status_code, 201, result.content)
                self.assertEqual(result.json()['marker' if kind == 'pdf' else 'motor']['maintenance']['next_date'], '2026-11-26')

    def test_publication_enforces_the_latest_category_policy_for_new_assignments(self):
        plan = self.plan('pdf')
        legacy = self.add_button('pdf', plan, category=self.optional)
        draft = self.post(plan['draft_url']).json()['plan']
        marker = self.add_button('pdf', draft, category=self.optional)
        ButtonCategory.objects.filter(pk=self.optional.pk).update(maintenance_required=True)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 400)
        self.assertEqual(self.post(marker['details_url'], {'maintenance': self.schedule}).status_code, 200)
        self.assertEqual(self.post(draft['save_project_url']).status_code, 200)
        existing = self.client.get(legacy['details_url']).json()
        self.assertTrue(existing['maintenance_required'])
        self.assertIsNone(existing['maintenance'])

    def test_category_inherited_from_a_saved_piece_also_requires_maintenance(self):
        def piece(name, category):
            workbook = WorkbookDocument.objects.create(name=name + '.xlsx', content=template_bytes())
            return SavedPiece.objects.create(name=name, name_key=name, fingerprint=name, category=category, workbook=workbook)

        optional_piece = piece('Sensor', self.optional)
        required_piece = piece('Bomba', self.category)
        plan = self.plan('pdf')
        payload = self.button_payload('pdf', category=self.optional)
        self.assertEqual(self.post(plan['markers_url'], {**payload, 'origin': 'piece',
                                                         'saved_piece_id': required_piece.pk}).status_code, 400)
        marker = self.add_button('pdf', plan, category=self.optional)
        self.assertEqual(self.post(marker['assign_url'], {'saved_piece_id': required_piece.pk}).status_code, 400)
        self.assertEqual(self.post(marker['assign_url'], {'saved_piece_id': optional_piece.pk}).status_code, 200)
        record = PdfMarker.objects.get(pk=marker['id'])
        source = record.source_workbook
        sheet = next(name for name, info in source.source_pieces.items() if info['id'] == str(required_piece.uuid))
        selection = {'action': 'select_source', 'source_sheet': sheet, 'source_workbook_id': source.pk}
        self.assertEqual(self.post(marker['assign_url'], selection).status_code, 400)
        self.assertEqual(self.post(marker['details_url'], {'maintenance': self.schedule}).status_code, 200)
        self.assertEqual(self.post(marker['assign_url'], selection).status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.category_id, self.category.pk)

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


class OverdueNotificationMigrationTests(TransactionTestCase):
    def test_migration_consolidates_existing_alerts_and_preserves_replies_and_reminders(self):
        previous = [('mobileApp', '0045_maintenance_reminders')]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps
        old = apps.get_model('mobileApp', 'MaintenanceNotification')
        users = apps.get_model('auth', 'User').objects
        creator = users.create(username='creator')
        admin = users.create(username='admin')
        data = {
            'maintenance_key': uuid4(), 'responsible_id': creator.pk, 'responsible_name': 'Creador',
            'last_maintenance': date(2026, 9, 26), 'due_date': date(2026, 10, 26),
            'plan_name': 'Plano', 'button_name': 'Motor',
        }
        reminder = old.objects.create(kind='reminder', recipient_id=creator.pk, day=date(2026, 10, 24), **data)
        for recipient in (creator, admin):
            old.objects.bulk_create([
                old(kind='alert', recipient_id=recipient.pk, day=date(2026, 10, day), **data)
                for day in range(27, 32)
            ])
        latest_creator = old.objects.filter(kind='alert', recipient_id=creator.pk).first()
        old_admin = old.objects.filter(kind='alert', recipient_id=admin.pk).last()
        latest_admin = old.objects.filter(kind='alert', recipient_id=admin.pk).first()
        reply = old.objects.create(kind='reply', recipient_id=creator.pk, day=date(2026, 10, 29),
                                   reply_to=old_admin, message='Coordina la revisión.', **data)
        other_button = old.objects.create(kind='alert', recipient_id=creator.pk, day=date(2026, 10, 27),
                                          **{**data, 'maintenance_key': uuid4()})

        MigrationExecutor(connection).migrate(latest)
        alerts = MaintenanceNotification.objects.filter(kind=MaintenanceNotification.Kind.ALERT)
        self.assertEqual(set(alerts.values_list('pk', flat=True)), {latest_creator.pk, latest_admin.pk, other_button.pk})
        self.assertEqual(MaintenanceNotification.objects.get(pk=reply.pk).reply_to_id, latest_admin.pk)
        self.assertEqual(MaintenanceNotification.objects.get(pk=reply.pk).message, 'Coordina la revisión.')
        self.assertTrue(MaintenanceNotification.objects.filter(pk=reminder.pk, kind=MaintenanceNotification.Kind.REMINDER).exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            MaintenanceNotification.objects.create(
                kind=MaintenanceNotification.Kind.ALERT, recipient_id=creator.pk, day=date(2026, 11, 1), **data,
            )
