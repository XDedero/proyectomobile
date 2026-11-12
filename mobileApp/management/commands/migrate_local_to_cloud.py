"""Copy one local installation to an empty private cloud database, never move it."""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.migrations.loader import MigrationLoader

from mobileApp.drive_storage import GoogleDriveStorage


SEED_CATEGORIES = {
    ('643ac8a7-0c92-4275-ab07-145cce6869e0', 'Motores'),
    ('f8e1774e-4bed-4195-b8d6-3841b82e095f', 'Controladores'),
}
SEED_GROUPS = {'Lectores de planos', 'Editores de motores'}


def installation_models():
    return [get_user_model(), Group, *apps.get_app_config('mobileApp').get_models()]


def require_empty_target():
    for model in installation_models():
        if model is Group:
            if set(model.objects.values_list('name', flat=True)) - SEED_GROUPS:
                raise CommandError('El destino ya contiene grupos propios; no se modificara.')
        elif model._meta.model_name == 'buttoncategory':
            rows = {(str(pk), name) for pk, name in model.objects.values_list('pk', 'name')}
            if rows - SEED_CATEGORIES:
                raise CommandError('El destino ya contiene categorias propias; no se modificara.')
        elif model.objects.exists():
            raise CommandError('El destino ya contiene datos de la aplicacion; no se sobrescribiran. Use un esquema privado nuevo.')


def local_original(record, field, media, source_alias):
    name = record[field]
    path = (media / name).resolve()
    if not path.is_relative_to(media):
        raise CommandError('Una referencia de archivo sale de la carpeta media; se cancela la copia.')
    if path.is_file():
        return path.read_bytes()
    kind = {'mobileapp.pdfplan': 'pdf', 'mobileapp.plan': 'dxf'}.get(record['model'].lower())
    if kind:
        saved_model = apps.get_model('mobileApp', 'SavedProject')
        saved = saved_model.objects.using(source_alias).filter(
            **{'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': record['pk']}
        ).only('content').first()
        content = local_original({'model': 'mobileapp.savedproject', 'pk': saved.pk, 'content': saved.__dict__['content']},
                                 'content', media, source_alias) if saved and saved.__dict__['content'] else b''
        if content:
            try:
                with ZipFile(BytesIO(bytes(content))) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                    source = f'plan/source.{kind}'
                    if (manifest.get('format') == 'motor-plan-project' and manifest.get('kind') == kind
                            and manifest.get('source') == source
                            and 0 < archive.getinfo(source).file_size <= settings.PLAN_UPLOAD_MAX_BYTES):
                        return archive.read(source)
            except (ValueError, KeyError, OSError):
                pass
    raise CommandError(f"Falta el original de {record['model']} #{record['pk']}; no se importaran referencias rotas.")


def copy_installation(source_alias, media, storage):
    """Data transfer separated from configuration so it can be tested in isolation."""
    require_empty_target()
    models = installation_models()
    objects = (obj for model in models for obj in model.objects.using(source_alias).order_by('pk').iterator())
    payload = json.loads(serializers.serialize('json', objects, use_natural_foreign_keys=True))
    # Serializer model labels are lowercase, including the legacy mixed-case app label.
    file_fields = {'mobileApp.plan': 'source', 'mobileApp.pdfplan': 'source', 'mobileApp.motor': 'excel'}
    file_fields.update({'mobileApp.workbookdocument': 'content', 'mobileApp.savedproject': 'content'})
    file_fields.update({name.lower(): value for name, value in list(file_fields.items())})
    uploads = []
    memo = {}
    try:
        for record in payload:
            field = file_fields.get(record['model'])
            if field and record['fields'].get(field):
                original_name = record['fields'][field]
                if original_name not in memo:
                    source_record = {**record, field: original_name}
                    content = local_original(source_record, field, media, source_alias)
                    remote = storage.save(original_name, ContentFile(content))
                    uploads.append(remote)
                    with storage.open(remote, 'rb') as downloaded:
                        if sha256(downloaded.read()).digest() != sha256(content).digest():
                            raise CommandError('La verificacion del original en Drive no coincide; se cancela la importacion.')
                    memo[original_name] = remote
                record['fields'][field] = memo[original_name]
        with transaction.atomic():
            # Serialize concurrent imports, and check again after the network transfer.
            if connections['default'].vendor == 'postgresql':
                with connections['default'].cursor() as cursor:
                    cursor.execute('SELECT pg_advisory_xact_lock(1794003501)')
            require_empty_target()
            # Historical model renames leave valid legacy permissions in SQLite.
            # Preserve their natural keys instead of dropping account privileges.
            for permission in Permission.objects.using(source_alias).select_related('content_type'):
                content_type, _ = ContentType.objects.get_or_create(
                    app_label=permission.content_type.app_label, model=permission.content_type.model,
                )
                Permission.objects.get_or_create(content_type=content_type, codename=permission.codename,
                                                 defaults={'name': permission.name})
            apps.get_model('mobileApp', 'ButtonCategory').objects.all().delete()
            Group.objects.all().delete()
            with TemporaryDirectory(prefix='reloncavi-fixture-') as directory:
                fixture = Path(directory) / 'installation.json'
                fixture.write_text(json.dumps(payload), encoding='utf-8')
                fixture.chmod(0o600)
                call_command('loaddata', str(fixture), verbosity=0)
            for model in models:
                if model.objects.count() != model.objects.using(source_alias).count():
                    raise CommandError('El recuento importado no coincide; se revierte la transaccion.')
        return {'records': len(payload), 'files': len(memo)}
    except Exception:
        for name in uploads:
            try:
                storage.delete(name)
            except Exception:
                pass  # Preserve original local files even if Drive is unavailable.
        raise


class Command(BaseCommand):
    help = 'Copia SQLite y media a un esquema privado vacio de Supabase y originales de Drive.'

    def add_arguments(self, parser):
        parser.add_argument('--sqlite', required=True)
        parser.add_argument('--media', required=True)
        parser.add_argument('--execute', action='store_true', help='Realiza la transferencia despues de comprobar que el destino esta vacio.')

    def handle(self, *args, **options):
        if not settings.PLANOS_CLOUD_MODE or connections['default'].vendor != 'postgresql':
            raise CommandError('Active PLANOS_CLOUD_MODE=1 y configure Supabase antes de transferir.')
        source = Path(options['sqlite']).resolve(strict=True)
        media = Path(options['media']).resolve(strict=True)
        if not media.is_dir():
            raise CommandError('--media debe ser una carpeta.')
        require_empty_target()
        if not options['execute']:
            self.stdout.write('Destino vacio. No se modifico ningun dato. Agregue --execute para copiar la instalacion.')
            return
        previous_debug = os.environ.get('APP_TERMINAL_DEBUG')
        os.environ['APP_TERMINAL_DEBUG'] = '0'
        alias = 'local_cloud_transfer'
        try:
            with TemporaryDirectory(prefix='reloncavi-snapshot-') as directory:
                snapshot = Path(directory) / 'db.sqlite3'
                with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
                    with closing(sqlite3.connect(snapshot)) as backup:
                        original.backup(backup)
                        if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise CommandError('La copia SQLite no supera la comprobacion de integridad.')
                config = deepcopy(connections.databases['default'])
                config.update(ENGINE='django.db.backends.sqlite3', NAME=snapshot.as_uri() + '?mode=ro',
                              USER='', PASSWORD='', HOST='', PORT='', OPTIONS={'uri': True})
                connections.databases[alias] = config
                try:
                    loader = MigrationLoader(connections[alias])
                    if set(loader.graph.leaf_nodes()) - set(loader.applied_migrations):
                        raise CommandError('Actualice primero las migraciones de la instalacion local.')
                    result = copy_installation(alias, media, GoogleDriveStorage())
                finally:
                    connections[alias].close()
                    del connections[alias]
                    del connections.databases[alias]
        finally:
            if previous_debug is None:
                os.environ.pop('APP_TERMINAL_DEBUG', None)
            else:
                os.environ['APP_TERMINAL_DEBUG'] = previous_debug
        self.stdout.write(self.style.SUCCESS(
            f"Copia verificada: {result['records']} registros y {result['files']} originales. SQLite y media locales se conservan."
        ))
