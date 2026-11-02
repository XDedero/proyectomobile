"""Open a saved PDF as another user in Safari's engine and older API profiles."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
    from scripts.browser_smoke import login
    from scripts.pdf_resolution_browser_smoke import generate_plan

    generate_plan(directory / 'compatibilidad.pdf')
    missing_features = '''
        Object.defineProperty(Promise, 'withResolvers', {value:undefined,writable:true,configurable:true});
        Object.defineProperty(URL, 'parse', {value:undefined,writable:true,configurable:true});
    '''
    with sync_playwright() as playwright:
        chrome = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            owner = chrome.new_page(viewport={'width': 412, 'height': 915})
            owner.goto(url)
            login(owner, 'admin', open_import=False)
            expect(owner.locator('#viewMainMenu')).to_be_visible()
            owner.locator('#btnGoToPdfSection').click()
            with owner.expect_response('**/api/pdf/plans/import/') as imported:
                owner.locator('#pdfFileInput').set_input_files(directory / 'compatibilidad.pdf')
            plan = imported.value.json()
            expect(owner.locator('#pdfViewport canvas')).to_be_visible(timeout=30000)
            owner.locator('#btnPdfSavePlan').click()
            expect(owner.locator('#pdfSaveBadge')).to_have_text('Guardado')
            # Reproduce a DB transferred without its ignored media directory.
            originals = list((directory / 'media' / 'pdf_plans').glob('*.pdf'))
            assert len(originals) == 1
            originals[0].unlink()

            for engine_name in ('chromium', 'webkit'):
                browser = chrome if engine_name == 'chromium' else playwright.webkit.launch(headless=True)
                try:
                    context = browser.new_context(viewport={'width': 390, 'height': 844},
                        device_scale_factor=3, is_mobile=True, has_touch=True)
                    context.add_init_script(missing_features)
                    def old_worker(route):
                        response = route.fetch()
                        route.fulfill(response=response, body=missing_features + response.text())
                    context.route('**/compat.mjs*', old_worker)
                    errors, requests = [], []
                    page = context.new_page()
                    page.on('console', lambda message: print(message.text, flush=True) if message.type == 'error' and message.text.startswith('[Visor PDF]') else None)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: requests.append(request.url))
                    page.goto(url)
                    login(page, 'lector', open_import=False)
                    expect(page.locator('#viewMainMenu')).to_be_visible()
                    page.locator('#btnSavedPlans').click()
                    page.locator('#savedPlansContainer .saved-plan-row > button').first.click()
                    try:
                        expect(page.locator('#pdfViewport canvas')).to_be_visible(timeout=20000)
                    except AssertionError:
                        reason = page.evaluate('import(document.querySelector("#pdfViewport").dataset.pdfjsUrl).then(()=>"library loaded").catch(e=>e.message)')
                        print(f'FAIL: {engine_name} missing APIs: {reason}; screen: {page.locator("#pdfViewport").inner_text()}', flush=True)
                        raise
                    page.wait_for_function('projectWorkspace.active?.viewer.canvas && !projectWorkspace.active.viewer.renderTask')
                    assert page.evaluate('Math.max(projectWorkspace.active.viewer.canvas.width, projectWorkspace.active.viewer.canvas.height)') == 1280
                    page.locator('#btnPdfFullscreen').click()
                    for number in ('2', '3'):
                        page.locator('#fullscreenPage').select_option(number)
                        page.wait_for_function('number => projectWorkspace.active.viewer.pageNumber === Number(number) && !!projectWorkspace.active.viewer.canvas && !projectWorkspace.active.viewer.renderTask', arg=number)
                    page.evaluate('projectWorkspace.active.viewer.zoom(4)')
                    page.wait_for_function('projectWorkspace.active.viewer.detailCanvas && !projectWorkspace.active.viewer.renderTask')
                    assert not errors, errors
                    assert any('pdf.worker-entry.mjs' in item for item in requests)
                    assert page.evaluate('''() => {
                        const node=document.querySelector('#pdfViewport');
                        const main=new URL(node.dataset.pdfjsUrl,location.href), worker=new URL(node.dataset.workerUrl,location.href);
                        return main.searchParams.has('v') && main.searchParams.get('v')===worker.searchParams.get('v');
                    }''')
                    page.screenshot(path=str(ROOT / f'.test-artifacts/pdf-compatibility-{engine_name}.png'))
                    page.locator('#btnExitPlanFullscreen').click()
                    missing_url = '**' + plan['source_url']
                    context.route(missing_url, lambda route: route.fulfill(status=404, content_type='application/json', body='{"error":"Missing PDF source"}'))
                    page.locator('#btnPdfSaved').click()
                    page.locator('#savedPlansContainer .saved-plan-row > button').first.click()
                    expect(page.locator('#pdfError')).to_contain_text('El archivo PDF no está disponible')
                    # Missing source bytes do not mean the plan record was deleted.
                    assert len(page.request.get(url + 'api/projects/').json()['plans']) == 1
                    assert page.evaluate('projectWorkspace.active.plan.id') == plan['id']
                    context.unroute(missing_url)
                    page.locator('#btnPdfSaved').click()
                    page.locator('#savedPlansContainer .saved-plan-row > button').first.click()
                    expect(page.locator('#pdfViewport canvas')).to_be_visible(timeout=20000)
                    expect(page.locator('#pdfError')).to_be_hidden()
                    assert not errors, errors
                    context.close()
                    print(f'PASS: {engine_name} opens the saved original without its media file or native Promise.withResolvers/URL.parse, including its worker, rotated page, scan and 1280px zoom', flush=True)
                finally:
                    if browser != chrome:
                        browser.close()
        finally:
            chrome.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
