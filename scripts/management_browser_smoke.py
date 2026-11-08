"""Exercise stale plans, ownership and administration using isolated local data."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
    from scripts.browser_smoke import login
    from scripts.generate_demo import generate
    from scripts.generate_pdf_demo import generate as generate_pdf
    generate(directory); generate_pdf(directory)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            author_context = browser.new_context(viewport={'width': 412, 'height': 915})
            reader_context = browser.new_context(viewport={'width': 412, 'height': 915})
            admin_context = browser.new_context(viewport={'width': 1280, 'height': 960})
            author, reader, admin = author_context.new_page(), reader_context.new_page(), admin_context.new_page()
            for page, username in [(author, 'empleado'), (reader, 'lector'), (admin, 'admin')]:
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('**/api/projects/cloud/', lambda route: route.fulfill(json={'plans': []}))
                page.goto(url); login(page, username, open_import=False)
                expect(page.locator('#viewMainMenu')).to_be_visible()
            for kind in ('pdf', 'dxf'):
                author.locator('#btnGoToPdfSection' if kind == 'pdf' else '#btnGoToImportSection').click()
                endpoint = '**/api/pdf/plans/import/' if kind == 'pdf' else '**/api/plans/import/'
                with author.expect_response(endpoint) as response:
                    author.locator('#pdfFileInput' if kind == 'pdf' else '#autocadFileInput').set_input_files(
                        directory / ('plano_pdf_configurable.pdf' if kind == 'pdf' else 'motores_demo.dxf'))
                plan = response.value.json()
                expect(author.locator('#btnPdfSavePlan' if kind == 'pdf' else '#btnCadSavePlan')).to_be_visible()
                author.locator('#btnPdfSavePlan' if kind == 'pdf' else '#btnCadSavePlan').click()
                expect(author.locator('#pdfSaveBadge' if kind == 'pdf' else '#cadSaveBadge')).to_have_text('Guardado')
                reader.locator('#btnSavedPlans').click()
                reader.locator('#savedPlansContainer .saved-plan-row > button').filter(has_text=plan['name']).first.click()
                expect(reader.locator('#pdfViewport canvas' if kind == 'pdf' else '#dxfViewport svg')).to_be_visible()
                expect(reader.locator('#modalSavedPlans')).to_be_hidden()
                expect(reader.locator('#btnPdfSavePlan' if kind == 'pdf' else '#btnCadSavePlan')).to_be_hidden()
                expect(reader.locator('#btnRenamePdfPlan' if kind == 'pdf' else '#btnRenameCadPlan')).to_be_hidden()
                expect(reader.locator('#btnPdfAddMarker' if kind == 'pdf' else '#btnAddCadMarker')).to_be_hidden()
                assert reader.request.post(url.rstrip('/') + plan['save_project_url'], headers={'X-CSRFToken': reader.evaluate('csrfToken()')}).status == 403
                author.evaluate('async url => {const r = await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":csrfToken()},body:JSON.stringify({name:"Plano actualizado"})});if(!r.ok)throw Error("Rename failed");}', plan['update_url'])
                author.locator('#btnPdfSavePlan' if kind == 'pdf' else '#btnCadSavePlan').click()
                expect(author.locator('#pdfSaveBadge' if kind == 'pdf' else '#cadSaveBadge')).to_have_text('Guardado')
                reader.evaluate('projectWorkspace.showSaved()')
                reader.locator('#savedPlansContainer .saved-plan-row > button').filter(has_text='Plano actualizado').first.click()
                expect(reader.locator('#planUpdateDialog')).to_be_visible()
                expect(reader.locator('#planUpdateTitle')).to_have_text('Este plano está desactualizado')
                warning = reader.locator('.plan-update-warning').evaluate('(node) => getComputedStyle(node).color')
                assert warning == 'rgb(172, 24, 39)', warning
                reader.screenshot(path=ROOT / '.test-artifacts' / f'{kind}-stale-warning.png')
                reader.locator('#btnCancelStalePlan').click()
                expect(reader.locator('#planUpdateDialog')).to_be_hidden()
                reader.locator('#savedPlansContainer .saved-plan-row > button').filter(has_text='Plano actualizado').first.click()
                expect(reader.locator('#planUpdateDialog')).to_be_visible()
                reader.locator('#btnUpdateStalePlan').click()
                expect(reader.locator('#planUpdateDialog')).to_be_hidden()
                reader.wait_for_function('projectWorkspace.active.plan.name === "Plano actualizado"')
                print(f'PASS: {kind.upper()} creator can save, another user cannot edit, and stale opening requires update', flush=True)
                # Remove this fixture so the next format has one unambiguous row.
                assert author.request.post(url.rstrip('/') + plan['delete_url'], headers={'X-CSRFToken': author.evaluate('csrfToken()')}).status == 200
                author.reload(); expect(author.locator('#viewMainMenu')).to_be_visible()
                reader.reload(); expect(reader.locator('#viewMainMenu')).to_be_visible()

            admin.locator('#btnAdminister').click()
            expect(admin).to_have_url(url + 'administracion/')
            admin.locator('#roleName').fill('Supervisores')
            admin.locator('#roleDescription').fill('Consulta de planos del turno')
            admin.locator('#roleForm button[type=submit]').click()
            expect(admin.locator('#roleRows')).to_contain_text('Supervisores')
            admin.locator('[data-tab=groups]').click()
            admin.locator('#groupName').fill('Turno A')
            admin.locator('#groupRoles label').filter(has_text='Supervisores').locator('input').check()
            admin.locator('#groupUsers label').filter(has_text='lector').locator('input').check()
            admin.locator('#groupForm button[type=submit]').click()
            expect(admin.locator('#groupRows')).to_contain_text('Turno A')
            admin.locator('[data-tab=categories]').click()
            admin.locator('#categoryName').fill('Válvulas')
            admin.locator('#categoryFile').set_input_files(directory / 'motor_01.xlsx')
            admin.locator('#categoryForm button[type=submit]').click()
            expect(admin.locator('#categoryRows')).to_contain_text('Válvulas')
            expect(admin.locator('#categoryTemplates')).to_contain_text('motor_01.xlsx')
            admin.reload()
            admin.locator('[data-tab=categories]').click()
            expect(admin.locator('#categoryRows')).to_contain_text('Válvulas')
            admin.screenshot(path=ROOT / '.test-artifacts' / 'management-desktop.png', full_page=True)
            admin.set_viewport_size({'width': 390, 'height': 844})
            admin.screenshot(path=ROOT / '.test-artifacts' / 'management-mobile.png', full_page=True)
            assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert reader.request.get(url + 'api/administration/').status == 403
            author.locator('#btnGoToPdfSection').click()
            author.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            expect(author.locator('#pdfViewport canvas')).to_be_visible()
            author.locator('#btnPdfAddMarker').click()
            expect(author.locator('#newPlanButton')).to_be_visible()
            author.locator('#newButtonCategory').select_option(label='Válvulas')
            author.locator('#btnNewButtonNext').click()
            author.locator('#btnNewButtonWithoutDescription').click()
            expect(author.locator('#btnNewButtonCategoryTemplate')).to_be_visible()
            expect(author.locator('#newButtonTemplate')).to_contain_text('motor_01.xlsx')
            author.locator('#btnNewButtonCategoryTemplate').click()
            expect(author.locator('#newPlanButton')).to_be_hidden()
            expect(author.locator('#pdfViewport')).to_have_class('pdf-plan-viewport placing-marker')
            author.locator('#pdfViewport .pdf-plan-page').click()
            author.wait_for_function('pdfWorkflow.currentPlan.markers.length === 1')
            assert author.evaluate('pdfWorkflow.currentPlan.markers[0].category_name') == 'Válvulas'
            assert not errors, errors
            print('PASS: administrator creates roles, assigns users through groups, and keeps category Excel after reload; mobile panel fits', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
