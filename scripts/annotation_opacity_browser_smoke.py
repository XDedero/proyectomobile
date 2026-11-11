"""Verify the real PDF/DXF viewers combine the radius and general opacities.

Uses an isolated browser document; no database, network or production data.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'mobileApp' / 'static' / 'mobileApp'


def effective_opacity(locator):
    return locator.evaluate('''node => {
        let value = 1;
        for (let current = node; current; current = current.parentElement)
            value *= Number(getComputedStyle(current).opacity);
        return value;
    }''')


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.set_content('<div id="pdf"></div><div id="dxf" class="dxf-viewport"></div>')
            for filename in ['pdf-viewer.css', 'dxf.css', 'project-workflow.css']:
                page.add_style_tag(path=str(ASSETS / filename))
            for filename in ['pdf-viewer.js', 'dxf-viewer.js', 'plan-display.js']:
                page.add_script_tag(path=str(ASSETS / filename))
            page.evaluate('''() => {
                window.pdf = new PdfPlanViewer(document.getElementById('pdf'));
                pdf.stage.hidden = false;
                pdf.stage.style.width = '600px'; pdf.stage.style.height = '350px';
                pdf.pageNumber = 1;
                pdf.setMarkers([{id:1,page:1,x:.5,y:.5,label:'Radio PDF',
                    has_area:true,area_radius_x:.12,area_radius_y:.12,button_color:'#2563eb'}]);
                window.cad = new DxfViewer(document.getElementById('dxf'), () => {});
                cad.load('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 350"></svg>',
                    [{id:1,name:'Radio DXF',x:300,y:175,svg_x:300,svg_y:175,
                      svg_bounds:[295,170,10,10],has_area:true,button_color:'#2563eb'}]);
                window.contexts = [
                    {kind:'pdf',viewer:pdf,viewport:pdf.container,plan:{markers:pdf.markers}},
                    {kind:'dxf',viewer:cad,viewport:cad.container,plan:{motors:[cad.pins.get(1).motor]}}
                ];
            }''')
            for index, kind in enumerate(['pdf', 'dxf']):
                page.evaluate('index => planDisplay.render(contexts[index])', index)
                marker = page.locator('.pdf-plan-marker' if kind == 'pdf' else '.motor-target')
                radius = page.locator('.pdf-marker-coverage-area' if kind == 'pdf' else '.motor-coverage')
                slider = page.locator('#planAnnotationOpacity')

                def change(value):
                    slider.evaluate('(node,value) => {node.value=value;node.dispatchEvent(new Event("input"));}', value)

                change(23)
                assert abs(effective_opacity(marker) - .23) < .001
                assert abs(effective_opacity(radius) - .23 * .25) < .001
                marker.hover()
                assert effective_opacity(marker) == 1
                assert effective_opacity(radius) == .25
                page.mouse.move(0, 0)
                assert abs(effective_opacity(radius) - .23 * .25) < .001
                page.evaluate('index => contexts[index].viewer.select(1)', index)
                assert effective_opacity(marker) == 1
                assert effective_opacity(radius) == .25
                page.evaluate('index => contexts[index].viewer.select(null)', index)
                change(0)
                assert effective_opacity(marker) == 0
                assert effective_opacity(radius) == 0
                change(100)
                assert effective_opacity(marker) == 1
                assert effective_opacity(radius) == .25
                print(f'PASS: {kind.upper()} radius stays translucent at full opacity and fades with the slider', flush=True)
        finally:
            browser.close()


if __name__ == '__main__':
    main()
