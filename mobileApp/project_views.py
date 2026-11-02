"""A common saved-plan library, plan-level motor lists and portable projects."""

from copy import copy
import csv
from io import BytesIO, StringIO
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from openpyxl import load_workbook

from .access import api_permissions, capabilities
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
    from .plan_workbook import workspace_data
    try:
        saved = plan.saved_project
    except SavedProject.DoesNotExist:
        saved = None
    return {
        'kind': kind, 'saved_at': saved.saved_at.isoformat() if saved else None,
        'save_project_url': reverse('save_project', args=[kind, plan.pk]),
        'export_project_url': reverse('download_project', args=[kind, plan.pk]),
        'assign_listing_url': reverse('assign_plan_listing', args=[kind, plan.pk]),
        'workbook_url': reverse('plan_workbook', args=[kind, plan.pk]),
        **workspace_data(plan),
    }


def saved_data(saved):
    kind = 'pdf' if saved.pdf_plan_id else 'dxf'
    plan = saved.pdf_plan if saved.pdf_plan_id else saved.cad_plan
    return {
        'id': saved.pk, 'plan_id': plan.pk, 'kind': kind, 'name': plan.name,
        'date': saved.saved_at.isoformat(),
        'detail_url': reverse('detail_pdf' if kind == 'pdf' else 'plan_detail', args=[plan.pk]),
        'export_url': reverse('download_saved_project', args=[saved.pk]),
        'delete_url': reverse('delete_pdf_plan' if kind == 'pdf' else 'delete_plan', args=[plan.pk]),
    }


@require_GET
@api_permissions()
def saved_projects(request):
    projects = SavedProject.objects.defer('content', 'cad_plan__svg').select_related('cad_plan', 'pdf_plan')
    return JsonResponse({'plans': [saved_data(saved) for saved in projects]})


@require_POST
@api_permissions()
def save_project(request, kind, plan_id):
    permission = 'can_save_pdf_plan' if kind == 'pdf' else 'can_save_plan'
    if not capabilities(request.user)[permission]:
        return error_response('No tiene permiso para guardar planos.', status=403)
    plan = get_plan(kind, plan_id)
    try:
        content = export_project(kind, plan)
        with transaction.atomic():
            saved, _ = SavedProject.objects.update_or_create(
                **{'pdf_plan' if kind == 'pdf' else 'cad_plan': plan},
                defaults={'name': plan.name, 'content': content, 'saved_by': request.user},
            )
    except (ValidationError, OSError) as exc:
        return error_response(exc)
    return JsonResponse({'saved': saved_data(saved), 'saved_at': saved.saved_at.isoformat()})


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
            add_listing(plan, request.user, upload)
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
    from .plan_workbook import ensure_workbook, import_workbook, initialize_marker, markers, workspace_data, WorkbookConflict
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
                import_workbook(plan, upload.name, upload.read(), request.user)
            elif json_body(request).get('action') == 'template':
                if document.plan_mode != 'template':
                    import_workbook(plan, 'Excel del plano.xlsx', listing_template(9), request.user)
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
