"""Verify bounded PDF rasterization, real crop quality and gesture scheduling."""
from io import BytesIO
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def generate_plan(path):
    from PIL import Image, ImageDraw
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
    for page_number, (width, height) in enumerate([(12000, 8000), (8000, 12000)]):
        page = writer.add_blank_page(width=width, height=height)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        commands = ['0.1 0.2 0.4 RG 5 w 30 30 %d %d re S' % (width - 60, height - 60)]
        for row in range(24):
            for column in range(36):
                x, y = 80 + column * (width - 160) / 36, 80 + row * (height - 160) / 24
                commands.append(f'0.12 0.25 0.4 RG 2 w {x} {y} 115 80 re S '
                    f'0.1 0.1 0.1 rg BT /F1 28 Tf {x + 5} {y + 90} Td (M{row:02d}-{column:02d}) Tj ET')
        stream = DecodedStreamObject()
        stream.set_data(('\n'.join(commands)).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        if page_number == 1:
            page.cropbox.lower_left = (100, 200)
            page.cropbox.upper_right = (7900, 11800)
            page.rotate(90)
    page = writer.add_blank_page(width=1800, height=1200)
    image = Image.linear_gradient('L').resize((1800, 1200)).convert('RGB')
    draw = ImageDraw.Draw(image)
    for index in range(0, 1800, 60):
        draw.line((index, 0, index, 1200), fill='#c02030', width=3)
    for index in range(0, 1200, 60):
        draw.line((0, index, 1800, index), fill='#2040c0', width=3)
    bitmap = DecodedStreamObject()
    bitmap.set_data(image.tobytes())
    bitmap.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
        NameObject('/Width'): NumberObject(1800), NameObject('/Height'): NumberObject(1200),
        NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Scan'): writer._add_object(bitmap.flate_encode())})})
    stream = DecodedStreamObject()
    stream.set_data(b'q 1800 0 0 1200 0 0 cm /Scan Do Q')
    page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as target:
        writer.write(target)


def ready(page, detail=False):
    page.wait_for_function('''detail => {
        const viewer = projectWorkspace.active?.viewer;
        if (!viewer?.canvas || viewer.renderTask || viewer.overviewPromise || viewer.renderTimer) return false;
        if (!detail) return true;
        const area = viewer.visibleRegion(), old = viewer.detailRegion;
        return viewer.detailCanvas && old && area && old.scale === viewer.scale &&
            ['x','y','width','height'].every(key => Math.abs(old[key] - area[key]) < .5);
    }''', arg=detail, timeout=30000)


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
    from scripts.browser_smoke import login
    generate_plan(directory / 'planos_resolucion.pdf')
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 412, 'height': 915}, device_scale_factor=3, is_mobile=True, has_touch=True)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, 'admin', open_import=False)
            expect(page.locator('#viewMainMenu')).to_be_visible()
            page.locator('#btnGoToPdfSection').click()
            page.locator('#pdfFileInput').set_input_files(directory / 'planos_resolucion.pdf')
            expect(page.locator('#pdfViewport canvas')).to_be_visible(timeout=30000)
            ready(page)
            initial = page.evaluate('''() => {
                const v=projectWorkspace.active.viewer, c=v.canvas, box=v.stage.getBoundingClientRect();
                return {width:c.width,height:c.height,ratio:box.width/box.height,sourceRatio:v.baseViewport.width/v.baseViewport.height};
            }''')
            assert max(initial['width'], initial['height']) == 1280, initial
            assert abs(initial['ratio'] - initial['sourceRatio']) < .00001, initial
            page.evaluate('''() => {
                const v=projectWorkspace.active.viewer;
                window.pdfTestOriginalRender = v.page.render.bind(v.page);
                window.pdfTestCalls=[];
                v.page.render = settings => {
                    const call={width:settings.canvas.width,height:settings.canvas.height,skipped:0,total:0};
                    const filter=settings.operationsFilter;
                    if (filter) settings.operationsFilter=(...args) => {const keep=filter(...args);call.total++;if(!keep)call.skipped++;return keep;};
                    pdfTestCalls.push(call);
                    return pdfTestOriginalRender(settings);
                };
                for(let i=0;i<5;i++) v.zoom(1.5);
            }''')
            ready(page, detail=True)
            calls = page.evaluate('pdfTestCalls')
            assert 1 <= len(calls) <= 2, calls
            assert all(max(call['width'], call['height']) == 1280 for call in calls), calls
            assert calls[-1]['skipped'] > calls[-1]['total'] * .5, calls

            # Compare the optimized detail against an unfiltered PDF.js render
            # of precisely the same region, and against an enlarged overview.
            quality = page.evaluate('''async () => {
                const v=projectWorkspace.active.viewer, r=v.detailRegion, output=v.resolution/Math.max(r.width,r.height);
                const reference=document.createElement('canvas');
                reference.width=v.detailCanvas.width; reference.height=v.detailCanvas.height;
                await pdfTestOriginalRender({canvas:reference,viewport:v.page.getViewport({scale:r.scale}),
                    transform:[output,0,0,output,-r.x*output,-r.y*output],background:'#ffffff'}).promise;
                const preview=document.createElement('canvas'); preview.width=reference.width;preview.height=reference.height;
                preview.getContext('2d').drawImage(v.canvas,r.x/r.pageWidth*v.canvas.width,r.y/r.pageHeight*v.canvas.height,
                    r.width/r.pageWidth*v.canvas.width,r.height/r.pageHeight*v.canvas.height,0,0,preview.width,preview.height);
                const pixels=c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data;
                const actual=pixels(v.detailCanvas), expected=pixels(reference), enlarged=pixels(preview);
                let diff=0, previewDiff=0;
                for(let i=0;i<actual.length;i++){diff+=Math.abs(actual[i]-expected[i]);previewDiff+=Math.abs(actual[i]-enlarged[i]);}
                reference.width=reference.height=preview.width=preview.height=0;
                return {referenceError:diff/actual.length,previewDifference:previewDiff/actual.length};
            }''')
            assert quality['referenceError'] < .5, quality
            assert quality['previewDifference'] > .5, quality
            print(f'PASS: 1280px overview and detail preserve proportions; optimized crop matches PDF reference ({quality["referenceError"]:.4f} error) and restores detail lost by enlarging the overview', flush=True)

            page.locator('#pdfViewport').scroll_into_view_if_needed()
            ready(page, detail=True)
            page.evaluate('pdfTestCalls.length=0')
            box = page.locator('#pdfViewport').bounding_box()
            start = (box['x'] + box['width'] * .7, box['y'] + box['height'] * .65)
            page.mouse.move(*start)
            page.mouse.down()
            page.mouse.move(start[0] - 50, start[1] - 70, steps=12)
            page.wait_for_timeout(250)
            assert page.evaluate('pdfTestCalls.length') == 0
            page.mouse.up()
            ready(page, detail=True)
            assert 1 <= page.evaluate('pdfTestCalls.length') <= 2
            page.evaluate('projectWorkspace.active.viewer.zoom(1e6)')
            ready(page, detail=True)
            maximum = page.evaluate('''() => {
                const v=projectWorkspace.active.viewer;
                return {zoom:v.scale/v.fitScale(),pixels:[v.canvas,v.detailCanvas].reduce((sum,c)=>sum+(c?.width||0)*(c?.height||0),0)};
            }''')
            assert abs(maximum['zoom'] - 32) < .000001, maximum
            assert maximum['pixels'] <= 2 * 1280 * 1280, maximum
            count = page.evaluate('pdfTestCalls.length')
            page.evaluate('projectWorkspace.active.viewer.zoom(2)')
            page.wait_for_timeout(250)
            assert page.evaluate('pdfTestCalls.length') == count
            page.screenshot(path=str(ROOT / '.test-artifacts/pdf-1280-detail.png'), animations='disabled')
            page.evaluate('projectWorkspace.active.viewer.fit()')
            ready(page)
            assert page.evaluate('projectWorkspace.active.viewer.detailCanvas === null')
            assert page.evaluate('pdfTestCalls.length') == count
            print('PASS: rapid zoom coalesces renders; dragging uses the preview until release; 32x zoom and two bounded canvases keep raster memory independent of page size', flush=True)

            # Fullscreen remains sharp without moving marker coordinates.
            page.locator('#btnPdfFullscreen').click()
            ready(page)
            page.evaluate('''() => {
                const v=projectWorkspace.active.viewer;
                v.setMarkers([{id:999,page:1,x:.4,y:.45,label:'Marcador de referencia'}]);
                v.zoom(4);
            }''')
            ready(page, detail=True)
            alignment = page.evaluate('''() => {
                const v=projectWorkspace.active.viewer, s=v.stage.getBoundingClientRect(), b=v.buttons.get(999).getBoundingClientRect();
                return {x:(b.left+b.width/2-s.left)/s.width,y:(b.top+b.height/2-s.top)/s.height};
            }''')
            assert abs(alignment['x'] - .4) < .00001 and abs(alignment['y'] - .45) < .00001, alignment
            page.evaluate('window.previousPdfPage=projectWorkspace.active.viewer.page;window.previousPdfCanvases=[...document.querySelectorAll("#pdfViewport canvas")]')
            page.locator('#fullscreenPage').select_option('2')
            page.wait_for_function('projectWorkspace.active.viewer.pageNumber === 2')
            ready(page)
            rotated = page.evaluate('''() => {
                const v=projectWorkspace.active.viewer,b=v.stage.getBoundingClientRect();
                return {ratio:b.width/b.height,expected:11600/7800,released:previousPdfCanvases.every(c=>c.width===0&&c.height===0),boundsReleased:previousPdfPage.recordedBBoxes===null};
            }''')
            # CSS layout rounds dimensions to subpixels (usually 1/64 px).
            assert abs(rotated['ratio'] - rotated['expected']) < .0002, rotated
            assert rotated['released'] and rotated['boundsReleased'], rotated
            page.locator('#fullscreenPage').select_option('3')
            page.wait_for_function('projectWorkspace.active.viewer.pageNumber === 3')
            ready(page)
            page.evaluate('projectWorkspace.active.viewer.zoom(8)')
            ready(page, detail=True)
            assert page.locator('#pdfViewport canvas').evaluate_all('nodes => nodes.every(c=>Math.max(c.width,c.height)===1280)')
            page.set_viewport_size({'width': 915, 'height': 412})
            ready(page, detail=True)
            page.set_viewport_size({'width': 2560, 'height': 1440})
            ready(page, detail=True)
            assert page.locator('#pdfViewport canvas').evaluate_all('nodes => nodes.every(c=>Math.max(c.width,c.height)===1280)')
            page.evaluate('projectWorkspace.active.viewer.clear()')
            page.wait_for_timeout(250)
            expect(page.locator('#pdfViewport canvas')).to_have_count(0)
            assert not errors, errors
            print('PASS: marker alignment, fullscreen, rotated crop boxes, scanned PDFs, orientation changes and canvas/page cleanup remain correct at 1280px', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    from scripts import browser_smoke
    browser_smoke.check_browser = check_browser
    browser_smoke.main()
