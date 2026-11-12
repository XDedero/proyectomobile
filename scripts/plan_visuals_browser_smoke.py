"""Direct figure manipulation and transient annotation opacity in PDF and DXF."""
from io import BytesIO
import json
from pathlib import Path
import re
import sys
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def hit_point(page, identifier, selectable=True):
    return page.evaluate('''({id,selectable}) => {
        const active=projectWorkspace.active,s=active.plan.sectors.find(row=>row.id===id);
        const group=document.querySelector(`[data-sector-id="${id}"]`),matrix=group.getScreenCTM();
        for(const [u,v] of [[.2,.2],[-.2,.2],[0,.3],[.25,0],[-.25,0],[0,0]]) {
            const p=new DOMPoint(u*s.width,v*s.height).matrixTransform(matrix);
            const target=document.elementFromPoint(p.x,p.y);
            if(selectable && target?.closest('.sector-selectable')?.dataset.sectorId===id) return {x:p.x,y:p.y};
            if(!selectable && target && active.viewport.contains(target) &&
                !target.closest('[data-sector-id], [data-pdf-marker-id], [data-motor-id]')) return {x:p.x,y:p.y};
        }
        throw Error('No visible point on sector '+s.name);
    }''', {'id': identifier, 'selectable': selectable})


def drag(page, point, dx, dy):
    page.mouse.move(point['x'], point['y'])
    page.mouse.down()
    page.mouse.move(point['x'] + dx, point['y'] + dy, steps=5)
    page.mouse.up()


def opacity(page, value):
    page.locator('#planAnnotationOpacity').evaluate('''(slider,value) => {
        slider.value=value;slider.dispatchEvent(new Event('input',{bubbles:true}));
        slider.dispatchEvent(new Event('change',{bubbles:true}));
    }''', value)


def open_plan(page, url, kind, plan):
    data = page.request.get(url.rstrip('/') + plan['detail_url']).json()
    page.evaluate('''async ({kind,plan}) => {
        await (kind==='pdf'?pdfWorkflow:planWorkflow).open(plan);
    }''', {'kind': kind, 'plan': data})


def check_button_placement(page, kind, plan):
    from playwright.sync_api import expect

    viewport = page.locator('#pdfViewport' if kind == 'pdf' else '#dxfViewport')
    drawing = viewport.locator('.pdf-plan-page' if kind == 'pdf' else 'svg').first
    pending = viewport.locator('.plan-pin-pending')
    saved = viewport.locator('[data-pdf-marker-id]' if kind == 'pdf' else '[data-motor-id]')
    initial = saved.count()
    requests = []
    pattern = '**' + plan['markers_url']
    hold = lambda route: requests.append(route)
    page.route(pattern, hold)
    try:
        for reject in (True, False):
            page.evaluate('''async () => {
                const context = projectWorkspace.active;
                await context.setMode('add', {origin:'blank', category_id:context.plan.button_categories[0].id});
            }''')
            expect(viewport).to_have_class(re.compile('placing-marker'))
            box = drawing.bounding_box()
            point = {'x': box['width'] * .16, 'y': box['height'] * .2}
            if kind == 'dxf':
                point = drawing.evaluate('''svg => {
                    const bounds = svg.getBoundingClientRect();
                    const {x, y, width, height} = svg.viewBox.baseVal;
                    const point = new DOMPoint(x + width * .16, y + height * .2).matrixTransform(svg.getScreenCTM());
                    return {x:point.x-bounds.left, y:point.y-bounds.top};
                }''')
            with page.expect_request(pattern):
                drawing.click(position=point)
            expect(pending).to_be_visible()
            assert 'placing-marker' not in viewport.get_attribute('class')
            expect(saved).to_have_count(initial)
            drawing.click(position=point)
            assert len(requests) == 1
            route = requests.pop()
            if reject:
                route.fulfill(status=503, json={'error': 'Fallo simulado al guardar.'})
                expect(pending).to_have_count(0)
                expect(saved).to_have_count(initial)
            else:
                # DXF must confirm by adding a pin to the SVG already on screen.
                svg = drawing.element_handle() if kind == 'dxf' else None
                with page.expect_response(pattern) as response:
                    route.continue_()
                assert response.value.status == 201, response.value.text()
                expect(pending).to_have_count(0)
                expect(saved).to_have_count(initial + 1)
                if svg:
                    assert svg.evaluate('node => node.isConnected')
    finally:
        page.unroute(pattern, hold)


def check_button_sizes(page, kind):
    from playwright.sync_api import expect

    pin = page.locator('.pdf-plan-marker' if kind == 'pdf' else '.motor-pin').first
    diameter = '''node => node instanceof SVGCircleElement
        ? 2 * node.r.baseVal.value * node.ownerSVGElement.getScreenCTM().a : node.offsetWidth'''
    desktop = pin.evaluate(diameter)
    size = page.viewport_size
    page.set_viewport_size({'width': 390, 'height': 844})
    try:
        page.wait_for_function('''({kind,desktop}) => {
            const pin = document.querySelector(kind==='pdf'?'.pdf-plan-marker':'.motor-pin');
            const width = pin instanceof SVGCircleElement
                ? 2 * pin.r.baseVal.value * pin.ownerSVGElement.getScreenCTM().a : pin?.offsetWidth;
            return width >= 18 && width < desktop;
        }''', arg={'kind': kind, 'desktop': desktop})
        expect(pin).to_be_visible()
    finally:
        page.set_viewport_size(size)


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
            page.route('**/api/projects/cloud/', lambda route: route.fulfill(json={'plans': []}))
            page.goto(url)
            login(page, 'empleado', open_import=False)
            expect(page.locator('#viewMainMenu')).to_be_visible()
            for kind, prefix in [('pdf', 'Pdf'), ('dxf', 'Cad')]:
                if not page.locator('#viewMainMenu').is_visible():
                    page.locator('#btnPdfBack' if kind == 'dxf' else '#btnBackToMainMenu').click()
                page.locator('#btnGoToPdfSection' if kind == 'pdf' else '#btnGoToImportSection').click()
                with page.expect_response('**/api/pdf/plans/import/' if kind == 'pdf' else '**/api/plans/import/') as response:
                    page.locator('#pdfFileInput' if kind == 'pdf' else '#autocadFileInput').set_input_files(
                        directory / ('plano_pdf_configurable.pdf' if kind == 'pdf' else 'motores_demo.dxf'))
                plan = response.value.json()
                expect(page.locator('#pdfViewport canvas').first if kind == 'pdf' else page.locator('.motor-target').first).to_be_visible()
                expect(page.locator(f'#btn{prefix}EditSectors')).to_be_hidden()
                check_button_placement(page, kind, plan)
                check_button_sizes(page, kind)
                print(f'PASS: {kind.upper()} responsive pins, immediate preview, rollback and confirmation', flush=True)
                dims = page.evaluate('''() => {
                    const {kind,viewer}=projectWorkspace.active;
                    const d=kind==='pdf'?viewer.baseViewport:viewer.initial,unit=Math.max(d.width,d.height)/1000;
                    return {width:d.width/unit,height:d.height/unit};
                }''')
                size = min(dims.values()) * .24
                sectors = []
                for index, shape in enumerate(['rectangle', 'circle', 'square', 'triangle']):
                    sector = {'id': str(uuid4()), 'name': f'{kind} {shape}', 'shape': shape, 'color': '#3b82f6', 'page': 1,
                              'x': dims['width'] * (.25 if index % 2 == 0 else .73),
                              'y': dims['height'] * (.28 if index < 2 else .64), 'width': size, 'height': size, 'rotation': 15}
                    result = page.request.post(url.rstrip('/') + plan['sectors_url'], data={'action': 'save', 'sector': sector},
                                               headers={'X-CSRFToken': page.evaluate('csrfToken()')})
                    assert result.status == 200, result.text()
                    sectors.append(sector)
                brush = {'id': str(uuid4()), 'name': 'Pincel', 'shape': 'brush', 'color': '#22c55e', 'page': 1,
                         'x': dims['width'] * .48, 'y': dims['height'] * .86, 'width': size, 'height': size, 'rotation': 0,
                         'brush_size': .2, 'strokes': [[[-.4, 0], [.4, 0]]]}
                result = page.request.post(url.rstrip('/') + plan['sectors_url'], data={'action': 'save', 'sector': brush},
                                           headers={'X-CSRFToken': page.evaluate('csrfToken()')})
                assert result.status == 200
                open_plan(page, url, kind, plan)
                expect(page.locator(f'#btn{prefix}EditSectors')).to_be_visible()
                expect(page.locator('.sector-selectable')).to_have_count(0)
                page.locator(f'#btn{prefix}Fullscreen').click()
                expect(page.get_by_text('Vista de botones', exact=True)).to_have_count(0)
                expect(page.locator('.plan-color-legend')).to_have_count(0)
                expect(page.locator('.plan-display-dock input:visible')).to_have_count(1)
                edit = page.locator('#fullscreenEditSectors')
                drag(page, hit_point(page, sectors[0]['id'], selectable=False), 18, 9)
                expect(page.locator('.sector-editor-dock')).to_be_hidden()
                expect(edit).to_have_attribute('aria-pressed', 'false')
                # Once editing is enabled, each figure moves on its first gesture.
                for original in sectors:
                    edit.click()
                    expect(edit).to_have_attribute('aria-pressed', 'true')
                    drag(page, hit_point(page, original['id']), 18, 9)
                    expect(page.locator('.sector-editor-dock')).to_be_visible()
                    expect(page.locator('#planSectorSettings')).to_be_hidden()
                    expect(page.locator('.sector-handle')).to_have_count(9)
                    edge = page.locator('[data-sector-handle="e"]').bounding_box()
                    drag(page, {'x': edge['x'] + edge['width']/2, 'y': edge['y'] + edge['height']/2}, 22, 4)
                    if original['shape'] == 'rectangle':
                        page.locator('#sectorEditShape').select_option('triangle')
                        page.screenshot(path=str(ROOT / f'.test-artifacts/{kind}-figure-direct.png'))
                    with page.expect_response('**/sectors/') as saved:
                        page.locator('#btnSectorSave').click()
                    assert saved.value.status == 200, saved.value.text()
                    changed = next(row for row in saved.value.json()['sectors'] if row['id'] == original['id'])
                    assert changed['x'] != original['x'] and changed['width'] != original['width']
                    assert changed['height'] == original['height']
                    expect(edit).to_have_attribute('aria-pressed', 'false')
                    expect(page.locator('.sector-selectable')).to_have_count(0)
                assert 'sector-selectable' not in page.locator(f'[data-sector-id="{brush["id"]}"]').get_attribute('class')
                # A painted region lets a tap pass through to plan navigation.
                p = page.evaluate('''id=>{
                    const s=projectWorkspace.active.plan.sectors.find(row=>row.id===id);
                    const matrix=document.querySelector(`[data-sector-id="${id}"]`).getScreenCTM();
                    const p=new DOMPoint(0,0).matrixTransform(matrix);return {x:p.x,y:p.y};
                }''', brush['id'])
                page.mouse.click(p['x'], p['y'])
                expect(page.locator('.sector-editor-dock')).to_be_hidden()
                print(f'PASS: {kind.upper()} free navigation, explicit sector editing and return to explore', flush=True)

                posts = []
                page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' and request.url.endswith('/display/') else None)
                opacity(page, 23)
                expect(page.locator('#planAnnotationOpacityValue')).to_have_text('23 %')
                page.locator('#fullscreenSavePlan').click()
                expect(page.locator(f'#{prefix.lower()}SaveBadge' if kind == 'pdf' else '#cadSaveBadge')).to_contain_text('Guardado')
                expect(page.locator('#planAnnotationOpacity')).to_have_value('23')
                page.evaluate('''()=>{
                    const a=projectWorkspace.active;
                    if(a.kind==='pdf') pdfWorkflow.select(a.plan.markers[0].id);
                    else planWorkflow.select(a.plan.motors[0].id);
                }''')
                # Clear the highlight before checking that zero hides annotations.
                page.evaluate('''()=>{
                    if(projectWorkspace.active.kind==='pdf') pdfWorkflow.deselect();
                    else planWorkflow.deselect();
                    document.activeElement?.blur();
                }''')
                page.mouse.move(0, 0)
                opacity(page, 0)
                marker = page.locator('.pdf-plan-marker' if kind == 'pdf' else '.motor-target').first
                expect(marker).to_have_css('opacity', '0')
                expect(page.locator('.plan-sectors-layer')).to_be_hidden()
                page.screenshot(path=str(ROOT / f'.test-artifacts/{kind}-bare-plan.png'))
                page.locator('#fullscreenSavePlan').click()
                expect(page.locator('#pdfSaveBadge' if kind == 'pdf' else '#cadSaveBadge')).to_contain_text('Guardado')
                archive = page.request.get(url.rstrip('/') + plan['export_project_url'])
                with ZipFile(BytesIO(archive.body())) as saved:
                    assert json.loads(saved.read('manifest.json'))['display_settings'] == {}
                assert posts == [], posts
                page.reload()
                page.wait_for_function("appState.user?.username === 'empleado'")
                open_plan(page, url, kind, plan)
                expect(page.locator('#planAnnotationOpacity')).to_have_value('100')
                expect(marker).to_be_visible()
                print(f'PASS: {kind.upper()} opacity hides all annotations, never posts/saves/exports and resets on reload', flush=True)

                if kind == 'pdf':
                    # Touch selection works without the settings modal; a reader
                    # can adjust opacity independently but cannot select figures.
                    for user in ['empleado', 'lector']:
                        mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
                        touch = mobile.new_page()
                        touch.on('pageerror', lambda error: errors.append(str(error)))
                        touch.goto(url)
                        login(touch, user, open_import=False)
                        touch.wait_for_function('name=>appState.user?.username===name', arg=user)
                        open_plan(touch, url, kind, plan)
                        touch.locator('#btnPdfFullscreen').click()
                        expect(touch.locator('#planAnnotationOpacity')).to_be_enabled()
                        expect(touch.locator('.sector-selectable')).to_have_count(0)
                        if user == 'empleado':
                            touch.locator('#fullscreenEditSectors').tap()
                            p = hit_point(touch, sectors[0]['id'])
                            figure = touch.locator(f'[data-sector-id="{sectors[0]["id"]}"]')
                            before = figure.get_attribute('transform')
                            session = mobile.new_cdp_session(touch)
                            session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [p]})
                            session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': p['x'] + 12, 'y': p['y'] + 8}]})
                            session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
                            expect(touch.locator('.sector-editor-dock')).to_be_visible()
                            expect(touch.locator('#planSectorSettings')).to_be_hidden()
                            assert figure.get_attribute('transform') != before
                            touch.locator('#btnSectorCancel').tap()
                            assert figure.get_attribute('transform') == before
                            expect(touch.locator('#fullscreenEditSectors')).to_have_attribute('aria-pressed', 'false')
                        else:
                            expect(touch.locator('#fullscreenEditSectors')).to_be_hidden()
                            expect(touch.locator('.sector-selectable')).to_have_count(0)
                        opacity(touch, 0)
                        expect(touch.locator('.pdf-plan-marker')).to_have_css('opacity', '0')
                        touch.screenshot(path=str(ROOT / f'.test-artifacts/opacity-mobile-{user}.png'))
                        mobile.close()
                    print('PASS: touch selection and read-only opacity on narrow mobile screens', flush=True)
            assert not errors, errors
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
