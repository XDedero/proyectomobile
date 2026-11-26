from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from mobileApp.cloud_projects import CloudUnavailable
from mobileApp.shared_accounts import enabled, sync_accounts


class Command(BaseCommand):
    help = 'Actualiza las cuentas del registro compartido.'

    def handle(self, *args, **options):
        if not enabled():
            raise CommandError('El registro compartido está desactivado. Configure APP_SHARED_ACCOUNTS=1.')
        try:
            sync_accounts()
        except (CloudUnavailable, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS('Cuentas compartidas actualizadas.'))
