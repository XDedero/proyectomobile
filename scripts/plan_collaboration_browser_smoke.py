"""Real Chrome desktop/mobile checks using an isolated database and two accounts."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def serve():
    os.environ.update(DJANGO_SETTINGS_MODULE='proyectomobile.settings', APP_SHARED_ACCOUNTS='0', PLANOS_CLOUD_MODE='0')
    from django.conf import settings
    directory = Path(os.environ['PLAN_BROWSER_DIR'])
    settings.DATABASES['default']['NAME'] = directory / 'test.sqlite3'
    settings.MEDIA_ROOT = directory / 'media'
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', verbosity=0)
    from django.contrib.auth import get_user_model
    from mobileApp.models import UserProfile
    get_user_model().objects.create_superuser('admin', password='PruebaDXF!')
    worker = get_user_model().objects.create_user('worker', password='PruebaDXF!')
    UserProfile.objects.create(user=worker, rut='11111111-1', role='worker')
    call_command('runserver', '127.0.0.1:' + os.environ['PLAN_BROWSER_PORT'], use_reloader=False)


def check_absolute_navigation(page, context, kind, mobile):
    """Use actual wheel/touch input, never call zoom() to simulate a gesture."""
    zoom = "(() => { const v=projectWorkspace.active.viewer; return projectWorkspace.active.kind === 'pdf' ? v.scale / v.fitScale() : v.initial.width / v.view.width; })()"
    position = "(() => { const v=projectWorkspace.active.viewer; return projectWorkspace.active.kind === 'pdf' ? [v.container.scrollLeft,v.container.scrollTop] : [v.view.x,v.view.y]; })()"
    original_buttons = page.evaluate('JSON.stringify(projectWorkspace.active.plan.markers || projectWorkspace.active.plan.motors)')
    rect = page.locator('#fullscreenPlanViewport').bounding_box()
    cx, cy = rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2
    before = page.evaluate(zoom)
    if mobile:
        cdp = context.new_cdp_session(page)
        def touch(event, points):
            cdp.send('Input.dispatchTouchEvent', {'type': event, 'touchPoints': [
                {'id': i, 'x': x, 'y': y} for i, (x, y) in enumerate(points)]})
        touch('touchStart', [(cx - 35, cy), (cx + 35, cy)])
        touch('touchMove', [(cx - 80, cy), (cx + 80, cy)])
        touch('touchEnd', [])
    else:
        page.mouse.move(cx, cy)
        page.mouse.wheel(0, -240)
    page.wait_for_function(f'{zoom} > {before} * 1.2')
    enlarged = page.evaluate(zoom)
    previous_position = page.evaluate(position)
    if mobile:
        touch('touchStart', [(cx, cy)])
        touch('touchMove', [(cx - 40, cy - 25)])
        touch('touchEnd', [])
    else:
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.move(cx - 40, cy - 25, steps=6)
        page.mouse.up()
    page.wait_for_function(f'previous => JSON.stringify({position}) !== JSON.stringify(previous)', arg=previous_position)
    if mobile:
        touch('touchStart', [(cx - 80, cy), (cx + 80, cy)])
        touch('touchMove', [(cx - 35, cy), (cx + 35, cy)])
        touch('touchEnd', [])
        cdp.detach()
    else:
        page.mouse.wheel(0, 240)
    page.wait_for_function(f'{zoom} < {enlarged} * .9')
    assert page.evaluate('window.visualViewport.scale') == 1, 'Browser page zoom intercepted the drawing gesture'
    assert page.evaluate('JSON.stringify(projectWorkspace.active.plan.markers || projectWorkspace.active.plan.motors)') == original_buttons
    assert not page.evaluate('projectWorkspace.active.plan._projectDirty'), 'Navigation changed the saved plan'
    assert not page.locator('.workbook-editor-overlay').is_visible(), 'A gesture opened a button by accident'
    page.evaluate('projectWorkspace.active.viewer.fit()')
    print(f'PASS: absolute {kind} {"touch pinch in/out and one-finger pan" if mobile else "wheel in/out and mouse pan"}', flush=True)


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
                context = browser.new_context(viewport={'width': 412 if mobile else 1360, 'height': 915 if mobile else 900},
                                              is_mobile=mobile, has_touch=mobile)
                page = context.new_page()
                errors, publications = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: publications.append(request.url) if request.method == 'POST' and request.url.endswith('/save/') else None)
                page.goto(url); login(page, 'admin', open_import=False)
                for kind in ('pdf', 'dxf'):
                    page.locator('#btnGoToPdfSection' if kind == 'pdf' else '#btnGoToImportSection').click()
                    path = '**/api/pdf/plans/import/' if kind == 'pdf' else '**/api/plans/import/'
                    with page.expect_response(path) as imported:
                        page.locator('#pdfFileInput' if kind == 'pdf' else '#autocadFileInput').set_input_files(
                            directory / ('plano_pdf_configurable.pdf' if kind == 'pdf' else 'motores_demo.dxf'))
                    original = imported.value.json()
                    page.wait_for_function('(kind) => projectWorkspace.active?.kind === kind && projectWorkspace.active.plan.is_draft && !projectWorkspace.active.isBusy()', arg=kind)
                    prefix = 'Pdf' if kind == 'pdf' else 'Cad'
                    page.locator(f'#btn{prefix}Fullscreen').click()
                    page.locator('#btnPlanFullscreenMenu').click()
                    page.locator('[data-project-action="add"]').click()
                    page.locator('#newButtonCategory').select_option(label='Motores')
                    page.locator('#btnConfirmNewButton').click()
                    page.wait_for_function('projectWorkspace.active.viewer.placing')
                    target = page.locator('#pdfViewport .pdf-plan-page' if kind == 'pdf' else '#dxfViewport')
                    bounds = target.bounding_box()
                    with page.expect_response('**/markers/'):
                        if mobile:
                            page.touchscreen.tap(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .4)
                        else:
                            page.mouse.click(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .4)
                    page.wait_for_function('!projectWorkspace.active.isBusy() && projectWorkspace.active.plan._projectDirty')
                    assert not publications, publications
                    listing = page.request.get(url + 'api/projects/').json()['plans']
                    assert not any(row['kind'] == kind and row['plan_id'] == original['id'] for row in listing)
                    page.locator('#fullscreenSavePlan').click()
                    page.wait_for_function('projectWorkspace.active.plan.saved_at && !projectWorkspace.active.plan._projectDirty')
                    assert len(publications) == 1, publications
                    page.locator('#btnPlanFullscreenMenu').click()
                    page.locator('#fullscreenAbsolute').click()
                    page.wait_for_function('projectWorkspace.absolute')
                    geometry = page.locator('#fullscreenPlanViewport').bounding_box()
                    window_size = page.evaluate('({width:innerWidth,height:innerHeight})')
                    assert geometry['x'] == geometry['y'] == 0, geometry
                    assert abs(geometry['width'] - window_size['width']) < 2 and abs(geometry['height'] - window_size['height']) < 2, (geometry, window_size)
                    expect(page.locator('.plan-button-sidebar:visible')).to_have_count(0)
                    expect(page.locator('#fullscreenSavePlan')).to_be_hidden()
                    check_absolute_navigation(page, context, kind, mobile)
                    expect(page.locator('#absoluteBack')).to_have_css('opacity', '0', timeout=7000)
                    if mobile: page.touchscreen.tap(window_size['width'] - 10, 10)
                    else: page.mouse.move(1, 1)
                    expect(page.locator('#absoluteBack')).to_have_css('opacity', '1')
                    page.evaluate("() => (projectWorkspace.active.kind === 'pdf' ? pdfWorkflow : planWorkflow).deselect()")
                    physical = page.locator('[data-pdf-marker-id]' if kind == 'pdf' else '#dxfViewport [data-motor-id]').first
                    physical.click()
                    expect(page.locator('#workbookEditorClose')).to_be_visible()
                    page.wait_for_function('workbookEditor.visible && !workbookEditor.busy')
                    assert page.evaluate('projectWorkspace.absolute')
                    page.locator('#workbookEditorClose').click()
                    expect(page.locator('.workbook-editor-overlay')).to_be_hidden()
                    page.mouse.move(2, 2)
                    page.locator('#absoluteBack').click()
                    assert not page.evaluate('projectWorkspace.absolute')
                    expect(page.locator('#fullscreenSavePlan')).to_be_visible()
                    page.locator('#btnPlanFullscreenMenu').click()
                    page.locator('#fullscreenPermissions').click()
                    permission = page.locator('.plan-permission-users label', has_text='worker').locator('input')
                    expect(permission).to_be_visible(); permission.check()
                    page.locator('[data-permission-save]').click()
                    expect(page.locator('.plan-permissions-dialog')).not_to_be_visible()
                    page.locator('#btnExitPlanFullscreen').click()
                    other_context = browser.new_context()
                    other = other_context.new_page(); other.goto(url)
                    expect(other.locator('#loginForm')).to_be_visible()
                    login(other, 'worker', open_import=False)
                    other.wait_for_function('appState.user?.username === "worker"')
                    result = other.request.get(url + original['detail_url'].lstrip('/'))
                    assert result.status == 200 and result.json()['permissions']['can_assign']
                    assert not result.json()['permissions']['can_manage_plan_permissions']
                    other.evaluate("async plan => { await (plan.kind === 'pdf' ? pdfWorkflow : planWorkflow).open(plan); }", result.json())
                    expect(other.locator(f'#btn{prefix}PlanPermissions')).to_be_hidden()
                    other_context.close()
                    publications.clear()
                    page.locator('#btnPdfBack' if kind == 'pdf' else '#btnBackToMainMenu').click()
                    print(f'PASS: {kind} {"mobile" if mobile else "desktop"}: manual save, full viewport, idle/wake, direct Excel and delegated worker', flush=True)
                assert not errors, errors
                context.close()
        finally:
            browser.close()


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts, prefix='plan-collaboration-') as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        environment = {**os.environ, 'PLAN_BROWSER_DIR': temporary, 'PLAN_BROWSER_PORT': str(port), 'PYTHONUNBUFFERED': '1'}
        with (artifacts / 'plan-collaboration-browser.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen([sys.executable, __file__, '--serve'], cwd=ROOT, env=environment,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                url = f'http://127.0.0.1:{port}/'
                for _ in range(150):
                    if server.poll() is not None: raise RuntimeError('Test server failed; see browser log')
                    try:
                        with urlopen(url, timeout=1): break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('Test server did not start')
                check(url, Path(temporary))
            finally:
                server.terminate(); server.wait(timeout=10)


if __name__ == '__main__':
    serve() if '--serve' in sys.argv else main()
