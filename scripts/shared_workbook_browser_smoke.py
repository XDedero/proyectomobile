"""Exercise the shared workbook workflow and listing ownership on a phone viewport."""
from io import BytesIO
import json
from pathlib import Path
import sys
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from openpyxl import Workbook, load_workbook
    from playwright.sync_api import expect, sync_playwright
    from scripts.browser_smoke import login
    from scripts.generate_pdf_demo import generate
    from scripts.generate_demo import generate as generate_dxf
    generate(directory)
    generate_dxf(directory)
    source = Workbook()
    source.active.title = 'Inventario'
    source.active.append(['Número', 'Motor'])
    source.active.append([1, 'Compresor'])
    for title, value in [('Motor A', 'Compresor'), ('Motor B', 'Bomba')]:
        sheet = source.create_sheet(title)
        sheet.append(['Equipo', 'Tensión'])
        sheet.append([value, 380])
    source.save(directory / 'Equipos.xlsx')
    source.close()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, 'admin', open_import=False)
            page.locator('#btnGoToPdfSection').click()
            with page.expect_response('**/api/pdf/plans/import/') as response:
                page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            plan = response.value.json()
            expect(page.locator('#pdfViewport canvas')).to_be_visible()
            page.locator('#btnPdfWorkbook').click()
            expect(page.locator('#btnPlanExcelImport')).to_be_enabled()
            page.locator('#planWorkbookInput').set_input_files(directory / 'Equipos.xlsx')
            expect(page.locator('#excelBookManager')).to_be_hidden()
            page.wait_for_function("projectWorkspace.active.plan.workbook_mode === 'imported'")

            def add_marker(x):
                page.locator('#btnPdfAddMarker').click()
                box = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
                page.touchscreen.tap(box['x'] + box['width'] * x, box['y'] + box['height'] * .4)
                expect(page.locator('#excelSheetAssignment')).to_be_visible()
                expect(page.locator('#btnAssignWorkbookSheet')).to_be_enabled()

            add_marker(.25)
            assert page.locator('#planMotorSheet option').all_text_contents() == ['Elija una hoja de motor', 'Motor A', 'Motor B']
            page.screenshot(path=str(ROOT / '.test-artifacts/shared-sheet-picker.png'), animations='disabled')
            page.locator('#planMotorSheet').select_option('Motor A')
            page.locator('#btnAssignWorkbookSheet').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Motor A')
            page.locator('#workbookEditorAddress').fill('B2')
            page.locator('#workbookEditorGo').click()
            page.locator('#workbookEditorFormula').fill('460')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_have_text('Cambios de Excel guardados.')
            page.locator('#workbookEditorSaveMotor').click()
            expect(page.locator('#motorRegisterName')).to_be_visible()
            page.locator('#motorRegisterName').fill('Compresor 460')
            page.locator('#btnConfirmRegisterMotor').click()
            expect(page.locator('#motorRegisterDialog')).to_be_hidden()
            page.locator('#workbookEditorClose').click()

            add_marker(.55)
            page.locator('#planMotorSheet').select_option('Motor A')
            page.locator('#btnAssignWorkbookSheet').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Motor A')
            page.locator('#workbookEditorAddress').fill('B2')
            page.locator('#workbookEditorGo').click()
            expect(page.locator('#workbookEditorFormula')).to_have_value('460')
            page.locator('#workbookEditorClose').click()
            add_marker(.75)
            page.locator('#btnAssignSavedMotorSheet').click()
            expect(page.locator('#savedMotorPicker')).to_be_visible()
            page.locator('#savedMotorPicker button').filter(has_text='Usar en botón seleccionado').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Motor A')
            page.locator('#workbookEditorClose').click()
            current = page.request.get(url.rstrip('/') + plan['detail_url']).json()
            assert {marker['sheet_name'] for marker in current['markers']} == {'Motor A'}
            assert len({marker['workbook']['id'] for marker in current['markers']}) == 1
            assert len(current['sheet_options']) == 3, current['sheet_options']
            print('PASS: imported first sheet becomes the listing; three distinct buttons share one motor sheet, including a saved motor', flush=True)

            page.locator('#btnPdfAssignListing').click()
            expect(page.locator('#excelListingManager')).to_be_visible()
            expect(page.locator('#btnAddPlanListing')).to_be_enabled()
            page.locator('#btnAddPlanListing').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Listado motores 2')
            assert page.locator('#workbookEditorSheet option').all_text_contents() == ['Inventario', 'Listado motores 2']
            page.locator('#workbookEditorListings').click()
            expect(page.locator('#excelListingManager')).to_be_visible()
            page.screenshot(path=str(ROOT / '.test-artifacts/shared-listings.png'), animations='disabled')
            page.evaluate('mobileApp.requestBack()')

            reader_context = browser.new_context(viewport={'width': 360, 'height': 740}, is_mobile=True, has_touch=True)
            reader = reader_context.new_page()
            reader.on('pageerror', lambda error: errors.append(str(error)))
            reader.goto(url)
            login(reader, 'lector', open_import=False)
            expect(reader.locator('#viewMainMenu')).to_be_visible()
            reader.evaluate('''async plan => { await pdfWorkflow.open(plan); }''', page.request.get(url.rstrip('/') + plan['detail_url']).json())
            reader.locator('#btnPdfAssignListing').click()
            expect(reader.locator('#btnAddPlanListing')).to_be_enabled()
            expect(reader.locator('#planListingsRows [data-listing-id]')).to_have_count(0)
            reader.locator('#btnAddPlanListing').click()
            expect(reader.locator('#workbookEditorSheet')).to_have_value('Listado motores 3')
            expect(reader.locator('#workbookEditorSave')).to_be_hidden()
            reader.locator('#workbookEditorListings').click()
            expect(reader.locator('#planListingsRows [data-listing-id]')).to_have_count(1)
            reader.once('dialog', lambda dialog: dialog.accept())
            reader.locator('#planListingsRows [data-listing-id]').click()
            expect(reader.locator('#planListingsRows [data-listing-id]')).to_have_count(0)
            assert reader.locator('#planListingsRows').inner_text().count('Original') == 1
            reader_context.close()
            print('PASS: additional listings appear as workbook sheets; a reader adds and deletes their own listing while the original remains protected', flush=True)

            with page.expect_download() as exported:
                page.locator('#btnPdfExportProject').click()
            bundle = Path(exported.value.path()).read_bytes()
            with ZipFile(BytesIO(bundle)) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                assert manifest['version'] == 3 and len(manifest['workbooks']) == 1
                workbook = load_workbook(BytesIO(archive.read('workbooks/w1.xlsx')))
                assert workbook.sheetnames == ['Inventario', 'Motor A', 'Motor B', 'Listado motores 2'], workbook.sheetnames
                assert workbook['Motor A']['B2'].value == 460
                workbook.close()
            print('PASS: project export contains one complete XLSX with its listings, motor data and shared button references; no JavaScript errors', flush=True)
            page.locator('#btnPdfBack').click()
            page.locator('#btnGoToImportSection').click()
            page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            expect(page.locator('.motor-target')).to_have_count(3)
            page.locator('#motorList button').first.click()
            page.locator('#btnPreviewExcel').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Motor 1')
            expect(page.locator('.wbe-sheet-chart')).to_have_count(2)
            page.locator('#workbookEditorClose').click()
            page.locator('#btnAssignExcelCorner').click()
            expect(page.locator('#excelSheetAssignment')).to_be_visible()
            page.locator('#btnAssignSavedMotorSheet').click()
            expect(page.locator('#savedMotorPicker')).to_be_visible()
            page.locator('#savedMotorPicker button').filter(has_text='Usar en botón seleccionado').click()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Compresor 460')
            page.locator('#workbookEditorClose').click()
            page.locator('#motorList button').nth(1).click()
            page.locator('#btnAssignExcelCorner').click()
            expect(page.locator('#btnAssignBlankSheet')).to_be_enabled()
            page.locator('#btnAssignBlankSheet').click()
            expect(page.locator('.wbe-sheet-chart')).to_have_count(2)
            page.screenshot(path=str(ROOT / '.test-artifacts/shared-dxf-template.png'), animations='disabled')
            assert not errors, errors
            print('PASS: DXF uses the same assignment picker, preserves blank form charts and incorporates saved motors into the drawing workbook', flush=True)
            from scripts.browser_smoke import check_login_without_cad_module
            check_login_without_cad_module(browser, url)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
