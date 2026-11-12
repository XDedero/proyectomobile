"""Copy all sector shapes in PDF/DXF without altering originals or save semantics."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def check(url,directory):
    from playwright.sync_api import sync_playwright,expect
    from scripts.browser_smoke import login
    from scripts.generate_demo import generate
    from scripts.generate_pdf_demo import generate as generate_pdf
    generate(directory);generate_pdf(directory)
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='chrome',headless=True)
        try:
            for mobile in (False,True):
                for kind in ('pdf','dxf'):
                    context=browser.new_context(viewport={'width':412 if mobile else 1360,'height':915 if mobile else 960},is_mobile=mobile,has_touch=mobile)
                    page=context.new_page();errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(url);expect(page.locator('#loginForm')).to_be_visible();login(page,'admin',open_import=False)
                    page.locator('#btnGoToPdfSection' if kind=='pdf' else '#btnGoToImportSection').click()
                    with page.expect_response('**/api/pdf/plans/import/' if kind=='pdf' else '**/api/plans/import/') as imported:
                        page.locator('#pdfFileInput' if kind=='pdf' else '#autocadFileInput').set_input_files(directory/('plano_pdf_configurable.pdf' if kind=='pdf' else 'motores_demo.dxf'))
                    original=imported.value.json()
                    page.wait_for_function('projectWorkspace.active?.plan.is_draft && !projectWorkspace.active.isBusy()')
                    sources=page.evaluate('''async () => {
                        const p=projectWorkspace.active.plan, sources=[];
                        for(const [i,shape] of ['circle','square','rectangle','triangle','brush'].entries()) {
                            const sector={id:crypto.randomUUID(),name:'Sector '+shape,color:'#22c55e',shape,page:1,x:220+i*65,y:200,width:100,height:shape==='circle'||shape==='square'?100:80,rotation:25};
                            if(shape==='brush') Object.assign(sector,{brush_size:.1,strokes:[[[-.3,-.2],[.1,.2],[.3,.1]]]});
                            const response=await fetch(p.sectors_url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken()},body:JSON.stringify({action:'save',sector,previous:null})});
                            if(!response.ok) throw new Error(await response.text());
                            p.sectors=(await response.json()).sectors;sources.push(sector);
                        }
                        projectWorkspace.dirty();return sources;
                    }''')
                    prefix='Pdf' if kind=='pdf' else 'Cad'
                    toolbar=page.locator('#pdfSectorActions' if kind=='pdf' else '#cadSectorActions')
                    assert toolbar.locator('button').all_text_contents()==['Sectores','Editar sectores','Crear sector']
                    bounds=toolbar.bounding_box();viewport=page.locator('#pdfViewport' if kind=='pdf' else '#dxfViewport').bounding_box()
                    assert bounds['y']+bounds['height']<=viewport['y'],(bounds,viewport)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Sector toolbar overflows the device'
                    page.locator(f'#btn{prefix}Fullscreen').click()

                    def copy_source(source):
                        page.locator('#btnPlanFullscreenMenu').click();page.locator('#fullscreenSectors').click()
                        page.locator('#planSectorRows button').filter(has_text=source['name']).click()
                        page.locator('#btnCopyPlanSector').click()
                        expect(page.locator('#btnSectorSave')).to_be_visible()

                    copy_source(sources[0]);page.locator('#btnSectorCancel').click()
                    assert page.evaluate('projectWorkspace.active.plan.sectors.length')==5
                    for source in sources:
                        copy_source(source)
                        assert page.evaluate('projectWorkspace.active.plan.sectors.length')==5+sources.index(source)
                        selection=page.locator('.sector-selection').bounding_box()
                        x,y=selection['x']+selection['width']/2,selection['y']+selection['height']/2
                        if mobile:
                            cdp=context.new_cdp_session(page)
                            for event,points in [('touchStart',[{'x':x,'y':y,'id':0}]),('touchMove',[{'x':x+35,'y':y+20,'id':0}]),('touchEnd',[])]:
                                cdp.send('Input.dispatchTouchEvent',{'type':event,'touchPoints':points})
                            cdp.detach()
                        else:
                            page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+35,y+20,steps=5);page.mouse.up()
                        with page.expect_response('**/sectors/') as saved:
                            page.locator('#btnSectorSave').click()
                        assert saved.value.status==200,saved.value.text()
                        page.wait_for_function('!planSectors.editing')
                        sectors=page.evaluate('projectWorkspace.active.plan.sectors')
                        assert next(row for row in sectors if row['id']==source['id'])==source
                        copied=[row for row in sectors if row['name']==source['name'] and row['id']!=source['id']][0]
                        assert (copied['x'],copied['y'])!=(source['x'],source['y'])
                        assert {k:v for k,v in copied.items() if k not in ('id','x','y')}=={k:v for k,v in source.items() if k not in ('id','x','y')}
                        expect(page.locator('.plan-sector-legend li')).to_have_count(5)
                    assert page.request.get(url+original['detail_url'].lstrip('/')).json()['sectors']==[], 'Copy implicitly saved the plan'
                    page.locator('#fullscreenSavePlan').click();page.wait_for_function('!projectWorkspace.active.plan._projectDirty')
                    assert len(page.request.get(url+original['detail_url'].lstrip('/')).json()['sectors'])==10
                    page.locator('#btnExitPlanFullscreen').click()
                    expect(toolbar).to_be_visible()
                    assert not errors,errors
                    context.close()
                    print(f'PASS: {kind} {"mobile" if mobile else "desktop"}: toolbar above viewer, copy/move/cancel five shapes, shared legend, explicit plan save',flush=True)
        finally:browser.close()


if __name__=='__main__':
    from scripts import plan_collaboration_browser_smoke as harness
    harness.check=check
    harness.main()
