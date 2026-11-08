"""Shared piece catalog: immutable local sheets and one workbook at Drive root."""
from io import BytesIO
import json
import hashlib
from pathlib import Path
import threading
import time
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4
from zipfile import ZipFile

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ButtonCategory, SavedPiece, PieceDeletion, WorkbookDocument, Motor, PdfMarker
from .motor_service import single_sheet, stamp_origin, inspect_motor
from .xlsx_package import append_sheet, rename_sheet, remove_sheet, sheet_names, unique_name

_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_QUEUED = False
_RETRY_AFTER = 0
_READ_AT = 0
_READ_LOCK = threading.Lock()


def piece_category(value):
    try:
        return ButtonCategory.objects.get(pk=UUID(str(value)))
    except (ButtonCategory.DoesNotExist, ValueError, TypeError, AttributeError) as exc:
        raise ValidationError('Seleccione la categoría de la pieza.') from exc


def read_catalog():
    from .cloud_projects import drive_command
    with TemporaryDirectory(prefix='pieces-read-') as directory:
        result = drive_command('--read-pieces', '--output', directory)
        content = (Path(directory)/'pieces.xlsx').read_bytes() if result['library'] else None
    return result['library'], result['pieces'], content


def discard_piece(record):
    document_id = record.workbook_id
    record.delete()
    if Motor.objects.filter(source_workbook_id=document_id).exists() or PdfMarker.objects.filter(source_workbook_id=document_id).exists():
        return
    WorkbookDocument.objects.filter(pk=document_id, saved_motor__isnull=True, markers__isnull=True, motors__isnull=True,
        pdf_workspaces__isnull=True, pdf_listings__isnull=True, cad_workspaces__isnull=True, cad_listings__isnull=True).delete()


def remote_deletions():
    from .cloud_projects import supabase_query
    return {str(UUID(row['id'])) for row in supabase_query('SELECT id::text FROM public.piece_deletions;', read_only=True).get('rows', []) if row.get('id')}


def apply_deletions(identifiers):
    for identifier in identifiers:
        PieceDeletion.objects.get_or_create(pk=identifier, defaults={'synced':True})
        record = SavedPiece.objects.filter(uuid=identifier).first()
        if record:
            discard_piece(record)


def import_catalog(index, content, deleted=()):
    apply_deletions(deleted)
    deleted = set(deleted) | {str(value) for value in PieceDeletion.objects.values_list('pk', flat=True)}
    if content is None:
        return
    names = sheet_names(content)
    with transaction.atomic():
        for item in index:
            identifier = UUID(item['id'])
            if str(identifier) in deleted:
                continue
            if (item.get('sheet') not in names or not isinstance(item.get('name'), str) or not item['name'].strip() or len(item['name']) > 120
                    or not isinstance(item.get('category_name'), str) or not item['category_name'].strip() or len(item['category_name']) > 80):
                raise ValidationError('El catálogo contiene una pieza no válida.')
            category = ButtonCategory.objects.filter(pk=UUID(item['category_id'])).first() or ButtonCategory.objects.filter(name__iexact=item['category_name']).first()
            if category is None:
                category = ButtonCategory.objects.create(pk=UUID(item['category_id']), name=item['category_name'])
            existing = SavedPiece.objects.select_related('workbook').filter(uuid=identifier).first()
            version = item.get('version', '')
            if existing and (existing.cloud_status != 'synced' or existing.cloud_version == version):
                continue
            selected = stamp_origin(single_sheet(content, item['sheet']), identifier)
            fingerprint, _ = inspect_motor(selected)
            if existing:
                WorkbookDocument.objects.filter(pk=existing.workbook_id).update(content=selected, revision=existing.workbook.revision+1,
                    name=item['name']+'.xlsx', updated_at=timezone.now())
                SavedPiece.objects.filter(pk=existing.pk).update(name=item['name'], name_key=f'{category.pk}:{item["name"].casefold()}',
                    category=category, cloud_sheet=item['sheet'], cloud_version=version, data_fingerprint='',
                    fingerprint=hashlib.sha256((str(identifier)+fingerprint).encode()).hexdigest())
                continue
            document = WorkbookDocument.objects.create(name=item['name']+'.xlsx', content=selected, is_motor=True, origin_uuid=identifier)
            from .plan_workbook import sheet_identity
            # Older records may already exist with this exact data/name.
            if SavedPiece.objects.filter(name_key=f'{category.pk}:{item["name"].casefold()}').exists():
                document.delete(); continue
            SavedPiece.objects.create(uuid=identifier, name=item['name'], name_key=f'{category.pk}:{item["name"].casefold()}',
                fingerprint=hashlib.sha256((str(identifier)+fingerprint).encode()).hexdigest(), data_fingerprint='', workbook=document, category=category,
                cloud_sheet=item['sheet'], cloud_status='synced', cloud_version=version)


def refresh_catalog():
    global _READ_AT
    with _READ_LOCK:
        if time.monotonic() - _READ_AT < 15:
            return
        _, index, content = read_catalog()
        import_catalog(index, content, remote_deletions())
        _READ_AT = time.monotonic()


def sync_catalog():
    """A database lease serializes catalog merges across computers, including first creation."""
    from .cloud_projects import CloudUnavailable, drive_command, supabase_query
    if not _LOCK.acquire(blocking=False):
        return
    lease = str(uuid4())
    acquired = False
    try:
        rows = supabase_query(f"""INSERT INTO public.piece_library_registry(singleton) VALUES(true) ON CONFLICT DO NOTHING;
            UPDATE public.piece_library_registry SET lease='{lease}', lease_until=now()+interval '5 minutes'
            WHERE singleton AND (lease_until IS NULL OR lease_until < now()) RETURNING singleton;""", read_only=False).get('rows', [])
        acquired = bool(rows)
        if not acquired:
            raise CloudUnavailable('Otra instalación está actualizando piezas.xlsx. Reintente en unos segundos.')
        library, index, content = read_catalog()
        deleted = remote_deletions() | {str(value) for value in PieceDeletion.objects.values_list('pk', flat=True)}
        apply_deletions(deleted)
        if deleted:
            ids = ','.join("'"+str(UUID(value))+"'" for value in deleted)
            # Record deletion intent first, so an offline cache cannot resurrect a piece.
            supabase_query(f"BEGIN; INSERT INTO public.piece_deletions(id) SELECT id::uuid FROM unnest(ARRAY[{ids}]) AS d(id) ON CONFLICT DO NOTHING; DELETE FROM public.saved_pieces WHERE id IN ({ids}); COMMIT;", read_only=False)
        for item in list(index):
            if item['id'] not in deleted:
                continue
            if len(sheet_names(content)) == 1:
                from .plan_workbook import empty_workbook
                content = rename_sheet(empty_workbook(), 'Sin fichas', 'Sin piezas')[0]
            else:
                content = remove_sheet(content, item['sheet'])
            index.remove(item)
        known = {row['id'] for row in index}
        records = list(SavedPiece.objects.select_related('category', 'workbook').order_by('created_at', 'id'))
        revisions = {str(record.uuid):record.workbook.revision for record in records}
        dirty_ids = {str(record.uuid) for record in records if record.cloud_status != 'synced'}
        for record in records:
            if str(record.uuid) in known:
                item = next(item for item in index if item['id']==str(record.uuid))
                if record.cloud_status != 'local':
                    continue
                if len(sheet_names(content)) == 1:
                    content = None
                else:
                    content = remove_sheet(content, item['sheet'])
                index.remove(item)
            category = record.category
            if category is None:
                category = ButtonCategory.objects.filter(name__iexact='Motores').first()
            if category is None:
                raise ValidationError('Asigne una categoría a las piezas existentes.')
            source = bytes(record.workbook.content)
            source_name = sheet_names(source)[0]
            requested = f'{category.name}-{record.name}'
            if content is None or (not index and sheet_names(content) == ['Sin piezas']):
                title = unique_name([], requested)
                content = rename_sheet(single_sheet(source, source_name), source_name, title)[0]
            else:
                content, title = append_sheet(content, source, source_name, requested)
            index.append({'id':str(record.uuid), 'name':record.name, 'category_id':str(category.pk), 'category_name':category.name, 'sheet':title, 'version':uuid4().hex})
            known.add(str(record.uuid))
        if content is None:
            PieceDeletion.objects.filter(pk__in=deleted).update(synced=True)
            return
        with TemporaryDirectory(prefix='pieces-write-') as directory:
            base = Path(directory)
            (base/'catalog.xlsx').write_bytes(content)
            (base/'index.json').write_text(json.dumps(index), encoding='utf-8')
            args = ['--write-pieces', '--file', str(base/'catalog.xlsx'), '--index', str(base/'index.json')]
            if library:
                args += ['--drive-id', library['id'], '--modified-time', library['modifiedTime']]
            published = drive_command(*args)['library']
        sql = ''
        for item in index:
            def literal(value):
                return "'"+str(value).replace("'", "''")+"'"
            sql += f"""INSERT INTO public.button_categories(id,name) VALUES({literal(item['category_id'])},{literal(item['category_name'])}) ON CONFLICT DO NOTHING;
                INSERT INTO public.saved_pieces(id,name,category_id,category_name,drive_file_id,sheet_name)
                VALUES({literal(item['id'])},{literal(item['name'])},(SELECT id FROM public.button_categories WHERE id={literal(item['category_id'])}::uuid OR lower(name)=lower({literal(item['category_name'])}) LIMIT 1),{literal(item['category_name'])},{literal(published['id'])},{literal(item['sheet'])})
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,category_id=excluded.category_id,category_name=excluded.category_name,
                drive_file_id=excluded.drive_file_id,sheet_name=excluded.sheet_name,updated_at=now();"""
            if item['id'] in dirty_ids:
                SavedPiece.objects.filter(uuid=item['id'], workbook__revision=revisions[item['id']]).update(cloud_sheet=item['sheet'], cloud_status='uploaded', cloud_version=item.get('version', ''))
        ids = ','.join("'"+str(UUID(item['id']))+"'" for item in index)
        sql += f"DELETE FROM public.saved_pieces WHERE drive_file_id='{published['id']}'" + (f' AND id NOT IN ({ids})' if ids else '') + ';'
        supabase_query('BEGIN; '+sql+f" UPDATE public.piece_library_registry SET drive_file_id='{published['id']}' WHERE singleton; COMMIT;", read_only=False)
        for item in index:
            SavedPiece.objects.filter(uuid=item['id'], cloud_status='uploaded', cloud_version=item.get('version', '')).update(cloud_status='synced')
        PieceDeletion.objects.filter(pk__in=deleted).update(synced=True)
        import_catalog(index, content, deleted)
    finally:
        try:
            if acquired:
                supabase_query(f"UPDATE public.piece_library_registry SET lease=NULL,lease_until=NULL WHERE lease='{lease}';", read_only=False)
        finally:
            _LOCK.release()


def queue_catalog_sync():
    from .terminal_monitor import enqueue_sync_task, is_monitor_enabled
    if not is_monitor_enabled():
        return
    def enqueue():
        global _QUEUED
        with _QUEUE_LOCK:
            if _QUEUED or time.monotonic() < _RETRY_AFTER:
                return
            _QUEUED = True
        def task():
            global _QUEUED, _RETRY_AFTER, _READ_AT
            try:
                sync_catalog()
                _READ_AT = 0
            except Exception:
                _RETRY_AFTER = time.monotonic()+45
                raise
            finally:
                with _QUEUE_LOCK:
                    _QUEUED = False
            if SavedPiece.objects.exclude(cloud_status='synced').exists() or PieceDeletion.objects.filter(synced=False).exists():
                queue_catalog_sync()
        enqueue_sync_task(task)
    transaction.on_commit(enqueue)
