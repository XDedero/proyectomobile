"""Regression: a delegated worker must explicitly save before any local recovery."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def place_button(page, kind, name, mobile):
    from playwright.sync_api import expect
    if not page.evaluate('projectWorkspace.full'):
        page.locator('#btnPdfFullscreen' if kind == 'pdf' else '#btnCadFullscreen').click()
    page.locator('#btnPlanFullscreenMenu').click()
    page.locator('[data-project-action="add"]').click()
    page.locator('#newButtonCategory').select_option(label='Motores')
    page.locator('#newButtonName').fill(name)
    page.locator('#btnConfirmNewButton').click()
    page.wait_for_function('projectWorkspace.active.viewer.placing')
    target = page.locator('#pdfViewport .pdf-plan-page' if kind == 'pdf' else '#dxfViewport')
    box = target.bounding_box()
    fraction = .7 if name.startswith('ALSO-') else .4
    point = {'x': box['x'] + box['width'] * fraction, 'y': box['y'] + box['height'] * fraction}
    if kind == 'dxf':
        point = page.evaluate('''fraction => {
            const viewer=projectWorkspace.active.viewer, bounds=viewer.initial;
            for(const [x,y] of [[fraction,.4],[.3,.7],[.8,.25],[.2,.2]]) {
                const p=viewer.svg.createSVGPoint();p.x=bounds.x+bounds.width*x;p.y=bounds.y+bounds.height*y;
                const position=p.matrixTransform(viewer.svg.getScreenCTM());
                if(!document.elementFromPoint(position.x,position.y)?.closest('[data-motor-id]')) return {x:position.x,y:position.y};
            }
            throw new Error('No empty drawing point for test marker');
        }''', fraction)
    with page.expect_response('**/markers/') as added:
        if mobile: page.touchscreen.tap(point['x'], point['y'])
        else: page.mouse.click(point['x'], point['y'])
    assert added.value.status == 201, added.value.text()
    page.wait_for_function('projectWorkspace.active.plan._projectDirty && !projectWorkspace.active.isBusy()')
    expect(page.locator('#fullscreenSavePlan')).to_be_visible()


def check(url, directory):
    from playwright.sync_api import sync_playwright, expect
    from scripts.browser_smoke import login
    from scripts.generate_demo import generate
    from scripts.generate_pdf_demo import generate as generate_pdf
    generate(directory); generate_pdf(directory)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            for mobile in (False, True):
                for kind in ('pdf', 'dxf'):
                    owner_context = browser.new_context()
                    owner = owner_context.new_page(); owner.goto(url)
                    expect(owner.locator('#loginForm')).to_be_visible(); login(owner, 'admin', open_import=False)
                    owner.locator('#btnGoToPdfSection' if kind == 'pdf' else '#btnGoToImportSection').click()
                    with owner.expect_response('**/api/pdf/plans/import/' if kind == 'pdf' else '**/api/plans/import/') as imported:
                        owner.locator('#pdfFileInput' if kind == 'pdf' else '#autocadFileInput').set_input_files(
                            directory / ('plano_pdf_configurable.pdf' if kind == 'pdf' else 'motores_demo.dxf'))
                    original = imported.value.json()
                    owner.wait_for_function('projectWorkspace.active?.plan.is_draft && !projectWorkspace.active.isBusy()')
                    prefix = 'Pdf' if kind == 'pdf' else 'Cad'
                    owner.locator(f'#btn{prefix}SavePlan').click()
                    owner.wait_for_function('projectWorkspace.active.plan.saved_at && !projectWorkspace.active.plan._projectDirty')

                    context = browser.new_context(viewport={'width': 412 if mobile else 1360, 'height': 915 if mobile else 900}, is_mobile=mobile, has_touch=mobile)
                    page = context.new_page(); errors = []; saves = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda req: saves.append(req.url) if req.method == 'POST' and '/projects/' in req.url and req.url.endswith('/save/') else None)
                    page.goto(url); expect(page.locator('#loginForm')).to_be_visible()
                    login(page, 'worker', open_import=False); page.wait_for_function('appState.user?.username === "worker"')

                    def open_published():
                        page.evaluate('''async url => {
                            const response=await fetch(url); const plan=await response.json();
                            await (plan.kind === 'pdf' ? pdfWorkflow : planWorkflow).open(plan);
                        }''', original['detail_url'])

                    open_published()
                    expect(page.locator(f'#btn{prefix}SavePlan')).to_be_hidden()
                    owner.locator(f'#btn{prefix}PlanPermissions').click()
                    owner.locator('.plan-permission-users label', has_text='worker').locator('input').check()
                    owner.locator('[data-permission-save]').click()
                    expect(owner.locator('.plan-permissions-dialog')).not_to_be_visible()
                    page.evaluate('projectWorkspace.listSaved({quiet:true,statusOnly:true})')
                    expect(page.locator(f'#btn{prefix}SavePlan')).to_be_visible()
                    baseline = len(page.request.get(url + original['detail_url'].lstrip('/')).json()['markers' if kind == 'pdf' else 'motors'])
                    temporary = f'NOT-SAVED-{kind}-{mobile}'
                    place_button(page, kind, temporary, mobile)
                    assert not saves, saves
                    transient = page.evaluate('projectWorkspace.active.plan.detail_url')
                    assert page.request.get(url + transient.lstrip('/')).headers.get('x-reloncavi-transient') == '1'
                    # Simulate a private copy left by the previous app release.
                    page.evaluate('''async () => {
                        const detail=projectWorkspace.active.plan, owner=appState.user.id;
                        const url=new URL(detail.detail_url,location.href).href, text=JSON.stringify(detail), key='legacy-private-'+owner;
                        const db=await new Promise(resolve=>{const r=indexedDB.open('reloncavi-offline-v1');r.onsuccess=()=>resolve(r.result);});
                        await new Promise((resolve,reject)=>{
                            const tx=db.transaction(['reads','readinfo','downloads','downloadFiles'],'readwrite');
                            tx.objectStore('reads').put({key:owner+'|'+url,body:new TextEncoder().encode(text).buffer,type:'application/json',time:Date.now()});
                            tx.objectStore('readinfo').put({key:owner+'|'+url,type:'application/json',time:Date.now(),size:text.length});
                            tx.objectStore('downloads').put({key,user:owner,kind:detail.kind,plan_id:detail.id,detail_url:detail.detail_url,source_url:detail.source_url||detail.svg_url,urls:[url]});
                            tx.objectStore('downloadFiles').put({key:key+'|'+url,accountUrl:owner+'|'+url,body:new Blob([text],{type:'application/json'})});
                            tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);
                        }); db.close();
                    }''')

                    # Hard reload on the same PC must not recover the old private URL.
                    page.reload(); page.wait_for_function('appState.user?.username === "worker"')
                    open_published()
                    names = page.evaluate('(projectWorkspace.active.plan.markers || projectWorkspace.active.plan.motors).map(row => row.label || row.name)')
                    assert temporary not in names and len(names) == baseline, names
                    assert not saves, saves
                    confirmed = f'SAVED-{kind}-{mobile}'
                    place_button(page, kind, confirmed, mobile)
                    if not mobile:
                        page.locator('#btnExitPlanFullscreen').click()
                        page.locator(f'#btn{prefix}SavePlan').click()
                    else: page.locator('#fullscreenSavePlan').click()
                    page.wait_for_function('!projectWorkspace.active.plan._projectDirty')
                    assert len(saves) == 1, saves
                    page.wait_for_function('''async id => (await relOffline.downloadedPlans()).some(row => row.plan_id === id)''', arg=original['id'])

                    # A later unsaved button must not contaminate the explicit download.
                    later = f'ALSO-NOT-SAVED-{kind}-{mobile}'
                    place_button(page, kind, later, mobile)
                    assert len(saves) == 1, saves
                    # Inspect durable browser storage, including ordinary automatic GET caches.
                    stored = page.evaluate('''async () => {
                        const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('reloncavi-offline-v1');r.onsuccess=()=>resolve(r.result);r.onerror=reject;});
                        async function rows(name) {return await new Promise(resolve=>{const r=db.transaction(name).objectStore(name).getAll();r.onsuccess=()=>resolve(r.result);});}
                        const texts=[];
                        for(const name of ['reads','downloadFiles']) for(const row of await rows(name)) {
                            texts.push(row.body instanceof Blob ? await row.body.text() : new TextDecoder().decode(row.body));
                        }
                        db.close(); return texts;
                    }''')
                    assert not any(temporary in text or later in text for text in stored), 'Unsaved button written to browser storage'
                    context.set_offline(True)
                    offline = page.evaluate('async url => await (await fetch(url)).json()', original['detail_url'])
                    offline_names = [row.get('label', row.get('name')) for row in offline['markers' if kind == 'pdf' else 'motors']]
                    assert confirmed in offline_names and temporary not in offline_names and later not in offline_names, offline_names
                    assert not offline['is_draft'] and not offline['permissions']['can_save_plan']
                    context.set_offline(False)
                    page.reload(); page.wait_for_function('appState.user?.username === "worker"'); open_published()
                    names = page.evaluate('(projectWorkspace.active.plan.markers || projectWorkspace.active.plan.motors).map(row => row.label || row.name)')
                    assert confirmed in names and temporary not in names and later not in names, names
                    owner.locator(f'#btn{prefix}PlanPermissions').click()
                    owner.locator('.plan-permission-users label', has_text='worker').locator('input').uncheck()
                    owner.locator('[data-permission-save]').click()
                    expect(owner.locator('.plan-permissions-dialog')).not_to_be_visible()
                    page.evaluate('projectWorkspace.listSaved({quiet:true,statusOnly:true})')
                    expect(page.locator(f'#btn{prefix}SavePlan')).to_be_hidden()
                    assert not errors, errors
                    context.close(); owner_context.close()
                    print(f'PASS: worker {kind} {"mobile" if mobile else "desktop"}: grant/revoke, reload discards unsaved buttons, explicit save only, confirmed offline copy', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import plan_collaboration_browser_smoke as harness
    harness.check = check
    harness.main()
