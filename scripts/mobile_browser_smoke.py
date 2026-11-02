"""Check the mobile host contract and local-only workflows with isolated data."""
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
    from scripts.browser_smoke import login
    from scripts.generate_pdf_demo import generate

    generate(directory)
    errors, external_requests = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(
                viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True,
            )
            origin = urlsplit(url).netloc

            def local_only(route):
                request_url = urlsplit(route.request.url)
                if request_url.scheme in ('http', 'https') and request_url.netloc != origin:
                    external_requests.append(route.request.url)
                    route.abort()
                else:
                    route.continue_()

            context.route('**/*', local_only)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, 'admin', open_import=False)
            expect(page.locator('#viewMainMenu')).to_be_visible()
            expect(page.locator('.phone-status-bar, .dynamic-island, .home-indicator-bar')).to_have_count(0)
            for width, height in [(320, 640), (412, 915), (915, 412), (768, 1024), (1280, 900)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function('''() => Math.abs(document.querySelector('.app-shell').getBoundingClientRect().height - visualViewport.height) < 2''')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, height)
                bounds = page.locator('.app-shell').bounding_box()
                assert bounds['x'] == 0 and bounds['y'] == 0 and abs(bounds['width'] - width) < 2, bounds
            print('PASS: full app viewport at phone, landscape, tablet and desktop sizes', flush=True)

            page.set_viewport_size({'width': 412, 'height': 915})
            page.locator('#btnGoToPdfSection').click()
            page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            expect(page.locator('#pdfViewport .pdf-plan-canvas')).to_be_visible()
            page.locator('#btnPdfFullscreen').click()
            page.evaluate("mobileApp.setInsets({top: 24, right: 8, bottom: 20, left: 8})")
            toolbar = page.locator('.plan-fullscreen-toolbar')
            assert toolbar.evaluate("e => parseFloat(getComputedStyle(e).paddingTop)") == 24
            page.locator('#btnPlanFullscreenMenu').click()
            assert page.evaluate('mobileApp.requestBack()') is True
            expect(page.locator('#fullscreenPlanMenu')).to_be_hidden()
            expect(page.locator('#planFullscreen')).to_be_visible()
            page.locator('#btnPlanFullscreenMenu').click()
            page.locator('#fullscreenPlanMenu [data-project-action="add"]').click()
            bounds = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
            page.touchscreen.tap(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .4)
            expect(page.locator('#workbookEditorFormula')).to_be_visible()
            page.locator('#workbookEditorAddress').fill('B2')
            page.locator('#workbookEditorGo').click()
            page.locator('#workbookEditorFormula').fill('Motor local sin Internet')

            # A smaller visible area approximates keyboard resize; rotation
            # must keep the same editor and its uncommitted draft alive.
            for width, height in [(412, 360), (915, 412), (320, 640)]:
                page.set_viewport_size({'width': width, 'height': height})
                page.wait_for_function('''() => Math.abs(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--viewport-height')) - visualViewport.height) < 2''')
                expect(page.locator('#workbookEditorFormula')).to_have_value('Motor local sin Internet')
                bounds = page.locator('.workbook-editor-panel').bounding_box()
                assert bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= height + 1, bounds
                expect(page.locator('#workbookEditorClose')).to_be_in_viewport()
                expect(page.locator('#workbookEditorFormula')).to_be_in_viewport()
            page.screenshot(path=str(ROOT / '.test-artifacts/mobile-editor-small.png'))

            save_url = page.evaluate('workbookEditor.metadata.save_url')
            assert save_url
            page.route('**' + save_url, lambda route: route.fulfill(
                status=500, content_type='application/json', body='{"error":"Fallo local de prueba"}',
            ))
            assert page.evaluate('mobileApp.requestBack()') is True
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Fallo local de prueba')
            expect(page.locator('.workbook-editor-overlay')).to_be_visible()
            assert page.evaluate('workbookEditor.hasUnsavedChanges')
            page.unroute('**' + save_url)
            assert page.evaluate('mobileApp.requestBack()') is True
            expect(page.locator('.workbook-editor-overlay')).to_be_hidden()
            print('PASS: insets, keyboard resize and rotation preserve drafts; Back saves and retains changes on failure', flush=True)

            page.evaluate('mobileApp.setInsets()')
            page.set_viewport_size({'width': 412, 'height': 915})
            page.locator('#fullscreenOpenExcel').click()
            page.locator('#workbookEditorAddress').fill('B2')
            page.locator('#workbookEditorGo').click()
            expect(page.locator('#workbookEditorFormula')).to_have_value('Motor local sin Internet')
            with page.expect_download() as download:
                page.locator('#workbookEditorCsv').click()
            assert download.value.suggested_filename.endswith('.csv')
            assert 'Motor local sin Internet' in Path(download.value.path()).read_text(encoding='utf-8-sig')

            # Exercise the optional native handler without implementing Android.
            page.evaluate('window.nativeExports = []; window.PlanosAndroid = {download: (url, name) => nativeExports.push({url, name})}')
            page.locator('#workbookEditorCsv').click()
            page.wait_for_function('nativeExports.length === 1')
            exported = page.evaluate('nativeExports[0]')
            assert exported['url'].startswith(url) and exported['name'].endswith('.csv'), exported
            page.evaluate('delete window.PlanosAndroid')
            assert page.evaluate('mobileApp.requestBack()') is True
            assert page.evaluate('mobileApp.requestBack()') is True
            expect(page.locator('#planFullscreen')).to_be_hidden()
            assert page.evaluate('mobileApp.requestBack()') is True
            expect(page.locator('#viewMainMenu')).to_be_visible()
            assert page.evaluate('mobileApp.requestBack()') is False
            page.locator('#btnSavedPlans').click()
            expect(page.locator('#modalSavedPlans')).to_be_visible()
            assert page.evaluate('mobileApp.requestBack()') is True
            expect(page.locator('#modalSavedPlans')).to_be_hidden()
            assert not external_requests, external_requests
            assert not errors, errors
            print('PASS: PDF, Excel editing and CSV exports work with external network blocked; native export and Back hooks work', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
