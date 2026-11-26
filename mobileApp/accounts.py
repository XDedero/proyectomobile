"""Identity validation and the three application roles."""
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone


def rut_check_digit(body):
    total = sum(int(digit) * (2 + index % 6) for index, digit in enumerate(reversed(body)))
    remainder = 11 - total % 11
    return '0' if remainder == 11 else 'K' if remainder == 10 else str(remainder)


def normalize_rut(value):
    if not isinstance(value, str):
        raise ValidationError('Ingrese un RUT válido con su dígito verificador.')
    value = value.strip().upper()
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
    from .models import CompanyAccount, CompanyAdminSetup, UserProfile
    profiles = UserProfile.objects.filter(rut=rut)
    users = get_user_model().objects.all()
    if exclude_user is not None:
        profiles = profiles.exclude(user_id=exclude_user)
        users = users.exclude(pk=exclude_user)
    return (profiles.exists() or users.filter(rut_usernames(rut)).exists()
            or CompanyAccount.objects.filter(rut=rut).exists() or CompanyAdminSetup.objects.filter(rut=rut).exists())


def rut_usernames(rut):
    body, verifier = rut.split('-')
    dotted = f'{int(body):,}'.replace(',', '.')
    return (Q(username__iexact=rut) | Q(username__iexact=body + verifier) |
            Q(username__iexact=f'{dotted}-{verifier}') | Q(username__iexact=dotted + verifier))


def local_user_for_rut(rut):
    from .models import UserProfile
    profile = UserProfile.objects.select_related('user').filter(rut=rut).first()
    if profile is not None:
        profile.user.app_profile = profile
        return profile.user
    return get_user_model().objects.filter(
        rut_usernames(rut), Q(app_profile__isnull=True) | Q(app_profile__rut_is_test=True),
    ).first()


def validate_account_password(password, *, rut, first_name, last_name):
    if not isinstance(password, str) or not password or len(password) > 256:
        raise ValidationError('Ingrese una contraseña válida de hasta 256 caracteres.')
    validate_password(password, user=get_user_model()(username=rut, first_name=first_name, last_name=last_name))


def next_test_rut():
    body = 90000000
    while body < 100000000:
        value = f'{body}-{rut_check_digit(str(body))}'
        if not rut_in_use(value):
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
