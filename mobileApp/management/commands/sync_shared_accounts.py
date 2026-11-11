from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from mobileApp.cloud_projects import CloudUnavailable
from mobileApp.shared_accounts import enabled, sync_accounts, sync_requests


class Command(BaseCommand):
    help = 'Envía las solicitudes locales pendientes y actualiza las cuentas y solicitudes compartidas.'

    def handle(self, *args, **options):
        if not enabled():
            raise CommandError('El registro compartido está desactivado. Configure APP_SHARED_ACCOUNTS=1.')
        try:
            sync_requests()
            sync_accounts()
        except (CloudUnavailable, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS('Solicitudes y cuentas compartidas actualizadas.'))
