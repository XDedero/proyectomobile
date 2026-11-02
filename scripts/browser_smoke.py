r"""Exercise the real UI in Chrome against an isolated database and media folder.

Run: .venv\Scripts\python.exe scripts\browser_smoke.py
Install requirements-dev.txt first. No production records are changed.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def format_workbook_fixture(path):
    """A maintenance form whose geometry and formatting matter to its reader."""
    from io import BytesIO
    from PIL import Image
    from openpyxl import load_workbook
    from openpyxl.drawing.image import Image as WorkbookImage
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    workbook = load_workbook(path)
    sheet = workbook.active
    for column, width in [('A', 15), ('B', 12), ('C', 10)]:
        sheet.column_dimensions[column].width = width
    sheet.column_dimensions['E'].hidden = True
    sheet.row_dimensions[5].hidden = True
    sheet.row_dimensions[6].height = 36
    sheet.merge_cells('A6:C6')
    title = sheet['A6']
    title.value = 'FICHA DEL MOTOR'
    title.font = Font(name='Arial', size=15, bold=True, color='FFFFFF')
    title.fill = PatternFill('solid', fgColor='174EA6')
    title.alignment = Alignment(horizontal='center', vertical='center')
    title.border = Border(bottom=Side(style='medium', color='000000'))
    sheet['B4'] = .25
    sheet['B4'].number_format = '0.0%'
    sheet['A8'] = 'Observaciones de mantenimiento en varias líneas'
    sheet['A8'].alignment = Alignment(wrap_text=True, vertical='top')
    sheet.row_dimensions[8].height = 42
    source = BytesIO()
    Image.new('RGB', (24, 18), '#174EA6').save(source, format='PNG')
    source.seek(0)
    sheet.add_image(WorkbookImage(source), 'C8')
    workbook.save(path)
    workbook.close()


def check_workbook_layout(page):
    from playwright.sync_api import expect
    page.locator('#workbookEditorAddress').fill('A6')
    page.locator('#workbookEditorGo').click()
    title = page.locator('#wbe-cell-6-1')
    expect(title).to_have_text('FICHA DEL MOTOR')
    assert page.locator('#wbe-cell-6-2').count() == 0
    assert page.locator('#wbe-cell-5-1').count() == 0
    assert page.locator('#wbe-cell-1-5').count() == 0
    geometry = page.evaluate('''() => {
        const node=document.querySelector('#wbe-cell-6-1');
        const style=getComputedStyle(node), rect=node.getBoundingClientRect();
        return {width:rect.width,height:rect.height,background:style.backgroundColor,color:style.color,font:style.fontWeight,align:style.textAlign,border:style.borderBottomStyle};
    }''')
    assert geometry['height'] == 48, geometry
    assert geometry['width'] > 250, geometry
    assert geometry['background'] == 'rgb(23, 78, 166)', geometry
    assert geometry['color'] == 'rgb(255, 255, 255)', geometry
    assert geometry['font'] == '700' and geometry['align'] == 'center' and geometry['border'] == 'solid', geometry
    expect(page.locator('#wbe-cell-4-2')).to_have_text('25.0%')
    expect(page.locator('.wbe-sheet-image')).to_have_count(1)
    assert page.locator('.wbe-sheet-image').evaluate('image => image.complete && image.naturalWidth === 24')
    page.locator('#workbookEditorAddress').fill('A1')
    page.locator('#workbookEditorGo').click()


def serve():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
    from django.conf import settings
    directory = Path(os.environ['DXF_BROWSER_TEST_DIR'])
    settings.DATABASES['default']['NAME'] = directory / 'test.sqlite3'
    settings.MEDIA_ROOT = directory / 'media'
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', verbosity=0)
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    User = get_user_model()
    editor = User.objects.create_user('empleado', password='PruebaDXF!')
    editor.groups.add(Group.objects.get(name='Editores de motores'))
    reader = User.objects.create_user('lector', password='PruebaDXF!')
    reader.groups.add(Group.objects.get(name='Lectores de planos'))
    User.objects.create_superuser('admin', password='PruebaDXF!')
    call_command('runserver', f"127.0.0.1:{os.environ['DXF_BROWSER_TEST_PORT']}", use_reloader=False)


def login(page, username='empleado', open_import=True):
    page.wait_for_function('appState.authReady')
    if page.locator('#loginForm').is_visible():
        page.locator('#usernameInput').fill(username)
        page.locator('#passwordInput').fill('PruebaDXF!')
        page.locator('#loginForm button[type=submit]').click()
    if open_import:
        page.locator('#btnGoToImportSection').click()


def check_login_without_cad_module(browser, url):
    """A failed viewer load must not break authentication or logout."""
    from playwright.sync_api import expect
    context = browser.new_context()
    errors = []
    try:
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/plan-workflow.js*', lambda route: route.abort())
        page.goto(url)
        page.wait_for_function('appState.authReady')
        assert page.evaluate('window.planWorkflow === undefined')
        # A preceding partial initialization can leave a previous account in
        # state: this exercises the precise failing planWorkflow.close path.
        page.evaluate('appState.user = { id: -1 }')
        login(page, open_import=False)
        expect(page.locator('#viewMainMenu')).to_be_visible()
        expect(page.locator('#loginAlert')).to_be_hidden()
        expect(page.locator('#appAuthError')).to_contain_text('visor de planos')
        assert page.request.get(url + 'api/auth/session/').json()['user']['username'] == 'empleado'
        page.reload()
        page.wait_for_function('appState.authReady')
        expect(page.locator('#viewMainMenu')).to_be_visible()
        page.locator('#userMenuBtn').click()
        page.locator('#menuItemLogout').click()
        expect(page.locator('#loginForm')).to_be_visible()
        assert page.request.get(url + 'api/auth/session/').json()['user'] is None
        login(page, 'lector', open_import=False)
        expect(page.locator('#viewMainMenu')).to_be_visible()
        assert not errors, errors
        print('PASS: login, session restoration, logout and account changes survive an unavailable CAD module', flush=True)
    finally:
        context.close()


def check_browser(url, directory):
    from playwright.sync_api import sync_playwright, expect
    from scripts.generate_demo import generate
    generate(directory)
    format_workbook_fixture(directory / 'motor_01.xlsx')
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True, device_scale_factor=1)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            page.wait_for_function('appState.authReady')
            page.locator('#usernameInput').fill('usuario_ficticio')
            page.locator('#passwordInput').fill('PruebaDXF!')
            page.locator('#btnLogin').click()
            expect(page.locator('#loginAlert')).to_contain_text('incorrectos')
            expect(page.locator('#viewMainMenu')).to_be_hidden()
            login(page)
            expect(page.locator('#btnAdminister')).to_be_hidden()
            page.locator('#btnAssignExcelCorner').click()
            expect(page.locator('#errorNoPlanText')).to_contain_text('Importe un plano DXF')
            page.locator('#autocadFileInput').set_input_files({
                'name': 'rechazado.pdf', 'mimeType': 'application/pdf', 'buffer': b'not a dxf',
            })
            expect(page.locator('#errorNoPlanText')).to_contain_text('Solo se admiten')
            page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            expect(page.locator('#dxfViewport svg')).to_be_visible()
            expect(page.locator('.motor-target')).to_have_count(3)
            label_box = page.locator('.motor-label').first.bounding_box()
            assert 4 < label_box['width'] < 20 and 8 < label_box['height'] < 24, label_box
            expect(page.locator('#errorNoPlanAlert')).to_be_hidden()
            print('PASS: DXF-only import and real SVG with three motors', flush=True)

            page.locator('#btnAssignExcelCorner').click()
            expect(page.locator('#errorNoPlanText')).to_contain_text('Toque un motor')
            page.locator('#errorNoPlanAlert').click()
            svg = page.locator('#dxfViewport svg')
            initial = svg.get_attribute('viewBox')
            page.locator('#btnZoomIn').click()
            assert float(svg.get_attribute('viewBox').split()[2]) < float(initial.split()[2])
            page.locator('#btnFitPlan').click()
            assert svg.get_attribute('viewBox') == initial
            page.locator('#dxfViewport').scroll_into_view_if_needed()
            rect = page.locator('#dxfViewport').bounding_box()
            start = (rect['x'] + rect['width'] * .5, rect['y'] + rect['height'] * .5)
            page.mouse.move(*start)
            page.mouse.down()
            page.mouse.move(start[0] + 40, start[1] + 30, steps=8)
            page.mouse.up()
            assert svg.get_attribute('viewBox') != initial
            expect(page.locator('#selectedMotorCard')).to_be_hidden()
            page.locator('#btnFitPlan').click()
            print('PASS: zoom, fit and mouse pan without accidental selection', flush=True)

            # Real touch gestures through Chrome's input protocol.
            cdp = context.new_cdp_session(page)
            page.locator('#dxfViewport').scroll_into_view_if_needed()
            rect = page.locator('#dxfViewport').bounding_box()
            cx, cy = rect['x'] + rect['width'] * .5, rect['y'] + rect['height'] * .5
            def touch(kind, points):
                cdp.send('Input.dispatchTouchEvent', {
                    'type': kind, 'touchPoints': [{'x': x, 'y': y, 'id': index} for index, (x, y) in enumerate(points)],
                })
            touch('touchStart', [(cx, cy)])
            touch('touchMove', [(cx + 30, cy + 20)])
            touch('touchEnd', [])
            assert svg.get_attribute('viewBox') != initial
            page.locator('#btnFitPlan').click()
            page.locator('#dxfViewport').scroll_into_view_if_needed()
            rect = page.locator('#dxfViewport').bounding_box()
            cx, cy = rect['x'] + rect['width'] * .5, rect['y'] + rect['height'] * .5
            touch('touchStart', [(cx - 30, cy), (cx + 30, cy)])
            touch('touchMove', [(cx - 65, cy), (cx + 65, cy)])
            touch('touchEnd', [])
            assert float(svg.get_attribute('viewBox').split()[2]) < float(initial.split()[2])
            page.locator('#btnFitPlan').click()
            print('PASS: touch pan and two-finger pinch zoom', flush=True)

            # Select the symbol on the drawing, then attach a real workbook.
            page.locator('.motor-pin').first.tap()
            expect(page.locator('#selectedMotorCard')).to_be_visible()
            expect(page.locator('#selectedMotorName')).to_have_text('MOTOR_BOMBA_01')
            expect(page.locator('#selectedMotorCoordinates')).to_contain_text('X: 30')
            expect(page.locator('#selectedMotorCoordinates')).to_contain_text('Y: 30')
            with page.expect_file_chooser() as chooser:
                page.locator('#btnAssignMotorExcel').click()
            chooser.value.set_files(directory / 'motor_01.xlsx')
            expect(page.locator('#excelFileName')).to_have_text('motor_01.xlsx')
            page.locator('#btnPreviewExcel').click()
            expect(page.locator('#workbookEditorSave')).to_be_visible()
            check_workbook_layout(page)
            page.locator('#workbookEditorAddress').fill('C2')
            page.locator('#workbookEditorGo').click()
            expect(page.locator('#workbookEditorFormula')).to_have_value('Operativo')
            page.locator('#workbookEditorFormula').fill('Revisado')
            page.locator('#workbookEditorFormula').press('Enter')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Cambios de Excel guardados')
            check_workbook_layout(page)
            page.locator('#workbookEditorClose').click()
            with page.expect_download() as download_info:
                page.locator('#excelFileName').click()
            assert download_info.value.suggested_filename == 'motor_01.xlsx'
            page.locator('#motorList button').nth(1).click()
            expect(page.locator('#excelLinkedSection')).to_be_visible()
            with page.expect_file_chooser() as chooser:
                page.locator('#btnAssignMotorExcel').click()
            chooser.value.set_files(directory / 'motor_02.xlsx')
            expect(page.locator('#excelFileName')).to_have_text('motor_02.xlsx')
            page.locator('#motorList button').first.click()
            expect(page.locator('#excelFileName')).to_have_text('motor_01.xlsx')
            with page.expect_file_chooser() as chooser:
                page.locator('#btnAssignMotorExcel').click()
            chooser.value.set_files({'name': 'falso.xlsx', 'mimeType': 'application/octet-stream', 'buffer': b'bad workbook'})
            expect(page.locator('#errorNoPlanText')).to_contain_text('no es un libro Excel')
            expect(page.locator('#excelFileName')).to_have_text('motor_01.xlsx')
            print('PASS: motor tapping, independent XLSX assignments, download and invalid content rejection', flush=True)

            page.locator('#btnCadSavePlan').click()
            expect(page.locator('#cadProjectSaveStatus')).to_have_text('Plano guardado')
            page.reload()
            login(page)
            expect(page.locator('.motor-target')).to_have_count(3)
            expect(page.locator('.motor-target.has-excel')).to_have_count(3)
            page.locator('#motorList button').first.click()
            expect(page.locator('#excelFileName')).to_have_text('motor_01.xlsx')
            page.locator('#motorList button').nth(1).click()
            expect(page.locator('#excelFileName')).to_have_text('motor_02.xlsx')
            page.locator('#btnRemovePlan').click()
            expect(page.locator('#dxfViewerSection')).to_be_hidden()
            page.locator('#btnBackToMainMenu').click()
            page.locator('[data-bs-toggle=dropdown]').click()
            page.locator('#menuItemPlanos').click()
            page.locator('#savedPlansContainer button').first.click()
            expect(page.locator('.motor-target')).to_have_count(3)
            expect(page.locator('.motor-target.has-excel')).to_have_count(3)
            page.locator('#motorList button').first.click()
            expect(page.locator('#excelFileName')).to_have_text('motor_01.xlsx')
            page.locator('#dxfViewport').scroll_into_view_if_needed()
            page.screenshot(path=ROOT / '.test-artifacts' / 'dxf-mobile.png')
            page.set_viewport_size({'width': 1280, 'height': 1024})
            page.screenshot(path=ROOT / '.test-artifacts' / 'dxf-desktop.png')

            reader_context = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            reader_page = reader_context.new_page()
            reader_page.on('pageerror', lambda error: errors.append(str(error)))
            reader_page.goto(url)
            login(reader_page, 'lector', open_import=False)
            expect(reader_page.locator('#permissionSummary')).to_contain_text('lectura')
            expect(reader_page.locator('#btnAdminister')).to_be_hidden()
            reader_page.locator('#userMenuBtn').click()
            reader_page.locator('#menuItemPlanos').click()
            reader_page.locator('#savedPlansContainer button').first.click()
            expect(reader_page.locator('.motor-target')).to_have_count(3)
            reader_page.locator('#motorList button').first.click()
            expect(reader_page.locator('#btnAssignExcelCorner')).to_be_visible()
            expect(reader_page.locator('#btnAssignMotorExcel')).to_be_visible()
            expect(reader_page.locator('#btnRenameCadPlan')).to_be_hidden()
            expect(reader_page.locator('#btnDeleteCadPlan')).to_be_hidden()
            expect(reader_page.locator('#excelFileName')).to_have_text('motor_01.xlsx')
            reader_page.locator('#btnPreviewExcel').click()
            expect(reader_page.locator('#workbookEditorStatus')).to_contain_text('Solo lectura')
            expect(reader_page.locator('#workbookEditorSave')).to_be_hidden()
            check_workbook_layout(reader_page)
            reader_page.locator('#workbookEditorAddress').fill('C2')
            reader_page.locator('#workbookEditorGo').click()
            expect(reader_page.locator('#workbookEditorFormula')).to_have_value('Revisado')
            assert reader_page.locator('#workbookEditorFormula').get_attribute('readonly') is not None
            reader_page.locator('#workbookEditorViewport').focus()
            reader_page.keyboard.press('Delete')
            reader_page.keyboard.press('Enter')
            expect(reader_page.locator('#workbookEditorCellInput')).to_have_count(0)
            reader_page.locator('#workbookEditorClose').click()
            with reader_page.expect_download() as download_info:
                reader_page.locator('#excelFileName').click()
            assert download_info.value.suggested_filename == 'motor_01.xlsx'
            blocked = reader_page.evaluate('''async () => {
                const form = new FormData();
                form.append('file', new File(['bad workbook'], 'prohibido.xlsx'));
                const motor = appState.currentPlan.motors[0];
                const response = await fetch(motor.assign_url, {method: 'POST', body: form, headers: {'X-CSRFToken': csrfToken()}});
                return response.status;
            }''')
            assert blocked == 400  # Upload is authorized; the malformed XLSX is rejected.
            with reader_page.expect_file_chooser() as chooser:
                reader_page.locator('#btnAssignMotorExcel').click()
            chooser.value.set_files(directory / 'motor_02.xlsx')
            expect(reader_page.locator('#excelFileName')).to_have_text('motor_02.xlsx')
            protected = reader_page.evaluate('''async () => {
                const plan=appState.currentPlan, headers={'Content-Type':'application/json','X-CSRFToken':csrfToken()};
                const rename=await fetch(plan.update_url,{method:'POST',headers,body:JSON.stringify({name:'No permitido'})});
                const remove=await fetch(plan.delete_url,{method:'POST',headers,body:'{}'});
                return [rename.status,remove.status];
            }''')
            assert protected == [403, 403], protected
            reader_page.locator('#selectedMotorCard').scroll_into_view_if_needed()
            reader_page.screenshot(path=ROOT / '.test-artifacts' / 'dxf-reader.png')
            reader_page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            expect(reader_page.locator('#planStatus')).to_contain_text('Plano listo')
            expect(reader_page.locator('.motor-target.has-excel')).to_have_count(3)
            expect(reader_page.locator('.workbook-editor-overlay')).to_be_hidden()
            print('PASS: reader views the original Excel layout and saved edits, imports DXF and assigns Excel; plan changes remain protected', flush=True)

            admin_context = browser.new_context()
            admin_page = admin_context.new_page()
            admin_page.on('pageerror', lambda error: errors.append(str(error)))
            admin_page.goto(url)
            login(admin_page, 'admin', open_import=False)
            expect(admin_page.locator('#btnAdminister')).to_be_visible()
            admin_page.locator('#btnAdminister').click()
            expect(admin_page).to_have_url(url + 'admin/')
            expect(admin_page.locator('a[href="/admin/auth/user/"]').first).to_be_visible()
            print('PASS: administrator button opens authenticated Django user administration', flush=True)

            page.locator('#btnBackToMainMenu').click()
            page.locator('#userMenuBtn').click()
            page.locator('#menuItemLogout').click()
            expect(page.locator('#loginForm')).to_be_visible()
            assert page.request.get(url + 'api/plans/').status == 401
            print('PASS: logout invalidates the real server session', flush=True)
            assert not errors, errors
            print('PASS: reload persistence, saved plan reopening and no JavaScript errors', flush=True)
            check_login_without_cad_module(browser, url)
        finally:
            browser.close()


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts, prefix='browser-') as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment.update({
            'DXF_BROWSER_TEST_DIR': temporary, 'DXF_BROWSER_TEST_PORT': str(port),
            'XDG_CACHE_HOME': str(artifacts / 'cache'), 'PYTHONUNBUFFERED': '1',
        })
        with (artifacts / 'browser-server.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen(
                [sys.executable, __file__, '--serve'], cwd=ROOT, env=environment,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            try:
                url = f'http://127.0.0.1:{port}/'
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Test server failed; see .test-artifacts/browser-server.log')
                    try:
                        with urlopen(url, timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Test server did not start.')
                check_browser(url, Path(temporary))
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve()
    else:
        main()
