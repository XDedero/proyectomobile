"""Exercise plan controls, Excel-only saves and permanent removal in Chrome."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
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
            login(page, 'admin')
            with page.expect_response('**/api/plans/import/') as response:
                page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            cad = response.value.json()
            expect(page.locator('.motor-target')).to_have_count(3)
            page.locator('#motorList button').first.click()
            page.locator('#btnPreviewExcel').click()
            expect(page.locator('#workbookEditorSave')).to_have_text('Guardar cambios')
            assert 'Guardar plano' not in page.locator('.workbook-editor-panel').inner_text()
            page.locator('#workbookEditorAddress').fill('B2')
            page.locator('#workbookEditorGo').click()
            page.locator('#workbookEditorFormula').fill('Ficha guardada sin guardar plano')
            page.locator('#workbookEditorFormula').press('Control+s')
            expect(page.locator('#workbookEditorStatus')).to_have_text('Cambios de Excel guardados.')
            assert page.request.get(url + 'api/projects/').json()['plans'] == []
            page.locator('#workbookEditorClose').click()
            page.locator('#btnCadSavePlan').click()
            expect(page.locator('#cadSaveBadge')).to_have_text('Guardado')
            assert len(page.request.get(url + 'api/projects/').json()['plans']) == 1
            print('PASS: Excel save and Ctrl+S update only the workbook; explicit plan Save creates the library entry', flush=True)

            page.locator('#btnCadFullscreen').click()
            expect(page.locator('#fullscreenSavePlan')).to_be_visible()
            expect(page.locator('#fullscreenDeleteMarker')).to_be_visible()
            page.once('dialog', lambda dialog: dialog.accept())
            with page.expect_response('**/api/motors/*/delete/'):
                page.locator('#fullscreenDeleteMarker').click()
            expect(page.locator('.motor-target')).to_have_count(2)
            page.locator('#fullscreenSavePlan').click()
            expect(page.locator('#fullscreenPlanStatus')).to_have_text('Plano guardado')
            page.screenshot(path=str(ROOT / '.test-artifacts/plan-actions-fullscreen.png'))
            page.locator('#btnExitPlanFullscreen').click()
            page.locator('#btnBackToMainMenu').click()
            page.locator('#btnSavedPlans').click()
            delete_button = page.locator('#savedPlansContainer [data-delete-plan]')
            expect(delete_button).to_be_visible()
            page.once('dialog', lambda dialog: dialog.dismiss())
            delete_button.click()
            expect(delete_button).to_be_visible()
            assert page.request.get(cad['detail_url'] if cad['detail_url'].startswith('http') else url.rstrip('/') + cad['detail_url']).status == 200
            page.once('dialog', lambda dialog: dialog.accept())
            with page.expect_response('**/api/plans/*/delete/'):
                delete_button.click()
            expect(page.locator('#savedPlansContainer [data-delete-plan]')).to_have_count(0)
            assert page.request.get(url.rstrip('/') + cad['detail_url']).status == 404
            page.reload()
            expect(page.locator('#viewMainMenu')).to_be_visible()
            assert page.evaluate('appState.currentPlan') is None
            assert page.request.get(url + 'api/projects/').json()['plans'] == []
            page.locator('#btnGoToImportSection').click()
            with page.expect_response('**/api/plans/import/'):
                page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            expect(page.locator('.motor-target')).to_have_count(3)
            assert page.request.get(url + 'api/projects/').json()['plans'] == []
            page.locator('#btnCadSavePlan').click()
            expect(page.locator('#cadSaveBadge')).to_have_text('Guardado')
            print('PASS: marker deletion is direct; library deletion supports cancel and stays deleted after reload until reimport and Save', flush=True)

            reader_context = browser.new_context(viewport={'width': 320, 'height': 640}, is_mobile=True, has_touch=True)
            reader = reader_context.new_page()
            reader.on('pageerror', lambda error: errors.append(str(error)))
            reader.goto(url)
            login(reader, 'lector', open_import=False)
            reader.locator('#btnSavedPlans').click()
            expect(reader.locator('#savedPlansContainer [data-delete-plan]')).to_have_count(0)
            reader.locator('#savedPlansContainer button').first.click()
            expect(reader.locator('#btnCadSavePlan')).to_be_hidden()
            expect(reader.locator('#btnDeleteCadPlan')).to_be_hidden()
            reader.locator('#motorList button').first.click()
            expect(reader.locator('#btnDeleteCadMarker')).to_be_hidden()
            reader.locator('#btnCadFullscreen').click()
            expect(reader.locator('#fullscreenSavePlan')).to_be_hidden()
            expect(reader.locator('#fullscreenSaveMenu')).to_be_hidden()
            expect(reader.locator('#fullscreenDeletePlan')).to_be_hidden()
            expect(reader.locator('#fullscreenDeleteMarker')).to_be_hidden()
            denied = reader.evaluate('''async () => {
                const headers = {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()};
                const plan = appState.currentPlan;
                const results = await Promise.all([plan.save_project_url, plan.delete_url, plan.motors[0].delete_url].map(url => fetch(url, {method: 'POST', headers, body: '{}'})));
                return results.map(result => result.status);
            }''')
            assert denied == [403, 403, 403], denied
            reader.locator('#btnPlanFullscreenMenu').click()
            with reader.expect_download():
                reader.locator('#fullscreenPlanMenu [data-project-action="export"]').click()
            reader_context.close()
            print('PASS: readers see no save/delete controls, forged actions are denied, and project export remains available', flush=True)

            page.locator('#btnBackToMainMenu').click()
            page.locator('#btnGoToPdfSection').click()
            with page.expect_response('**/api/pdf/plans/import/') as response:
                page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            pdf = response.value.json()
            expect(page.locator('#pdfViewport canvas')).to_be_visible()
            page.locator('#btnPdfFullscreen').click()
            page.locator('#fullscreenAddMarker').click()
            bounds = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
            page.touchscreen.tap(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .3)
            expect(page.locator('#workbookEditorDeleteMarker')).to_be_visible()
            page.once('dialog', lambda dialog: dialog.accept())
            with page.expect_response('**/api/pdf/markers/*/delete/'):
                page.locator('#workbookEditorDeleteMarker').click()
            expect(page.locator('.workbook-editor-overlay')).to_be_hidden()
            expect(page.locator('#pdfViewport .pdf-plan-marker')).to_have_count(0)
            page.locator('#fullscreenSavePlan').click()
            expect(page.locator('#fullscreenPlanStatus')).to_have_text('Plano guardado')
            page.once('dialog', lambda dialog: dialog.accept())
            with page.expect_response('**/api/pdf/plans/*/delete/'):
                page.locator('#fullscreenDeletePlan').click()
            expect(page.locator('#planFullscreen')).to_be_hidden()
            expect(page.locator('#pdfPlanSection')).to_be_hidden()
            assert page.request.get(url.rstrip('/') + pdf['detail_url']).status == 404
            assert len(page.request.get(url + 'api/projects/').json()['plans']) == 1
            assert not errors, errors
            print('PASS: PDF quick actions and Excel titlebar remove the selected marker; fullscreen deletion closes the removed plan', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
