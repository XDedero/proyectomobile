"""Create empty, private workspaces for verified company accounts."""
import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection, transaction
from psycopg import sql

from mobileApp.company_access import registration_companies
from mobileApp.models import CompanyDriveConnection, RELONCAVI_COMPANY_ID
from mobileApp.tenant_runtime import load_drive, schema_name, using_schema, workspace_is_ready


MIRROR_TABLES = (
    'button_categories', 'pdf_plans', 'pdf_markers', 'workbook_documents',
    'plan_workbook_sheets', 'workbook_button_description_refs', 'saved_projects',
    'saved_pieces', 'piece_deletions', 'piece_library_registry',
)


def prepare_mirror(cursor, schema):
    for table in MIRROR_TABLES:
        target = sql.Identifier(schema, table)
        cursor.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {} (LIKE {} INCLUDING ALL)').format(
            target, sql.Identifier('public', table)))
        cursor.execute('''
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s AND column_default LIKE 'nextval(%%'
        ''', [table])
        for (column,) in cursor.fetchall():
            sequence_name = f'{table}_{column}_seq'
            sequence = sql.Identifier(schema, sequence_name)
            cursor.execute(sql.SQL('CREATE SEQUENCE IF NOT EXISTS {} OWNED BY {}.{}').format(
                sequence, target, sql.Identifier(column)))
            cursor.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET DEFAULT nextval({}::regclass)').format(
                target, sql.Identifier(column), sql.Literal(f'{schema}.{sequence_name}')))


def prepare_workspace(schema, *, only_if_missing=False):
    # Migrations need their own transaction boundaries on PostgreSQL.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_lock(hashtextextended(%s, 0))', [schema])
    try:
        if only_if_missing and workspace_is_ready(schema):
            return
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
            cursor.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(schema)))
            for role in ('anon', 'authenticated'):
                cursor.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', [role])
                if cursor.fetchone():
                    cursor.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(
                        sql.Identifier(schema), sql.Identifier(role)))
        with using_schema(schema):
            call_command('migrate', interactive=False, verbosity=0)
            with transaction.atomic(), connection.cursor() as cursor:
                prepare_mirror(cursor, schema)
                cursor.execute('CREATE TABLE IF NOT EXISTS workspace_ready (ready boolean PRIMARY KEY)')
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', [schema])


class Command(BaseCommand):
    help = 'Prepare private drawing and cloud metadata schemas for verified companies.'

    def handle(self, *args, **options):
        if not settings.PLANOS_CLOUD_MODE or not os.environ.get('PANEL_DRIVE_ENCRYPTION_KEY', '').strip():
            return
        companies = list(registration_companies().exclude(pk=RELONCAVI_COMPANY_ID))
        for company in companies:
            drive = CompanyDriveConnection.objects.get(company=company)
            if load_drive(company.pk, drive) is None:
                self.stderr.write(f'{company.name}: la clave del servidor no abre su conexión.')
                continue
            schema = schema_name(company.pk)
            try:
                prepare_workspace(schema)
            except DatabaseError as exc:
                code = getattr(exc.__cause__, 'sqlstate', None) or type(exc).__name__
                self.stderr.write(f'{company.name}: el espacio de planos no se pudo preparar ({code}).')
                continue
            self.stdout.write(f'{company.name}: espacio de planos preparado.')
