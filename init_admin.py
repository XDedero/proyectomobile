import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from mobileApp.accounts import next_test_rut
from mobileApp.models import UserProfile

User = get_user_model()
username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@ejemplo.com')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

if username and password:
    user, created = User.objects.get_or_create(username=username, defaults={'email': email})
    user.is_superuser = True
    user.is_staff = True
    user.is_active = True
    user.set_password(password)
    user.user_permissions.set(Permission.objects.all())
    user.save()
    profile = getattr(user, 'app_profile', None)
    if profile is None:
        UserProfile.objects.create(user=user, rut=next_test_rut(), rut_is_test=True, role='administrator',
                                   is_primary_administrator=user.username == 'admin')
    elif profile.role != 'administrator' or (user.username == 'admin' and not profile.is_primary_administrator):
        profile.role = 'administrator'
        if user.username == 'admin':
            profile.is_primary_administrator = True
        profile.save(update_fields=['role', 'is_primary_administrator'])
    if created:
        print(f"Superusuario '{username}' creado exitosamente con permisos totales.")
    else:
        print(f"Superusuario '{username}' actualizado con permisos totales y nueva contraseña.")

