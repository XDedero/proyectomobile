import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

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
    if created:
        print(f"Superusuario '{username}' creado exitosamente con permisos totales.")
    else:
        print(f"Superusuario '{username}' actualizado con permisos totales y nueva contraseña.")

