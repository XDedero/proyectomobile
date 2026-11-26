"""Check or install the private identity registry without replacing app data."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from mobileApp.cloud_projects import CloudUnavailable, supabase_query
from mobileApp.shared_accounts import enabled


REGISTRY_CHECK = "SELECT to_regclass('app_identity.accounts') IS NOT NULL AS accounts_table;"
REQUIRED_OBJECTS = ('accounts_table',)


class Command(BaseCommand):
    help = 'Comprueba la conexión a Supabase y, con --initialize, instala el registro privado de cuentas.'

    def add_arguments(self, parser):
        parser.add_argument('--initialize', action='store_true',
                            help='Prepara el registro privado sin eliminar cuentas existentes.')

    def handle(self, *args, **options):
        if not enabled():
            raise CommandError('El registro compartido está desactivado. Configure APP_SHARED_ACCOUNTS=1.')
        try:
            if options['initialize']:
                migration = settings.BASE_DIR / 'supabase/migrations/20261126010000_identity_accounts_only.sql'
                supabase_query(migration.read_text(encoding='utf-8-sig'), read_only=False)
            rows = supabase_query(REGISTRY_CHECK, read_only=True).get('rows', [])
        except (CloudUnavailable, OSError) as exc:
            if isinstance(exc, CloudUnavailable):
                # Connector messages are sanitized and never contain credentials.
                raise CommandError(f'{exc} (código: {exc.code})') from exc
            raise CommandError('No se pudo leer la migración del registro privado.') from exc
        if not rows or not all(rows[0].get(key) is True for key in REQUIRED_OBJECTS):
            raise CommandError(
                'La conexión responde, pero falta la tabla privada de cuentas. '
                'Ejecute: python manage.py setup_shared_accounts --initialize'
            )
        self.stdout.write(self.style.SUCCESS('Conexión a Supabase y registro privado de cuentas verificados.'))
