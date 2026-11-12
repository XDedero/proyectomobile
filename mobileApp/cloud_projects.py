"""Open a local copy of a PDF and XLSX published to the shared Drive folders."""

import hashlib
import json
import logging
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from .models import PdfPlan, SavedProject
from .project_service import MAX_PROJECT_BYTES, export_project, finite, import_shared_project, validate_pdf_source


class CloudUnavailable(Exception):
    def __init__(self, message, *, code='unavailable'):
        super().__init__(message)
        self.code = code


def command_json(command, message, *, timeout, env=None):
    try:
        result = subprocess.run(command, cwd=settings.BASE_DIR, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout, check=True, env=env)
        # The Supabase CLI can print its target before the JSON result.
        start = min((position for character in ('{', '[')
                     if (position := result.stdout.find(character)) >= 0), default=-1)
        if start < 0:
            raise ValueError
        return json.JSONDecoder().raw_decode(result.stdout[start:])[0]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        code = 'command_failed'
        if isinstance(exc, FileNotFoundError):
            code = 'client_missing'
        elif isinstance(exc, PermissionError):
            code = 'client_permission'
        elif isinstance(exc, subprocess.TimeoutExpired):
            code = 'client_timeout'
        elif isinstance(exc, subprocess.CalledProcessError):
            detail = ((exc.stderr or '') + (exc.stdout or '')).lower()
            if 'necessary privileges' in detail or 'status 403' in detail:
                code = 'account_permission'
            elif 'access token not provided' in detail or 'status 401' in detail:
                code = 'account_authentication'
        elif isinstance(exc, ValueError):
            code = 'invalid_response'
        logging.getLogger(__name__).warning(
            'Cloud connector failed: code=%s executable=%s exit=%s',
            code, Path(command[0]).name, getattr(exc, 'returncode', None),
        )
        raise CloudUnavailable(message, code=code) from exc


def drive_command(*args):
    if not (settings.BASE_DIR / 'gdrive-token.json').is_file():
        raise CloudUnavailable('Esta instalación necesita autorización para consultar los archivos de la nube.')
    result = command_json(
        ['node', str(settings.BASE_DIR / 'gdrive-sync-cli.js'), *args],
        'No se pudo consultar el almacenamiento en la nube. Revisa la conexión y los permisos.',
        timeout=65,
    )
    if not result.get('ok'):
        raise CloudUnavailable('No se pudo leer el proyecto en la nube.')
    return result


def list_cloud_projects():
    return sorted(drive_command('--list-projects')['projects'], key=lambda item: item.get('date') or '', reverse=True)


def project_title(name):
    return re.sub(r'(?: - \d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)?\.pdf$', '', name, flags=re.I).strip()


def supabase_command():
    """Find the installed CLI, including npm's local cache on Windows."""
    cli = shutil.which('supabase')
    if cli and Path(cli).suffix.lower() not in ('.cmd', '.bat'):
        return [cli]
    candidates = []
    if cli:
        candidates.append(Path(cli).parent / 'node_modules' / 'supabase' / 'dist' / 'supabase.js')
    candidates.append(
        Path(os.getenv('APPDATA') or Path.home() / 'AppData' / 'Roaming')
        / 'npm' / 'node_modules' / 'supabase' / 'dist' / 'supabase.js')
    node = shutil.which('node')
    if node:
        for candidate in candidates:
            if candidate.is_file():
                return [node, str(candidate)]
    # `npx supabase` installs a native CLI in npm's cache, without adding it
    # to PATH. Reuse that installation instead of requiring another login.
    cache = Path(os.environ.get('npm_config_cache') or
                 Path(os.getenv('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'npm-cache')
    binaries = list(cache.glob('_npx/*/node_modules/@supabase/cli-windows-x64/bin/supabase.exe'))
    if binaries:
        return [str(max(binaries, key=lambda path: path.stat().st_mtime))]
    raise CloudUnavailable('Configure la conexión PostgreSQL privada en SUPABASE_DATABASE_URL o '
                           'conexion-base-datos.txt. No se encontró un conector alternativo de Supabase.',
                           code='client_missing')


def supabase_database_url():
    """Return the private server connection without requiring a developer CLI login."""
    value = os.environ.get('SUPABASE_DATABASE_URL', '').strip()
    if not value:
        profile = settings.BASE_DIR / 'conexion-base-datos.txt'
        try:
            if profile.is_file():
                value = profile.read_text(encoding='utf-8-sig').strip()
        except (OSError, UnicodeError) as exc:
            raise CloudUnavailable('No se pudo leer la configuración privada de la base de datos.',
                                   code='database_configuration') from exc
    if value and (not value.startswith(('postgresql://', 'postgres://'))
                  or any(character.isspace() for character in value)):
        raise CloudUnavailable('La configuración privada de la base de datos no es válida.',
                               code='database_configuration')
    if value:
        try:
            parsed = urlsplit(value)
            # Reject example strings before attempting DNS or authentication. Never
            # include the URI in an exception: it contains the database password.
            placeholder = re.search(r'\[(?:tu[_ -]?password|your[_ -]?password|password)\]',
                                    unquote(parsed.password or ''), re.I)
            if not parsed.hostname or any(char in parsed.hostname for char in '<>') or placeholder:
                raise ValueError
            parsed.port
        except ValueError as exc:
            raise CloudUnavailable(
                'Complete la URI PostgreSQL privada en SUPABASE_DATABASE_URL o conexion-base-datos.txt; '
                'los valores de ejemplo no permiten conectarse.', code='database_configuration',
            ) from exc
    return value


def postgres_query(database_url, sql, *, read_only):
    """Run SQL through PostgreSQL directly and return the CLI-compatible row shape."""
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise CloudUnavailable('Esta instalación no tiene el conector de la base de datos.',
                               code='client_missing') from exc
    connection_options = {
        'connect_timeout': 12,
        'autocommit': not read_only,
        'prepare_threshold': None,
        'row_factory': dict_row,
    }
    try:
        with psycopg.connect(database_url, **connection_options) as connection:
            if read_only:
                # Psycopg emits BEGIN READ ONLY for this transaction. A startup
                # option or a session SET can fail or leak across pooler clients.
                connection.read_only = True
            with connection.cursor() as cursor:
                cursor.execute(sql, prepare=False)
                rows = []
                while True:
                    if cursor.description is not None:
                        rows = cursor.fetchall()
                    if not cursor.nextset():
                        break
        return {'rows': rows}
    except psycopg.Error as exc:
        state = getattr(exc, 'sqlstate', None)
        code = {
            '42P01': 'database_schema', '3F000': 'database_schema',
            '42883': 'database_schema', '42703': 'database_schema',
            '42501': 'database_permission', '28P01': 'database_authentication',
        }.get(state, 'database_query' if state else 'database_connection')
        logging.getLogger(__name__).warning(
            'Database connector failed: code=%s sqlstate=%s', code, getattr(exc, 'sqlstate', None),
        )
        raise CloudUnavailable(
            'No se pudo consultar la base de datos. Revisa la conexión y los permisos.', code=code,
        ) from exc


def supabase_query(sql, *, read_only):
    message = 'No se pudo consultar la base de datos. Revisa la conexión y los permisos.'
    database_url = supabase_database_url()
    if database_url:
        return postgres_query(database_url, sql, read_only=read_only)
    command = supabase_command()
    workdir = os.environ.get('PLANOS_SUPABASE_WORKDIR')
    if not workdir:
        profile = settings.BASE_DIR / 'supabase-workdir.txt'
        try:
            if profile.is_file():
                workdir = profile.read_text(encoding='utf-8-sig').strip()
        except (OSError, UnicodeError) as exc:
            raise CloudUnavailable(message) from exc
    if workdir:
        directory = Path(workdir)
        if not directory.is_absolute() or not (directory / 'supabase' / '.temp' / 'project-ref').is_file():
            raise CloudUnavailable(message, code='project_configuration')
        command.extend(['--workdir', workdir])
    with TemporaryDirectory(prefix='planos-supabase-') as directory:
        query = Path(directory) / 'query.sql'
        query.write_text(sql, encoding='utf-8')
        flag = '--read-only' if read_only else '--yes'
        environment = os.environ.copy()
        environment.update(DO_NOT_TRACK='1', SUPABASE_TELEMETRY_DISABLED='1', SUPABASE_NO_UPDATE_NOTIFIER='1')
        result = command_json(
            [*command, '--agent=no', 'db', 'query', '--output', 'json', flag, '--file', str(query), '--remote'],
            message, timeout=30 if read_only else 90, env=environment,
        )
        # CLI v3 returns a JSON array for remote queries; native PostgreSQL and
        # older CLI releases use an object containing `rows`.
        if isinstance(result, list):
            result = {'rows': result}
        if not isinstance(result, dict) or not isinstance(result.get('rows'), list):
            raise CloudUnavailable('Supabase devolvió una respuesta de consulta no válida.', code='invalid_response')
        return result


def supabase_metadata(pdf_id, excel_id):
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key) for key in (pdf_id, excel_id)):
        raise ValidationError('Los identificadores del proyecto en la nube no son válidos.')
    sql = f"""
    select p.name, p.display_settings, w.plan_mode,
        coalesce((select jsonb_agg(jsonb_build_object(
            'page', m.page, 'x', m.x, 'y', m.y, 'label', m.label, 'sheet', m.sheet_name,
            'has_area', m.has_area, 'area_radius_x', m.area_radius_x, 'area_radius_y', m.area_radius_y,
            'area_color', m.area_color, 'area_shape', m.area_shape, 'button_color', m.button_color,
            'show_description', m.show_description, 'piece_source',m.piece_source, 'button_scale',m.button_scale, 'category_id', m.category_id, 'category_name', c.name
        ) order by m.id) from public.pdf_markers m
        left join public.button_categories c on c.id = m.category_id where m.plan_id = p.id), '[]') as markers
    from public.pdf_plans p
    left join public.workbook_documents w on w.drive_file_id = '{excel_id}'
    where p.drive_file_id = '{pdf_id}' limit 1;
    """
    result = supabase_query(sql, read_only=True)
    return next(iter(result.get('rows', [])), None)


def saved_manifest(project, source, content):
    key = project.get('projectKey')
    if not key:
        return None
    saved = SavedProject.objects.filter(pdf_plan__cloud_key=key).only('content').first()
    if not saved:
        return None
    try:
        if not saved.content:
            return None
        with ZipFile(BytesIO(saved.content)) as archive:
            if archive.read('plan/source.pdf') == source and archive.read('workbooks/w1.xlsx') == content:
                return json.loads(archive.read('manifest.json'))
    except (BadZipFile, KeyError, ValueError, OSError, CloudUnavailable):
        pass
    return None


def workbook_version(project):
    return f"{project['excelId']}:{project['date']}"


def drive_pair_version(project):
    fields = ('projectKey', 'id', 'pdfRevision', 'excelId', 'excelRevision')
    values = [project.get(field) for field in fields]
    return values if all(isinstance(value, str) and value for value in values) else None


def snapshot_identity(project, metadata, portable):
    transport_fields = {'pdfChecksum', 'excelChecksum'}
    stable_project = {key: value for key, value in project.items() if key not in transport_fields}
    return hashlib.sha256(json.dumps([stable_project, metadata, portable], sort_keys=True).encode()).hexdigest()


def manifest_metadata(manifest):
    return {'name': manifest.get('name'), 'plan_mode': manifest.get('workbook_mode'), 'markers': manifest.get('markers'), 'display_settings':manifest.get('display_settings', {}), 'sectors': manifest.get('sectors', [])}


def read_cloud_snapshot(pdf_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', pdf_id):
        raise ValidationError('El identificador del plano en la nube no es válido.')
    with TemporaryDirectory(prefix='planos-drive-') as directory:
        result = drive_command('--read-project', '--drive-id', pdf_id, '--output', directory)
        project = result['project']
        portable = result.get('manifest')
        source = (Path(directory) / 'plan.pdf').read_bytes()
        content = (Path(directory) / 'project.xlsx').read_bytes()
    for field, data in (('pdfChecksum', source), ('excelChecksum', content)):
        if project.get(field) and project[field].lower() != hashlib.sha256(data).hexdigest():
            raise CloudUnavailable('Los archivos cambiaron durante la descarga. Vuelve a abrir el plano.')
    warning = ''
    metadata = None
    if portable is None:
        try:
            metadata = supabase_metadata(pdf_id, project['excelId'])
        except CloudUnavailable:
            warning = 'La base de datos no respondió al consultar los botones. El Excel completo está disponible en «Ver hojas de Excel».'
        if not metadata or not metadata.get('markers'):
            portable = saved_manifest(project, source, content)
        if not metadata and portable is None and not warning:
            warning = 'Este guardado no tiene coordenadas de botones en la base de datos. Puedes consultar sus hojas de Excel.'
    if portable is not None:
        if (not isinstance(portable, dict) or portable.get('format') != 'motor-plan-project'
                or portable.get('version') != 3 or portable.get('kind') != 'pdf'):
            raise ValidationError('El Excel contiene una configuración de proyecto incompatible.')
        metadata = manifest_metadata(portable)
        warning = ''
    return project, source, content, metadata, portable, warning


def cloud_project_reference(pdf_id):
    """Look up the pair without transferring file contents."""
    return drive_command('--publish-target', '--drive-id', pdf_id)['project']


def shared_publish_target(plan_id, pdf_id, source):
    """Resolve the original files; an explicit save replaces their current contents."""
    project = cloud_project_reference(pdf_id)
    if project is None:
        raise CloudUnavailable('El PDF original ya no está disponible en la nube.')
    return project


def open_cloud_project(pdf_id, user, replace_plan=None):
    from .authorization import can_access_plan
    from .views import validate_excel

    candidates = PdfPlan.objects.filter(cloud_origin=pdf_id, cloud_snapshot__isnull=False)
    candidates = [plan for plan in candidates if (plan.cloud_base_state or {}).get('drive_pair')
                  and can_access_plan(user, plan)]
    if candidates:
        reference = cloud_project_reference(pdf_id)
        if not reference or not reference.get('excelId'):
            raise CloudUnavailable('El par PDF y Excel ya no está disponible en la nube.')
        version = drive_pair_version(reference)
        previous = next((plan for plan in candidates if plan.cloud_base_state['drive_pair'] == version), None)
        if previous:
            if replace_plan and previous.pk != replace_plan.pk:
                _replace_cached_plan(replace_plan, previous)
            return previous, ''

    project, source, content, metadata, portable, warning = read_cloud_snapshot(pdf_id)
    version = workbook_version(project)
    # Only the embedded manifest binds the configuration to the exact Drive
    # bytes. Legacy projects can get independent metadata updates in Supabase.
    pair = drive_pair_version(project) if portable and project.get('pdfChecksum') and project.get('excelChecksum') else None
    identity = snapshot_identity(project, metadata, portable)
    previous = PdfPlan.objects.filter(cloud_snapshot=identity).first()
    if replace_plan and previous and previous.pk != replace_plan.pk:
        # Reuse an already validated download, preserving role assignments.
        _replace_cached_plan(replace_plan, previous)
    if previous:
        fields = {}
        if not previous.cloud_workbook_version:
            fields.update(cloud_origin=pdf_id, cloud_workbook_version=version)
        if pair and previous.cloud_base_state:
            fields['cloud_base_state'] = {**previous.cloud_base_state, 'drive_pair': pair}
        if fields:
            PdfPlan.objects.filter(pk=previous.pk).update(**fields)
            for key, value in fields.items():
                setattr(previous, key, value)
        return previous, warning

    name = (metadata or {}).get('name') or project_title(project['name'])
    excel_name = project['excelName'][:-5][:250] + '.xlsx'
    pages = validate_pdf_source(source)
    sheets = validate_excel(SimpleUploadedFile(excel_name, content), max_bytes=MAX_PROJECT_BYTES)
    markers = (metadata or {}).get('markers', [])
    if not isinstance(markers, list) or len(markers) > 5000 or any(
        not isinstance(marker, dict) or type(marker.get('page')) is not int or not 1 <= marker['page'] <= len(pages)
        or any(not finite(marker.get(axis)) or not 0 <= marker[axis] <= 1 for axis in ('x', 'y'))
        or not isinstance(marker.get('label', ''), str) or len(marker.get('label', '')) > 120
        or any(marker.get(key) is not None and (not isinstance(marker[key], str) or len(marker[key]) > limit)
               for key, limit in [('sheet', 31), ('category_id', 36), ('category_name', 80), ('area_color', 50)])
        or any(marker.get(key) is not None and (not finite(marker[key]) or not 0 < marker[key] <= 1)
               for key in ('area_radius_x', 'area_radius_y'))
        for marker in markers
    ):
        raise ValidationError('La configuración de botones no es válida para este PDF.')
    markers = [item.copy() for item in markers]
    for marker in markers:
        sheet = marker.get('sheet') or ''
        if sheet and sheet not in sheets:
            if portable is not None:
                raise ValidationError('La hoja de un botón no está en el Excel de este guardado.')
            warning = 'Se recuperaron los botones. Algunas fichas referenciadas faltan en el Excel almacenado en la nube.'
            sheet = ''
        marker.update(sheet=sheet, workbook='w1' if sheet else None, label=marker.get('label') or '',
                      has_area=bool(marker.get('has_area')), area_color=marker.get('area_color') or 'rgba(59, 130, 246, 0.25)')
    used = {marker['sheet'] for marker in markers}
    listings = [sheet for sheet in sheets if sheet not in used and sheet.casefold().startswith('listado')]
    mode = (metadata or {}).get('plan_mode')
    manifest = {'display_settings':(metadata or {}).get('display_settings', {}), 'workbook': 'w1', 'workbook_mode': mode if mode in ('template', 'imported') else 'imported', 'sheets': [
        {'name': sheet, 'is_listing': sheet in listings, 'is_primary': bool(listings and sheet == listings[0]),
         'listing_order': listings.index(sheet) + 1 if sheet in listings else 0}
        for sheet in sheets
    ]}
    if portable is not None:
        manifest = portable
    owner = (portable or {}).get('owner_username', '')
    if not isinstance(owner, str) or len(owner) > 150:
        raise ValidationError('El creador del plano publicado no es válido.')
    plan = PdfPlan(name=name[:255], size=len(source), pages=pages, imported_by=user, owner_username=owner, cloud_snapshot=identity,
                   cloud_origin=pdf_id, cloud_workbook_version=version)
    if hasattr(plan, 'source_content'):
        plan.source_content = source
    try:
        with transaction.atomic():
            import_shared_project(plan, 'pdf', source, None, markers,
                                  {'w1': {'name': excel_name, 'content': content, 'sheets': sheets}}, manifest, user)
            from .cloud_save import capture_base_state
            bundle = export_project('pdf', plan, source=source)
            plan.cloud_base_state = capture_base_state(plan, bundle)
            if pair:
                plan.cloud_base_state['drive_pair'] = pair
            plan.save(update_fields=['cloud_base_state'])
            if replace_plan and replace_plan.pk != plan.pk:
                _replace_cached_plan(replace_plan, plan, content=bundle)
    except Exception as exc:
        if plan.source:
            plan.source.delete(save=False)
        if isinstance(exc, IntegrityError):
            previous = PdfPlan.objects.filter(cloud_snapshot=identity).first()
            if previous:
                if replace_plan and previous.pk != replace_plan.pk:
                    _replace_cached_plan(replace_plan, previous)
                return previous, warning
        raise
    return plan, warning


@transaction.atomic
def _replace_cached_plan(old, new, *, content=None):
    from .models import PlanRole
    from .project_views import clear_plan_files
    if old.pk == new.pk or not PdfPlan.objects.filter(pk=old.pk).exists():
        return
    for role in PlanRole.objects.filter(pdf_plans=old):
        role.pdf_plans.add(new)
    saved = SavedProject.objects.filter(pdf_plan=old).first()
    if saved:
        SavedProject.objects.update_or_create(pdf_plan=new, defaults={
            'name': new.name, 'content': content if content is not None else export_project('pdf', new), 'saved_by': saved.saved_by,
            'cloud_status': 'synced', 'cloud_error': ''})
    original_key = old.cloud_key
    clear_plan_files('pdf', old)
    PdfPlan.objects.filter(pk=new.pk).update(cloud_key=original_key)
    new.cloud_key = original_key
