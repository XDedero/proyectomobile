/* Optional, plan-owned regions shared by the PDF and DXF workspaces. */
(() => {
    const byId=id=>document.getElementById(id);
    // getRandomValues also works in mobile WebViews served on a local network.
    const newId=()=>crypto.randomUUID?.() || '10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>
        (Number(c) ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> Number(c)/4).toString(16));
    const svgNode=(name,attrs={})=>{
        const node=document.createElementNS('http://www.w3.org/2000/svg',name);
        for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,value);
        return node;
    };
    function dialog(id,title,body) {
        const node=document.createElement('div'); node.id=id; node.className='modal fade'; node.tabIndex=-1;
        node.setAttribute('aria-labelledby',id+'Title');
        node.innerHTML=`<div class="modal-dialog modal-dialog-centered modal-dialog-scrollable"><div class="modal-content">
          <div class="modal-header"><h2 class="modal-title fs-5" id="${id}Title">${title}</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
          <div class="modal-body">${body}<p class="small text-danger mt-2 mb-0" role="status"></p></div></div></div>`;
        document.body.append(node); return {node,modal:new bootstrap.Modal(node)};
    }
    const list=dialog('planSectorsModal','Sectores del plano',`
      <p class="small text-muted">Un botón pertenece al sector cuando más del 50 % de su superficie queda dentro. Puede pertenecer a varios sectores superpuestos.</p>
      <div class="d-flex gap-2 mb-3"><button id="btnCreatePlanSector" class="btn btn-primary" type="button">Crear sector</button><button id="btnSectorBack" class="btn btn-outline-secondary" type="button" hidden>Todos los sectores</button></div>
      <div id="planSectorRows" class="sector-list"></div><div id="planSectorMembers" hidden></div>`);
    const settings=dialog('planSectorSettings','Crear sector',`
      <label class="form-label" for="sectorName">Nombre del sector</label>
      <input id="sectorName" class="form-control mb-3" maxlength="120" required>
      <label class="form-label" for="sectorShape">Forma</label>
      <select id="sectorShape" class="form-select mb-3"><option value="brush">Pincel libre</option><option value="circle">Circular</option><option value="square">Cuadrada</option><option value="rectangle">Rectangular</option><option value="triangle">Triangular</option></select>
      <label class="form-label" for="sectorColor">Color del sector</label>
      <div class="d-flex gap-2 align-items-center mb-3"><input type="color" id="sectorColor" class="form-control form-control-color" value="#22c55e"><div id="sectorPalette" class="sector-palette"></div></div>
      <p class="small text-muted">Después podrás dibujar, mover, girar y cambiar el tamaño directamente sobre el plano.</p>
      <button id="btnStartSectorDrawing" type="button" class="btn btn-primary w-100">Dibujar en el plano</button>`);
    for(const color of ['#22c55e','#3b82f6','#eab308','#ef4444','#a855f7','#f97316']) {
        const button=document.createElement('button'); button.type='button';button.style.background=color;
        button.title=color;button.setAttribute('aria-label','Color '+color);button.onclick=()=>{byId('sectorColor').value=color;};
        byId('sectorPalette').append(button);
    }
    const dock=document.createElement('section');dock.className='sector-editor-dock';dock.hidden=true;
    dock.setAttribute('aria-label','Editar sector sobre el plano');
    dock.innerHTML=`<div class="d-flex flex-wrap align-items-center gap-2"><strong id="sectorEditingName" class="text-break me-auto"></strong>
      <button id="btnSectorSave" type="button" class="btn btn-success btn-sm">Guardar sector</button><button id="btnSectorCancel" type="button" class="btn btn-outline-secondary btn-sm">Cancelar</button></div>
      <div id="sectorFigureTools" class="d-flex flex-wrap align-items-center gap-2 mt-2">
        <label class="small d-flex align-items-center gap-2">Forma <select id="sectorEditShape" class="form-select form-select-sm">
          <option value="circle">Círculo / óvalo</option><option value="square">Cuadrado</option><option value="rectangle">Rectángulo</option><option value="triangle">Triángulo</option>
        </select></label>
        <label class="small"><input type="checkbox" id="sectorKeepRatio"> Mantener proporciones</label>
      </div>
      <p id="sectorDrawingHint" class="small my-2" role="status"></p>
      <div id="sectorBrushTools" class="d-flex flex-wrap align-items-center gap-2 mb-2" hidden>
        <button type="button" id="btnSectorPaint" class="btn btn-outline-primary btn-sm" aria-pressed="true">Pintar</button>
        <button type="button" id="btnSectorTransform" class="btn btn-outline-primary btn-sm" aria-pressed="false">Mover / ajustar</button>
        <button type="button" id="btnSectorUndo" class="btn btn-outline-secondary btn-sm">Deshacer trazo</button>
        <label class="small">Pincel <input type="range" id="sectorBrushWidth" min="4" max="120" value="30" aria-label="Grosor del pincel"></label>
      </div>
      <label class="small d-flex align-items-center gap-2">Color del sector <input type="color" id="sectorEditColor" class="form-control form-control-color"></label>`;
    document.body.append(dock);
    let current=null,layer=null,editing=null,gesture=null,saving=false,paint=false,memberSector=null,configTarget=null;
    const editable=()=>Boolean(current?.plan.permissions?.can_assign);
    const selectable=sector=>sector?.shape!=='brush' && editable() && !editing && !saving && !current.isBusy?.() &&
        !current.viewer.placing && !current.viewer.moving && !current.viewport.classList.contains('plan-annotations-hidden');
    const valid=context=>context && appState.user && window.projectWorkspace?.active?.plan===context.plan;
    const clone=value=>JSON.parse(JSON.stringify(value));
    const status=(target,text)=>{target.node.querySelector('[role=status]').textContent=text;};
    function dimensions(context=current) {
        const viewer=context?.viewer;
        const width=context?.kind==='pdf'?viewer.baseViewport?.width:viewer?.initial?.width;
        const height=context?.kind==='pdf'?viewer.baseViewport?.height:viewer?.initial?.height;
        if(!width||!height) return null;
        const unit=Math.max(width,height)/1000;
        return {width:width/unit,height:height/unit,unit,page:context.kind==='pdf'?viewer.pageNumber:1};
    }
    function point(event) {
        const matrix=layer?.getScreenCTM();
        if(!matrix) return null;
        return new DOMPoint(event.clientX,event.clientY).matrixTransform(matrix.inverse());
    }
    function removeLayer() {layer?.remove();layer=null;}
    function ensureLayer() {
        const dims=dimensions();if(!dims) return false;
        const viewer=current.viewer;
        const host=current.kind==='pdf'?viewer.stage:viewer.svg;
        if(!host) return false;
        if(layer?.parentNode!==host) {
            removeLayer();layer=svgNode(current.kind==='pdf'?'svg':'g',{'class':'plan-sectors-layer','aria-label':'Sectores del plano'});
            if(current.kind==='pdf') host.insertBefore(layer,viewer.layer);
            else host.insertBefore(layer,host.querySelector('.motor-layer'));
        }
        if(current.kind==='pdf') {
            layer.setAttribute('viewBox',`0 0 ${dims.width} ${dims.height}`);
            layer.setAttribute('preserveAspectRatio','none');
        } else layer.setAttribute('transform',`translate(${viewer.initial.x} ${viewer.initial.y}) scale(${dims.unit})`);
        layer.classList.toggle('editing',Boolean(editing));
        return true;
    }
    function drawSector(sector,selected=false) {
        const group=svgNode('g',{'data-sector-id':sector.id,'class':'plan-sector',transform:`translate(${sector.x} ${sector.y}) rotate(${sector.rotation})`});
        if(selectable(sector)) {
            group.classList.add('sector-selectable');
            group.setAttribute('tabindex','0');group.setAttribute('role','button');
            group.setAttribute('aria-label',`Seleccionar sector ${sector.name}`);
        }
        const content=svgNode('g',{'class':'sector-figure',transform:`scale(${sector.width} ${sector.height})`,fill:sector.color});
        if(sector.shape==='brush') {
            // One path paints overlapping strokes once, keeping opacity uniform.
            const path=svgNode('path',{d:(sector.strokes||[]).map(stroke=>stroke.length===1?`M ${stroke[0]} l .000001 0`:`M ${stroke.map(p=>p.join(' ')).join(' L ')}`).join(' '),
                fill:'none',stroke:sector.color,'stroke-width':sector.brush_size,'stroke-linecap':'round','stroke-linejoin':'round'});
            content.append(path);
        } else if(sector.shape==='circle') content.append(svgNode('circle',{r:.5}));
        else if(sector.shape==='triangle') content.append(svgNode('polygon',{points:'0,-.5 .5,.5 -.5,.5'}));
        else content.append(svgNode('rect',{x:-.5,y:-.5,width:1,height:1}));
        content.setAttribute('opacity',.3);group.append(content);
        const text=svgNode('text',{x:0,y:0,'class':'sector-label','text-anchor':'middle'});text.textContent=sector.name;
        group.append(text);
        if(selected) {
            const width=sector.width,height=sector.height;
            const rect=svgNode('rect',{x:-width/2,y:-height/2,width,height,'class':'sector-selection','data-sector-handle':'move'});
            group.append(rect);
            const pixel=1/(layer.getScreenCTM()?.a?Math.hypot(layer.getScreenCTM().a,layer.getScreenCTM().b):1);
            const handleSize=9*pixel;
            for(const [id,x,y] of [['nw',-width/2,-height/2],['n',0,-height/2],['ne',width/2,-height/2],['e',width/2,0],
                ['se',width/2,height/2],['s',0,height/2],['sw',-width/2,height/2],['w',-width/2,0]]) {
                group.append(svgNode('rect',{x:x-handleSize,y:y-handleSize,width:handleSize*2,height:handleSize*2,'class':'sector-handle','data-sector-handle':id}));
            }
            const rotateY=-height/2-32*pixel;
            group.append(svgNode('line',{x1:0,y1:-height/2,x2:0,y2:rotateY,stroke:'#1d4ed8','vector-effect':'non-scaling-stroke'}));
            group.append(svgNode('circle',{cx:0,cy:rotateY,r:handleSize,'class':'sector-handle sector-rotation','data-sector-handle':'rotate'}));
        }
        layer.append(group);
    }
    function draw() {
        if(!current||!ensureLayer()) return;
        layer.replaceChildren();
        const dims=dimensions();
        for(const sector of current.plan.sectors||[]) if(sector.page===dims.page && sector.id!==editing?.draft.id) drawSector(sector);
        if(editing && editing.draft.page===dims.page) drawSector(editing.draft,!paint);
    }
    function syncFields() {
        if(!editing) return;
        const s=editing.draft;
        byId('sectorEditColor').value=s.color;
        byId('sectorEditingName').textContent=s.name;
        byId('sectorBrushTools').hidden=s.shape!=='brush';
        byId('sectorFigureTools').hidden=s.shape==='brush';
        if(s.shape!=='brush') byId('sectorEditShape').value=s.shape;
        byId('btnSectorPaint').setAttribute('aria-pressed',String(paint));
        byId('btnSectorTransform').setAttribute('aria-pressed',String(!paint));
        byId('sectorDrawingHint').textContent=paint?'Dibuja sobre el plano. Puedes añadir varios trazos; «Mover / ajustar» permite transformar el sector.':'Arrastra la figura para moverla, sus bordes o esquinas para estirarla y el punto superior para girarla. Mayús mantiene las proporciones.';
    }
    function beginEditing(draft,previous) {
        editing={context:current,previous,draft};paint=draft.shape==='brush';
        byId('sectorKeepRatio').checked=false;
        current.viewport.classList.add('sector-editing');dock.hidden=false;syncFields();draw();
        window.projectWorkspace.update();
    }
    function stopEditing() {
        gesture=null;editing=null;paint=false;dock.hidden=true;
        if(current) current.viewport.classList.remove('sector-editing');
        for(const id of ['fullscreenCreateSector','btnPdfCreateSector','btnCadCreateSector','fullscreenSectors','btnPdfSectors','btnCadSectors']) {
            if(byId(id)) byId(id).disabled=Boolean(current?.isBusy?.());
        }
        draw();
    }
    async function hide(target) {
        if(!target.node.classList.contains('show')) return;
        await new Promise(resolve=>{target.node.addEventListener('hidden.bs.modal',resolve,{once:true});target.modal.hide();});
    }
    async function request(context,body) {
        const response=await fetch(context.plan.sectors_url,{credentials:'same-origin',cache:'no-store',
            ...(body?{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken()},body:JSON.stringify(body)}:{})});
        const data=await response.json();
        if(!response.ok) throw new Error(data.error||'No se pudieron guardar los sectores.');
        if(!valid(context)) return null;
        context.plan.sectors=data.sectors;
        draw();return data;
    }
    async function openList() {
        if(!current||editing||!(await window.workbookEditor.requestClose())) return;
        const context=current;memberSector=null;status(list,'Cargando sectores…');
        byId('planSectorRows').replaceChildren();byId('planSectorMembers').hidden=true;
        byId('planSectorRows').hidden=false;byId('btnSectorBack').hidden=true;
        byId('btnCreatePlanSector').hidden=!editable();list.modal.show();
        try {if(await request(context)) {renderList();status(list,'');}}
        catch(error) {if(valid(context)) status(list,error.message);}
    }
    function renderList() {
        const rows=byId('planSectorRows');rows.replaceChildren();
        for(const sector of current.plan.sectors||[]) {
            const button=document.createElement('button');button.type='button';button.className='btn btn-outline-secondary text-start sector-row';
            const swatch=document.createElement('span');swatch.className='sector-swatch';swatch.style.background=sector.color;
            const name=document.createElement('span');name.textContent=sector.name+(current.kind==='pdf'?` · Página ${sector.page}`:'');
            button.append(swatch,name);button.onclick=()=>showMembers(sector);rows.append(button);
        }
        if(!rows.childElementCount) rows.textContent='Este plano no tiene sectores. Puedes crearlos cuando los necesites.';
    }
    function footprint(button) {
        const d=dimensions();
        if(current.kind==='pdf') {
            const element=current.viewer.buttons.get(button.id),stage=current.viewer.stage.getBoundingClientRect();
            const radius=element && button.page===d.page ? element.offsetWidth/2/stage.width*d.width : 17 * (button.button_scale ?? 1);
            return {page:button.page,x:button.x*d.width,y:button.y*d.height,radius};
        }
        const scale=current.viewer.svg.getScreenCTM()?.a||1;
        return {page:1,x:(button.svg_x-current.viewer.initial.x)/d.unit,y:(button.svg_y-current.viewer.initial.y)/d.unit,radius:12*(button.button_scale ?? 1)/scale/d.unit};
    }
    async function showMembers(sector) {
        // Load the selected PDF page so membership uses the same geometry and
        // visible button footprint as its drawing, including rotated pages.
        const context=current;
        if(context.kind==='pdf' && context.viewer.pageNumber!==sector.page) await context.viewer.setPage(sector.page);
        if(!valid(context)) return;
        memberSector=sector;
        byId('planSectorRows').hidden=true;byId('btnSectorBack').hidden=false;
        const body=byId('planSectorMembers');body.hidden=false;body.replaceChildren();
        const title=document.createElement('h3');title.className='h6 text-break';title.textContent=sector.name;body.append(title);
        if(editable()) {
            const actions=document.createElement('div');actions.className='d-flex gap-2 mb-3';
            const edit=document.createElement('button');edit.type='button';edit.className='btn btn-outline-primary btn-sm';edit.textContent='Editar sector';edit.onclick=()=>configure(sector);
            const remove=document.createElement('button');remove.type='button';remove.className='btn btn-outline-danger btn-sm';remove.textContent='Eliminar sector';
            remove.onclick=async()=>{
                if(saving || !confirm(`¿Eliminar el sector «${sector.name}»? Sus botones se conservarán.`)) return;
                saving=true;remove.disabled=true;
                try {
                    if(await request(context,{action:'delete',id:sector.id,previous:sector})) {
                        window.projectWorkspace.dirty();byId('btnSectorBack').click();
                    }
                } catch(error){status(list,error.message);}
                finally{saving=false;remove.disabled=false;draw();}
            };
            actions.append(edit,remove);body.append(actions);
        }
        const rows=document.createElement('div');rows.className='sector-list';body.append(rows);
        const buttons=context.kind==='pdf'?context.plan.markers:context.plan.motors;
        let count=0;
        for(const [index,button] of buttons.entries()) {
            if((button.page||1)!==sector.page || !SectorGeometry.contains(sector,footprint(button))) continue;
            count++;
            const row=document.createElement('button');row.type='button';row.className='btn btn-outline-secondary text-start';
            row.textContent=[button.label||button.name||`Botón ${index+1}`,button.category_name,button.sheet_name||button.source_sheet||'Sin hoja'].filter(Boolean).join(' · ');
            row.onclick=async()=>{await hide(list);if(!valid(context))return;
                if(context.kind==='pdf') window.pdfWorkflow.select(button.id,false);
                else window.planWorkflow.select(button.id,true);
            };rows.append(row);
        }
        const total=document.createElement('p');total.className='small text-muted';total.textContent=`${count} ${count===1?'botón pertenece':'botones pertenecen'} a este sector.`;title.after(total);
        if(!count) rows.textContent='No hay botones con más del 50 % de su superficie dentro del sector.';
    }
    async function configure(sector=null) {
        if(!editable()||editing||!(await window.workbookEditor.requestClose())) return;
        const context=current;
        await hide(list);if(!valid(context)) return;
        configTarget={context,previous:sector?clone(sector):null};
        byId('planSectorSettingsTitle').textContent=sector?'Editar sector':'Crear sector';
        byId('sectorName').value=sector?.name||'';byId('sectorShape').value=sector?.shape||'brush';
        byId('sectorColor').value=sector?.color||'#22c55e';status(settings,'');settings.modal.show();
    }
    byId('btnStartSectorDrawing').onclick=async()=>{
        const target=configTarget;
        if(!target||!valid(target.context)||!editable()) return;
        const name=byId('sectorName').value.trim();if(!name) {status(settings,'Escribe un nombre para el sector.');return;}
        const shape=byId('sectorShape').value,color=byId('sectorColor').value;
        await hide(settings);if(!valid(target.context)) return;
        await current.setMode('explore');
        const d=dimensions();if(!d) return;
        const previous=target.previous;
        let draft=previous?clone(previous):{id:newId(),page:d.page,x:d.width/2,y:d.height/2,width:Math.min(d.width,d.height)*.35,height:Math.min(d.width,d.height)*.35,rotation:0};
        if(!previous) {
            // Start shapes in the visible portion of a zoomed or panned plan.
            ensureLayer();const rect=current.viewport.getBoundingClientRect();
            const center=point({clientX:rect.left+rect.width/2,clientY:rect.top+rect.height/2});
            if(center) {draft.x=Math.max(0,Math.min(d.width,center.x));draft.y=Math.max(0,Math.min(d.height,center.y));}
        }
        if(shape==='brush' && previous?.shape!=='brush') {draft.strokes=[];draft.brush_size=.1;}
        if((!previous||shape!==previous.shape)&&(shape==='circle'||shape==='square')) draft.height=draft.width;
        if(shape==='rectangle' && !previous) draft.height=draft.width*.6;
        Object.assign(draft,{shape,name,color});
        beginEditing(draft,previous);
    };
    byId('btnCreatePlanSector').onclick=()=>configure();
    byId('btnSectorBack').onclick=()=>{memberSector=null;byId('planSectorMembers').hidden=true;byId('planSectorRows').hidden=false;byId('btnSectorBack').hidden=true;renderList();status(list,'');};
    byId('btnSectorCancel').onclick=()=>{if(!saving)stopEditing();};
    byId('btnSectorSave').onclick=async()=>{
        const draft=editing;if(!draft||saving||gesture) return;
        if(draft.draft.shape==='brush'&&!draft.draft.strokes?.length) {byId('sectorDrawingHint').textContent='Dibuja al menos un trazo antes de guardar.';return;}
        saving=true;dock.querySelectorAll('button,input,select').forEach(node=>{node.disabled=true;});
        try {
            if(await request(draft.context,{action:'save',sector:draft.draft,previous:draft.previous})) {
                window.projectWorkspace.dirty();stopEditing();
            }
        }catch(error){byId('sectorDrawingHint').textContent=error.message;}
        finally{saving=false;dock.querySelectorAll('button,input,select').forEach(node=>{node.disabled=false;});draw();}
    };
    byId('btnSectorPaint').onclick=()=>{paint=true;syncFields();draw();};
    byId('btnSectorTransform').onclick=()=>{paint=false;syncFields();draw();};
    byId('btnSectorUndo').onclick=()=>{editing?.draft.strokes?.pop();draw();};
    byId('sectorEditShape').onchange=()=>{
        if(!editing||saving||editing.draft.shape==='brush') return;
        editing.draft.shape=byId('sectorEditShape').value;
        if(['circle','square'].includes(editing.draft.shape)) editing.draft.height=editing.draft.width;
        syncFields();draw();
    };
    byId('sectorEditColor').oninput = event => {
        if(!editing||saving) return;
        editing.draft.color = event.target.value;
        draw();
    };
    function localPoint(p,s) {
        const angle=-s.rotation*Math.PI/180,dx=p.x-s.x,dy=p.y-s.y;
        return [(dx*Math.cos(angle)-dy*Math.sin(angle))/s.width,(dx*Math.sin(angle)+dy*Math.cos(angle))/s.height];
    }
    function pointerDown(event) {
        if(!current||saving||!valid(current)||event.button!==0||!layer) return;
        if(gesture) {event.preventDefault();event.stopImmediatePropagation();return;}
        let handle=event.target.closest('[data-sector-handle]')?.dataset.sectorHandle;
        if(!editing) {
            const id=event.target.closest('.sector-selectable')?.dataset.sectorId;
            const sector=current.plan.sectors?.find(row=>row.id===id);
            if(!sector||!selectable(sector)) return;
            // Select and start moving on the initial press: no modal or second click.
            current.setMode('explore');
            beginEditing(clone(sector),clone(sector));handle='move';
        }
        const p=point(event),d=dimensions();if(!p) return;
        if(!paint&&!handle) return;
        if(paint&&(p.x<0||p.y<0||p.x>d.width||p.y>d.height)) return;
        event.preventDefault();event.stopImmediatePropagation();
        if(gesture) return;
        current.viewport.setPointerCapture(event.pointerId);
        const s=editing.draft;
        gesture={id:event.pointerId,start:p,before:clone(s),handle:paint?'paint':handle};
        if(paint) {gesture.points=[[p.x,p.y]];gesture.previous=clone(s.strokes||[]);paintStroke();}
    }
    function paintStroke() {
        const s=editing.draft,g=gesture;
        const previous=g.previous.map(stroke=>stroke.map(p=>SectorGeometry.transform(g.before,p)));
        const strokes=[...previous,g.points],pts=strokes.flat(),r=Number(byId('sectorBrushWidth').value)/2;
        const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
        const left=Math.min(...xs)-r,right=Math.max(...xs)+r,top=Math.min(...ys)-r,bottom=Math.max(...ys)+r;
        s.x=(left+right)/2;s.y=(top+bottom)/2;s.width=s.height=Math.max(right-left,bottom-top);s.rotation=0;
        s.brush_size=r*2/s.width;
        s.strokes=strokes.map(stroke=>stroke.map(p=>[(p[0]-s.x)/s.width,(p[1]-s.y)/s.height]));
        draw();syncFields();
    }
    function pointerMove(event) {
        if(!gesture||event.pointerId!==gesture.id) return;
        event.preventDefault();event.stopImmediatePropagation();
        const p=point(event);if(!p) return;
        const g=gesture,s=editing.draft,b=g.before,d=dimensions();
        if(g.handle==='paint') {
            const last=g.points[g.points.length-1];
            if(Math.hypot(p.x-last[0],p.y-last[1])<1) return;
            if(g.points.length>=5000) return;
            g.points.push([Math.max(0,Math.min(d.width,p.x)),Math.max(0,Math.min(d.height,p.y))]);paintStroke();return;
        }
        if(g.handle==='move') {s.x=Math.max(0,Math.min(d.width,b.x+p.x-g.start.x));s.y=Math.max(0,Math.min(d.height,b.y+p.y-g.start.y));}
        else if(g.handle==='rotate') {
            const a=Math.atan2(g.start.y-b.y,g.start.x-b.x),angle=Math.atan2(p.y-b.y,p.x-b.x);
            s.rotation=((b.rotation+(angle-a)*180/Math.PI+540)%360)-180;
        } else {
            Object.assign(s,SectorGeometry.resize(b,g.handle,p,byId('sectorKeepRatio').checked||event.shiftKey));
            s.x=Math.max(0,Math.min(d.width,s.x));s.y=Math.max(0,Math.min(d.height,s.y));
        }
        draw();syncFields();
    }
    function pointerUp(event) {
        if(!gesture||event.pointerId!==gesture.id) return;
        event.preventDefault();event.stopImmediatePropagation();
        if(event.type==='pointercancel') editing.draft=gesture.before;
        gesture=null;
        if(current.viewport.hasPointerCapture(event.pointerId))current.viewport.releasePointerCapture(event.pointerId);
        draw();syncFields();
    }
    const bound=new WeakSet();
    function render(context) {
        if(!context) {stopEditing();removeLayer();current=null;list.modal.hide();settings.modal.hide();return;}
        if(current?.plan!==context.plan) {stopEditing();removeLayer();list.modal.hide();settings.modal.hide();}
        current=context;
        if(editing&&!editable()) stopEditing();
        for(const prefix of ['Pdf','Cad']) {
            const relevant=context.kind===(prefix==='Pdf'?'pdf':'dxf');
            byId(`btn${prefix}CreateSector`).hidden=!relevant||!editable();
        }
        byId('fullscreenCreateSector').hidden=!editable();
        for(const id of ['fullscreenCreateSector','btnPdfCreateSector','btnCadCreateSector','fullscreenSectors','btnPdfSectors','btnCadSectors']) byId(id).disabled=Boolean(editing)||Boolean(context.isBusy?.());
        if(!bound.has(context.viewport)) {
            bound.add(context.viewport);
            context.viewport.addEventListener('pointerdown',pointerDown,true);
            context.viewport.addEventListener('pointermove',pointerMove,true);
            context.viewport.addEventListener('pointerup',pointerUp,true);
            context.viewport.addEventListener('pointercancel',pointerUp,true);
            context.viewport.addEventListener('contextmenu',event=>{
                if(event.target.closest('.sector-selectable,[data-sector-handle]')) event.preventDefault();
            });
            context.viewport.addEventListener('keydown',event=>{
                if(!['Enter',' '].includes(event.key)||editing) return;
                const id=event.target.closest('.sector-selectable')?.dataset.sectorId;
                const sector=current?.plan.sectors?.find(row=>row.id===id);
                if(!sector||!selectable(sector)) return;
                event.preventDefault();event.stopImmediatePropagation();
                current.setMode('explore');beginEditing(clone(sector),clone(sector));byId('btnSectorSave').focus();
            },true);
            context.viewport.addEventListener('plan-view-changed',()=>{if(current?.viewport===context.viewport)draw();});
        }
        if(editing && dimensions()?.page!==editing.draft.page) stopEditing();
        draw();
    }
    for(const prefix of ['Pdf','Cad']) {
        for(const [suffix,label,action] of [['Sectors','Sectores',openList],['CreateSector','Crear sector',()=>configure()]]) {
            const button=document.createElement('button');button.id=`btn${prefix}${suffix}`;button.type='button';button.className='btn btn-outline-primary btn-sm';button.textContent=label;button.onclick=action;
            byId(`btn${prefix}ExportProject`).before(button);
        }
    }
    for(const [id,label,action] of [['fullscreenSectors','Sectores',openList],['fullscreenCreateSector','Crear sector',()=>configure()]]) {
        const button=document.createElement('button');button.id=id;button.type='button';button.textContent=label;
        button.onclick=()=>{byId('btnPlanFullscreenMenu').click();action();};byId('fullscreenPlanMenu').append(button);
    }
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&editing&&!saving) {event.preventDefault();stopEditing();}});
    window.planSectors={render,open:openList,create:()=>configure(),get editing(){return Boolean(editing);}};
    render(window.projectWorkspace?.active);
})();
