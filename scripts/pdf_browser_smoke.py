r"""Real Chrome test of PDF markers and complete editable XLSX windows.

Run: .venv\Scripts\python.exe scripts\pdf_browser_smoke.py
Uses an isolated database, media directory, an Excel editor and a reader.
"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def serve():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
    from django.conf import settings
    directory = Path(os.environ['PDF_BROWSER_TEST_DIR'])
    settings.DATABASES['default']['NAME'] = directory / 'test.sqlite3'
    settings.MEDIA_ROOT = directory / 'media'
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    call_command('migrate', verbosity=0)
    User = get_user_model()
    editor = User.objects.create_user('pdf_usuario', password='PruebaPDF!')
    editor.user_permissions.add(*Permission.objects.filter(
        content_type__app_label='mobileApp', codename__in=['change_workbookdocument', 'change_pdfplan'],
    ))
    User.objects.create_user('pdf_otro', password='PruebaPDF!')
    call_command('runserver', f"127.0.0.1:{os.environ['PDF_BROWSER_TEST_PORT']}", use_reloader=False)


def login(page, username='pdf_usuario'):
    from playwright.sync_api import expect
    page.wait_for_function('appState.authReady')
    if page.locator('#loginForm').is_visible():
        page.locator('#usernameInput').fill(username)
        page.locator('#passwordInput').fill('PruebaPDF!')
        page.locator('#btnLogin').click()
    expect(page.locator('#btnGoToPdfSection')).to_be_visible()
    page.locator('#btnGoToPdfSection').click()
    expect(page.locator('#viewImportPDF')).to_be_visible()


def detail(page, url):
    response = page.request.get(urljoin(page.url, url))
    assert response.status == 200, response.text()
    return response.json()


def place_marker(page, x, y):
    from playwright.sync_api import expect
    page.locator('#btnPdfAddMarker').click()
    stage = page.locator('#pdfViewport .pdf-plan-page')
    stage.scroll_into_view_if_needed()
    box = stage.bounding_box()
    page.touchscreen.tap(box['x'] + box['width'] * x, box['y'] + box['height'] * y)
    expect(page.locator('#workbookEditorSheet')).to_have_value('1')
    page.locator('#workbookEditorClose').click()
    page.locator('#btnPdfConfigureMarker').click()
    expect(page.locator('#pdfMarkerDialog')).to_be_visible()


def edit_cell(page, address, expected, replacement):
    from playwright.sync_api import expect
    page.locator('#workbookEditorAddress').fill(address)
    page.locator('#workbookEditorGo').click()
    expect(page.locator('#workbookEditorFormula')).to_have_value(expected)
    page.locator('#workbookEditorFormula').fill(replacement)
    page.locator('#workbookEditorFormula').press('Enter')
    page.locator('#workbookEditorSave').click()
    expect(page.locator('#workbookEditorStatus')).to_contain_text('Plano guardado')


def run_checks(url, directory):
    from openpyxl import load_workbook
    from playwright.sync_api import sync_playwright, expect
    from scripts.generate_pdf_demo import generate
    from scripts.browser_smoke import format_workbook_fixture, check_workbook_layout
    generate(directory)
    format_workbook_fixture(directory / 'equipos_pdf.xlsx')
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True, device_scale_factor=1)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page)
            assert page.request.get(url + 'api/plans/').status == 200
            page.locator('#pdfFileInput').set_input_files({'name': 'falso.pdf', 'mimeType': 'application/pdf', 'buffer': b'bad pdf'})
            expect(page.locator('#pdfError')).to_be_visible()
            page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            expect(page.locator('#pdfViewport canvas')).to_be_visible(timeout=20000)
            expect(page.locator('#pdfViewport')).to_have_attribute('data-pdf-page', '1')
            assert page.locator('#pdfViewport canvas').evaluate('''canvas => {
                const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
                let nonwhite=0; for(let i=0;i<data.length;i+=4) if(data[i]<230||data[i+1]<230||data[i+2]<230) nonwhite++;
                return nonwhite>100;
            }''')
            plan = detail(page, url + 'api/pdf/plans/')['plans'][0]
            plan = detail(page, plan['detail_url'])
            assert len(plan['pages']) == 2
            print('PASS: user without CAD permissions imports and renders a real two-page PDF; invalid PDF is rejected', flush=True)

            place_marker(page, .25, .35)
            page.locator('#pdfMarkerLabel').fill('Bomba PDF 01')
            page.locator('#btnPdfSaveMarkerLabel').click()
            expect(page.locator('#pdfMarkerDialogStatus')).to_contain_text('guardado')
            first = detail(page, plan['detail_url'])['markers'][0]
            assert first['workbook'] is not None
            assert abs(first['x'] - .25) < .02 and abs(first['y'] - .35) < .02
            with page.expect_file_chooser() as chooser:
                page.locator('#btnPdfAssignDevice').click()
            chooser.value.set_files(directory / 'equipos_pdf.xlsx')
            expect(page.locator('#workbookEditorSave')).to_be_visible()
            expect(page.locator('#workbookEditorFormula')).to_have_value('Equipo')
            check_workbook_layout(page)
            expect(page.locator('#workbookEditorCalculationNotice')).to_be_visible()
            expect(page.locator('#workbookEditorCalculationNotice')).to_contain_text('no se recalculan')
            edit_cell(page, 'AE350', 'Dato lejano de la hoja completa', 'Actualizado desde PDF')
            expect(page.locator('#workbookEditorCalculationNotice')).to_be_visible()
            page.locator('#workbookEditorAddRow').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Fila agregada')
            page.locator('#workbookEditorAddColumn').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Columna agregada')
            edit_cell(page, 'AF351', '', '123.5')
            page.locator('#workbookEditorSheet').select_option('Mantenimiento')
            expect(page.locator('#workbookEditorCalculationNotice')).to_be_hidden()
            edit_cell(page, 'B2', 'Inspeccion inicial', 'Inspeccion actualizada')
            with page.expect_download() as downloaded:
                page.locator('#workbookEditorDownload').click()
            downloaded.value.save_as(directory / 'descarga.xlsx')
            workbook = load_workbook(directory / 'descarga.xlsx')
            assert workbook['Equipos']['AE350'].value == 'Actualizado desde PDF'
            assert workbook['Equipos']['AF351'].value == 123.5
            assert workbook['Equipos']['D2'].value == '=B2*2'
            assert workbook['Equipos']['B2'].font.bold
            assert str(workbook['Equipos'].merged_cells) == 'A6:C6'
            assert workbook['Equipos'].row_dimensions[6].height == 36
            assert workbook['Equipos'].column_dimensions['A'].width == 15
            assert workbook['Equipos']['A6'].fill.fgColor.rgb == '00174EA6'
            assert len(workbook['Equipos']._images) == 1
            assert workbook['Mantenimiento']['B2'].value == 'Inspeccion actualizada'
            workbook.close()
            page.screenshot(path=ROOT / '.test-artifacts' / 'pdf-excel-mobile.png')
            page.locator('#workbookEditorClose').click()
            expect(page.locator('#workbookEditorSave')).to_be_hidden()
            print('PASS: marker starts with a blank motor form; device Excel and all-sheet edits/save/download work', flush=True)

            page.locator('#btnPdfExplore').click()
            page.locator('#btnPdfMoveMarker').click()
            marker = page.locator(f'#pdfViewport [data-pdf-marker-id="{first["id"]}"]')
            marker.scroll_into_view_if_needed()
            box = marker.bounding_box()
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith(first['update_url'])) as moved:
                page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                page.mouse.down()
                page.mouse.move(box['x'] + box['width'] / 2 + 40, box['y'] + box['height'] / 2 + 25, steps=8)
                page.mouse.up()
            assert moved.value.status == 200
            first = detail(page, plan['detail_url'])['markers'][0]
            assert first['x'] > .25 and first['y'] > .35
            page.locator('#btnPdfExplore').click()
            for _ in range(3):
                page.locator('#btnPdfZoomIn').click()
            assert float(page.locator('#pdfViewport').get_attribute('data-pdf-zoom')) > 2
            alignment = marker.evaluate('''button => {
                const stage=button.closest('.pdf-plan-page').getBoundingClientRect(), b=button.getBoundingClientRect();
                return {x:(b.x+b.width/2-stage.x)/stage.width,y:(b.y+b.height/2-stage.y)/stage.height};
            }''')
            assert abs(alignment['x'] - first['x']) < .01 and abs(alignment['y'] - first['y']) < .01
            viewport = page.locator('#pdfViewport')
            viewport.scroll_into_view_if_needed()
            before = viewport.evaluate('element => [element.scrollLeft,element.scrollTop]')
            box = viewport.bounding_box()
            x, y = box['x'] + box['width'] * .8, box['y'] + box['height'] * .8
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x - 55, y - 35, steps=7)
            page.mouse.up()
            after = viewport.evaluate('element => [element.scrollLeft,element.scrollTop]')
            assert before != after
            page.locator('#btnPdfFit').click()
            print('PASS: moving and zooming preserve marker alignment; dragging pans the PDF', flush=True)

            page.locator('#pdfPageSelect').select_option('2')
            expect(viewport).to_have_attribute('data-pdf-page', '2')
            expect(page.locator('#pdfViewport [data-pdf-marker-id]')).to_have_count(0)
            place_marker(page, .65, .6)
            stored = detail(page, plan['detail_url'])['markers'][0]['workbook']
            registered = page.evaluate('''async (doc) => {
                const response = await fetch('/api/motor-library/register/', {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken()}, body:JSON.stringify({workbook_id:doc.id,revision:doc.revision,name:'Bomba PDF registrada'})});
                if (!response.ok) throw new Error(await response.text());
                return (await response.json()).motor;
            }''', stored)
            page.locator('#pdfMarkerDialog .btn-close').click()
            page.locator('#btnPdfConfigureMarker').click()
            expect(page.locator(f'#pdfWorkbookLibrary option[value="{registered["id"]}"]')).to_have_count(1)
            page.locator('#pdfWorkbookLibrary').select_option(str(registered['id']))
            page.locator('#btnPdfAssignLibrary').click()
            expect(page.locator('#workbookEditorSave')).to_be_visible()
            page.locator('#workbookEditorClose').click()
            latest = detail(page, plan['detail_url'])
            second = latest['markers'][1]
            assert second['page'] == 2 and second['workbook']['id'] != stored['id']
            page.locator('#pdfViewport').scroll_into_view_if_needed()
            page.screenshot(path=ROOT / '.test-artifacts' / 'pdf-plan-mobile.png')
            print('PASS: second-page marker reuses an existing Excel from the database library', flush=True)

            page.reload()
            login(page)
            page.locator('#btnPdfSaved').click()
            page.locator('#savedPlansContainer button').first.click()
            expect(page.locator('#pdfViewport canvas')).to_be_visible()
            page.locator('#pdfPageSelect').select_option('1')
            expect(page.locator(f'#pdfViewport [data-pdf-marker-id="{first["id"]}"]')).to_be_visible()
            page.locator(f'#pdfViewport [data-pdf-marker-id="{first["id"]}"]').tap()
            expect(page.locator('#workbookEditorSave')).to_be_visible()
            page.locator('#workbookEditorAddress').fill('AE350')
            page.locator('#workbookEditorGo').click()
            expect(page.locator('#workbookEditorFormula')).to_have_value('Actualizado desde PDF')
            check_workbook_layout(page)
            page.locator('#workbookEditorClose').click()
            print('PASS: PDF, page-specific buttons, coordinates and workbook edits persist after reload/reopening', flush=True)
            page.locator('#btnPdfBack').click()
            other_context = browser.new_context(viewport={'width': 1280, 'height': 900}, has_touch=True)
            other = other_context.new_page()
            other.on('pageerror', lambda error: errors.append(str(error)))
            other.goto(url)
            login(other, 'pdf_otro')
            other.locator('#btnPdfSaved').click()
            other.locator('#savedPlansContainer button').first.click()
            expect(other.locator('#pdfViewport canvas')).to_be_visible()
            other.locator(f'#pdfViewport [data-pdf-marker-id="{first["id"]}"]').click()
            expect(other.locator('#workbookEditorStatus')).to_contain_text('Solo lectura')
            expect(other.locator('#workbookEditorSave')).to_be_hidden()
            check_workbook_layout(other)
            other.locator('#workbookEditorAddress').fill('B2')
            other.locator('#workbookEditorGo').click()
            expect(other.locator('#workbookEditorFormula')).to_have_value('15')
            assert other.locator('#workbookEditorFormula').get_attribute('readonly') is not None
            other.locator('#workbookEditorClose').click()
            metadata = detail(other, stored['metadata_url'])
            cells = detail(other, metadata['cells_url'] + '?sheet=Equipos&row=2&col=2&rows=1&cols=1')
            assert cells['cells'][0]['value'] == 15
            blocked = other.evaluate('''async metadata => {
                const response=await fetch(metadata.save_url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken()},body:JSON.stringify({revision:metadata.revision,edits:[{sheet:'Equipos',row:2,col:2,value:22}]})});
                return response.status;
            }''', metadata)
            assert blocked == 403
            place_marker(other, .7, .7)
            other.locator('#pdfWorkbookLibrary').select_option(str(registered['id']))
            other.locator('#btnPdfAssignLibrary').click()
            expect(other.locator('#workbookEditorStatus')).to_contain_text('Solo lectura')
            expect(other.locator('#workbookEditorSave')).to_be_hidden()
            other.locator('#workbookEditorClose').click()
            assert len(detail(other, plan['detail_url'])['markers']) == 3
            anonymous = browser.new_context()
            assert anonymous.request.get(url + 'api/pdf/plans/').status == 401
            assert anonymous.request.get(urljoin(url, stored['metadata_url'])).status == 401
            assert not errors, errors
            print('PASS: reader can add buttons and assign saved Excel; formatted workbook is read-only and server rejects content edits; anonymous requests are blocked', flush=True)
        except Exception:
            if 'page' in locals():
                page.screenshot(path=ROOT / '.test-artifacts' / 'pdf-failure.png')
            if errors:
                print('JavaScript errors:', errors, flush=True)
            raise
        finally:
            browser.close()


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts, prefix='pdf-browser-') as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment.update({
            'PDF_BROWSER_TEST_DIR': temporary, 'PDF_BROWSER_TEST_PORT': str(port),
            'XDG_CACHE_HOME': str(artifacts / 'cache'), 'PYTHONUNBUFFERED': '1',
        })
        with (artifacts / 'pdf-browser-server.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen(
                [sys.executable, __file__, '--serve'], cwd=ROOT, env=environment,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            try:
                url = f'http://127.0.0.1:{port}/'
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError('Test server failed; see .test-artifacts/pdf-browser-server.log')
                    try:
                        with urlopen(url, timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Test server did not start.')
                run_checks(url, Path(temporary))
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve()
    else:
        main()
