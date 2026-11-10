"""Identity validation and the three application roles."""
import re

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.utils import timezone


def rut_check_digit(body):
    total = sum(int(digit) * (2 + index % 6) for index, digit in enumerate(reversed(body)))
    remainder = 11 - total % 11
    return '0' if remainder == 11 else 'K' if remainder == 10 else str(remainder)


def normalize_rut(value, *, strict=False):
    if not isinstance(value, str):
        raise ValidationError('Ingrese un RUT válido con su dígito verificador.')
    value = value.strip().upper()
    if strict and not re.fullmatch(r'[1-9][0-9]{6,7}-[0-9K]', value):
        raise ValidationError('Ingrese el RUT sin puntos y con guion, por ejemplo 12345678-5.')
    if not re.fullmatch(r'(?:[1-9][0-9]{6,7}|[1-9][0-9]?\.[0-9]{3}\.[0-9]{3})-?[0-9K]', value):
        raise ValidationError('Ingrese un RUT válido, por ejemplo 12.345.678-5.')
    compact = value.replace('.', '').replace('-', '')
    body, verifier = compact[:-1], compact[-1]
    if rut_check_digit(body) != verifier:
        raise ValidationError('El dígito verificador del RUT no es válido.')
    return f'{body}-{verifier}'


def application_role(user):
    """Profiles define new accounts; legacy callers retain their existing access."""
    if not user.is_authenticated or not user.is_active:
        return None
    profile = getattr(user, 'app_profile', None)
    if profile is not None:
        return profile.role
    if user.is_superuser or (user.is_staff and user.has_perm('mobileApp.administer_application')):
        return 'administrator'
    return 'worker'


def validate_name(value, label):
    if not isinstance(value, str):
        raise ValidationError(f'Ingrese su {label}.')
    value = ' '.join(value.split())
    if not value or len(value) > 150 or not any(c.isalpha() for c in value) or any(
            not (c.isalpha() or c in " '-’") for c in value):
        raise ValidationError(f'Ingrese un {label} válido de hasta 150 caracteres.')
    return value


def rut_in_use(rut, *, exclude_user=None):
    from .models import UserProfile
    profiles = UserProfile.objects.filter(rut=rut)
    users = get_user_model().objects.all()
    if exclude_user is not None:
        profiles = profiles.exclude(user_id=exclude_user)
        users = users.exclude(pk=exclude_user)
    body, verifier = rut.split('-')
    dotted = f'{int(body):,}'.replace(',', '.') + '-' + verifier
    return profiles.exists() or any(users.filter(username__iexact=value).exists()
                                    for value in (rut, body + verifier, dotted))


def next_test_rut():
    from .models import RegistrationRequest
    body = 90000000
    while body < 100000000:
        value = f'{body}-{rut_check_digit(str(body))}'
        if not rut_in_use(value) and not RegistrationRequest.objects.filter(rut=value, status='pending').exists():
            return value
        body += 1
    raise ValidationError('No se pudo asignar un RUT de prueba.')


def invalidate_user_sessions(user_id):
    """A role change takes effect only after the affected user signs in again."""
    ids = [session.pk for session in Session.objects.filter(expire_date__gt=timezone.now())
           if str(session.get_decoded().get('_auth_user_id')) == str(user_id)]
    if ids:
        Session.objects.filter(pk__in=ids).delete()


def is_primary_administrator(user):
    from .authorization import is_administrator
    profile = getattr(user, 'app_profile', None) if user.is_authenticated else None
    return is_administrator(user) and bool(profile and profile.is_primary_administrator)


def can_edit_user(actor, user):
    from .authorization import is_administrator
    profile = getattr(user, 'app_profile', None)
    administrator = profile.role == 'administrator' if profile else (
        user.is_superuser or (user.is_staff and user.has_perm('mobileApp.administer_application')))
    return is_administrator(actor) and (actor.pk == user.pk or not administrator or is_primary_administrator(actor))


def user_data(user):
    profile = getattr(user, 'app_profile', None)
    role = profile.role if profile else application_role(user)
    return {'id': user.pk, 'name': user.get_full_name() or user.username,
            'username': user.username, 'first_name': user.first_name, 'last_name': user.last_name,
            'rut': profile.rut if profile else '', 'rut_is_test': bool(profile and profile.rut_is_test),
            'primary_administrator': bool(profile and profile.is_primary_administrator),
            'role': role, 'administrator': role == 'administrator', 'is_active': user.is_active}


def registration_data(item):
    return {'id': item.pk, 'first_name': item.first_name, 'last_name': item.last_name,
            'name': f'{item.first_name} {item.last_name}', 'rut': item.rut,
            'status': item.status, 'created_at': item.created_at.isoformat(),
            'reviewed_at': item.reviewed_at.isoformat() if item.reviewed_at else None,
            'reviewed_by': item.reviewed_by.get_full_name() or item.reviewed_by.username if item.reviewed_by else None,
            'user_id': item.user_id}
