"""
Monitor en tiempo real para mostrar en la terminal todos los cambios que se
realizan mientras se prueba la aplicación (Planos, Botones, Área Circular,
Tablas Excel/CSV, y sincronización con Supabase Cloud y Google Drive).
"""

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db import transaction, close_old_connections
from django.db.models import Q
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    ButtonCategory,
    Motor,
    PdfMarker,
    PdfPlan,
    Plan,
    PlanWorkbookSheet,
    SavedMotor,
    SavedProject,
    WorkbookDocument,
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, 'debug_app.log')
GDRIVE_CLI = os.path.join(BASE_DIR, 'gdrive-sync-cli.js')

# Cola secuencial para garantizar que las subidas a Google Drive y consultas SQL
# a Supabase se ejecuten en orden cronológico estricto sin condiciones de carrera.
_SYNC_QUEUE = queue.Queue()
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def is_monitor_enabled():
    if 'test' in sys.argv:
        return False
    return os.environ.get('APP_TERMINAL_DEBUG') == '1' or 'runserver' in sys.argv


def _log_line(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        print(msg.encode(encoding, errors='backslashreplace').decode(encoding), flush=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')
    except OSError:
        pass


def _ensure_worker():
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True

        def _worker_loop():
            while True:
                task = _SYNC_QUEUE.get()
                try:
                    close_old_connections()
                    task()
                except Exception as exc:
                    _log_line(f"  ↳ [SYNC INFO] Error en tarea en segundo plano: {exc}")
                finally:
                    close_old_connections()
                    _SYNC_QUEUE.task_done()

        t = threading.Thread(target=_worker_loop, daemon=True)
        t.start()


def enqueue_sync_task(fn):
    if not is_monitor_enabled():
        return
    _ensure_worker()
    _SYNC_QUEUE.put(fn)


def print_live_event(tag, title, lines):
    if not is_monitor_enabled():
        return
    now = datetime.now().strftime('%H:%M:%S')
    border = '─' * 72
    output = [
        f"\n┌{border}",
        f"│ [{now}] [{tag}] {title}",
    ]
    for line in lines:
        output.append(f"│   • {line}")
    output.append(f"└{border}")
    _log_line('\n'.join(output))


def run_supabase_sql_sync(description, sql_statement):
    from .cloud_projects import CloudUnavailable, supabase_query

    now = datetime.now().strftime('%H:%M:%S')
    try:
        supabase_query(sql_statement, read_only=False)
    except CloudUnavailable as exc:
        _log_line(f"  ↳ [{now}] [SUPABASE-CLOUD WARN] {description}: {exc}")
        return False
    else:
        _log_line(f"  ↳ [{now}] [SUPABASE-CLOUD OK] {description}")
        return True


def run_gdrive_upload_sync(file_path, file_name, assoc_key, local_key, extra_args=()):
    """Invoca gdrive-sync-cli.js (Node.js) para subir o actualizar el archivo en Google Drive."""
    try:
        proc = subprocess.run(
            [
                'node', GDRIVE_CLI,
                '--upload',
                '--file', file_path,
                '--name', file_name,
                '--key', assoc_key,
                '--local-key', local_key,
                *extra_args,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=45,
        )
        now = datetime.now().strftime('%H:%M:%S')
        stdout = (proc.stdout or '').strip().splitlines()
        last_line = stdout[-1] if stdout else '{}'
        data = json.loads(last_line)
        if data.get('ok') and data.get('record'):
            rec = data['record']
            verb = 'Actualizado en' if rec.get('action') == 'updated' else 'Subido a'
            _log_line(
                f"  ↳ [{now}] [GDRIVE-CLOUD OK] {verb} carpeta '{rec.get('folder_name')}': "
                f"'{rec.get('name')}' (Drive ID: {rec.get('drive_file_id')})"
            )
            return rec
        elif data.get('readonlyScope'):
            _log_line(
                f"  ↳ [{now}] [GDRIVE-CLOUD PERMISO REQUERIDO] No se pudo subir '{file_name}' porque el token "
                f"actual es de SOLO LECTURA ({data.get('currentScope')}). "
                f"Autoriza permiso de escritura con: node gdrive-sync-cli.js --exchange-code <CODIGO>"
            )
            return None
        else:
            _log_line(f"  ↳ [{now}] [GDRIVE-CLOUD WARN] '{file_name}': {data.get('error', 'Error desconocido')}")
            return None
    except Exception as exc:
        _log_line(f"  ↳ [GDRIVE-CLOUD INFO] Error al ejecutar puente Node.js: {exc}")
        return None


def sync_sql_to_supabase_async(description, sql_statement):
    enqueue_sync_task(lambda: run_supabase_sql_sync(description, sql_statement))


def category_upsert_sql(category):
    name = category.name.replace("'", "''")
    return f"""
    INSERT INTO public.button_categories (id, name)
    VALUES ('{category.pk}', '{name}')
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;
    """


@receiver(post_save, sender=ButtonCategory)
def on_category_saved(sender, instance, using, **kwargs):
    sql = category_upsert_sql(instance)
    transaction.on_commit(lambda: sync_sql_to_supabase_async('Categoría de botón guardada', sql), using=using)


@receiver(post_delete, sender=ButtonCategory)
def on_category_deleted(sender, instance, using, **kwargs):
    sql = f"DELETE FROM public.button_categories WHERE id = '{instance.pk}';"
    transaction.on_commit(lambda: sync_sql_to_supabase_async('Categoría de botón eliminada', sql), using=using)


@receiver(user_logged_in)
def on_user_login(sender, request, user, **kwargs):
    print_live_event(
        'SESIÓN INICIADA',
        f"Usuario '{user.username}' ingresó a la aplicación",
        [
            f"ID Usuario: {user.pk} | Email: {user.email or 'N/A'}",
            f"Rol Superusuario/Admin: {'SÍ' if user.is_superuser else 'Usuario Normal'}",
        ],
    )


@receiver(user_logged_out)
def on_user_logout(sender, request, user, **kwargs):
    if user:
        print_live_event(
            'SESIÓN CERRADA',
            f"Usuario '{user.username}' cerró sesión",
            [f"ID Usuario: {user.pk}"],
        )


def _upload_drive_content(content, file_name, assoc_key, local_key, manifest=None, project_source=None, drive_target=None):
    if not content:
        return None
    with tempfile.TemporaryDirectory(prefix='planos-publish-') as directory:
        from pathlib import Path
        path = Path(directory)
        (path / 'content').write_bytes(content)
        extra = []
        if manifest is not None and project_source is not None:
            (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            (path / 'plan.source').write_bytes(project_source)
            extra = ['--manifest', str(path / 'manifest.json'), '--project-source', str(path / 'plan.source')]
        if drive_target:
            extra += ['--drive-id', drive_target['id'], '--modified-time', drive_target['modifiedTime']]
        return run_gdrive_upload_sync(str(path / 'content'), file_name, assoc_key, local_key, extra)


def _sync_pdf_plan_worker(plan_pk, plan_name, source, pages_list, file_name, assoc_key=None, drive_target=None):
    local_key = assoc_key or f"local_pdf_{plan_pk}"
    rec = _upload_drive_content(source, file_name, local_key, local_key, drive_target=drive_target)
    if not rec or not rec.get('drive_file_id'):
        return
    drive_id = rec['drive_file_id']
    stream_url = rec.get('drive_stream_url', f"https://www.googleapis.com/drive/v3/files/{drive_id}?alt=media")
    preview_url = rec.get('drive_preview_url', f"https://drive.google.com/file/d/{drive_id}/preview")

    safe_name = plan_name.replace("'", "''")
    pages_json = json.dumps(pages_list or []).replace("'", "''")
    legacy_match = '' if drive_target else f" OR drive_file_id = '{local_key}'"
    sql = f"""
    UPDATE public.pdf_plans
    SET association_key = '{local_key}',
        name = '{safe_name}',
        drive_file_id = '{drive_id}',
        size_bytes = {len(source)},
        pages_data = '{pages_json}',
        drive_stream_url = '{stream_url}',
        drive_preview_url = '{preview_url}'
    WHERE drive_file_id = '{drive_id}'{legacy_match};

    INSERT INTO public.pdf_plans (
        company_id, association_key, name, drive_file_id, drive_folder_id,
        size_bytes, pages_data, drive_stream_url, drive_preview_url
    )
    SELECT
        1, '{local_key}', '{safe_name}', '{drive_id}', '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX',
        {len(source)}, '{pages_json}', '{stream_url}', '{preview_url}'
    WHERE NOT EXISTS (
        SELECT 1 FROM public.pdf_plans WHERE drive_file_id = '{drive_id}'
    );
    """
    rec['supabase_ok'] = run_supabase_sql_sync(
        f"Plano PDF '{plan_name}' sincronizado en Supabase (tabla pdf_plans, Drive ID: {drive_id})",
        sql,
    )
    return rec


@receiver(post_save, sender=PdfPlan)
def on_pdf_plan_saved(sender, instance, created, **kwargs):
    action = 'NUEVO PLANO PDF IMPORTADO' if created else 'PLANO PDF ACTUALIZADO'
    print_live_event(
        action,
        f"Plano PDF: '{instance.name}' (ID Local: {instance.pk})",
        [
            f"Páginas detectadas : {len(instance.pages or [])} página(s)",
            f"Tamaño del archivo : {instance.size:,} bytes",
            f"Carpeta Google Drive destino: pdf (1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX)",
            f"Libro Excel vinculado ID: {instance.workbook_id or 'En creación...'}",
        ],
    )


@receiver(post_delete, sender=Plan)
@receiver(post_delete, sender=PdfPlan)
def on_plan_deleted(sender, instance, **kwargs):
    if not is_monitor_enabled() or getattr(instance, 'cloud_snapshot', None) or getattr(instance, '_keep_cloud', False):
        return
    kind = 'pdf' if sender is PdfPlan else 'dxf'
    project_key = instance.cloud_key
    workbook_id = instance.workbook_id or instance.listing_id
    workbook_key = ''
    if workbook_id:
        references = Q(workbook_id=workbook_id) | Q(listing_id=workbook_id)
        if not (PdfPlan.objects.filter(references).exists() or Plan.objects.filter(references).exists()
                or SavedMotor.objects.filter(workbook_id=workbook_id).exists()):
            workbook_key = f'{project_key}_xlsx' if project_key.startswith('project_') else f'local_xlsx_{workbook_id}'

    def enqueue():
        enqueue_sync_task(lambda: _delete_drive_copy('excel', 'workbook_documents', workbook_key, project_key))
        if sender is PdfPlan:
            enqueue_sync_task(lambda: _delete_drive_copy('pdf', 'pdf_plans', project_key, project_key))

    transaction.on_commit(enqueue, using=kwargs.get('using'))


def _delete_drive_copy(folder, table, local_key, project_key):
    args = ['node', GDRIVE_CLI, '--delete', '--folder', folder, '--project-key', project_key]
    if local_key:
        args.extend(['--local-key', local_key])
    proc = subprocess.run(
        args,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=30,
    )
    output = (proc.stdout or '').strip().splitlines()
    data = json.loads(output[-1]) if output else {}
    if not data.get('ok'):
        raise RuntimeError(f"Google Drive ({project_key}, {folder}): {data.get('error', 'no se pudo eliminar la copia')}")
    result = data['result']
    errors = result.get('errors', [])
    ids = result.get('driveFileIds', [])
    if local_key and not errors:
        ids.append(local_key)
    if ids:
        targets = ', '.join("'" + identifier.replace("'", "''") + "'" for identifier in ids)
        run_supabase_sql_sync(
            f"Registros de {project_key} retirados de {table}",
            f"DELETE FROM public.{table} WHERE drive_file_id IN ({targets});",
        )
    if errors:
        raise RuntimeError(f"Google Drive ({project_key}, {folder}): {'; '.join(errors)}")
    if not result.get('deleted'):
        _log_line(f"  ↳ [GDRIVE-CLOUD INFO] No se encontró una copia {folder} para {project_key}.")


@receiver(post_save, sender=PdfMarker)
def on_pdf_marker_saved(sender, instance, created, **kwargs):
    if not is_monitor_enabled() or instance.plan.cloud_snapshot:
        return
    action = 'BOTÓN CREADO EN PLANO PDF' if created else 'BOTÓN MODIFICADO EN PLANO PDF'
    has_area = bool(instance.has_area)
    rx_str = f"{instance.area_radius_x * 100:.1f}% (valor SQL: {instance.area_radius_x:.4f})" if instance.area_radius_x is not None else "NULL (No obligatorio)"
    ry_str = f"{instance.area_radius_y * 100:.1f}% (valor SQL: {instance.area_radius_y:.4f})" if instance.area_radius_y is not None else "NULL (No obligatorio)"

    print_live_event(
        action,
        f"Botón #{instance.pk} — Nombre: '{instance.label or f'Botón {instance.pk}'}'",
        [
            f"Plano PDF ID       : {instance.plan_id} | Página: {instance.page}",
            f"Coordenadas (X, Y) : X = {instance.x:.4f} ({instance.x * 100:.1f}%), Y = {instance.y:.4f} ({instance.y * 100:.1f}%)",
            f"Hoja Excel asignada: '{instance.sheet_name or 'Sin hoja'}'",
            f"Casilla Área Circular/Elíptica (has_area): {'✅ ACTIVADA' if has_area else '⬜ DESACTIVADA'}",
            f"Radio Horizontal (area_radius_x): {rx_str}",
            f"Radio Vertical   (area_radius_y): {ry_str}",
        ],
    )

    # Marker state is published atomically with its PDF/XLSX snapshot on Save.


@receiver(post_delete, sender=PdfMarker)
def on_pdf_marker_deleted(sender, instance, **kwargs):
    if not is_monitor_enabled() or getattr(kwargs.get('origin'), 'cloud_snapshot', None):
        return
    if PdfPlan.objects.filter(pk=instance.plan_id, cloud_snapshot__isnull=False).exists():
        return
    print_live_event(
        'BOTÓN ELIMINADO',
        f"Se eliminó el Botón #{instance.pk} ('{instance.label or 'Sin nombre'}')",
        [f"Plano ID: {instance.plan_id} | Página: {instance.page}"],
    )


@receiver(post_delete, sender=PdfMarker)
@receiver(post_delete, sender=Motor)
def delete_button_source(sender, instance, using, **kwargs):
    if instance.source_workbook_id:
        WorkbookDocument.objects.using(using).filter(pk=instance.source_workbook_id, plan_mode='source').delete()


def _sync_workbook_worker(wb_pk, wb_revision, wb_plan_mode, wb_is_motor, content_bytes, assoc_key, drive_filename,
                          manifest=None, project_source=None, drive_target=None):
    local_key = f"{assoc_key}_xlsx" if assoc_key.startswith("project_") else f"local_xlsx_{wb_pk}"
    rec = _upload_drive_content(content_bytes, drive_filename, assoc_key, local_key, manifest, project_source, drive_target)
    if not rec or not rec.get('drive_file_id'):
        return
    drive_id = rec['drive_file_id']
    stream_url = rec.get('drive_stream_url', f"https://www.googleapis.com/drive/v3/files/{drive_id}?alt=media")

    safe_name = drive_filename.replace("'", "''")
    file_fmt = 'csv' if drive_filename.lower().endswith('.csv') else 'xlsx'
    legacy_match = '' if drive_target else f" OR drive_file_id = '{local_key}'"
    sql = f"""
    UPDATE public.workbook_documents
    SET association_key = '{assoc_key}',
        name = '{safe_name}',
        drive_file_id = '{drive_id}',
        size_bytes = {len(content_bytes)},
        drive_stream_url = '{stream_url}',
        revision = {int(wb_revision or 1)},
        updated_at = CURRENT_TIMESTAMP
    WHERE drive_file_id = '{drive_id}'{legacy_match};

    INSERT INTO public.workbook_documents (
        company_id, association_key, name, file_format, plan_mode,
        drive_file_id, drive_folder_id, size_bytes, drive_stream_url, revision, is_motor
    )
    SELECT
        1, '{assoc_key}', '{safe_name}', '{file_fmt}', '{wb_plan_mode or 'pdf'}',
        '{drive_id}', '13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s',
        {len(content_bytes)}, '{stream_url}',
        {int(wb_revision or 1)}, {'TRUE' if wb_is_motor else 'FALSE'}
    WHERE NOT EXISTS (
        SELECT 1 FROM public.workbook_documents WHERE drive_file_id = '{drive_id}'
    );
    """
    if manifest is not None:
        sheets = json.dumps(manifest['sheets']).replace("'", "''")
        references = json.dumps([index for index, marker in enumerate(manifest['markers']) if marker.get('descriptions')])
        sql += f"""
        DELETE FROM public.plan_workbook_sheets WHERE workbook_id IN
            (SELECT id FROM public.workbook_documents WHERE drive_file_id = '{drive_id}');
        INSERT INTO public.plan_workbook_sheets(workbook_id, name, is_listing, is_primary, is_protected, listing_order, origin_uuid)
        SELECT w.id, item->>'name', (item->>'is_listing')::boolean, (item->>'is_primary')::boolean,
            (item->>'is_protected')::boolean, (item->>'listing_order')::integer, item->>'origin_uuid'
        FROM public.workbook_documents w, jsonb_array_elements('{sheets}'::jsonb) WITH ORDINALITY AS tabs(item, position)
        WHERE w.drive_file_id = '{drive_id}' ORDER BY position;
        DELETE FROM public.workbook_button_description_refs WHERE workbook_id IN
            (SELECT id FROM public.workbook_documents WHERE drive_file_id = '{drive_id}');
        INSERT INTO public.workbook_button_description_refs(workbook_id, button_index, description_ref)
        SELECT w.id, idx::integer, 'drive:' || w.drive_file_id || '#markers/' || idx || '/descriptions'
        FROM public.workbook_documents w, jsonb_array_elements_text('{references}'::jsonb) AS buttons(idx)
        WHERE w.drive_file_id = '{drive_id}';
        """
    if (manifest or {}).get('kind') == 'pdf' or assoc_key.startswith('local_pdf_'):
        listing = 'book.id' if manifest is None or manifest['listing'] else 'NULL'
        sql += f"""
        UPDATE public.pdf_plans AS plan
        SET workbook_id = book.id, listing_id = {listing}
        FROM public.workbook_documents AS book
        WHERE plan.company_id = book.company_id AND plan.association_key = '{assoc_key}'
          AND book.drive_file_id = '{drive_id}';
        """
    rec['supabase_ok'] = run_supabase_sql_sync(
        f"Libro Excel '{drive_filename}' (Rev #{wb_revision}) sincronizado en Supabase (workbook_documents, Drive ID: {drive_id})",
        f'BEGIN; {sql} COMMIT;',
    )
    return rec


@receiver(post_save, sender=WorkbookDocument)
def on_workbook_saved(sender, instance, created, **kwargs):
    action = 'NUEVO LIBRO EXCEL CREADO' if created else 'CAMBIOS GUARDADOS EN LIBRO EXCEL'
    print_live_event(
        action,
        f"Libro Excel: '{instance.name}' (ID: {instance.pk})",
        [
            f"Revisión actual     : #{instance.revision}",
            f"Tamaño en memoria   : {len(instance.content):,} bytes",
        ],
    )


@receiver(post_save, sender=PlanWorkbookSheet)
def on_sheet_saved(sender, instance, created, **kwargs):
    if created:
        tipo = 'Listado de motores' if instance.is_listing else 'Ficha de motor'
        print_live_event(
            'HOJA DE EXCEL REGISTRADA',
            f"Hoja '{instance.name}' en Libro ID {instance.workbook_id}",
            [
                f"Tipo de hoja: {tipo}",
                f"Protegida   : {'Sí' if instance.is_protected else 'No'}",
            ],
        )


@receiver(post_save, sender=SavedProject)
def on_project_saved(sender, instance, created, **kwargs):
    action = 'PROYECTO GUARDADO EN BIBLIOTECA' if created else 'PROYECTO ACTUALIZADO EN BIBLIOTECA'
    content_bytes = bytes(instance.content) if instance.content else b''
    print_live_event(
        action,
        f"Proyecto: '{instance.name}' (ID: {instance.pk})",
        [
            f"Plano PDF vinculado ID: {instance.pdf_plan_id or 'N/A'}",
            f"Plano DXF vinculado ID: {instance.cad_plan_id or 'N/A'}",
            f"Tamaño paquete (.planproj): {len(content_bytes):,} bytes",
        ],
    )


def _sync_marker_snapshot(pdf_id, manifest):
    """Replace only this project's saved buttons; local numeric IDs are not global."""
    payload = json.dumps(manifest.get('markers', [])).replace("'", "''")
    pdf_id = pdf_id.replace("'", "''")
    sql = f"""
    BEGIN;
    LOCK TABLE public.pdf_markers IN SHARE ROW EXCLUSIVE MODE;
    SELECT setval(pg_get_serial_sequence('public.pdf_markers', 'id'),
        GREATEST(COALESCE((SELECT max(id) FROM public.pdf_markers), 0) + 1, 1), false);
    INSERT INTO public.button_categories(id, name)
        SELECT DISTINCT (item->>'category_id')::uuid, item->>'category_name'
        FROM jsonb_array_elements('{payload}'::jsonb) item
        WHERE item->>'category_id' IS NOT NULL AND item->>'category_name' IS NOT NULL
        ON CONFLICT DO NOTHING;
    DELETE FROM public.pdf_markers WHERE plan_id IN
        (SELECT id FROM public.pdf_plans WHERE drive_file_id = '{pdf_id}');
    INSERT INTO public.pdf_markers(plan_id, page, x, y, label, sheet_name, workbook_id,
        category_id, has_area, area_radius_x, area_radius_y, area_color)
    SELECT p.id, (item->>'page')::int, (item->>'x')::float, (item->>'y')::float,
        COALESCE(item->>'label', ''), COALESCE(item->>'sheet', ''),
        CASE WHEN COALESCE(item->>'sheet', '') <> '' THEN p.workbook_id END,
        (SELECT c.id FROM public.button_categories c
         WHERE c.id::text = item->>'category_id' OR lower(c.name) = lower(item->>'category_name') LIMIT 1),
        COALESCE((item->>'has_area')::boolean, false), (item->>'area_radius_x')::float,
        (item->>'area_radius_y')::float, COALESCE(item->>'area_color', 'rgba(59, 130, 246, 0.25)')
    FROM public.pdf_plans p, jsonb_array_elements('{payload}'::jsonb) item
    WHERE p.drive_file_id = '{pdf_id}';
    COMMIT;
    """
    return run_supabase_sql_sync('Botones del proyecto guardado sincronizados', sql)


def queue_saved_project_sync(kind, plan, content, saved_at):
    """Publish one immutable save, then record its actual remote outcome."""
    if not is_monitor_enabled():
        return
    from .cloud_projects import CloudUnavailable, manifest_metadata, shared_publish_target, snapshot_identity, workbook_version

    plan_pk, plan_name = plan.pk, plan.name
    document = plan.workbook
    assoc_key = plan.cloud_key
    shared = bool(getattr(plan, 'cloud_origin', ''))
    pdf_id = getattr(plan, 'cloud_origin', '')
    timestamp = timezone.localtime(saved_at).strftime('%Y-%m-%d_%H-%M-%S')
    title = re.sub(r'\.(pdf|dxf|xlsx|csv)$', '', plan_name, flags=re.IGNORECASE).strip() or 'Plano'
    basename = f'{title[:255 - len(timestamp) - 8].rstrip()} - {timestamp}'
    workbook_args = document.pk, document.revision, document.plan_mode, document.is_motor
    pages = list(plan.pages) if kind == 'pdf' else []
    target = {'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': plan_pk, 'saved_at': saved_at}
    SavedProject.objects.filter(**target).update(cloud_status='pending', cloud_error='')

    def publish():
        state, error = 'error', 'No se pudo publicar el proyecto completo en Drive. Vuelva a pulsar Guardar plano.'
        try:
            with ZipFile(BytesIO(content)) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                source = archive.read(f'plan/source.{kind}')
                key = assoc_key
                pdf_options, workbook_options = {}, {}
                if shared:
                    if not pdf_id:
                        raise CloudUnavailable('Vuelve a abrir la versión de la nube para vincular el Excel original de Drive.')
                    remote = shared_publish_target(plan_pk, pdf_id, source)
                    key = remote['projectKey']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', key):
                        raise CloudUnavailable('El proyecto original tiene una asociación no válida en Drive.')
                    pdf_options['drive_target'] = {'id': pdf_id, 'modifiedTime': remote['pdfVersion']}
                    workbook_options['drive_target'] = {'id': remote['excelId'], 'modifiedTime': remote['date']}
                pdf = _sync_pdf_plan_worker(plan_pk, plan_name, source, pages, f'{basename}.pdf', key, **pdf_options) if kind == 'pdf' else None
                if kind == 'pdf' and not pdf:
                    raise RuntimeError('No se pudo subir el PDF.')
                book = _sync_workbook_worker(*workbook_args, archive.read('workbooks/w1.xlsx'), key,
                    f'{basename}.xlsx', manifest, source, **workbook_options)
                if not book:
                    raise RuntimeError('No se pudo subir el Excel.')
                if kind == 'pdf':
                    if not shared:
                        remote = {'id': pdf['drive_file_id'], 'excelId': book['drive_file_id'], 'projectKey': key}
                    remote.update(name=f'{basename}.pdf', pdfVersion=pdf['modified_time'],
                                  excelName=f'{basename}.xlsx', date=book['modified_time'])
                    identity = snapshot_identity(remote, manifest_metadata(manifest), manifest)
                    local = PdfPlan.objects.filter(pk=plan_pk)
                    local.update(cloud_origin=pdf['drive_file_id'], cloud_workbook_version=workbook_version(remote),
                                 owner_username=manifest.get('owner_username', ''))
                    if plan.cloud_snapshot and not PdfPlan.objects.exclude(pk=plan_pk).filter(cloud_snapshot=identity).exists():
                        local.update(cloud_snapshot=identity)
                database_ok = book.get('supabase_ok') and (kind != 'pdf' or pdf.get('supabase_ok'))
                if database_ok and kind == 'pdf':
                    database_ok = _sync_marker_snapshot(pdf['drive_file_id'], manifest)
                state = 'synced' if database_ok else 'partial'
                error = '' if database_ok else 'Disponible en Drive. Supabase no confirmó el registro; vuelva a guardar para reintentar.'
        except CloudUnavailable as exc:
            error = str(exc)
        except Exception as exc:
            _log_line(f'  ↳ [SYNC ERROR] No se completó la publicación de {plan_name}: {exc}')
        finally:
            SavedProject.objects.filter(**target).update(cloud_status=state, cloud_error=error)

    transaction.on_commit(lambda: enqueue_sync_task(publish))


@receiver(post_save, sender=Plan)
def on_cad_plan_saved(sender, instance, created, **kwargs):
    action = 'NUEVO PLANO DXF IMPORTADO' if created else 'PLANO DXF ACTUALIZADO'
    print_live_event(
        action,
        f"Plano AutoCAD DXF: '{instance.name}' (ID: {instance.pk})",
        [f"Tamaño: {instance.size:,} bytes"],
    )


@receiver(post_save, sender=Motor)
def on_cad_motor_saved(sender, instance, created, **kwargs):
    if not created:
        print_live_event(
            'MOTOR DXF ACTUALIZADO',
            f"Motor '{instance.name}' (ID: {instance.pk})",
            [
                f"Posición (X, Y): ({instance.x:.2f}, {instance.y:.2f})",
                f"Hoja asignada  : '{instance.sheet_name or 'Sin hoja'}'",
                f"Área activa    : {'Sí' if instance.has_area else 'No'}",
            ],
        )


class TerminalDebugMiddleware:
    """Middleware que informa en la terminal cada cambio o acción interactiva en la app."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_monitor_enabled() and request.path.startswith('/api/'):
            if request.method == 'POST':
                now = datetime.now().strftime('%H:%M:%S')
                user_str = request.user.username if getattr(request, 'user', None) and request.user.is_authenticated else 'anónimo'
                _log_line(f"\n⚡ [{now}] [ACCIÓN EN APP] Usuario '{user_str}' -> {request.method} {request.path}")
            elif re.match(r'^/api/(pdf/plans|plans|workbooks)/\d+/$', request.path):
                now = datetime.now().strftime('%H:%M:%S')
                _log_line(f"👁️  [{now}] [CARGANDO EN VISOR] {request.path} (En memoria - Sin descargar al equipo)")

        response = self.get_response(request)
        return response
