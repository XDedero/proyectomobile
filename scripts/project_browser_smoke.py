"""Real full-screen project workflow against temporary DB/media, in Chrome."""

from io import BytesIO
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def menu(page, action):
    page.locator('#btnPlanFullscreenMenu').click()
    page.locator(f'#fullscreenPlanMenu [data-project-action="{action}"]').click()


def check_browser(url, directory):
    from playwright.sync_api import sync_playwright, expect
    from scripts.browser_smoke import login
    from scripts.generate_demo import generate
    from scripts.generate_pdf_demo import generate as generate_pdf
    generate(directory)
    generate_pdf(directory)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, 'admin', open_import=False)
            expect(page.locator('#btnSavedExcel')).to_have_count(0)
            page.locator('#btnGoToPdfSection').click()
            with page.expect_response('**/api/pdf/plans/import/') as imported:
                page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            pdf = imported.value.json()
            expect(page.locator('#pdfViewport .pdf-plan-canvas')).to_be_visible()
            page.locator('#btnPdfFullscreen').click()
            expect(page.locator('#planFullscreen')).to_be_visible()
            page.wait_for_function("document.querySelector('#fullscreenPlanViewport #pdfViewport') !== null")
            bounds = page.locator('#planFullscreen').bounding_box()
            assert bounds['x'] == 0 and bounds['y'] == 0 and bounds['width'] >= 400 and bounds['height'] >= 900, bounds
            menu(page, 'add')
            stage = page.locator('#pdfViewport .pdf-plan-page')
            box = stage.bounding_box()
            page.touchscreen.tap(box['x'] + box['width']*.3, box['y'] + box['height']*.35)
            expect(page.locator('#workbookEditorSheet')).to_have_value('1')
            page.locator('#workbookEditorClose').click()
            page.locator('#fullscreenAssignExcel').click()
            expect(page.locator('#pdfMarkerDialog')).to_be_visible()
            with page.expect_response('**/api/pdf/markers/*/workbook/') as assigned:
                page.locator('#pdfExcelFileInput').set_input_files(directory / 'equipos_pdf.xlsx')
            marker = assigned.value.json()['marker']
            expect(page.locator('#workbookEditorSheet')).to_be_visible()
            page.locator('#workbookEditorAddress').fill('B2')
            page.locator('#workbookEditorGo').click()
            page.locator('#workbookEditorFormula').fill('Motor actualizado en pantalla completa')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Cambios de Excel guardados')
            with page.expect_download() as motor_csv:
                page.locator('#workbookEditorCsv').click()
            assert 'Motor actualizado en pantalla completa' in Path(motor_csv.value.path()).read_text(encoding='utf-8-sig')
            page.locator('#workbookEditorClose').click()
            menu(page, 'move')
            button = page.locator(f'[data-pdf-marker-id="{marker["id"]}"]')
            box = button.bounding_box()
            start = (box['x'] + box['width']/2, box['y'] + box['height']/2)
            with page.expect_response(f'**/api/pdf/markers/{marker["id"]}/') as moved_response:
                page.mouse.move(*start)
                page.mouse.down()
                page.mouse.move(start[0]+30, start[1]+18, steps=8)
                page.mouse.up()
            moved = moved_response.value.json()['marker']
            assert abs(moved['x']-marker['x']) > .01
            print('PASS: mobile fullscreen fills the device; floating menu adds, assigns, edits and moves a PDF motor button', flush=True)

            menu(page, 'assign-listing')
            expect(page.locator('#btnCreatePlanListing')).to_be_visible()
            with page.expect_response('**/api/projects/pdf/*/listing/') as listing_response:
                page.locator('#btnCreatePlanListing').click()
            listing = listing_response.value.json()['listing']
            expect(page.locator('#workbookEditorSheet')).to_have_value('Listado')
            expect(page.locator('.wbe-sheet-image')).to_have_count(1)
            page.locator('#workbookEditorAddColumn').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Columna agregada')
            page.locator('#workbookEditorFormula').fill('Columna adicional')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Cambios de Excel guardados')
            page.locator('#workbookEditorAddRow').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Fila agregada')
            page.locator('#workbookEditorFormula').fill('Motor adicional')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Cambios de Excel guardados')
            with page.expect_download() as csv_download:
                page.locator('#workbookEditorCsv').click()
            assert csv_download.value.suggested_filename.endswith('.csv')
            csv_content = Path(csv_download.value.path()).read_text(encoding='utf-8-sig')
            assert 'Columna adicional' in csv_content
            assert 'Motor adicional' in csv_content
            with page.expect_download() as xlsx_download:
                page.locator('#workbookEditorDownload').click()
            assert xlsx_download.value.suggested_filename.endswith('.xlsx')
            page.locator('#workbookEditorClose').click()
            with page.expect_download() as pdf_export:
                menu(page, 'export')
            pdf_bundle = Path(pdf_export.value.path()).read_bytes()
            assert pdf_export.value.suggested_filename.endswith('.planproj')
            with ZipFile(BytesIO(pdf_bundle)) as archive:
                pdf_manifest = json.loads(archive.read('manifest.json'))
                assert pdf_manifest['listing'] and pdf_manifest['markers'][0]['workbook']
            page.screenshot(path=str(ROOT / '.test-artifacts/project-pdf-fullscreen.png'))
            page.locator('#btnExitPlanFullscreen').click()
            expect(page.locator('#planFullscreen')).to_be_hidden()
            expect(page.locator('#pdfPlanSection #pdfViewport')).to_be_visible()
            print('PASS: plan-level template matches the list, supports columns and exports CSV/XLSX; PDF archive contains every attachment', flush=True)

            page.locator('#btnPdfBack').click()
            page.locator('#btnGoToImportSection').click()
            with page.expect_response('**/api/plans/import/') as cad_response:
                page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            cad = cad_response.value.json()
            expect(page.locator('#dxfViewport svg')).to_be_visible()
            page.locator('#btnCadFullscreen').click()
            menu(page, 'add')
            point = page.evaluate('''() => {
                const svg=document.querySelector('#dxfViewport svg'), box=svg.viewBox.baseVal;
                const p=svg.createSVGPoint(); p.x=box.x+box.width*.55; p.y=box.y+box.height*.45;
                const screen=p.matrixTransform(svg.getScreenCTM()); return {x:screen.x,y:screen.y};
            }''')
            with page.expect_response('**/api/plans/*/markers/') as cad_marker_response:
                page.touchscreen.tap(point['x'], point['y'])
            manual = cad_marker_response.value.json()['motor']
            expect(page.locator('.motor-target')).to_have_count(4)
            with page.expect_file_chooser() as chooser:
                page.locator('#fullscreenAssignExcel').click()
            with page.expect_response(f'**/api/motors/{manual["id"]}/excel/'):
                chooser.value.set_files(directory / 'motor_01.xlsx')
            menu(page, 'move')
            pin = page.locator(f'[data-motor-id="{manual["id"]}"] .motor-pin')
            box = pin.bounding_box()
            start = (box['x']+box['width']/2, box['y']+box['height']/2)
            with page.expect_response(f'**/api/motors/{manual["id"]}/position/') as cad_moved:
                page.mouse.move(*start)
                page.mouse.down()
                page.mouse.move(start[0]+22, start[1]+15, steps=8)
                page.mouse.up()
            assert cad_moved.value.status == 200, cad_moved.value.text()
            moved_cad = cad_moved.value.json()['motor']
            menu(page, 'assign-listing')
            page.locator('#btnCreatePlanListing').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Listado')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Sin cambios pendientes')
            page.locator('#workbookEditorClose').click()
            with page.expect_download() as cad_export:
                menu(page, 'export')
            cad_bundle = Path(cad_export.value.path()).read_bytes()
            page.screenshot(path=str(ROOT / '.test-artifacts/project-cad-fullscreen.png'))
            page.locator('#btnExitPlanFullscreen').click()
            page.locator('#btnBackToMainMenu').click()
            page.locator('#btnSavedPlans').click()
            expect(page.locator('#savedPlansContainer .project-kind-badge')).to_have_count(2)
            assert set(page.locator('#savedPlansContainer .project-kind-badge').all_text_contents()) == {'PDF', 'DXF'}
            page.locator('#savedPlansContainer button').filter(has_text=cad['name']).click()
            expect(page.locator('#viewImportAutoCAD')).to_be_visible()
            expect(page.locator('.motor-target')).to_have_count(4)
            print('PASS: DXF fullscreen placement/movement and assigned files persist; one saved library opens both PDF and DXF with format badges', flush=True)

            page.locator('#btnBackToMainMenu').click()
            page.locator('#btnGoToPdfSection').click()
            for kind, bundle in [('dxf', cad_bundle), ('pdf', pdf_bundle)]:
                with page.expect_response('**/api/projects/import/') as restored_response:
                    page.locator('#projectFileInput').set_input_files({'name': f'{kind}.planproj', 'mimeType': 'application/zip', 'buffer': bundle})
                response = restored_response.value
                assert response.status == 201, response.text()
                restored = response.json()['plan']
                assert restored['listing'] is not None
                if kind == 'dxf':
                    expect(page.locator('#viewImportAutoCAD')).to_be_visible()
                    expect(page.locator('.motor-target')).to_have_count(4)
                    assert any(motor['excel'] for motor in restored['motors'])
                    restored_manual = next(motor for motor in restored['motors'] if motor['entity_key'] == moved_cad['entity_key'])
                    assert abs(restored_manual['x']-moved_cad['x']) < 1e-8
                    assert abs(restored_manual['y']-moved_cad['y']) < 1e-8
                    page.locator('#btnBackToMainMenu').click()
                    page.locator('#btnGoToPdfSection').click()
                else:
                    expect(page.locator('#viewImportPDF')).to_be_visible()
                    assert abs(restored['markers'][0]['x']-moved['x']) < 1e-8
                    assert restored['markers'][0]['workbook']
                    listing_cells = page.request.get(url.rstrip('/') + restored['listing']['cells_url'] + '?sheet=Listado&row=7&col=9&rows=1&cols=1').json()['cells']
                    assert listing_cells[0]['value'] == 'Columna adicional'
            assert not errors, errors
            print('PASS: importing either complete project from the PDF section restores its source format, marker coordinates, motor sheets and plan listing', flush=True)

            reader_context = browser.new_context(viewport={'width': 1280, 'height': 900})
            reader = reader_context.new_page()
            reader.on('pageerror', lambda error: errors.append(str(error)))
            reader.goto(url)
            login(reader, 'lector', open_import=False)
            reader.locator('#btnSavedPlans').click()
            reader.locator('#savedPlansContainer button').filter(has_text=pdf['name']).first.click()
            expect(reader.locator('#pdfViewport canvas')).to_be_visible()
            reader.locator('#btnPdfFullscreen').click()
            reader.locator('#btnPlanFullscreenMenu').click()
            expect(reader.locator('#fullscreenMoveMarker')).to_be_hidden()
            reader.screenshot(path=str(ROOT / '.test-artifacts/project-desktop-menu.png'))
            reader.locator('#fullscreenPlanMenu [data-project-action="explore"]').click()
            reader.locator(f'[data-pdf-marker-id="{marker["id"]}"]').click()
            expect(reader.locator('#workbookEditorSave')).to_be_hidden()
            expect(reader.locator('#workbookEditorAddColumn')).to_be_hidden()
            with reader.expect_download() as reader_csv:
                reader.locator('#workbookEditorCsv').click()
            assert reader_csv.value.suggested_filename.endswith('.csv')
            assert 'Motor actualizado en pantalla completa' in Path(reader_csv.value.path()).read_text(encoding='utf-8-sig')
            reader.locator('#workbookEditorClose').click()
            reader.locator('#btnExitPlanFullscreen').click()
            reader_context.close()
            assert not errors, errors
            print('PASS: desktop fullscreen menu honors modification rights; readers export motor CSV while cells and movement remain protected', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
