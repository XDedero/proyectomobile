"""Check listing names and removal of the plan Excel button in an isolated app."""
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
            for kind, mobile in [('pdf', False), ('dxf', True)]:
                context = browser.new_context(
                    viewport={'width': 360 if mobile else 1280, 'height': 740 if mobile else 900},
                    is_mobile=mobile, has_touch=mobile,
                )
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                login(page, 'admin', open_import=False)
                prefix = 'Pdf' if kind == 'pdf' else 'Cad'
                page.locator('#btnGoToPdfSection' if kind == 'pdf' else '#btnGoToImportSection').click()
                with page.expect_response('**/api/pdf/plans/import/' if kind == 'pdf' else '**/api/plans/import/') as imported:
                    page.locator('#pdfFileInput' if kind == 'pdf' else '#autocadFileInput').set_input_files(
                        directory / ('plano_pdf_configurable.pdf' if kind == 'pdf' else 'motores_demo.dxf'))
                original = imported.value.json()
                page.wait_for_function('projectWorkspace.active?.plan.is_draft && !projectWorkspace.active.isBusy()')
                expect(page.get_by_role('button', name='Excel del plano', exact=True)).to_have_count(0)
                expect(page.locator('#btnCadWorkbook, #btnPdfWorkbook, #fullscreenManageWorkbook')).to_have_count(0)
                expect(page.locator(f'#btn{prefix}WorkbookSheets')).to_be_visible()
                page.locator(f'#btn{prefix}AssignListing').click()
                page.locator('#btnDefaultPlanListing').click()
                expect(page.locator('#workbookEditorSheet')).to_have_value('Listado')
                page.locator('#workbookEditorListings').click()
                page.locator('#btnAddPlanListing').click()
                page.locator('#btnDefaultPlanListing').click()
                expect(page.locator('#workbookEditorSheet')).to_have_value('Listado motores 2')
                page.locator('#workbookEditorListings').click()

                rename = page.get_by_role('button', name='Cambiar nombre de Listado', exact=True)
                rename.click()
                page.locator('#planListingName').fill('Nombre cancelado')
                page.locator('#btnCancelListingRename').click()
                expect(page.locator('#planListingRename')).to_be_hidden()
                expect(rename).to_be_visible()
                rename.click()
                page.locator('#planListingName').fill('Tableros eléctricos')
                page.locator('#planListingName').press('Enter')
                expect(page.locator('#planListingRename')).to_be_hidden()
                expect(page.get_by_role('button', name='Cambiar nombre de Tableros eléctricos', exact=True)).to_be_visible()
                page.get_by_role('button', name='Cambiar nombre de Listado motores 2', exact=True).click()
                for invalid, error in [('Tableros eléctricos', 'Ya existe una hoja'), ('Sector/1', 'El nombre no puede contener')]:
                    page.locator('#planListingName').fill(invalid)
                    page.locator('#planListingName').press('Enter')
                    expect(page.locator('#excelListingManager .excel-manager-status')).to_contain_text(error)
                    expect(page.locator('#planListingRename')).to_be_visible()
                page.locator('#planListingName').fill('Sensores y válvulas')
                page.locator('#planListingName').press('Enter')
                expect(page.locator('#planListingRename')).to_be_hidden()
                expect(page.locator('#excelListingManager .excel-manager-status')).to_contain_text('Nombre actualizado')
                bounds = page.locator('#planListingsRows').evaluate('(node) => ({width: node.clientWidth, scroll: node.scrollWidth})')
                assert bounds['scroll'] <= bounds['width'] + 1, bounds
                page.screenshot(path=str(ROOT / f'.test-artifacts/listing-names-{kind}.png'))
                page.locator('#planListingsRows > div > button:first-child').last.click()
                expect(page.locator('#workbookEditorSheet')).to_have_value('Sensores y válvulas')
                assert page.locator('#workbookEditorSheet option').all_text_contents() == ['Tableros eléctricos', 'Sensores y válvulas']
                page.locator('#workbookEditorClose').click()
                page.locator(f'#btn{prefix}SavePlan').click()
                page.wait_for_function('projectWorkspace.active.plan.saved_at && !projectWorkspace.active.plan._projectDirty')
                page.reload()
                page.wait_for_function('appState.authReady')
                page.evaluate('''async detail => {
                    const response = await fetch(detail);
                    const plan = await response.json();
                    await (plan.kind === 'pdf' ? pdfWorkflow : planWorkflow).open(plan);
                }''', original['detail_url'])
                page.locator(f'#btn{prefix}Fullscreen').click()
                page.locator('#btnPlanFullscreenMenu').click()
                expect(page.get_by_role('button', name='Excel del plano', exact=True)).to_have_count(0)
                page.locator('#fullscreenOpenListing').click()
                expect(page.locator('#workbookEditorSheet')).to_have_value('Tableros eléctricos')
                assert page.locator('#workbookEditorSheet option').all_text_contents() == ['Tableros eléctricos', 'Sensores y válvulas']
                page.locator('#workbookEditorListings').click()
                page.get_by_role('button', name='Cambiar nombre de Sensores y válvulas', exact=True).click()
                expect(page.locator('#planListingName')).to_have_value('Sensores y válvulas')
                page.locator('#btnCancelListingRename').click()
                page.locator('#excelListingManager .btn-close').click()

                reader_context = browser.new_context(viewport={'width': 360, 'height': 740}, is_mobile=True, has_touch=True)
                reader = reader_context.new_page()
                reader.on('pageerror', lambda error: errors.append(str(error)))
                reader.goto(url)
                login(reader, 'lector', open_import=False)
                reader.wait_for_function('appState.user?.username === "lector"')
                reader.evaluate('''async detail => {
                    const response = await fetch(detail);
                    const plan = await response.json();
                    await (plan.kind === 'pdf' ? pdfWorkflow : planWorkflow).open(plan);
                }''', original['detail_url'])
                reader.locator(f'#btn{prefix}OpenListing').click()
                expect(reader.locator('#workbookEditorSheet')).to_have_value('Tableros eléctricos')
                reader.locator('#workbookEditorListings').click()
                expect(reader.locator('#planListingsRows button[aria-label^="Cambiar nombre de "]')).to_have_count(0)
                denied = reader.evaluate('''async () => {
                    const plan = projectWorkspace.active.plan;
                    const response = await fetch(plan.assign_listing_url, {
                        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()},
                        body: JSON.stringify({action: 'rename', listing_id: plan.listings[0].id, name: 'Sin permiso'})
                    });
                    return response.status;
                }''')
                assert denied == 403, denied
                reader_context.close()
                context.close()
                print(f'PASS: {kind} {"mobile" if mobile else "desktop"}: custom names, cancel, validation, saved reopening, fullscreen and reader permissions', flush=True)
            assert not errors, errors
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
