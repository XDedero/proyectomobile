"""Exercise the unified button modal and plan-owned sectors in real Chromium."""
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
            context = browser.new_context(viewport={'width': 1365, 'height': 960})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, 'empleado', open_import=False)
            page.locator('#btnGoToPdfSection').click()
            with page.expect_response('**/api/pdf/plans/import/') as response:
                page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            pdf = response.value.json()
            expect(page.locator('#pdfViewport canvas').first).to_be_visible()
            page.locator('#btnPdfFullscreen').click()
            page.locator('#fullscreenAddMarker').click()
            expect(page.locator('#newPlanButton')).to_be_visible()
            for selector in ['#newButtonCategory', '#newButtonDescriptionEnabled', '#newButtonOrigin', '#newButtonColor']:
                expect(page.locator(selector)).to_be_visible()
            page.locator('#newButtonCategory').select_option(label='Motores')
            page.locator('#newButtonName').fill('Bomba unificada')
            page.locator('#newButtonDescriptionEnabled').check()
            page.locator('#btnNewButtonAddDescription').click()
            page.locator('#newButtonDescriptions input').fill('Ubicación')
            page.locator('#newButtonDescriptions textarea').fill('Sala norte')
            page.locator('#newButtonColor').fill('#a855f7')
            page.screenshot(path=str(ROOT / '.test-artifacts/button-unified-modal.png'))
            page.locator('#btnConfirmNewButton').click()
            expect(page.locator('#newPlanButton')).to_be_hidden()
            expect(page.locator('#pdfViewport')).to_have_class('pdf-plan-viewport placing-marker')
            page.screenshot(path=str(ROOT / '.test-artifacts/button-placement.png'))
            bounds = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
            with page.expect_response('**/api/pdf/plans/*/markers/'):
                page.mouse.click(bounds['x'] + bounds['width'] * .35, bounds['y'] + bounds['height'] * .4)
            expect(page.locator('.pdf-plan-marker')).to_have_count(1)
            marker = page.request.get(url.rstrip('/') + pdf['detail_url']).json()['markers'][0]
            assert marker['label'] == 'Bomba unificada' and marker['button_color'] == '#a855f7'
            details = page.request.get(url.rstrip('/') + marker['details_url']).json()
            assert details['show_description'] and details['descriptions'][0]['value'] == 'Sala norte'
            print('PASS: all button settings in one fullscreen modal persist atomically', flush=True)

            # Create and transform a rectangle using the same viewer beneath the modal.
            page.locator('#btnPlanFullscreenMenu').click()
            page.locator('#fullscreenCreateSector').click()
            page.locator('#sectorName').fill('Sala norte')
            page.locator('#sectorShape').select_option('rectangle')
            page.locator('#sectorColor').fill('#ef4444')
            page.locator('#btnStartSectorDrawing').click()
            expect(page.locator('.sector-editor-dock')).to_be_visible()
            page.locator('.sector-editor-dock summary').click()
            for selector, value in [('#sectorX', '35'), ('#sectorY', '40'), ('#sectorWidth', '40'), ('#sectorHeight', '45'), ('#sectorRotation', '20')]:
                page.locator(selector).fill(value)
            # Drag the rotation handle and a resize corner; numeric controls follow.
            for selector, dx, dy in [('[data-sector-handle="rotate"]', 18, 4), ('[data-sector-handle="se"]', 12, 8)]:
                box = page.locator(selector).bounding_box()
                page.mouse.move(box['x'] + box['width']/2, box['y'] + box['height']/2)
                page.mouse.down()
                page.mouse.move(box['x'] + box['width']/2 + dx, box['y'] + box['height']/2 + dy, steps=5)
                page.mouse.up()
            page.screenshot(path=str(ROOT / '.test-artifacts/sector-transform-fullscreen.png'))
            with page.expect_response('**/sectors/') as response:
                page.locator('#btnSectorSave').click()
            assert response.value.status == 200, response.value.text()
            expect(page.locator('.sector-editor-dock')).to_be_hidden()
            assert len(response.value.json()['sectors']) == 1
            page.locator('#btnPlanFullscreenMenu').click()
            page.locator('#fullscreenSectors').click()
            page.locator('#planSectorRows button').click()
            expect(page.locator('#planSectorMembers')).to_contain_text('Bomba unificada')
            page.locator('#planSectorsModal .btn-close').click()
            print('PASS: rectangle creation, size/rotation handles and member navigation in fullscreen', flush=True)

            # Brush paint: two disconnected strokes, undo, and save.
            page.locator('#btnPlanFullscreenMenu').click()
            page.locator('#fullscreenCreateSector').click()
            page.locator('#sectorName').fill('Pasillo pintado')
            page.locator('#sectorShape').select_option('brush')
            page.locator('#btnStartSectorDrawing').click()
            expect(page.locator('.sector-editor-dock')).to_be_visible()
            bounds = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
            for y in [.35, .6]:
                page.mouse.move(bounds['x'] + bounds['width']*.2, bounds['y'] + bounds['height']*y)
                page.mouse.down()
                page.mouse.move(bounds['x'] + bounds['width']*.55, bounds['y'] + bounds['height']*y, steps=15)
                page.mouse.up()
            expect(page.locator('.plan-sectors-layer path')).to_have_count(1)
            page.locator('#btnSectorUndo').click()
            page.locator('#btnSectorTransform').click()
            with page.expect_response('**/sectors/') as response:
                page.locator('#btnSectorSave').click()
            sectors = response.value.json()['sectors']
            brush = next(row for row in sectors if row['shape'] == 'brush')
            assert len(brush['strokes']) == 1 and len(brush['strokes'][0]) > 1
            page.locator('#btnExitPlanFullscreen').click()
            print('PASS: free painting, disconnected strokes, undo and brush persistence', flush=True)

            # Reopen on a narrow touch viewport and verify read-only access.
            reader_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            reader = reader_context.new_page()
            reader.on('pageerror', lambda error: errors.append(str(error)))
            reader.goto(url)
            login(reader, 'lector', open_import=False)
            reader.wait_for_function("appState.user?.username === 'lector'")
            reader.evaluate('''async plan => { switchView(document.getElementById('viewImportPDF')); await pdfWorkflow.open(plan); }''',
                            reader.request.get(url.rstrip('/') + pdf['detail_url']).json())
            expect(reader.locator('#btnPdfCreateSector')).to_be_hidden()
            reader.locator('#btnPdfSectors').click()
            expect(reader.locator('#planSectorRows button')).to_have_count(2)
            expect(reader.locator('#btnCreatePlanSector')).to_be_hidden()
            reader.locator('#planSectorRows button').first.click()
            expect(reader.locator('#planSectorMembers')).to_contain_text('Bomba unificada')
            expect(reader.get_by_role('button', name='Editar sector', exact=True)).to_have_count(0)
            reader.screenshot(path=str(ROOT / '.test-artifacts/sector-mobile-reader.png'))
            reader_context.close()
            print('PASS: sectors and member lists on mobile; readers cannot edit', flush=True)

            # DXF shares the modal, persistence and transformation controls.
            page.locator('#btnPdfBack').click()
            page.locator('#btnGoToImportSection').click()
            with page.expect_response('**/api/plans/import/'):
                page.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            expect(page.locator('.motor-target')).to_have_count(3)
            page.locator('#btnCadCreateSector').click()
            page.locator('#sectorName').fill('Sector DXF')
            page.locator('#sectorShape').select_option('circle')
            page.locator('#btnStartSectorDrawing').click()
            with page.expect_response('**/sectors/') as response:
                page.locator('#btnSectorSave').click()
            assert response.value.status == 200
            expect(page.locator('#dxfViewport [data-sector-id]')).to_have_count(1)
            page.locator('#btnCadSectors').click()
            page.locator('#planSectorRows button').click()
            page.get_by_role('button', name='Editar sector', exact=True).click()
            page.locator('#sectorName').fill('Sector renombrado')
            page.locator('#btnStartSectorDrawing').click()
            with page.expect_response('**/sectors/'):
                page.locator('#btnSectorSave').click()
            page.locator('#btnCadSectors').click()
            expect(page.locator('#planSectorRows')).to_contain_text('Sector renombrado')
            page.locator('#planSectorRows button').click()
            page.once('dialog', lambda dialog: dialog.accept())
            page.get_by_role('button', name='Eliminar sector', exact=True).click()
            expect(page.locator('#planSectorRows')).to_contain_text('no tiene sectores')
            expect(page.locator('.motor-target')).to_have_count(3)
            assert not errors, errors
            print('PASS: DXF regions, rename and removal preserve all buttons; no JavaScript errors', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
