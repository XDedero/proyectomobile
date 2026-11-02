"""Check a real motor workbook in an isolated application, never the user's DB.

Run with --workbook PATH to compare an existing XLSX, or omit it for a fixture.
"""

import argparse
from io import BytesIO
from pathlib import Path
import sys
import re
from urllib.parse import urljoin
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def check_browser(url, directory, source):
    from playwright.sync_api import sync_playwright, expect
    from scripts.browser_smoke import login
    artifact_prefix = 'motor-ficha' if source else 'motor-fixture'
    if source is None:
        from mobileApp.test_motor_forms import motor_form_workbook
        source = directory / 'motor-forms.xlsx'
        source.write_bytes(motor_form_workbook())
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(viewport={'width': 1280, 'height': 960})
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            login(page, open_import=False)
            from scripts.generate_pdf_demo import generate
            generate(directory)
            page.locator('#btnGoToPdfSection').click()
            page.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            expect(page.locator('#pdfViewport canvas')).to_be_visible()
            page.locator('#btnPdfAddMarker').click()
            stage = page.locator('#pdfViewport .pdf-plan-page').bounding_box()
            page.mouse.click(stage['x'] + stage['width']*.3, stage['y'] + stage['height']*.3)
            expect(page.locator('#workbookEditorSheet')).to_have_value('1')
            page.locator('#workbookEditorClose').click()
            page.locator('#btnPdfConfigureMarker').click()
            expect(page.locator('#pdfMarkerDialog')).to_be_visible()
            with page.expect_response('**/api/pdf/markers/*/workbook/') as uploaded:
                page.locator('#pdfExcelFileInput').set_input_files(source)
            marker = uploaded.value.json()['marker']
            document = page.request.get(urljoin(url, marker['workbook']['metadata_url'])).json()
            expect(page.locator('#workbookEditorSheet')).to_have_value('Listado')
            expect(page.locator('.workbook-editor-panel')).not_to_have_class(re.compile(r'\bis-form-view\b'))
            for name in [sheet['name'] for sheet in document['sheets'] if sheet['name'].isdigit()]:
                page.locator('#workbookEditorSheet').select_option(name)
                expect(page.locator('.workbook-editor-panel')).to_have_class('workbook-editor-panel is-form-view')
                expect(page.locator('.wbe-sheet-chart')).to_have_count(2)
                page.wait_for_function('''() => [...document.querySelectorAll('.wbe-sheet-image')].every(image => image.complete && image.naturalWidth > 0)''')
                expect(page.locator('#wbe-cell-6-2')).to_contain_text('FICHA')
                expect(page.locator('#wbe-cell-64-2')).to_have_text('OBSERVACIONES')
                geometry = page.evaluate('''() => {
                    const editor = window.workbookEditor;
                    const canvas = editor.canvas.getBoundingClientRect(), viewport = editor.viewport.getBoundingClientRect();
                    const title = document.querySelector('#wbe-cell-6-2').getBoundingClientRect();
                    return { canvas: {width:canvas.width,height:canvas.height}, viewport: {width:viewport.width,height:viewport.height},titleWidth:title.width,zoom:editor.zoom };
                }''')
                assert geometry['canvas']['width'] <= geometry['viewport']['width'] + 1, geometry
                assert geometry['canvas']['height'] <= geometry['viewport']['height'] + 1, geometry
                assert abs(geometry['titleWidth'] - geometry['canvas']['width']) < 1, geometry
                if name == '1':
                    page.locator('.wbe-grid-canvas').screenshot(path=str(ROOT / f'.test-artifacts/{artifact_prefix}-1.png'))
                    page.screenshot(path=str(ROOT / f'.test-artifacts/{artifact_prefix}-desktop.png'))
            print('PASS: all numbered sheets retain full form proportions, logos, merged titles and both charts', flush=True)
            page.locator('#workbookEditorSheet').select_option('1')
            page.locator('#workbookEditorFitWidth').click()
            page.locator('#workbookEditorZoom').select_option('1')
            page.locator('#workbookEditorAddress').fill('C18')
            page.locator('#workbookEditorGo').click()
            page.locator('#workbookEditorFormula').fill('Motor de prueba')
            page.locator('#workbookEditorSave').click()
            expect(page.locator('#workbookEditorStatus')).to_contain_text('Cambios de Excel guardados')
            expect(page.locator('.wbe-sheet-chart')).to_have_count(2)
            page.locator('#workbookEditorPresentation').click()
            expect(page.locator('.workbook-editor-panel')).not_to_have_class(re.compile(r'\bis-form-view\b'))
            page.locator('#workbookEditorAddress').fill('AE350')
            page.locator('#workbookEditorGo').click()
            expect(page.locator('#workbookEditorAddress')).to_have_value('AE350')
            saved = page.request.get(urljoin(url, document['download_url'])).body()
            with ZipFile(BytesIO(source.read_bytes())) as original, ZipFile(BytesIO(saved)) as downloaded:
                assert original.namelist() == downloaded.namelist()
                for part in original.namelist():
                    if not part.startswith('xl/worksheets/sheet'):
                        assert original.read(part) == downloaded.read(part), part
            print('PASS: zoom, full-grid navigation and edits preserve original chart/drawing/style bytes', flush=True)
            page.locator('#workbookEditorClose').click()
            context.close()
            mobile = browser.new_context(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            reader = mobile.new_page()
            reader.on('pageerror', lambda error: errors.append(str(error)))
            reader.goto(url)
            login(reader, 'lector', open_import=False)
            reader.locator('#btnSavedPlans').click()
            reader.locator('#savedPlansContainer button').first.click()
            expect(reader.locator('#pdfViewport canvas')).to_be_visible()
            reader.locator(f'[data-pdf-marker-id="{marker["id"]}"]').click()
            reader.locator('#workbookEditorSheet').select_option('1')
            expect(reader.locator('#workbookEditorSave')).to_be_hidden()
            expect(reader.locator('.wbe-sheet-chart')).to_have_count(2)
            reader.wait_for_function("!!document.querySelector('#wbe-cell-18-3')?.textContent.includes('Motor de prueba')")
            reader.screenshot(path=str(ROOT / f'.test-artifacts/{artifact_prefix}-mobile.png'))
            denied = reader.request.post(urljoin(url, document['save_url']), data={'revision': 2, 'edits': [{'sheet': '1', 'row': 18, 'col': 3, 'value': 'No permitido'}]})
            assert denied.status == 403
            mobile.close()
            assert not errors, errors
            print('PASS: readers see the same motor form and saved edits on mobile without editing rights', flush=True)
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workbook', type=Path)
    args = parser.parse_args()
    source = args.workbook.resolve() if args.workbook else None
    from scripts import browser_smoke
    browser_smoke.check_browser = lambda url, directory: check_browser(url, directory, source)
    browser_smoke.main()


if __name__ == '__main__':
    main()
