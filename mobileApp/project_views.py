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
    try:
        saved = plan.saved_project
    except SavedProject.DoesNotExist:
        saved = None
    return {
        'kind': kind, 'saved_at': saved.saved_at.isoformat() if saved else None,
        'save_project_url': reverse('save_project', args=[kind, plan.pk]),
        'export_project_url': reverse('download_project', args=[kind, plan.pk]),
        'assign_listing_url': reverse('assign_plan_listing', args=[kind, plan.pk]),
        'listing': workbook_data(plan.listing, sheets=False) if plan.listing_id else None,
    }


def saved_data(saved):
    kind = 'pdf' if saved.pdf_plan_id else 'dxf'
    plan = saved.pdf_plan if saved.pdf_plan_id else saved.cad_plan
    return {
        'id': saved.pk, 'plan_id': plan.pk, 'kind': kind, 'name': plan.name,
        'date': saved.saved_at.isoformat(),
        'detail_url': reverse('detail_pdf' if kind == 'pdf' else 'plan_detail', args=[plan.pk]),
        'export_url': reverse('download_saved_project', args=[saved.pk]),
    }


@require_GET
@api_permissions()
def saved_projects(request):
    projects = SavedProject.objects.defer('content', 'cad_plan__svg').select_related('cad_plan', 'pdf_plan')
    return JsonResponse({'plans': [saved_data(saved) for saved in projects]})


@require_POST
@api_permissions()
def save_project(request, kind, plan_id):
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
    try:
        upload = request.FILES.get('file')
        if upload is not None:
            document = uploaded_document(upload, request.user)
        else:
            data = json_body(request)
            if data.get('create_template') is True:
                count = plan.markers.count() if kind == 'pdf' else plan.motors.count()
                document = WorkbookDocument.objects.create(name='Listado de motores.xlsx', content=listing_template(count), updated_by=request.user)
            elif 'workbook_id' in data:
                document = get_object_or_404(WorkbookDocument, pk=positive_integer(data['workbook_id'], 'Excel'))
            else:
                raise ValidationError('Elija un Excel o cree el listado de motores del plano.')
        plan.listing = document
        plan.save(update_fields=['listing'])
    except ValidationError as exc:
        return error_response(exc)
    return JsonResponse({'listing': workbook_data(document, sheets=False)})


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
