"""Open a local copy of a PDF and XLSX published to the shared Drive folders."""

import hashlib
import json
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from .models import PdfPlan, SavedProject
from .project_service import MAX_PROJECT_BYTES, finite, import_shared_project, validate_pdf_source


class CloudUnavailable(Exception):
    pass


def command_json(command, message, *, timeout):
    try:
        result = subprocess.run(command, cwd=settings.BASE_DIR, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout, check=True)
        # The Supabase CLI can print its target before the JSON result.
        start = result.stdout.find('{')
        if start < 0:
            raise ValueError
        return json.JSONDecoder().raw_decode(result.stdout[start:])[0]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise CloudUnavailable(message) from exc


def drive_command(*args):
    if not (settings.BASE_DIR / 'gdrive-token.json').is_file():
        raise CloudUnavailable('Esta instalación necesita una conexión autorizada a Google Drive para ver la nube.')
    result = command_json(
        ['node', str(settings.BASE_DIR / 'gdrive-sync-cli.js'), *args],
        'No se pudo consultar Google Drive desde esta instalación. Revisa la conexión y la autorización de Drive.',
        timeout=65,
    )
    if not result.get('ok'):
        raise CloudUnavailable('No se pudo leer el proyecto en Google Drive.')
    return result


def list_cloud_projects():
    return sorted(drive_command('--list-projects')['projects'], key=lambda item: item.get('date') or '', reverse=True)


def project_title(name):
    return re.sub(r'(?: - \d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)?\.pdf$', '', name, flags=re.I).strip()


def supabase_query(sql, *, read_only):
    message = 'Esta instalación no pudo consultar Supabase. Comprueba que su CLI esté enlazada y autorizada.'
    cli = shutil.which('supabase')
    npm_cli = (Path(os.getenv('APPDATA') or Path.home() / 'AppData' / 'Roaming')
               / 'npm' / 'node_modules' / 'supabase' / 'dist' / 'supabase.js')
    if cli and Path(cli).suffix.lower() not in ('.cmd', '.bat'):
        command = [cli]
    elif npm_cli.is_file():
        command = ['node', str(npm_cli)]
    else:
        raise CloudUnavailable(message)
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
            raise CloudUnavailable(message)
        command.extend(['--workdir', workdir])
    with TemporaryDirectory(prefix='planos-supabase-') as directory:
        query = Path(directory) / 'query.sql'
        query.write_text(sql, encoding='utf-8')
        flag = '--read-only' if read_only else '--yes'
        return command_json(
            [*command, 'db', 'query', '--remote', flag, '--file', str(query)],
            message, timeout=30 if read_only else 90,
        )


def supabase_metadata(pdf_id, excel_id):
    if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key) for key in (pdf_id, excel_id)):
        raise ValidationError('Los identificadores del proyecto en la nube no son válidos.')
    sql = f"""
    select p.name, p.display_settings, w.plan_mode,
        coalesce((select jsonb_agg(jsonb_build_object(
            'page', m.page, 'x', m.x, 'y', m.y, 'label', m.label, 'sheet', m.sheet_name,
            'has_area', m.has_area, 'area_radius_x', m.area_radius_x, 'area_radius_y', m.area_radius_y,
            'area_color', m.area_color, 'area_shape', m.area_shape, 'button_color', m.button_color,
            'show_description', m.show_description, 'piece_source',m.piece_source, 'category_id', m.category_id, 'category_name', c.name
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
    if not saved or not saved.content:
        return None
    try:
        with ZipFile(BytesIO(saved.content)) as archive:
            if archive.read('plan/source.pdf') == source and archive.read('workbooks/w1.xlsx') == content:
                return json.loads(archive.read('manifest.json'))
    except (BadZipFile, KeyError, ValueError):
        pass
    return None


def workbook_version(project):
    return f"{project['excelId']}:{project['date']}"


def snapshot_identity(project, metadata, portable):
    return hashlib.sha256(json.dumps([project, metadata, portable], sort_keys=True).encode()).hexdigest()


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
    warning = ''
    metadata = None
    if portable is None:
        try:
            metadata = supabase_metadata(pdf_id, project['excelId'])
        except CloudUnavailable:
            warning = 'Supabase no respondió al consultar los botones. El Excel completo está disponible en «Ver hojas de Excel».'
        if not metadata or not metadata.get('markers'):
            portable = saved_manifest(project, source, content)
        if not metadata and portable is None and not warning:
            warning = 'Este guardado no tiene coordenadas de botones en Supabase. Puedes consultar sus hojas de Excel.'
    if portable is not None:
        if (not isinstance(portable, dict) or portable.get('format') != 'motor-plan-project'
                or portable.get('version') != 3 or portable.get('kind') != 'pdf'):
            raise ValidationError('El Excel contiene una configuración de proyecto incompatible.')
        metadata = manifest_metadata(portable)
        warning = ''
    return project, source, content, metadata, portable, warning


def shared_publish_target(plan_id, pdf_id, source):
    """Resolve the original files; an explicit save replaces their current contents."""
    project = drive_command('--publish-target', '--drive-id', pdf_id)['project']
    if project is None:
        raise CloudUnavailable('El PDF original ya no está disponible en Drive.')
    return project


def open_cloud_project(pdf_id, user, replace_plan=None):
    from .views import validate_excel

    project, source, content, metadata, portable, warning = read_cloud_snapshot(pdf_id)
    version = workbook_version(project)
    identity = snapshot_identity(project, metadata, portable)
    previous = PdfPlan.objects.filter(cloud_snapshot=identity).first()
    if replace_plan and previous and previous.pk != replace_plan.pk:
        # Reuse an already validated download, preserving role assignments.
        _replace_cached_plan(replace_plan, previous)
    if previous:
        if not previous.cloud_workbook_version:
            PdfPlan.objects.filter(pk=previous.pk).update(cloud_origin=pdf_id, cloud_workbook_version=version)
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
            warning = 'Se recuperaron los botones. Algunas fichas referenciadas faltan en el Excel de Drive.'
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
            plan.cloud_base_state = capture_base_state(plan)
            plan.save(update_fields=['cloud_base_state'])
            if replace_plan and replace_plan.pk != plan.pk:
                _replace_cached_plan(replace_plan, plan)
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
def _replace_cached_plan(old, new):
    from .models import PlanRole
    from .project_views import clear_plan_files
    from .project_service import export_project
    if old.pk == new.pk or not PdfPlan.objects.filter(pk=old.pk).exists():
        return
    for role in PlanRole.objects.filter(pdf_plans=old):
        role.pdf_plans.add(new)
    saved = SavedProject.objects.filter(pdf_plan=old).first()
    if saved:
        SavedProject.objects.update_or_create(pdf_plan=new, defaults={
            'name': new.name, 'content': export_project('pdf', new), 'saved_by': saved.saved_by,
            'cloud_status': 'synced', 'cloud_error': ''})
    original_key = old.cloud_key
    clear_plan_files('pdf', old)
    PdfPlan.objects.filter(pk=new.pk).update(cloud_key=original_key)
    new.cloud_key = original_key
