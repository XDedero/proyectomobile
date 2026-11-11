"""Only the primary admin can manage other admins; any admin can grant the role."""
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .accounts import is_primary_administrator
from .models import UserProfile


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PrimaryAdministratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.primary = User.objects.create_superuser('admin', password='Bosque!Invierno2026')
        cls.administrator = User.objects.create_superuser('administrador-normal', password='Bosque!Invierno2026')
        cls.worker = User.objects.create_user('trabajador', password='Bosque!Invierno2026')
        for user, rut, role in ((cls.primary, '12345678-5', 'administrator'),
                                (cls.administrator, '11111111-1', 'administrator'),
                                (cls.worker, '22222222-2', 'worker')):
            UserProfile.objects.create(user=user, rut=rut, role=role,
                                       is_primary_administrator=user.pk == cls.primary.pk)

    def post(self, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse('manage_user'), data, content_type='application/json')

    def test_database_allows_only_one_primary_and_requires_administrator_role(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.filter(user=self.administrator).update(is_primary_administrator=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.filter(user=self.primary).update(role='worker')
        self.assertEqual(UserProfile.objects.filter(is_primary_administrator=True).count(), 1)
        self.assertFalse(is_primary_administrator(self.administrator))

    def test_administrators_can_grant_roles_but_only_primary_can_manage_other_administrators(self):
        granted = self.post(self.administrator, id=self.worker.pk, role='administrator')
        self.assertEqual(granted.status_code, 200, granted.content)
        self.worker.refresh_from_db()
        self.assertEqual(self.worker.app_profile.role, 'administrator')
        self.assertFalse(self.worker.app_profile.is_primary_administrator)
        self.assertEqual(self.post(self.administrator, id=self.worker.pk, first_name='Cambio').status_code, 403)
        self.assertEqual(self.post(self.administrator, id=self.worker.pk, role='worker').status_code, 403)
        self.assertEqual(self.post(self.primary, id=self.worker.pk, first_name='Luis').status_code, 200)
        self.assertEqual(self.post(self.primary, id=self.worker.pk, role='worker').status_code, 200)
        self.assertEqual(self.post(self.primary, id=self.worker.pk, role='administrator').status_code, 200)
        self.assertEqual(self.post(self.administrator, id=self.worker.pk, action='delete').status_code, 403)
        self.assertEqual(self.post(self.primary, id=self.worker.pk, action='delete').status_code, 200)
        self.assertFalse(get_user_model().objects.filter(pk=self.worker.pk).exists())

    def test_primary_cannot_be_deleted_demoted_or_copied_through_the_api(self):
        for changes in ({'role': 'viewer'}, {'password': 'Otra!Clave2026'}, {'action': 'delete'}):
            self.assertEqual(self.post(self.administrator, id=self.primary.pk, **changes).status_code, 403)
        self.assertEqual(self.post(self.primary, id=self.primary.pk, role='worker').status_code, 400)
        self.assertEqual(self.post(self.primary, id=self.primary.pk, action='delete').status_code, 400)
        self.assertEqual(self.post(self.administrator, id=self.administrator.pk,
                                   is_primary_administrator=True).status_code, 200)
        self.administrator.refresh_from_db()
        self.assertFalse(self.administrator.app_profile.is_primary_administrator)
        self.assertEqual(UserProfile.objects.filter(is_primary_administrator=True).count(), 1)

    def test_primary_can_edit_self_and_regular_administrator_keeps_own_and_worker_edits(self):
        self.assertEqual(self.post(self.primary, id=self.primary.pk, first_name='Principal').status_code, 200)
        self.assertEqual(self.post(self.administrator, id=self.administrator.pk, first_name='Usuario').status_code, 200)
        self.assertEqual(self.post(self.administrator, id=self.worker.pk, first_name='Trabajador').status_code, 200)
        self.client.force_login(self.primary)
        primary_data = self.client.get(reverse('management_data')).json()
        self.assertTrue(primary_data['can_manage_administrators'])
        other = next(user for user in primary_data['users'] if user['id'] == self.administrator.pk)
        self.assertTrue(other['can_edit'])
        self.assertTrue(other['can_delete'])
        self.client.force_login(self.administrator)
        regular_data = self.client.get(reverse('management_data')).json()
        self.assertTrue(regular_data['can_manage_administrators'])
        primary = next(user for user in regular_data['users'] if user['id'] == self.primary.pk)
        self.assertFalse(primary['can_edit'])
        self.assertFalse(primary['can_delete'])

    def test_django_admin_cannot_be_used_to_grant_primary_or_administrator_privileges(self):
        model_admin = admin.site._registry[get_user_model()]
        request = RequestFactory().get('/admin/auth/user/')
        request.user = self.administrator
        form = model_admin.get_form(request, self.worker)
        for field in ('is_staff', 'is_superuser', 'user_permissions', 'groups', 'is_primary_administrator'):
            self.assertNotIn(field, form.base_fields)
        self.client.force_login(self.administrator)
        self.assertEqual(self.client.get(reverse('admin:auth_user_add')).status_code, 403)
        self.assertEqual(self.client.post(reverse('admin:auth_user_password_change', args=[self.primary.pk]), {}).status_code, 403)
        self.client.force_login(self.primary)
        self.assertEqual(self.client.get(reverse('admin:auth_user_add')).status_code, 200)
        request.user = self.primary
        self.assertTrue(model_admin.has_change_permission(request, self.administrator))
        self.assertTrue(model_admin.has_delete_permission(request, self.administrator))
        self.assertFalse(model_admin.has_delete_permission(request, self.primary))
        primary_form = model_admin.get_form(request, self.primary)
        for field in ('username', 'is_active', 'is_staff', 'is_superuser'):
            self.assertNotIn(field, primary_form.base_fields)

    def test_django_permissions_cannot_substitute_for_the_unique_primary_flag(self):
        self.administrator.is_superuser = False
        self.administrator.is_staff = True
        self.administrator.save()
        self.administrator.user_permissions.set(Permission.objects.all())
        self.assertFalse(is_primary_administrator(self.administrator))
        self.assertEqual(self.post(self.administrator, id=self.worker.pk, role='administrator').status_code, 200)
        for target in (self.primary, self.worker):
            for changes in ({'first_name': 'Cambio'}, {'role': 'worker'}, {'action': 'delete'}):
                with self.subTest(target=target.username, changes=changes):
                    self.assertEqual(self.post(self.administrator, id=target.pk, **changes).status_code, 403)
        self.administrator.refresh_from_db()
        self.worker.refresh_from_db()
        self.assertFalse(self.administrator.app_profile.is_primary_administrator)
        self.assertFalse(self.worker.app_profile.is_primary_administrator)

    def test_migration_assigns_admin_once_preserving_passwords_and_other_accounts(self):
        original_password = self.primary.password
        UserProfile.objects.filter(user=self.primary).update(is_primary_administrator=False)
        seed = import_module('mobileApp.migrations.0030_primary_administrator').assign_primary_administrator
        for _ in range(2):
            seed(apps, SimpleNamespace(connection=connection))
        self.primary.refresh_from_db()
        self.administrator.refresh_from_db()
        self.assertEqual(self.primary.password, original_password)
        self.assertTrue(self.primary.app_profile.is_primary_administrator)
        self.assertFalse(self.administrator.app_profile.is_primary_administrator)
        self.assertEqual(UserProfile.objects.filter(is_primary_administrator=True).count(), 1)
