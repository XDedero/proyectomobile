"""Track the last shared state and merge saves that only add new button sheets."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .models import PdfMarker, PdfPlan, PlanWorkbookSheet, WorkbookDocument
from .project_service import export_project, open_plan_source
from .xlsx_package import S, append_sheet, arrange_sheets, package, relpath, resolve, sheet_names, write_sheet_origins


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _sheet_hashes(content, rows):
    """Ignore automatic tab renames, ZIP timestamps and unused appended styles."""
    from .workbook_service import _sheet_paths

    aliases = {row.name: f'Sheet_{row.pk}' for row in rows}
    normalized = arrange_sheets(content, aliases, sheet_names(content))
    parts = package(normalized)
    with ZipFile(BytesIO(normalized)) as archive:
        paths = _sheet_paths(archive)
    styles = ET.fromstring(parts['xl/styles.xml'])
    format_nodes = styles.find('{' + S + '}numFmts')
    formats = {item.get('numFmtId'): item for item in (format_nodes if format_nodes is not None else [])}

    def serialized(node):
        return ET.canonicalize(ET.tostring(node, encoding='unicode')).encode()

    def graph(part, seen):
        if part in seen:
            return b''
        seen.add(part)
        data = parts[part]
        value = serialized(ET.fromstring(data)) if part.endswith('.xml') else data
        relationships = relpath(part)
        if relationships in parts:
            root = ET.fromstring(parts[relationships])
            value += serialized(root)
            for relation in root:
                if relation.get('TargetMode') != 'External':
                    target = resolve(part, relation.get('Target'))
                    if target in parts:
                        value += graph(target, seen)
        return value

    result = {}
    for row in rows:
        path = paths[aliases[row.name]]
        root = ET.fromstring(parts[path])
        value = graph(path, set()) + parts.get('xl/theme/theme1.xml', b'')
        indices = {int(node.get('s', node.get('style', '0'))) for node in root.iter() if node.tag.split('}')[-1] in ('c', 'row', 'col')}
        for index in sorted(indices):
            xf = styles.find('{' + S + '}cellXfs')[index]
            value += serialized(xf)
            for attr, group in [('fontId', 'fonts'), ('fillId', 'fills'), ('borderId', 'borders'), ('xfId', 'cellStyleXfs')]:
                if xf.get(attr) is not None:
                    value += serialized(styles.find('{' + S + '}' + group)[int(xf.get(attr))])
            if xf.get('numFmtId') in formats:
                value += serialized(formats[xf.get('numFmtId')])
        for node in root.iter():
            if node.get('dxfId') is not None:
                value += serialized(styles.find('{' + S + '}dxfs')[int(node.get('dxfId'))])
        if 'xl/sharedStrings.xml' in parts:
            strings = ET.fromstring(parts['xl/sharedStrings.xml'])
            for cell in root.findall('.//{' + S + '}c[@t="s"]'):
                value += serialized(strings[int(cell.find('{' + S + '}v').text)])
        result[str(row.pk)] = _digest(value)
    return result


def capture_base_state(plan, content=None):
    if content is None:
        content = export_project('pdf', plan)
    with ZipFile(BytesIO(content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        workbook = archive.read('workbooks/w1.xlsx')
        source_hash = _digest(archive.read('plan/source.pdf'))
    rows = list(plan.workbook.plan_sheets.all())
    by_name = {row.name: str(row.pk) for row in rows}
    hashes = _sheet_hashes(workbook, rows)
    buttons = {}
    for button, item in zip(plan.markers.order_by('pk'), manifest['markers']):
        item = dict(item)
        item.pop('workbook', None)
        item['sheet'] = by_name.get(item['sheet'])
        buttons[str(button.pk)] = item
    return {'name': manifest['name'], 'mode': manifest['workbook_mode'], 'source': source_hash, 'display': manifest.get('display_settings', {}),
            'buttons': buttons, 'sheets': {str(row.pk): {
                'hash': hashes[str(row.pk)], 'listing': row.is_listing, 'primary': row.is_primary,
                'protected': row.is_protected, 'order': row.listing_order, 'origin': str(row.origin_uuid or ''),
            } for row in rows if row.name != 'Sin fichas' or row.is_listing or row.name in {item['sheet'] for item in manifest['markers']}}}


def added_buttons(base, current):
    if not base or any(base.get(key) != current.get(key) for key in ('name', 'mode', 'source', 'display')):
        return []
    for group in ('buttons', 'sheets'):
        if any(current[group].get(key) != value for key, value in base[group].items()):
            return []
    additions = [key for key in current['buttons'] if key not in base['buttons']]
    extra_sheets = set(current['sheets']) - set(base['sheets'])
    if (not additions or not extra_sheets
            or any(current['buttons'][key]['sheet'] not in extra_sheets for key in additions)
            or extra_sheets != {current['buttons'][key]['sheet'] for key in additions}):
        return []
    return additions


def merge_only_additions(plan, user):
    from .cloud_projects import open_cloud_project, shared_publish_target, workbook_version
    from .plan_workbook import EMPTY_SHEET, organize_project_sheets, store
    from .project_views import clear_plan_files

    if not plan.cloud_origin or not plan.cloud_base_state:
        return False
    additions = added_buttons(plan.cloud_base_state, capture_base_state(plan))
    if not additions:
        return False
    with open_plan_source('pdf', plan) as stream:
        source = stream.read()
    target = shared_publish_target(plan.pk, plan.cloud_origin, source)
    if not target.get('excelId') or workbook_version(target) == plan.cloud_workbook_version:
        return False
    existing = set(PdfPlan.objects.values_list('pk', flat=True))
    remote, _ = open_cloud_project(plan.cloud_origin, user)
    if remote.pk == plan.pk:
        return False
    try:
        with open_plan_source('pdf', remote) as stream:
            if stream.read() != source:
                return False
        document = WorkbookDocument.objects.get(pk=plan.workbook_id)
        content = bytes(remote.workbook.content)
        specs = list(remote.workbook.plan_sheets.values('name', 'is_listing', 'is_primary', 'is_protected', 'listing_order', 'origin_uuid'))
        local_rows = {row.name: row for row in document.plan_sheets.all()}
        new_buttons = list(plan.markers.filter(pk__in=additions).order_by('pk'))
        names = {}
        for button in new_buttons:
            name = button.sheet_name
            if name not in names:
                content, title = append_sheet(content, document.content, name, name)
                names[name] = title
                row = local_rows[name]
                specs.append({'name': title, 'is_listing': False, 'is_primary': False, 'is_protected': False,
                              'listing_order': 0, 'origin_uuid': row.origin_uuid})
        if sheet_names(remote.workbook.content) == [EMPTY_SHEET] and not remote.markers.exists() and not specs[0]['is_listing']:
            from .xlsx_package import remove_sheet
            content = remove_sheet(content, EMPTY_SHEET)
            specs = specs[1:]
        fields = [field.attname for field in PdfMarker._meta.fields if field.name not in ('id', 'plan', 'workbook')]
        restored = [{field: deepcopy(getattr(button, field)) for field in fields} for button in remote.markers.order_by('pk')]
        for button in new_buttons:
            item = {field: deepcopy(getattr(button, field)) for field in fields}
            item['sheet_name'] = names[button.sheet_name]
            if item['source_output'] == button.sheet_name:
                item['source_output'] = item['sheet_name']
            restored.append(item)
        # Keep imported source files alive while recreating their marker rows.
        plan.markers.filter(pk__in=additions).update(source_workbook=None)
        plan.markers.all().delete()
        content = write_sheet_origins(content, {row['name']: row['origin_uuid'] for row in specs if row['origin_uuid']})
        document.plan_mode = remote.workbook.plan_mode
        document.save(update_fields=['plan_mode'])
        store(document, content, user)
        document.plan_sheets.all().delete()
        PlanWorkbookSheet.objects.bulk_create([PlanWorkbookSheet(workbook=document, created_by=user, **spec) for spec in specs])
        PdfMarker.objects.bulk_create([PdfMarker(plan=plan, workbook=document, **item) for item in restored])
        plan.name, plan.owner_username = remote.name, remote.owner_username
        plan.display_settings = remote.display_settings
        plan.cloud_workbook_version = remote.cloud_workbook_version
        plan.workbook = document
        plan.save(update_fields=['name', 'owner_username', 'cloud_workbook_version', 'display_settings'])
        organize_project_sheets(plan, user)
        return True
    finally:
        if remote.pk not in existing:
            clear_plan_files('pdf', remote)
