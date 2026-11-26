import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import transaction
from mobileApp.accounts import next_test_rut, normalize_rut, rut_in_use
from mobileApp.models import UserProfile

User = get_user_model()
username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@ejemplo.com')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
configured_rut = os.environ.get('DJANGO_SUPERUSER_RUT')

if username and password:
    with transaction.atomic():
        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        rut = normalize_rut(configured_rut) if configured_rut else None
        if rut and rut_in_use(rut, exclude_user=user.pk):
            raise SystemExit('El RUT configurado para el administrador ya pertenece a otra cuenta.')
        user.is_superuser = True
        user.is_staff = True
        user.is_active = True
        if created or os.environ.get('DJANGO_RESET_SUPERUSER_PASSWORD') == '1':
            user.set_password(password)
        user.user_permissions.set(Permission.objects.all())
        user.save()
        profile = getattr(user, 'app_profile', None)
        primary = not UserProfile.objects.exclude(user=user).filter(is_primary_administrator=True).exists()
        if profile is None:
            UserProfile.objects.create(user=user, rut=rut or next_test_rut(), rut_is_test=rut is None,
                                       role='administrator', is_primary_administrator=primary)
        else:
            profile.role = 'administrator'
            profile.is_primary_administrator = primary
            if rut:
                profile.rut, profile.rut_is_test = rut, False
            profile.save(update_fields=['role', 'is_primary_administrator', 'rut', 'rut_is_test'])
        if created:
            print(f"Superusuario '{username}' creado exitosamente con permisos totales.")
        else:
            print(f"Superusuario '{username}' verificado; se conserva su contraseña salvo reinicio explícito.")

