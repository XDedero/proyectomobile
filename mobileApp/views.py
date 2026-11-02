import json
import math
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree
from zipfile import ZipFile

from django.conf import settings
from django.contrib.auth import login as django_login, logout as django_logout
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from openpyxl import load_workbook

from .dxf_service import import_dxf
from .access import api_permissions, capabilities
from .models import Motor, Plan, WorkbookDocument, SavedMotor


@ensure_csrf_cookie
@never_cache
def index(request):
    # A fresh page must not load cached scripts from an older interface whose
    # controls differ from those expected by the current drawing workflows.
    app_directory = Path(__file__).resolve().parent
    frontend_files = [app_directory / 'templates' / 'mobileApp' / 'index.html']
    frontend_files.extend((app_directory / 'static' / 'mobileApp').glob('*.js'))
    frontend_files.extend((app_directory / 'static' / 'mobileApp').glob('*.css'))
    asset_version = max(path.stat().st_mtime_ns for path in frontend_files)
    return render(request, 'mobileApp/index.html', {'asset_version': str(asset_version)})


def session_data(request):
    user = request.user
    return {
        'user': {
            'id': user.pk, 'username': user.get_username(),
            'display_name': user.get_full_name() or user.get_username(),
            'permissions': capabilities(user),
        } if user.is_authenticated and user.is_active else None,
        'csrf_token': get_token(request),
    }


@require_GET
@ensure_csrf_cookie
@never_cache
def auth_session(request):
    return JsonResponse(session_data(request))


@require_POST
@never_cache
def auth_login(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeError):
        return JsonResponse({'error': 'Solicitud de acceso no válida.'}, status=400)
    if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in ('username', 'password')):
        return JsonResponse({'error': 'Ingrese su nombre de usuario y contraseña.'}, status=400)
    form = AuthenticationForm(request, data=data)
    if not form.is_valid():
        return JsonResponse({'error': 'Usuario o contraseña incorrectos, o cuenta inactiva.'}, status=401)
    django_login(request, form.get_user())
    return JsonResponse(session_data(request))


@require_POST
@never_cache
def auth_logout(request):
    django_logout(request)
    return JsonResponse(session_data(request))


def motor_data(motor):
    excel = None
    if motor.workbook_id:
        # Imported here because the PDF API also uses validate_excel.
        from .pdf_views import workbook_data
        excel = workbook_data(motor.workbook, sheets=False)
        from .workbook_service import document_sheets
        excel['sheets'] = motor.excel_sheets or [sheet['name'] for sheet in document_sheets(motor.workbook.content)]
        excel['url'] = excel['download_url']
        excel['preview_url'] = reverse('motor_excel_preview', args=[motor.pk])
    elif motor.excel:
        excel = {
            'name': motor.excel_name, 'sheets': motor.excel_sheets,
            'url': reverse('motor_excel_download', args=[motor.pk]),
            'preview_url': reverse('motor_excel_preview', args=[motor.pk]),
            'metadata_url': reverse('motor_workbook_metadata', args=[motor.pk]),
        }
    return {
        'id': motor.pk, 'name': motor.name, 'entity_key': motor.entity_key,
        'x': motor.x, 'y': motor.y, 'z': motor.z,
        'svg_x': motor.svg_x, 'svg_y': motor.svg_y, 'svg_bounds': motor.svg_bounds,
        'excel': excel,
        'assign_url': reverse('motor_excel', args=[motor.pk]),
        'move_url': reverse('move_cad_marker', args=[motor.pk]),
    }


def plan_data(plan, detail=False):
    data = {
        'id': plan.pk, 'name': plan.name, 'size': plan.size,
        'date': plan.imported_at.isoformat(), 'motor_count': plan.motors.count(),
        'motor_keyword': settings.DXF_MOTOR_KEYWORD,
        'detail_url': reverse('plan_detail', args=[plan.pk]),
        'svg_url': reverse('plan_svg', args=[plan.pk]),
        'markers_url': reverse('create_cad_marker', args=[plan.pk]),
        'update_url': reverse('update_plan', args=[plan.pk]),
        'delete_url': reverse('delete_plan', args=[plan.pk]),
    }
    if detail:
        data['motors'] = [motor_data(motor) for motor in plan.motors.select_related('workbook').defer('workbook__content').all()]
    from .project_views import project_plan_data
    data.update(project_plan_data('dxf', plan))
    return data


@require_GET
@api_permissions()
def plans(request):
    return JsonResponse({'plans': [plan_data(plan) for plan in Plan.objects.all()]})


@require_POST
@api_permissions()
def plan_import(request):
    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'error': 'Seleccione un archivo .dxf.'}, status=400)
    try:
        svg_content, motors = import_dxf(upload)
    except ValidationError as exc:
        return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    plan = Plan(
        imported_by=request.user,
        name=upload.name, size=upload.size, svg=svg_content,
    )
    try:
        with transaction.atomic():
            plan.source.save(upload.name, upload, save=False)
            plan.save()
            from .motor_service import template_bytes
            documents = WorkbookDocument.objects.bulk_create([
                WorkbookDocument(name=f'Motor {index+1}.xlsx', content=template_bytes(), is_motor=True, updated_by=request.user)
                for index in range(len(motors))
            ])
            Motor.objects.bulk_create([Motor(plan=plan, workbook=documents[index], excel_sheets=['1'], **motor) for index, motor in enumerate(motors)])
    except Exception:
        if plan.source:
            plan.source.delete(save=False)
        raise
    return JsonResponse(plan_data(plan, detail=True), status=201)


@require_GET
@api_permissions()
def plan_detail(request, plan_id):
    plan = get_object_or_404(Plan, pk=plan_id)
    return JsonResponse(plan_data(plan, detail=True))


@require_GET
@api_permissions()
def plan_svg(request, plan_id):
    plan = get_object_or_404(Plan, pk=plan_id)
    response = HttpResponse(plan.svg, content_type='image/svg+xml')
    response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def validate_excel(upload):
    if Path(upload.name).suffix.lower() != '.xlsx':
        raise ValidationError('Solo se admiten archivos Excel .xlsx.')
    if not upload.size or upload.size > settings.PLAN_UPLOAD_MAX_BYTES:
        raise ValidationError('El Excel debe tener contenido y pesar como máximo 20 MB.')
    try:
        with ZipFile(upload) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                raise ValidationError('El contenido descomprimido del Excel es demasiado grande.')
            if any(item.filename.lower().endswith('vbaproject.bin') for item in entries):
                raise ValidationError('Seleccione un libro .xlsx sin macros.')
        upload.seek(0)
        workbook = load_workbook(upload, read_only=True, data_only=True, keep_links=False)
        try:
            sheets = workbook.sheetnames
            if not sheets:
                raise ValidationError('El Excel debe contener al menos una hoja.')
            if workbook.mime_type != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml':
                raise ValidationError('El archivo debe ser un libro Excel .xlsx.')
        finally:
            workbook.close()
        return sheets
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError('El archivo no es un libro Excel .xlsx válido.') from exc
    finally:
        upload.seek(0)


@require_POST
@api_permissions()
def motor_excel(request, motor_id):
    motor = get_object_or_404(Motor, pk=motor_id)
    upload = request.FILES.get('file')
    if upload is None:
        from .pdf_views import json_body, error_response
        from .workbook_service import positive_integer
        try:
            data = json_body(request)
            from .motor_service import copy_motor
            if 'saved_motor_id' in data:
                saved = get_object_or_404(SavedMotor, pk=positive_integer(data['saved_motor_id'], 'Motor guardado'))
                document = copy_motor(saved.workbook, request.user)
            else:
                document = copy_motor(get_object_or_404(WorkbookDocument, pk=positive_integer(data.get('workbook_id'), 'Excel')), request.user)
        except ValidationError as exc:
            return error_response(exc)
        motor.workbook = document
        from .workbook_service import document_sheets
        motor.excel_sheets = [sheet['name'] for sheet in document_sheets(document.content)]
        motor.save(update_fields=['workbook', 'excel_sheets'])
        return JsonResponse({'motor': motor_data(motor)})
    try:
        sheets = validate_excel(upload)
    except ValidationError as exc:
        return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    previous = motor.excel.name
    storage = motor.excel.storage
    try:
        with transaction.atomic():
            document = WorkbookDocument.objects.create(
                name=upload.name, content=upload.read(), updated_by=request.user,
            )
            from .motor_service import mark_motor
            mark_motor(document)
            upload.seek(0)
            motor.excel.save(upload.name, upload, save=False)
            motor.excel_name = upload.name
            motor.excel_sheets = sheets
            motor.workbook = document
            motor.save(update_fields=['excel', 'excel_name', 'excel_sheets', 'workbook'])
            if previous:
                transaction.on_commit(lambda: storage.delete(previous))
    except Exception:
        if motor.excel.name != previous:
            motor.excel.delete(save=False)
        raise
    return JsonResponse({'motor': motor_data(motor)})


@require_GET
@api_permissions()
def motor_excel_download(request, motor_id):
    motor = get_object_or_404(Motor, pk=motor_id)
    if motor.workbook_id:
        return FileResponse(
            BytesIO(bytes(motor.workbook.content)), as_attachment=True, filename=motor.workbook.name,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    if not motor.excel:
        return JsonResponse({'error': 'Este motor aún no tiene un Excel asignado.'}, status=404)
    return FileResponse(
        motor.excel.open('rb'), as_attachment=True, filename=motor.excel_name,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@require_GET
@api_permissions()
@never_cache
def motor_excel_preview(request, motor_id):
    motor = get_object_or_404(Motor, pk=motor_id)
    if not motor.excel and not motor.workbook_id:
        return JsonResponse({'error': 'Este motor aún no tiene un Excel asignado.'}, status=404)
    with (BytesIO(bytes(motor.workbook.content)) if motor.workbook_id else motor.excel.open('rb')) as source:
        workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
        try:
            sheet_name = request.GET.get('sheet') or workbook.sheetnames[0]
            if sheet_name not in workbook.sheetnames:
                return JsonResponse({'error': 'La hoja solicitada no existe.'}, status=400)
            sheet = workbook[sheet_name]
            rows = list(sheet.iter_rows(
                max_row=min(sheet.max_row or 101, 101),
                max_col=min(sheet.max_column or 20, 20), values_only=True,
            ))
            return JsonResponse({
                'sheet': sheet_name, 'sheets': workbook.sheetnames, 'rows': rows[:100],
                'truncated': len(rows) > 100 or (sheet.max_column or 0) > 20,
            })
        finally:
            workbook.close()


@require_GET
@api_permissions()
def motor_workbook_metadata(request, motor_id):
    """Upgrade an older CAD attachment without altering the original file."""
    from .pdf_views import workbook_data
    with transaction.atomic():
        motor = get_object_or_404(Motor.objects.select_for_update(), pk=motor_id)
        if not motor.workbook_id:
            if not motor.excel:
                return JsonResponse({'error': 'Este motor aún no tiene un Excel asignado.'}, status=404)
            with motor.excel.open('rb') as source:
                motor.workbook = WorkbookDocument.objects.create(
                    name=motor.excel_name, content=source.read(), updated_by=request.user, is_motor=True,
                )
            motor.save(update_fields=['workbook'])
    return JsonResponse(workbook_data(motor.workbook, user=request.user))


@require_POST
@api_permissions('mobileApp.change_plan')
def update_plan(request, plan_id):
    from .pdf_views import json_body, error_response
    plan = get_object_or_404(Plan, pk=plan_id)
    try:
        name = json_body(request).get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise ValidationError('Ingrese un nombre de hasta 255 caracteres.')
    except ValidationError as exc:
        return error_response(exc)
    plan.name = name.strip()
    plan.save(update_fields=['name'])
    return JsonResponse(plan_data(plan, detail=True))


@require_POST
@api_permissions('mobileApp.delete_plan')
def delete_plan(request, plan_id):
    plan = get_object_or_404(Plan, pk=plan_id)
    files = [(plan.source.storage, plan.source.name)]
    files.extend((motor.excel.storage, motor.excel.name) for motor in plan.motors.all() if motor.excel)
    with transaction.atomic():
        plan.delete()
        for storage, name in files:
            transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))
    # WorkbookDocument objects remain in the library when a plan is removed.
    return JsonResponse({'deleted': True})


def cad_world_position(plan, sx, sy):
    if any(type(value) not in (float, int) or not math.isfinite(value) for value in (sx, sy)):
        raise ValidationError('Seleccione coordenadas dentro del plano.')
    root = ElementTree.fromstring(plan.svg)
    vx, vy, vw, vh = map(float, root.attrib['viewBox'].split())
    if not (vx <= sx <= vx + vw and vy <= sy <= vy + vh):
        raise ValidationError('El botón debe estar dentro del plano.')
    # New imports store the DXF->SVG affine transform alongside the drawing.
    transform = root.get('data-wcs-transform')
    if not transform and plan.source:
        # Recover the transform from an older saved DXF. Re-rendering does
        # not replace the saved drawing or its Excel associations.
        with plan.source.open('rb') as source:
            from django.core.files.uploadedfile import SimpleUploadedFile
            upload = SimpleUploadedFile('saved.dxf', source.read())
        recovered_svg, _ = import_dxf(upload)
        recovered = ElementTree.fromstring(recovered_svg)
        transform = recovered.get('data-wcs-transform')
        if recovered.get('viewBox') != root.get('viewBox'):
            transform = None
        if transform:
            a, b, c, d, e, f = map(float, transform.split())
            tolerance = max(vw, vh) * 1e-5
            for existing in plan.motors.all():
                if abs(a * existing.x + c * existing.y + e - existing.svg_x) > tolerance or abs(b * existing.x + d * existing.y + f - existing.svg_y) > tolerance:
                    transform = None
                    break
    if transform:
        a, b, c, d, e, f = map(float, transform.split())
        determinant = a * d - b * c
        x = (d * (sx - e) - c * (sy - f)) / determinant
        y = (-b * (sx - e) + a * (sy - f)) / determinant
    else:
        raise ValidationError('Reimporte este plano antiguo para añadir botones con coordenadas CAD.')
    return x, y


@require_POST
@api_permissions()
def create_cad_marker(request, plan_id):
    from .pdf_views import json_body, error_response
    from .dxf_service import MAX_MOTORS
    plan = get_object_or_404(Plan, pk=plan_id)
    try:
        data = json_body(request)
        sx, sy = data.get('svg_x'), data.get('svg_y')
        if any(type(value) not in (float, int) or not math.isfinite(value) for value in (sx, sy)):
            raise ValidationError('Seleccione una ubicación válida dentro del plano.')
        name = data.get('name', 'Motor')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 255:
            raise ValidationError('Ingrese un nombre de hasta 255 caracteres.')
        x, y = cad_world_position(plan, sx, sy)
        if plan.motors.count() >= MAX_MOTORS:
            raise ValidationError('El plano supera el límite de 5.000 botones.')
    except (ValidationError, ValueError, KeyError, ZeroDivisionError, OSError, ElementTree.ParseError) as exc:
        return error_response(exc)
    from .motor_service import blank_motor
    with transaction.atomic():
        motor = Motor.objects.create(
            plan=plan, entity_key=f'manual/{uuid4().hex}', name=name.strip(),
            x=x, y=y, z=0, svg_x=sx, svg_y=sy, svg_bounds=[sx, sy, 0, 0],
            workbook=blank_motor(request.user, f'{name.strip()}.xlsx'), excel_sheets=['1'],
        )
    return JsonResponse({'motor': motor_data(motor)}, status=201)


@require_POST
@api_permissions('mobileApp.change_plan')
def move_cad_marker(request, motor_id):
    from .pdf_views import json_body, error_response
    motor = get_object_or_404(Motor.objects.select_related('plan'), pk=motor_id)
    try:
        data = json_body(request)
        sx, sy = data.get('svg_x'), data.get('svg_y')
        x, y = cad_world_position(motor.plan, sx, sy)
        dx, dy = sx - motor.svg_x, sy - motor.svg_y
        bounds = motor.svg_bounds or [motor.svg_x, motor.svg_y, 0, 0]
        motor.svg_bounds = [bounds[0] + dx, bounds[1] + dy, bounds[2], bounds[3]]
        motor.x, motor.y, motor.svg_x, motor.svg_y = x, y, sx, sy
        motor.save(update_fields=['x', 'y', 'svg_x', 'svg_y', 'svg_bounds'])
    except (ValidationError, ValueError, KeyError, ZeroDivisionError, OSError, ElementTree.ParseError) as exc:
        return error_response(exc)
    return JsonResponse({'motor': motor_data(motor)})
