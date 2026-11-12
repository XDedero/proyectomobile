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
import time
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


def run_supabase_sql_sync(description, sql_statement, attempts=3):
    from .cloud_projects import CloudUnavailable, supabase_query

    now = datetime.now().strftime('%H:%M:%S')
    for attempt in range(attempts):
        try:
            supabase_query(sql_statement, read_only=False)
        except CloudUnavailable as exc:
            _log_line(f"  ↳ [{now}] [SUPABASE-CLOUD RETRY {attempt + 1}/3] {description}: {exc}")
            if attempt < attempts - 1:
                time.sleep(attempt + 1)
        else:
            _log_line(f"  ↳ [{now}] [SUPABASE-CLOUD OK] {description}")
            return True
    return False


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
            verb = {'updated': 'Actualizado en', 'reused': 'Reutilizado en'}.get(rec.get('action'), 'Subido a')
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
    if not kwargs.get('created') and SavedMotor.objects.filter(category=instance).exists():
        SavedMotor.objects.filter(category=instance).update(cloud_status='local')
        from .piece_service import queue_catalog_sync
        queue_catalog_sync()


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
            if drive_target.get('force'):
                extra += ['--force-replace']
            if drive_target.get('missing'):
                extra += ['--create-only']
            else:
                extra += ['--drive-id', drive_target['id'], '--modified-time', drive_target['modifiedTime']]
        return run_gdrive_upload_sync(str(path / 'content'), file_name, assoc_key, local_key, extra)


def _sync_pdf_plan_worker(plan_pk, plan_name, source, pages_list, file_name, assoc_key=None, drive_target=None, register=True):
    local_key = assoc_key or f"local_pdf_{plan_pk}"
    rec = _upload_drive_content(source, file_name, local_key, local_key, drive_target=drive_target)
    if not rec or not rec.get('drive_file_id'):
        return
    return _register_pdf_record(rec, plan_pk, plan_name, source, pages_list, assoc_key, drive_target) if register else rec


def _register_pdf_record(rec, plan_pk, plan_name, source, pages_list, assoc_key=None, drive_target=None, sql_only=False):
    local_key = assoc_key or f"local_pdf_{plan_pk}"
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
    if sql_only:
        return sql
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
                          manifest=None, project_source=None, drive_target=None, register=True):
    local_key = f"{assoc_key}_xlsx" if assoc_key.startswith("project_") else f"local_xlsx_{wb_pk}"
    rec = _upload_drive_content(content_bytes, drive_filename, assoc_key, local_key, manifest, project_source, drive_target)
    if not rec or not rec.get('drive_file_id'):
        return
    return _register_workbook_record(rec, wb_pk, wb_revision, wb_plan_mode, wb_is_motor, content_bytes,
                                     assoc_key, drive_filename, manifest, drive_target) if register else rec


def _register_workbook_record(rec, wb_pk, wb_revision, wb_plan_mode, wb_is_motor, content_bytes, assoc_key,
                              drive_filename, manifest=None, drive_target=None, sql_only=False):
    local_key = f"{assoc_key}_xlsx" if assoc_key.startswith("project_") else f"local_xlsx_{wb_pk}"
    drive_id = rec['drive_file_id']
    stream_url = rec.get('drive_stream_url', f"https://www.googleapis.com/drive/v3/files/{drive_id}?alt=media")

    safe_name = drive_filename.replace("'", "''")
    file_fmt = 'csv' if drive_filename.lower().endswith('.csv') else 'xlsx'
    legacy_match = '' if drive_target else f" OR drive_file_id = '{local_key}'"
    if drive_target and drive_target.get('missing'):
        previous_id = drive_target.get('previous_id', '')
        if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', previous_id):
            legacy_match = f" OR (drive_file_id = '{previous_id}' AND association_key = '{assoc_key}' AND NOT EXISTS (SELECT 1 FROM public.workbook_documents WHERE drive_file_id = '{drive_id}'))"
    sql = f"""
    UPDATE public.workbook_documents
    SET association_key = '{assoc_key}',
        name = '{safe_name}',
        plan_mode = '{wb_plan_mode or 'pdf'}',
        is_motor = {'TRUE' if wb_is_motor else 'FALSE'},
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
    if sql_only:
        return sql
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


def _sync_marker_snapshot(pdf_id, manifest, sql_only=False):
    """Replace only this project's saved buttons; local numeric IDs are not global."""
    payload = json.dumps(manifest.get('markers', [])).replace("'", "''")
    pdf_id = pdf_id.replace("'", "''")
    sql = f"""
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
        category_id, has_area, area_radius_x, area_radius_y, area_color, area_shape, button_color, show_description, piece_source, button_scale)
    SELECT p.id, (item->>'page')::int, (item->>'x')::float, (item->>'y')::float,
        COALESCE(item->>'label', ''), COALESCE(item->>'sheet', ''),
        CASE WHEN COALESCE(item->>'sheet', '') <> '' THEN p.workbook_id END,
        (SELECT c.id FROM public.button_categories c
         WHERE c.id::text = item->>'category_id' OR lower(c.name) = lower(item->>'category_name') LIMIT 1),
        COALESCE((item->>'has_area')::boolean, false), (item->>'area_radius_x')::float,
        (item->>'area_radius_y')::float, COALESCE(item->>'area_color', 'rgba(59, 130, 246, 0.25)'),
        COALESCE(item->>'area_shape', 'ellipse'), COALESCE(item->>'button_color', ''), COALESCE((item->>'show_description')::boolean, false), COALESCE((item->>'piece_source')::boolean,false), COALESCE((item->>'button_scale')::float,1)
    FROM public.pdf_plans p, jsonb_array_elements('{payload}'::jsonb) item
    WHERE p.drive_file_id = '{pdf_id}';
    """
    if sql_only:
        return sql
    return run_supabase_sql_sync('Botones del proyecto guardado sincronizados', f'BEGIN; {sql} COMMIT;')


def _verify_saved_registration(kind, manifest, source, content, pdf_id, excel_id, key, revision, filename):
    """Confirm the exact saved data when a write response was lost or timed out."""
    from .cloud_projects import CloudUnavailable, supabase_query

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    def payload(value):
        return literal(json.dumps(value)) + '::jsonb'

    sheets = [{name: row.get(name) for name in ('name', 'is_listing', 'is_primary', 'is_protected', 'listing_order', 'origin_uuid')}
              for row in manifest['sheets']]
    references = [{'button_index': index, 'description_ref': f'drive:{excel_id}#markers/{index}/descriptions'}
                  for index, button in enumerate(manifest['markers']) if button.get('descriptions')]
    expected_markers = [{
        'page': button['page'], 'x': button['x'], 'y': button['y'], 'label': button.get('label', ''),
        'sheet': button.get('sheet', ''), 'category_name': button.get('category_name'),
        'has_area': button.get('has_area', False), 'area_radius_x': button.get('area_radius_x'),
        'area_radius_y': button.get('area_radius_y'), 'area_color': button.get('area_color') or 'rgba(59, 130, 246, 0.25)',
        'area_shape':button.get('area_shape', 'ellipse'), 'button_color':button.get('button_color', ''), 'show_description':button.get('show_description', False),
        'piece_source':button.get('piece_source',False), 'button_scale':button.get('button_scale',1),
        'workbook_drive_id': excel_id if button.get('sheet') else None,
    } for button in manifest['markers']] if kind == 'pdf' else []
    pdf_ok, markers_ok = 'TRUE', 'TRUE'
    if kind == 'pdf':
        pdf_ok = f"EXISTS(SELECT 1 FROM public.pdf_plans WHERE drive_file_id={literal(pdf_id)} AND name={literal(manifest['name'])} AND size_bytes={len(source)})"
        actual_markers = f"""COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'page', m.page, 'x', m.x, 'y', m.y, 'label', m.label, 'sheet', m.sheet_name,
            'category_name', c.name, 'has_area', m.has_area, 'area_radius_x', m.area_radius_x,
            'area_radius_y', m.area_radius_y, 'area_color', m.area_color, 'workbook_drive_id', w.drive_file_id
            , 'area_shape', m.area_shape, 'button_color', m.button_color, 'show_description', m.show_description
            , 'piece_source', m.piece_source, 'button_scale', m.button_scale
        ) ORDER BY m.id) FROM public.pdf_markers m
            JOIN public.pdf_plans p ON p.id=m.plan_id
            LEFT JOIN public.button_categories c ON c.id=m.category_id
            LEFT JOIN public.workbook_documents w ON w.id=m.workbook_id
            WHERE p.drive_file_id={literal(pdf_id)}), '[]'::jsonb)"""
        # Supabase numeric columns can round coordinates to fewer decimals.
        # Compare every other field exactly and positions within 1e-12.
        markers_ok = f"""NOT EXISTS(SELECT 1 FROM
            jsonb_array_elements({actual_markers}) WITH ORDINALITY AS a(item, position)
            FULL JOIN jsonb_array_elements({payload(expected_markers)}) WITH ORDINALITY AS e(item, position) USING(position)
            WHERE a.item IS NULL OR e.item IS NULL
            OR (a.item - ARRAY['x','y','area_radius_x','area_radius_y']) <> (e.item - ARRAY['x','y','area_radius_x','area_radius_y'])
            OR EXISTS(SELECT 1 FROM unnest(ARRAY['x','y','area_radius_x','area_radius_y']) AS coordinates(field)
                WHERE ((a.item->>field) IS NULL) IS DISTINCT FROM ((e.item->>field) IS NULL)
                OR abs((a.item->>field)::double precision - (e.item->>field)::double precision) > 1e-12))"""
    sql = f"""SELECT {pdf_ok} AS pdf_ok, {markers_ok} AS markers_ok,
        EXISTS(SELECT 1 FROM public.workbook_documents WHERE drive_file_id={literal(excel_id)}
            AND association_key={literal(key)} AND revision={int(revision)}
            AND size_bytes={len(content)} AND name={literal(filename)})
        AND COALESCE((SELECT jsonb_agg(jsonb_build_object('name', s.name, 'is_listing', s.is_listing,
            'is_primary', s.is_primary, 'is_protected', s.is_protected, 'listing_order', s.listing_order,
            'origin_uuid', s.origin_uuid) ORDER BY s.id)
            FROM public.plan_workbook_sheets s JOIN public.workbook_documents w ON w.id=s.workbook_id
            WHERE w.drive_file_id={literal(excel_id)}), '[]'::jsonb) = {payload(sheets)}
        AND COALESCE((SELECT jsonb_agg(jsonb_build_object('button_index', r.button_index,
            'description_ref', r.description_ref) ORDER BY r.button_index)
            FROM public.workbook_button_description_refs r JOIN public.workbook_documents w ON w.id=r.workbook_id
            WHERE w.drive_file_id={literal(excel_id)}), '[]'::jsonb) = {payload(references)} AS workbook_ok;"""
    try:
        rows = supabase_query(sql, read_only=True).get('rows', [])
        return bool(rows and all(rows[0].get(name) is True for name in ('pdf_ok', 'workbook_ok', 'markers_ok')))
    except CloudUnavailable:
        return False


def queue_saved_project_sync(kind, plan, content, saved_at):
    """Publish one immutable save, then record its actual remote outcome."""
    if not is_monitor_enabled():
        return
    from .cloud_projects import (CloudUnavailable, drive_pair_version, manifest_metadata,
                                 shared_publish_target, snapshot_identity, workbook_version)
    from .cloud_save import capture_base_state

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
    base_state = capture_base_state(plan, content) if kind == 'pdf' else None
    target = {'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': plan_pk, 'saved_at': saved_at}
    SavedProject.objects.filter(**target).update(cloud_status='pending', cloud_error='', cloud_stage='queued', cloud_progress=5)
    queued_at = time.perf_counter()

    def publish():
        state, error = 'error', 'No se pudo almacenar el proyecto completo en la nube. Vuelva a pulsar Guardar plano.'
        completed = 5
        timings = []
        stage, started = 'queued', queued_at
        pdf_reused = False

        def next_stage(name):
            nonlocal stage, started
            now = time.perf_counter()
            timings.append(f'{stage}={(now - started) * 1000:.0f}ms')
            stage, started = name, now

        def progress(name, value):
            nonlocal completed
            next_stage(name)
            completed = value
            SavedProject.objects.filter(**target).update(cloud_stage=name, cloud_progress=value)
        try:
            with ZipFile(BytesIO(content)) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                source = archive.read(f'plan/source.{kind}')
                key = assoc_key
                workbook_content = archive.read('workbooks/w1.xlsx')
                pdf_options, workbook_options = {}, {}
                progress('preparing', 10)
                if shared:
                    if not pdf_id:
                        raise CloudUnavailable('Vuelve a abrir la versión de la nube para vincular el Excel original.')
                    remote = shared_publish_target(plan_pk, pdf_id, source)
                    key = remote['projectKey']
                    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', key):
                        raise CloudUnavailable('El proyecto original tiene una asociación no válida en la nube.')
                    pdf_options['drive_target'] = {'id': pdf_id, 'modifiedTime': remote['pdfVersion'], 'force': True}
                    if remote.get('excelId'):
                        workbook_options['drive_target'] = {'id': remote['excelId'], 'modifiedTime': remote['date'], 'force': True}
                    else:
                        workbook_options['drive_target'] = {
                            'missing': True, 'previous_id': plan.cloud_workbook_version.partition(':')[0], 'force': True,
                        }
                progress('pdf', 20)
                pdf = _sync_pdf_plan_worker(plan_pk, plan_name, source, pages, f'{basename}.pdf', key, register=False, **pdf_options) if kind == 'pdf' else None
                if kind == 'pdf' and not pdf:
                    raise RuntimeError('No se pudo subir el PDF.')
                pdf_reused = bool(pdf and pdf.get('action') == 'reused')
                progress('excel', 50)
                book = _sync_workbook_worker(*workbook_args, workbook_content, key,
                    f'{basename}.xlsx', manifest, source, register=False, **workbook_options)
                if not book:
                    raise RuntimeError('No se pudo subir el Excel.')
                if kind == 'pdf':
                    if not shared:
                        remote = {'id': pdf['drive_file_id'], 'excelId': book['drive_file_id'], 'projectKey': key}
                    remote.update(excelId=book['drive_file_id'], name=f'{basename}.pdf', pdfVersion=pdf['modified_time'],
                                  excelName=f'{basename}.xlsx', date=book['modified_time'])
                    remote.pop('pdfHash', None)
                    for field, record in [('pdfRevision', pdf), ('excelRevision', book)]:
                        remote.pop(field, None)
                        if record.get('drive_version'):
                            remote[field] = record['drive_version']
                    identity = snapshot_identity(remote, manifest_metadata(manifest), manifest)
                    local = PdfPlan.objects.filter(pk=plan_pk)
                    local.update(cloud_origin=pdf['drive_file_id'], cloud_workbook_version=workbook_version(remote),
                                 owner_username=manifest.get('owner_username', ''), cloud_base_state=base_state)
                    if plan.cloud_snapshot and not PdfPlan.objects.exclude(pk=plan_pk).filter(cloud_snapshot=identity).exists():
                        local.update(cloud_snapshot=identity)
                sql = _register_pdf_record(pdf, plan_pk, plan_name, source, pages, key, pdf_options.get('drive_target'), sql_only=True) if kind == 'pdf' else ''
                sql += _register_workbook_record(book, *workbook_args, workbook_content, key,
                    f'{basename}.xlsx', manifest, workbook_options.get('drive_target'), sql_only=True)
                if kind == 'pdf':
                    sql += _sync_marker_snapshot(pdf['drive_file_id'], manifest, sql_only=True)
                    display = json.dumps(manifest.get('display_settings', {})).replace("'", "''")
                    sql += f"UPDATE public.pdf_plans SET display_settings='{display}'::jsonb WHERE drive_file_id='{pdf['drive_file_id']}';"
                sql = f'BEGIN; {sql} COMMIT; SELECT true AS saved;'
                # Receipt survives restarts: retry database registration without uploading files again.
                SavedProject.objects.filter(**target).update(cloud_receipt={'sql': sql}, cloud_status='uploaded', cloud_error='', cloud_stage='database', cloud_progress=100)
                next_stage('database')
                completed = 100
                database_ok = run_supabase_sql_sync('Proyecto completo registrado en una transacción', sql, attempts=1)
                if not database_ok:
                    database_ok = _verify_saved_registration(kind, manifest, source, workbook_content,
                        pdf['drive_file_id'] if pdf else '', book['drive_file_id'], key, document.revision, f'{basename}.xlsx')
                state = 'synced' if database_ok else 'uploaded'
                error = ''
                if database_ok and kind == 'pdf' and (pair := drive_pair_version(remote)):
                    local.filter(cloud_workbook_version=workbook_version(remote), saved_project__saved_at=saved_at).update(
                        cloud_base_state={**base_state, 'drive_pair': pair})
        except CloudUnavailable as exc:
            error = str(exc)
        except Exception as exc:
            _log_line(f'  ↳ [SYNC ERROR] No se completó la publicación de {plan_name}: {exc}')
        finally:
            next_stage('finished')
            _log_line(f"  [PROJECT PERF] kind={kind} state={state} pdf_reused={pdf_reused} {' '.join(timings)}")
            SavedProject.objects.filter(**target).update(cloud_status=state, cloud_error=error,
                cloud_stage='done' if state == 'synced' else state, cloud_progress=100 if state == 'synced' else completed)

    transaction.on_commit(lambda: enqueue_sync_task(publish))


_REGISTRATION_RETRIES = {}
_REGISTRATION_LOCK = threading.Lock()


def retry_cloud_registrations(project_ids=None):
    """Resume acknowledged file uploads after a lost DB reply or an app restart."""
    if not is_monitor_enabled():
        return
    candidates = SavedProject.objects.filter(cloud_status='uploaded')
    if project_ids is not None:
        candidates = candidates.filter(pk__in=project_ids)
    for saved in candidates.only('id', 'saved_at', 'cloud_receipt'):
        sql = saved.cloud_receipt.get('sql')
        if not sql:
            continue
        with _REGISTRATION_LOCK:
            key = (saved.pk, saved.saved_at.isoformat())
            if time.monotonic() - _REGISTRATION_RETRIES.get(key, -1000) < 60:
                continue
            _REGISTRATION_RETRIES[key] = time.monotonic()
        def resume(pk=saved.pk, stamp=saved.saved_at, sql=sql):
            query = SavedProject.objects.filter(pk=pk, saved_at=stamp, cloud_status='uploaded')
            if query.exists() and run_supabase_sql_sync('Confirmación automática del guardado', sql, attempts=1):
                query.update(cloud_status='synced', cloud_stage='done', cloud_progress=100, cloud_error='')
        enqueue_sync_task(resume)


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
