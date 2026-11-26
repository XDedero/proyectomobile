from django.core.management.base import BaseCommand

from mobileApp.maintenance_alerts import generate_all_workspaces, watch_for_overdue_maintenance


class Command(BaseCommand):
    help = 'Genera las alertas pendientes de mantención en los buzones.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true', help='Revisa las mantenciones cada cinco minutos.')

    def handle(self, *args, **options):
        if options['watch']:
            watch_for_overdue_maintenance()
        else:
            generate_all_workspaces()
