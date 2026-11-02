"""Check permanent PDF/DXF removal across independent browser sessions."""
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
            device_a = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            device_b = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            owner, reader, library = device_a.new_page(), device_b.new_page(), device_b.new_page()
            for page in (owner, reader, library):
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
            login(owner, 'admin', open_import=False)
            login(reader, 'lector', open_import=False)
            expect(owner.locator('#viewMainMenu')).to_be_visible()
            expect(reader.locator('#viewMainMenu')).to_be_visible()
            library.reload()
            expect(library.locator('#viewMainMenu')).to_be_visible()
            for kind in ('pdf', 'dxf'):
                owner.reload()
                expect(owner.locator('#viewMainMenu')).to_be_visible()
                owner.locator('#btnGoToPdfSection' if kind == 'pdf' else '#btnGoToImportSection').click()
                endpoint = '**/api/pdf/plans/import/' if kind == 'pdf' else '**/api/plans/import/'
                with owner.expect_response(endpoint) as response:
                    owner.locator('#pdfFileInput' if kind == 'pdf' else '#autocadFileInput').set_input_files(
                        directory / ('plano_pdf_configurable.pdf' if kind == 'pdf' else 'motores_demo.dxf'))
                plan = response.value.json()
                if kind == 'pdf':
                    expect(owner.locator('#pdfViewport canvas')).to_be_visible()
                    owner.evaluate('''async url => {
                        const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken()},body:JSON.stringify({page:1,x:.4,y:.3})});
                        if (!response.ok) throw new Error('Could not create marker');
                        return (await response.json()).marker;
                    }''', plan['markers_url'])
                else:
                    expect(owner.locator('.motor-target')).to_have_count(3)
                # An imported working plan is valid before it is saved.
                owner.evaluate('window.dispatchEvent(new Event("focus"))')
                owner.wait_for_timeout(250)
                assert owner.evaluate('projectWorkspace.active.plan.id') == plan['id']
                owner.locator('#btnPdfSavePlan' if kind == 'pdf' else '#btnCadSavePlan').click()
                expect(owner.locator('#pdfSaveBadge' if kind == 'pdf' else '#cadSaveBadge')).to_have_text('Guardado')
                reader.locator('#btnSavedPlans').click()
                reader.locator('#savedPlansContainer .saved-plan-row > button').first.click()
                expect(reader.locator('#pdfPlanSection' if kind == 'pdf' else '#loadedPlanCard')).to_be_visible()
                reader.locator('#btnPdfFullscreen' if kind == 'pdf' else '#btnCadFullscreen').click()
                if kind == 'pdf':
                    reader.locator('#pdfViewport .pdf-plan-marker').click()
                else:
                    reader.locator('#motorList button').first.evaluate('button => button.click()')
                    reader.locator('#fullscreenOpenExcel').click()
                expect(reader.locator('.workbook-editor-overlay')).to_be_visible()
                library.locator('#btnSavedPlans').click()
                expect(library.locator('#savedPlansContainer .saved-plan-row')).to_have_count(1)

                owner.once('dialog', lambda dialog: dialog.accept())
                with owner.expect_response('**' + plan['delete_url']):
                    owner.locator('#btnDeletePdfPlan' if kind == 'pdf' else '#btnDeleteCadPlan').click()
                expect(library.locator('#savedPlansContainer .saved-plan-row')).to_have_count(0, timeout=12000)
                expect(reader.locator('#planFullscreen')).to_be_hidden(timeout=12000)
                expect(reader.locator('.workbook-editor-overlay')).to_be_hidden()
                expect(reader.locator('#pdfPlanSection' if kind == 'pdf' else '#loadedPlanCard')).to_be_hidden()
                assert reader.request.get(url.rstrip('/') + plan['detail_url']).status == 404
                assert reader.request.post(url.rstrip('/') + plan['save_project_url']).status == 403  # Reader still has no save rights.
                reader.reload()
                expect(reader.locator('#viewMainMenu')).to_be_visible()
                assert reader.evaluate('appState.currentPlan') is None
                library.reload()
                expect(library.locator('#viewMainMenu')).to_be_visible()
                library.locator('#btnSavedPlans').click()
                expect(library.locator('#savedPlansContainer .saved-plan-row')).to_have_count(0)
                library.locator('#modalSavedPlans [data-bs-dismiss="modal"]').first.click()
                print(f'PASS: {kind.upper()} disappears from another device, its open viewer, Excel and library; reload cannot restore the deleted plan', flush=True)

            # A delayed library response must not replace a newer empty result.
            paused = []
            def delay_old_library(route):
                paused.append((route, route.fetch().json()))
            library.route('**/api/projects/?*', delay_old_library, times=1)
            library.evaluate('void projectWorkspace.listSaved()')
            library.wait_for_function('document.querySelector("#savedPlansContainer").textContent.includes("Cargando")')
            library.wait_for_timeout(100)
            assert paused
            library.evaluate('projectWorkspace.listSaved()')
            old_route, stale = paused.pop()
            stale['plans'] = [{'id': 999, 'plan_id': 999, 'kind': 'pdf', 'name': 'Respuesta antigua'}]
            old_route.fulfill(json=stale)
            library.wait_for_timeout(200)
            expect(library.locator('#savedPlansContainer .saved-plan-row')).to_have_count(0)

            # Failure to reach the app must not be interpreted as deletion.
            owner.reload()
            expect(owner.locator('#viewMainMenu')).to_be_visible()
            owner.locator('#btnGoToPdfSection').click()
            owner.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            expect(owner.locator('#pdfViewport canvas')).to_be_visible()
            owner.route('**/api/projects/?*', lambda route: route.abort(), times=1)
            owner.evaluate('window.dispatchEvent(new Event("focus"))')
            owner.wait_for_timeout(300)
            expect(owner.locator('#pdfPlanSection')).to_be_visible()
            assert not errors, errors
            print('PASS: out-of-order responses cannot restore a removed entry; connection failures preserve the last confirmed plan', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
