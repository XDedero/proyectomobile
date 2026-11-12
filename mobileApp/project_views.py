"""A common saved-plan library, plan-level motor lists and portable projects."""

from copy import copy
import csv
from io import BytesIO, StringIO
from pathlib import Path
from time import perf_counter

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from openpyxl import load_workbook

from .access import api_permissions, capabilities, can_access_plan, can_edit_plan, can_delete_plan, is_administrator
from .models import Motor, PdfPlan, Plan, SavedProject, WorkbookDocument
from .pdf_views import error_response, json_body, pdf_data, uploaded_document, workbook_data
from .project_service import export_project, import_project, plan_model
from .workbook_service import positive_integer, formatted_value


def get_plan(kind, plan_id):
    try:
        return get_object_or_404(plan_model(kind), pk=plan_id)
    except ValidationError:
        from django.http import Http404
        raise Http404


def project_plan_data(kind, plan):
    from .display_settings import clean_display
    from .plan_workbook import workspace_data
    try:
        saved = plan.saved_project
    except SavedProject.DoesNotExist:
        saved = None
    return {
        'sectors': plan.sectors,
        'sectors_url': reverse('plan_sectors', args=[kind, plan.pk]),
        'display_settings': clean_display(plan.display_settings),
        'display_url': reverse('project_display', args=[kind, plan.pk]),
        'kind': kind, 'saved_at': saved.saved_at.isoformat() if saved else None,
        'project_version': saved.saved_at.isoformat() if saved else '',
        'cloud_status': saved.cloud_status if saved else 'local',
        'cloud_error': public_cloud_error(saved.cloud_error) if saved else '',
        'cloud_stage': saved.cloud_stage if saved else 'local',
        'cloud_progress': saved.cloud_progress if saved else 0,
        'version_url': reverse('project_version', args=[kind, plan.pk]),
        'refresh_url': reverse('refresh_project', args=[kind, plan.pk]),
        'cloud_key': plan.cloud_key,
        'cloud_origin': getattr(plan, 'cloud_origin', ''),
        'save_project_url': reverse('save_project', args=[kind, plan.pk]),
        'export_project_url': reverse('download_project', args=[kind, plan.pk]),
        'assign_listing_url': reverse('assign_plan_listing', args=[kind, plan.pk]),
        'workbook_url': reverse('plan_workbook', args=[kind, plan.pk]),
        **workspace_data(plan),
    }


@require_POST
@api_permissions()
def project_display(request, kind, plan_id):
    from .display_settings import clean_display, clean_shape, clean_button_scale
    from .plan_workbook import markers
    plan = get_plan(kind, plan_id)
    if not can_edit_plan(request.user, plan):
        return error_response('No tiene permiso para modificar este plano.', 403)
    try:
        data = json_body(request)
        if 'display_settings' in data:
            plan.display_settings = clean_display(data['display_settings'])
            plan.save(update_fields=['display_settings'])
        if 'button_id' in data:
            marker = get_object_or_404(markers(plan), pk=positive_integer(data['button_id'], 'Botón'))
            if 'button_scale' in data:
                marker.button_scale = clean_button_scale(data['button_scale'])
                marker.save(update_fields=['button_scale'])
                return JsonResponse({'button_scale': marker.button_scale})
            values = {}
            for key in ('area_radius_x', 'area_radius_y'):
                value = data.get(key, .05)
                from .project_service import finite
                if not finite(value) or not 0 < value <= 1:
                    raise ValidationError('El tamaño del área debe ser mayor que 0 y hasta 100 %.')
                values[key] = value
            if type(data.get('has_area')) is not bool:
                raise ValidationError('Indique si desea mostrar el área.')
            values.update(has_area=data['has_area'], area_shape=clean_shape(data.get('area_shape', 'ellipse')))
            for key, value in values.items():
                setattr(marker, key, value)
            marker.save(update_fields=list(values))
        return JsonResponse({'display_settings': plan.display_settings})
    except ValidationError as exc:
        return error_response(exc)


def public_cloud_error(message):
    # Saved projects can retain an error recorded by an older version.
    if 'Supabase' in message and 'CLI' in message:
        return 'No se pudo consultar la base de datos. Revisa la conexión y los permisos.'
    return message.replace('Google Drive', 'la nube').replace('Drive', 'la nube').replace('Supabase', 'la base de datos')


def local_copy_data(kind, plan, user):
    return {
        'plan_id': plan.pk, 'kind': kind, 'name': plan.name,
        'permissions': capabilities(user, plan),
        'cloud_key': plan.cloud_key, 'cloud_origin': getattr(plan, 'cloud_origin', ''),
        'detail_url': reverse('detail_pdf' if kind == 'pdf' else 'plan_detail', args=[plan.pk]),
        'clear_url': reverse('clear_local_project', args=[kind, plan.pk]) if can_clear_local(kind, plan, user) else None,
    }


def saved_data(saved, user):
    kind = 'pdf' if saved.pdf_plan_id else 'dxf'
    plan = saved.pdf_plan if saved.pdf_plan_id else saved.cad_plan
    return {
        **local_copy_data(kind, plan, user), 'id': saved.pk,
        'date': saved.saved_at.isoformat(),
        'cloud_status': saved.cloud_status, 'cloud_error': public_cloud_error(saved.cloud_error),
        'cloud_stage': saved.cloud_stage, 'cloud_progress': saved.cloud_progress,
        'export_url': reverse('download_saved_project', args=[saved.pk]),
        'delete_url': reverse('delete_pdf_plan' if kind == 'pdf' else 'delete_plan', args=[plan.pk]),
    }


@require_GET
@api_permissions()
def saved_projects(request):
    mine = request.GET.get('mine') == '1'
    status_only = request.GET.get('status') == '1'
    if mine and not capabilities(request.user)['can_history']:
        return error_response('El espectador no tiene historial de planos creados.', 403)
    from .terminal_monitor import retry_cloud_registrations
    projects = SavedProject.objects.select_related('cad_plan', 'pdf_plan').only(
        'id', 'cad_plan_id', 'pdf_plan_id', 'saved_at',
        'cloud_status', 'cloud_error', 'cloud_stage', 'cloud_progress',
        'cad_plan__id', 'cad_plan__name', 'cad_plan__cloud_key', 'cad_plan__imported_by_id',
        'cad_plan__owner_username',
        'pdf_plan__id', 'pdf_plan__name', 'pdf_plan__cloud_key', 'pdf_plan__cloud_origin', 'pdf_plan__imported_by_id',
        'pdf_plan__owner_username',
    )
    if mine:
        from django.db.models import Q
        username = request.user.get_username()
        projects = projects.filter(
            Q(cad_plan__owner_username=username) | Q(pdf_plan__owner_username=username) |
            Q(cad_plan__imported_by=request.user, cad_plan__owner_username='') |
            Q(pdf_plan__imported_by=request.user, pdf_plan__owner_username='', pdf_plan__cloud_origin=''))
    availability = {}
    try:
        for kind, model in (('pdf', PdfPlan), ('dxf', Plan)):
            value = request.GET.get(kind)
            if value is not None:
                if not value.isascii() or not value.isdecimal() or len(value) > 19:
                    raise ValidationError('Plano debe ser un entero válido mayor que cero.')
                plan_id = positive_integer(int(value), 'Plano', maximum=2**63 - 1)
                availability[kind] = {'id': plan_id, 'exists': model.objects.filter(pk=plan_id).exists()}
    except ValidationError as exc:
        return error_response(exc)
    if status_only:
        from django.db.models import Q
        selected = Q(pk__in=[])
        for kind, state in availability.items():
            selected |= Q(**{'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': state['id']})
        projects = projects.filter(selected)
    retry_cloud_registrations(projects.values('pk') if status_only else None)
    # Check working plans as well as saved copies: a newly imported plan is
    # intentionally absent from the library until the user saves it.
    return JsonResponse({'plans': [saved_data(saved, request.user) for saved in projects
                                  if can_access_plan(request.user, saved.pdf_plan if saved.pdf_plan_id else saved.cad_plan)],
                         'cached': [] if mine or status_only else cached_projects(request.user), 'availability': availability})


def cached_projects(user):
    plans = PdfPlan.objects.exclude(cloud_origin='').filter(saved_project__isnull=True).only(
        'id', 'name', 'cloud_key', 'cloud_origin', 'owner_username', 'imported_by_id', 'imported_at')
    return [{**local_copy_data('pdf', plan, user), 'date': plan.imported_at.isoformat(),
             'export_url': reverse('download_project', args=['pdf', plan.pk])} for plan in plans if can_access_plan(user, plan)]


def can_clear_local(kind, plan, user):
    from django.conf import settings
    if settings.PLANOS_CLOUD_MODE:
        return False
    from .authorization import application_profile
    if application_profile(user) is not None:
        return can_delete_plan(user, plan)
    return can_delete_plan(user, plan) or (
        kind == 'pdf' and bool(plan.cloud_origin) and plan.imported_by_id == user.pk and not hasattr(plan, 'saved_project')
    )


def clear_plan_files(kind, plan):
    files = [(plan.source.storage, plan.source.name)] if plan.source else []
    if kind == 'dxf':
        files.extend((motor.excel.storage, motor.excel.name) for motor in plan.motors.all() if motor.excel)
    workbook_ids = {plan.workbook_id, plan.listing_id} - {None}
    plan._keep_cloud = True
    with transaction.atomic():
        plan.delete()
        for workbook_id in workbook_ids:
            WorkbookDocument.objects.filter(pk=workbook_id, pdf_workspaces__isnull=True, pdf_listings__isnull=True,
                cad_workspaces__isnull=True, cad_listings__isnull=True, markers__isnull=True, motors__isnull=True,
                saved_motor__isnull=True).delete()
        for storage, name in files:
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))


@require_POST
@api_permissions()
def clear_local_project(request, kind, plan_id):
    plan = get_plan(kind, plan_id)
    if not can_clear_local(kind, plan, request.user):
        return error_response('No tiene permiso para limpiar esta copia.', status=403)
    clear_plan_files(kind, plan)
    return JsonResponse({'deleted': True})


@require_GET
@api_permissions()
def cloud_projects(request):
    from .cloud_projects import CloudUnavailable, list_cloud_projects, project_title

    try:
        return JsonResponse({'plans': [
            {'id': project['id'], 'name': project['name'], 'title': project_title(project['name']), 'date': project['date'],
             'cloud_key': project.get('projectKey'), 'open_url': reverse('open_cloud_project', args=[project['id']]),
             'delete_url': reverse('delete_cloud_project', args=[project['id']])}
            for project in list_cloud_projects() if can_open_cloud(request.user, project)
        ]})
    except CloudUnavailable as exc:
        return error_response(exc, status=503)


def can_open_cloud(user, project):
    from .authorization import application_profile, roles_for, has_role_configuration
    if application_profile(user) is not None:
        return True
    if is_administrator(user):
        return True
    roles = roles_for(user)
    if not has_role_configuration(user) or roles.filter(can_view=True, all_plans=True).exists():
        return True
    from django.db.models import Q
    copies = PdfPlan.objects.filter(Q(cloud_origin=project['id']) | Q(cloud_key=project.get('projectKey')))
    return any(can_access_plan(user, plan) for plan in copies)


@require_POST
@api_permissions()
def open_cloud_project(request, pdf_id):
    from .cloud_projects import CloudUnavailable, open_cloud_project as restore, list_cloud_projects

    started = perf_counter()
    try:
        from .authorization import application_profile, roles_for, has_role_configuration
        roles = roles_for(request.user)
        if application_profile(request.user) is None and has_role_configuration(request.user) and not is_administrator(request.user) and not roles.filter(can_view=True, all_plans=True).exists():
            project = next((p for p in list_cloud_projects() if p['id'] == pdf_id), None)
            if not project or not can_open_cloud(request.user, project):
                return error_response('Su rol no permite abrir este plano de la nube.', 403)
        cached = next((p for p in PdfPlan.objects.filter(cloud_origin=pdf_id)
                       if can_access_plan(request.user, p)), None)
        if cached:
            plan, warning = restore(pdf_id, request.user, replace_plan=cached)
        else:
            plan, warning = restore(pdf_id, request.user)
        if not can_access_plan(request.user, plan):
            return error_response('Su rol no permite abrir este plano.', 403)
        response = JsonResponse({'plan': pdf_data(plan), 'warning': warning})
        response['Server-Timing'] = f'cloud-open;dur={(perf_counter() - started) * 1000:.1f}'
        return response
    except CloudUnavailable as exc:
        return error_response(exc, status=503)
    except ValidationError as exc:
        return error_response(exc)


@require_POST
@api_permissions()
def delete_cloud_project(request, pdf_id):
    from .cloud_projects import CloudUnavailable, drive_command, list_cloud_projects, project_title, supabase_query

    try:
        project = next((item for item in list_cloud_projects() if item['id'] == pdf_id), None)
        if not project:
            return error_response('El proyecto ya no está en la nube.', status=404)
        if not is_administrator(request.user):
            from django.db.models import Q
            from .authorization import application_profile
            profile = application_profile(request.user)
            originals = PdfPlan.objects.filter(Q(cloud_origin=pdf_id) | Q(cloud_key=project.get('projectKey')))
            if profile is None or profile.role != 'worker' or not any(can_delete_plan(request.user, plan) for plan in originals):
                return error_response('Solo puede eliminar de la nube los proyectos que usted creó.', status=403)
        excel_id = project['excelId']
        if not all(value and all(char.isascii() and (char.isalnum() or char in '_-') for char in value) for value in (pdf_id, excel_id)):
            return error_response('El identificador del archivo en la nube no es válido.')
        supabase_query('SELECT 1;', read_only=True)
        drive_command('--delete-project', '--drive-id', pdf_id, '--excel-id', excel_id)
        try:
            supabase_query(f"BEGIN; DELETE FROM public.saved_projects WHERE pdf_drive_file_id = '{pdf_id}'; "
                           f"DELETE FROM public.pdf_plans WHERE drive_file_id = '{pdf_id}'; "
                           f"DELETE FROM public.workbook_documents WHERE drive_file_id = '{excel_id}'; COMMIT;", read_only=False)
        except CloudUnavailable:
            drive_command('--restore-project', '--drive-id', pdf_id, '--excel-id', excel_id)
            raise
    except CloudUnavailable as exc:
        return error_response(exc, status=503)
    with transaction.atomic():
        copies = list(PdfPlan.objects.filter(cloud_origin=pdf_id))
        original = PdfPlan.objects.filter(cloud_key=project.get('projectKey')).first()
        if original and (original.cloud_key.startswith('project_') or project_title(original.name).casefold() == project_title(project['name']).casefold()):
            copies.append(original)
        removed = [plan.pk for plan in copies]
        for plan in copies:
            clear_plan_files('pdf', plan)
    return JsonResponse({'deleted': True, 'local_ids': removed})


@require_POST
@api_permissions()
def save_project(request, kind, plan_id):
    from .cloud_projects import CloudUnavailable
    from .cloud_save import merge_only_additions
    from .plan_workbook import materialize_button_sources, organize_project_sheets
    from .terminal_monitor import is_monitor_enabled, queue_saved_project_sync

    started = perf_counter()
    permission = 'can_save_pdf_plan' if kind == 'pdf' else 'can_save_plan'
    if not capabilities(request.user)[permission]:
        return error_response('No tiene permiso para guardar planos.', status=403)
    plan = get_plan(kind, plan_id)
    try:
        with transaction.atomic():
            layout_changed = materialize_button_sources(plan, request.user)
            layout_changed = organize_project_sheets(plan, request.user) or layout_changed
            content = export_project(kind, plan)
            if kind == 'pdf' and is_monitor_enabled() and merge_only_additions(plan, request.user, content):
                layout_changed = True
                content = export_project(kind, plan)
            saved, _ = SavedProject.objects.update_or_create(
                **{'pdf_plan' if kind == 'pdf' else 'cad_plan': plan},
                defaults={'name': plan.name, 'content': content, 'saved_by': request.user},
            )
            queue_saved_project_sync(kind, plan, content, saved.saved_at)
    except (ValidationError, OSError, CloudUnavailable) as exc:
        return error_response(exc)
    saved.refresh_from_db(fields=['cloud_status', 'cloud_error', 'cloud_stage', 'cloud_progress'])
    result = {'saved': saved_data(saved, request.user), 'saved_at': saved.saved_at.isoformat()}
    if layout_changed:
        from .views import plan_data
        result['plan'] = pdf_data(plan) if kind == 'pdf' else plan_data(plan, detail=True)
    response = JsonResponse(result)
    response['Server-Timing'] = f'save-prepare;dur={(perf_counter() - started) * 1000:.1f}'
    return response


def project_file(content, name):
    return FileResponse(BytesIO(content), as_attachment=True, filename=Path(name).stem + '.planproj', content_type='application/zip')


@require_GET
@api_permissions()
def download_project(request, kind, plan_id):
    plan = get_plan(kind, plan_id)
    try:
        return project_file(export_project(kind, plan), plan.name)
    except (ValidationError, OSError) as exc:
        return error_response(exc)


@require_GET
@api_permissions()
def download_saved_project(request, saved_id):
    saved = get_object_or_404(SavedProject.objects.select_related('cad_plan', 'pdf_plan'), pk=saved_id)
    plan = saved.pdf_plan if saved.pdf_plan_id else saved.cad_plan
    if not can_access_plan(request.user, plan) or not can_access_plan(request.user, plan, 'can_export'):
        return error_response('Su rol no permite exportar este plano.', 403)
    try:
        content = bytes(saved.content)
        if not content:
            kind = 'pdf' if saved.pdf_plan_id else 'dxf'
            content = export_project(kind, saved.pdf_plan if saved.pdf_plan_id else saved.cad_plan)
        return project_file(content, saved.name)
    except (ValidationError, OSError) as exc:
        return error_response(exc)


@require_POST
@api_permissions()
def upload_project(request):
    upload = request.FILES.get('file')
    if upload is None:
        return error_response('Seleccione un archivo de proyecto .planproj.')
    try:
        kind, plan = import_project(upload, request.user)
    except ValidationError as exc:
        return error_response(exc)
    from .views import plan_data
    return JsonResponse({'kind': kind, 'plan': pdf_data(plan) if kind == 'pdf' else plan_data(plan, detail=True)}, status=201)


def listing_template(count):
    source = (Path(__file__).resolve().parent / 'data' / 'motor-list-template.xlsx').read_bytes()
    if count <= 9:
        return source
    workbook = load_workbook(BytesIO(source))
    try:
        sheet = workbook['Listado']
        row_merges = [(item.min_col, item.max_col) for item in sheet.merged_cells.ranges if item.min_row == 16 and item.max_row == 16]
        for number in range(10, min(count, 5000) + 1):
            row = 7 + number
            for column in range(2, sheet.max_column + 1):
                cell = sheet.cell(row, column)
                cell._style = copy(sheet.cell(16, column)._style)
            for first, last in row_merges:
                sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
            sheet.cell(row, 2, number)
            sheet.row_dimensions[row].height = sheet.row_dimensions[16].height
        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
    finally:
        workbook.close()


@require_POST
@api_permissions()
def assign_plan_listing(request, kind, plan_id):
    plan = get_plan(kind, plan_id)
    from .plan_workbook import add_listing, add_existing_listing, change_primary_listing, delete_listing, workspace_data, WorkbookConflict
    try:
        upload = request.FILES.get('file')
        if upload is not None:
            if request.POST.get('action') == 'inspect':
                from .button_import import inspect_button_file
                return JsonResponse(inspect_button_file(upload, request.POST.get('source_sheet'), preview=True))
            add_listing(plan, request.user, upload, request.POST.get('source_sheet'))
        else:
            data = json_body(request)
            if data.get('action') == 'delete':
                delete_listing(plan, positive_integer(data.get('listing_id'), 'Listado'), request.user)
            elif data.get('action') == 'set_primary':
                change_primary_listing(plan, data.get('sheet'), request.user)
            elif data.get('action') == 'add_sheet':
                add_existing_listing(plan, data.get('sheet'), request.user)
            elif data.get('action') == 'add' or data.get('create_template') is True:
                add_listing(plan, request.user)
            else:
                raise ValidationError('Elija una hoja o agregue un nuevo listado.')
    except PermissionError as exc:
        return error_response(exc, status=403)
    except ValidationError as exc:
        return error_response(exc, status=409 if isinstance(exc, WorkbookConflict) else 400)
    return JsonResponse(workspace_data(plan, request.user))


@api_permissions()
def plan_workbook(request, kind, plan_id):
    from .plan_workbook import empty_workbook, ensure_workbook, import_workbook, initialize_marker, markers, workspace_data, WorkbookConflict
    from .views import validate_excel, plan_data
    if request.method not in ('GET', 'POST'):
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['GET', 'POST'])
    plan = get_plan(kind, plan_id)
    try:
        document = ensure_workbook(plan, request.user)
        if request.method == 'POST':
            upload = request.FILES.get('file')
            if upload is not None:
                validate_excel(upload)
                import_workbook(plan, upload.name, upload.read(), request.user,
                                first_as_listing=request.POST.get('first_as_listing') == 'true')
            elif json_body(request).get('action') == 'template':
                if document.plan_mode != 'template':
                    import_workbook(plan, 'Excel del plano.xlsx', empty_workbook(), request.user)
                    document.refresh_from_db()
                    document.plan_mode = 'template'
                    document.save(update_fields=['plan_mode'])
                for marker in markers(plan).filter(sheet_name=''):
                    initialize_marker(marker, request.user)
            else:
                raise ValidationError('Seleccione un Excel o el formato vacío.')
        result = workspace_data(plan, request.user)
        if request.method == 'POST':
            result['plan'] = pdf_data(plan) if kind == 'pdf' else plan_data(plan, detail=True)
        return JsonResponse(result)
    except ValidationError as exc:
        return error_response(exc, status=409 if isinstance(exc, WorkbookConflict) else 400)


@require_GET
@api_permissions()
def workbook_csv(request, workbook_id):
    document = get_object_or_404(WorkbookDocument, pk=workbook_id)
    workbook = load_workbook(BytesIO(bytes(document.content)), data_only=True)
    try:
        name = request.GET.get('sheet') or workbook.sheetnames[0]
        if name not in workbook.sheetnames:
            return error_response('La hoja solicitada no existe.')
        sheet = workbook[name]
        used = [cell for cell in sheet._cells.values() if cell.value is not None]
        rows = max((cell.row for cell in used), default=1)
        cols = max((cell.column for cell in used), default=1)
        if rows * cols > 5_000_000:
            return error_response('La hoja es demasiado grande para exportarla a CSV. Exporte el libro como XLSX.')
        stream = StringIO(newline='')
        writer = csv.writer(stream)
        for row in sheet.iter_rows(max_row=rows, max_col=cols):
            values = []
            for cell in row:
                value = '' if cell.value is None else formatted_value(cell.value, cell.number_format, workbook.epoch)
                # CSV has no cell types. Keep user text from becoming a formula
                # when a spreadsheet application opens the exported file.
                if isinstance(cell.value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                values.append(value)
            writer.writerow(values)
        return FileResponse(BytesIO(stream.getvalue().encode('utf-8-sig')), as_attachment=True, filename=f'{Path(document.name).stem}-{name}.csv', content_type='text/csv; charset=utf-8')
    finally:
        workbook.close()


@require_GET
@api_permissions()
def project_version(request, kind, plan_id):
    from .cloud_projects import CloudUnavailable, cloud_project_reference, drive_pair_version, workbook_version
    plan = get_plan(kind, plan_id)
    target = {'pdf_plan_id' if kind == 'pdf' else 'cad_plan_id': plan.pk}
    saved_at = SavedProject.objects.filter(**target).values_list('saved_at', flat=True).first()
    current = saved_at.isoformat() if saved_at else ''
    known = request.GET.get('version', current)
    stale = bool(known and current and known != current)
    result = {'stale': stale, 'remote': False, 'latest_version': current,
              'refresh_url': reverse('refresh_project', args=[kind, plan.pk]), 'verified': True}
    if kind == 'pdf' and plan.cloud_origin and plan.cloud_workbook_version:
        try:
            remote = cloud_project_reference(plan.cloud_origin)
            if remote and remote.get('excelId'):
                previous = (plan.cloud_base_state or {}).get('drive_pair')
                latest = drive_pair_version(remote)
                result['remote'] = (latest != previous if previous and latest else
                                    workbook_version(remote) != plan.cloud_workbook_version)
                result['stale'] = stale or result['remote']
            else:
                result.update(verified=False, warning='El par PDF y Excel no está disponible en la nube. Se conserva la copia descargada.')
        except CloudUnavailable:
            result.update(verified=False, warning='No se pudo comprobar la versión en la nube. Se conserva la última copia descargada.')
    return JsonResponse(result)


@require_POST
@api_permissions()
def refresh_project(request, kind, plan_id):
    from .cloud_projects import CloudUnavailable, open_cloud_project as restore
    from .views import plan_data
    plan = get_plan(kind, plan_id)
    try:
        warning = ''
        if kind == 'pdf' and plan.cloud_origin:
            plan, warning = restore(plan.cloud_origin, request.user, replace_plan=plan)
        return JsonResponse({'plan': pdf_data(plan) if kind == 'pdf' else plan_data(plan, detail=True), 'warning': warning})
    except CloudUnavailable as exc:
        return error_response(exc, 503)
    except (ValidationError, OSError) as exc:
        return error_response(exc)
