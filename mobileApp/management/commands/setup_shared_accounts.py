"""Check or install the private identity registry without replacing app data."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mobileApp.cloud_projects import CloudUnavailable, supabase_query
from mobileApp.shared_accounts import enabled


REGISTRY_CHECK = """
SELECT
    to_regclass('app_identity.accounts') IS NOT NULL AS accounts_table,
    to_regclass('app_identity.registration_requests') IS NOT NULL AS requests_table,
    to_regprocedure('app_identity.submit_registration(uuid,text,text,text,text,timestamp with time zone)')
        IS NOT NULL AS submit_function,
    to_regprocedure('app_identity.review_registration(uuid,text,text,text)')
        IS NOT NULL AS review_function;
"""
REQUIRED_OBJECTS = ('accounts_table', 'requests_table', 'submit_function', 'review_function')


class Command(BaseCommand):
    help = 'Comprueba la conexión a Supabase y, con --initialize, instala el registro privado de cuentas.'

    def add_arguments(self, parser):
        parser.add_argument('--initialize', action='store_true',
                            help='Crea las tablas y funciones privadas que falten, conservando los registros existentes.')

    def handle(self, *args, **options):
        if not enabled():
            raise CommandError('El registro compartido está desactivado. Configure APP_SHARED_ACCOUNTS=1.')
        try:
            if options['initialize']:
                migration = settings.BASE_DIR / 'supabase/migrations/20261111000000_shared_registration.sql'
                supabase_query(migration.read_text(encoding='utf-8-sig'), read_only=False)
            rows = supabase_query(REGISTRY_CHECK, read_only=True).get('rows', [])
        except (CloudUnavailable, OSError) as exc:
            if isinstance(exc, CloudUnavailable):
                # Connector messages are sanitized and never contain credentials.
                raise CommandError(f'{exc} (código: {exc.code})') from exc
            raise CommandError('No se pudo leer la migración del registro privado.') from exc
        if not rows or not all(rows[0].get(key) is True for key in REQUIRED_OBJECTS):
            raise CommandError(
                'La conexión responde, pero faltan tablas o funciones de cuentas. '
                'Ejecute: python manage.py setup_shared_accounts --initialize'
            )
        self.stdout.write(self.style.SUCCESS('Conexión a Supabase y registro privado de cuentas verificados.'))
