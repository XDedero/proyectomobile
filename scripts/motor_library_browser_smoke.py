"""Exercise named motor records and independent standard forms in real Chrome."""

from io import BytesIO
from pathlib import Path
import sys
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from openpyxl import load_workbook
    from playwright.sync_api import sync_playwright, expect
    from scripts.browser_smoke import login
    from scripts.generate_pdf_demo import generate
    from scripts.project_browser_smoke import menu

    generate(directory)
    errors = []

    def value(content, address='C18'):
        book = load_workbook(BytesIO(content))
        try:
            return book['1'][address].value
        finally:
            book.close()

    def edit(page, address, text):
        page.locator('#workbookEditorAddress').fill(address)
        page.locator('#workbookEditorGo').click()
        page.locator('#workbookEditorFormula').fill(text)
        page.locator('#workbookEditorFormula').press('Enter')

    def add(page, x):
        menu(page, 'add')
        box = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
        page.mouse.click(box['x'] + box['width']*x, box['y'] + box['height']*.35)
        expect(page.locator('#workbookEditorSheet')).to_have_value('1')
        expect(page.locator('.wbe-sheet-chart')).to_have_count(2)
        expect(page.locator('.wbe-sheet-image:not(.wbe-sheet-chart)')).to_have_count(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 1280, 'height': 960})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, 'admin', open_import=False)
            page.locator('#btnGoToPdfSection').click()
            with page.expect_response('**/api/pdf/plans/import/') as imported:
                page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            plan = imported.value.json()
            expect(page.locator('#pdfViewport canvas')).to_be_visible()
            page.locator('#btnPdfFullscreen').click()
            add(page, .25)
            first_id = page.evaluate('window.workbookEditor.metadata.id')
            page.locator('.wbe-grid-canvas').screenshot(path=ROOT / '.test-artifacts/motor-standard-blank.png')
            page.locator('#workbookEditorSaveMotor').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('vacía')
            expect(page.locator('#motorRegisterDialog')).to_be_hidden()
            edit(page, 'C18', 'WEG original')
            edit(page, 'C20', 'SERIE-001')
            page.locator('#workbookEditorSaveMotor').click()
            expect(page.locator('#motorRegisterDialog')).to_be_visible()
            page.locator('#motorRegisterName').fill('')
            page.locator('#btnConfirmRegisterMotor').click()
            expect(page.locator('#motorRegisterDialog')).to_be_visible()
            page.locator('#motorRegisterName').fill('Motor bomba principal')
            page.locator('#btnConfirmRegisterMotor').click()
            expect(page.locator('#motorRegisterDialog')).to_be_hidden()
            expect(page.locator('#workbookEditorSaveMotor')).to_be_disabled()
            expect(page.locator('#workbookEditorMotorRegistration')).to_contain_text('Motor bomba principal')
            records = page.request.get(url + 'api/motor-library/').json()['motors']
            assert len(records) == 1
            record = records[0]
            with page.expect_download() as exported:
                page.locator('#workbookEditorDownload').click()
            exported.value.save_as(directory / 'registered-export.xlsx')
            edit(page, 'C18', 'WEG copia del primer botón')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Cambios de Excel guardados')
            page.locator('#workbookEditorClose').click()
            print('PASS: blank standard form, required name, filled named registration and duplicate button lock', flush=True)

            add(page, .65)
            second_blank_id = page.evaluate('window.workbookEditor.metadata.id')
            assert second_blank_id != first_id
            page.locator('#workbookEditorClose').click()
            menu(page, 'saved-motors')
            expect(page.locator('#savedMotorPicker')).to_be_visible()
            page.locator('#savedMotorPicker .btn-success').click()
            expect(page.locator('#savedMotorPicker')).to_be_hidden()
            expect(page.locator('#workbookEditorSheet')).to_have_value('1')
            second_id = page.evaluate('window.workbookEditor.metadata.id')
            assert second_id not in (first_id, record['workbook']['id'])
            expect(page.locator('#workbookEditorSaveMotor')).to_be_disabled()
            edit(page, 'C18', 'Siemens copia del segundo botón')
            with page.expect_download() as exported:
                page.locator('#workbookEditorDownload').click()
            second_bytes = Path(exported.value.path()).read_bytes()
            assert value(second_bytes) == 'Siemens copia del segundo botón'
            with page.expect_download() as exported_csv:
                page.locator('#workbookEditorCsv').click()
            assert 'Siemens copia del segundo botón' in Path(exported_csv.value.path()).read_text(encoding='utf-8-sig')
            source = page.request.get(urljoin(url, record['workbook']['download_url'])).body()
            assert value(source) == 'WEG original'
            first = page.request.get(url + f'api/pdf/workbooks/{first_id}/metadata/').json()
            assert value(page.request.get(urljoin(url, first['download_url'])).body()) == 'WEG copia del primer botón'
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Sin cambios pendientes')
            page.locator('#workbookEditorClose').click()
            print('PASS: fullscreen saved motor picker creates independent XLSX/CSV data and leaves original intact', flush=True)

            page.locator('#btnExitPlanFullscreen').click()
            page.evaluate('switchView(viewMainMenu)')
            page.locator('#btnSavedMotors').click()
            expect(page.locator('#savedMotorsList .saved-motor-card')).to_have_count(1)
            page.locator('#savedMotorsSearch').fill('inexistente')
            expect(page.locator('#savedMotorsList .saved-motor-card')).to_have_count(0)
            page.locator('#savedMotorsSearch').fill('BOMBA')
            expect(page.locator('#savedMotorsList .saved-motor-card')).to_have_count(1)
            page.locator('#savedMotorsList .btn-outline-success').click()
            expect(page.locator('#workbookEditorFormula')).to_have_attribute('readonly', '')
            expect(page.locator('#workbookEditorSaveMotor')).to_be_disabled()
            page.locator('#workbookEditorClose').click()
            page.locator('#motorRecordFileInput').set_input_files(directory / 'registered-export.xlsx')
            expect(page.locator('#workbookEditorSaveMotor')).to_be_disabled()
            expect(page.locator('#workbookEditorMotorRegistration')).to_contain_text('Motor bomba principal')
            page.locator('#workbookEditorClose').click()
            print('PASS: searchable saved section, immutable original, device export reimport recognizes existing motor', flush=True)

            device_book = load_workbook(ROOT / 'mobileApp/data/motor-template.xlsx')
            device_book['1']['C18'] = 'ABB del dispositivo'
            device_book.save(directory / 'new-device.xlsx')
            device_book.close()
            mobile = browser.new_context(viewport={'width':412,'height':915}, is_mobile=True, has_touch=True)
            reader = mobile.new_page()
            reader.on('pageerror', lambda error: errors.append(str(error)))
            reader.goto(url)
            login(reader, 'lector', open_import=False)
            reader.locator('#btnSavedMotors').click()
            reader.locator('#motorRecordFileInput').set_input_files(directory / 'new-device.xlsx')
            expect(reader.locator('#workbookEditorFormula')).to_have_attribute('readonly', '')
            expect(reader.locator('#workbookEditorSaveMotor')).to_be_enabled()
            reader.locator('#workbookEditorSaveMotor').click()
            reader.locator('#motorRegisterName').fill('Motor externo ABB')
            reader.locator('#btnConfirmRegisterMotor').click()
            expect(reader.locator('#motorRegisterDialog')).to_be_hidden()
            expect(reader.locator('#workbookEditorSaveMotor')).to_be_disabled()
            reader.screenshot(path=ROOT / '.test-artifacts/motor-library-reader-mobile.png')
            reader.locator('#workbookEditorClose').click()
            expect(reader.locator('#savedMotorsList .saved-motor-card')).to_have_count(2)
            mobile.close()
            context.close()
            assert not errors, errors
            print('PASS: mobile reader registers external filled motor but cannot edit cells; no JavaScript errors', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
