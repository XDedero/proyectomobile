"""PWA acceptance tests with real Chromium and isolated database/media."""
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory):
    from playwright.sync_api import sync_playwright, expect
    from scripts.browser_smoke import login
    from scripts.generate_pdf_demo import generate

    generate(directory)
    with sync_playwright() as playwright:
        browser = playwright.webkit.launch(headless=True) if '--webkit' in sys.argv else playwright.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(**playwright.devices['iPhone 13'])
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(url)
            login(page, 'empleado', open_import=False)
            page.evaluate('navigator.serviceWorker.ready')
            page.wait_for_function('navigator.serviceWorker.controller !== null')
            assert page.request.get(url + 'manifest.webmanifest').json()['display'] == 'standalone'
            page.locator('#pwaOpen').click()
            expect(page.locator('#pwaPanel')).to_be_visible()
            page.screenshot(path=str(ROOT / '.test-artifacts/pwa-install-mobile.png'))
            assert page.locator('#pwaPanel').evaluate('node => node.getBoundingClientRect().width') <= 390
            page.locator('.pwa-close').click()
            page.locator('#btnGoToPdfSection').click()
            with page.expect_response('**/api/pdf/plans/import/') as response:
                page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            plan = response.value.json()
            expect(page.locator('#pdfViewport canvas').first).to_be_visible()
            page.locator('#btnPdfSavePlan').click()
            page.evaluate("""async () => {
                for (let attempt=0; attempt<100; attempt++) {
                    if ((await relOffline.downloadedPlans()).length === 1) return;
                    await new Promise(resolve => setTimeout(resolve,100));
                }
                throw new Error('El plano guardado no se conservó automáticamente en el dispositivo.');
            }""")
            page.evaluate("""async () => {
                const detail = projectWorkspace.active.plan;
                const source = await relOffline.planSource(detail);
                const sourceRevision = 'verified-pdf-fixture';
                await relOffline.keepPlan({...detail, project_version:'first', source_revision:sourceRevision}, source);
                // With the same verified PDF, a new book and URL can use the
                // saved blob. Fetching this URL would fail the test.
                const reused = await relOffline.planSource({...detail, project_version:'second',
                    source_revision:sourceRevision, source_url:detail.source_url.replace('/'+detail.id+'/', '/2147483647/')});
                if (!reused || reused.size !== source.size) throw new Error('El cambio de XLSX volvió a descargar el PDF.');
                await relOffline.keepPlan({...detail, source_revision:sourceRevision}, source);
            }""")
            expect(page.locator('.plan-sector-legend')).to_be_hidden()
            sector = {'id': str(uuid4()), 'name': 'Sala offline', 'shape': 'rectangle', 'color': '#ef4444',
                      'page': 1, 'x': 450, 'y': 300, 'width': 200, 'height': 100, 'rotation': 0}
            # Load the plan's Excel and first visible tile before disconnecting.
            page.evaluate('workbookEditor.open(projectWorkspace.active.plan.workbook)')
            page.wait_for_function('workbookEditor.tiles.size > 0 && !workbookEditor.busy')
            workbook = page.evaluate('workbookEditor.metadata')
            sheet = page.evaluate('workbookEditor.sheet')
            # Wi-Fi can stay "online" while the server is unreachable.
            context.set_offline(True)
            page.evaluate("Object.defineProperty(navigator,'onLine',{configurable:true,get:()=>true})")
            assert page.evaluate("""async () => {
                workbookEditor.setPending(workbookEditor.sheet,1,1,'Cambio sin señal');
                return workbookEditor.save();
            }""")
            expect(page.locator('#workbookEditorStatus')).to_contain_text('dispositivo')
            page.evaluate('delete navigator.onLine')
            page.locator('#workbookEditorClose').click()
            page.evaluate("""async sector => {
                const ctx=projectWorkspace.active;
                const result=await relOffline.sectorRequest(ctx,{action:'save',sector,previous:null},()=>fetch(ctx.plan.sectors_url,{method:'POST'}));
                ctx.plan.sectors=result.sectors;planSectors.render(ctx);
            }""", sector)
            expect(page.locator('.plan-sector-legend')).to_contain_text('Sala offline')
            page.screenshot(path=str(ROOT / '.test-artifacts/pwa-offline-plan-mobile.png'))
            assert page.evaluate('(async()=> (await relOffline.state()).count)()') == 2
            # Worker shell, cached identity, complete PDF, and local edits survive reload.
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('appState.authReady && appState.user !== null')
            page.evaluate('async url => pdfWorkflow.open(await (await fetch(url)).json())', plan['detail_url'])
            expect(page.locator('#pdfViewport canvas').first).to_be_visible()
            expect(page.locator('.plan-sector-legend')).to_contain_text('Sala offline')
            page.evaluate('metadata => workbookEditor.open(metadata)', workbook)
            page.wait_for_function('!workbookEditor.busy && workbookEditor.tiles.size > 0')
            assert page.evaluate('workbookEditor.cellValue(1,1).value') == 'Cambio sin señal'
            page.locator('#workbookEditorClose').click()
            print('PASS: offline launch, PDF rendering, sector legend and durable Excel/sector drafts', flush=True)
            context.set_offline(False)
            page.evaluate('relOffline.sync()')
            page.wait_for_function('(async()=> (await relOffline.state()).count === 0)()')
            remote = page.request.get(url.rstrip('/') + plan['sectors_url']).json()
            assert remote['sectors'][0]['name'] == 'Sala offline'
            cells = page.request.get(url + f"api/pdf/workbooks/{workbook['id']}/cells/", params={'sheet': sheet, 'row': 1, 'col': 1, 'rows': 1, 'cols': 1}).json()
            assert cells['cells'][0]['value'] == 'Cambio sin señal', cells
            print('PASS: version-checked reconnection writes actual sectors and workbook cells', flush=True)
            context.set_offline(True)
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('appState.authReady && appState.user !== null')
            page.evaluate('async url => pdfWorkflow.open(await (await fetch(url)).json())', plan['detail_url'])
            page.evaluate('metadata => workbookEditor.open(metadata)', workbook)
            page.wait_for_function('!workbookEditor.busy && workbookEditor.tiles.size > 0')
            assert page.evaluate('workbookEditor.cellValue(1,1).value') == 'Cambio sin señal'
            page.locator('#workbookEditorClose').click()
            print('PASS: acknowledged changes remain available on the next offline launch', flush=True)
            # Two devices edit the same sector: the local draft must remain recoverable.
            original = remote['sectors'][0]
            edited = {**original, 'name': 'Mi borrador'}
            context.set_offline(True)
            page.evaluate("""async ({sector,previous}) => {
                const ctx=projectWorkspace.active;
                await relOffline.sectorRequest(ctx,{action:'save',sector,previous},()=>fetch(ctx.plan.sectors_url,{method:'POST'}));
            }""", {'sector': edited, 'previous': original})
            context.set_offline(False)
            # Abort automatic synchronization while simulating another client.
            page.wait_for_function('!relOffline.syncing')
            # Queue again with an intentionally old previous value to verify 409 handling.
            page.evaluate("""async ({sector,previous}) => {
                const ctx=projectWorkspace.active;
                await relOffline.sectorRequest(ctx,{action:'save',sector,previous},()=>{throw new TypeError('offline');});
            }""", {'sector': {**edited, 'name': 'Borrador en conflicto'}, 'previous': original})
            page.evaluate('relOffline.sync()')
            assert page.evaluate('(async()=> (await relOffline.state()).rows.some(row=>Boolean(row.conflict)))()')
            assert 'Borrador en conflicto' in page.evaluate('relOffline.exportDrafts()')
            page.evaluate('relOffline.discard()')
            page.evaluate('switchView(viewMainMenu)')
            context.set_offline(True)
            page.locator('#btnSavedPlans').click()
            row = page.locator('.saved-plan-row').filter(
                has=page.get_by_role('button', name='Quitar copia de este dispositivo'))
            expect(row).to_be_visible()
            assert page.evaluate("""async url => {
                const response = await fetch(url);
                const bytes = new Uint8Array(await response.arrayBuffer());
                return response.headers.get('X-Reloncavi-Offline') === '1' && bytes[0] === 80 && bytes[1] === 75;
            }""", plan['workbook']['download_url'])
            row.get_by_role('button', name='Quitar copia de este dispositivo').click()
            page.get_by_role('button', name='Quitar copia', exact=True).click()
            expect(row).to_have_count(0)
            assert page.evaluate('relOffline.downloadedPlans()') == []
            context.set_offline(False)
            assert page.request.get(url.rstrip('/') + plan['detail_url']).ok
            page.locator('#modalSavedPlans .btn-close').click()
            print('PASS: offline PDF and XLSX are discoverable and removing them preserves the server plan', flush=True)
            page.locator('#userMenuBtn').click()
            page.locator('#menuItemLogout').click()
            expect(page.locator('#loginForm')).to_be_visible()
            context.set_offline(True)
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('appState.authReady')
            assert page.evaluate('appState.user') is None
            print('PASS: conflict preservation, JSON recovery and logout removes offline access', flush=True)
            assert not errors, errors
        finally:
            browser.close()


if __name__ == '__main__':
    import scripts.browser_smoke as runner
    runner.check_browser = check_browser
    runner.main()
