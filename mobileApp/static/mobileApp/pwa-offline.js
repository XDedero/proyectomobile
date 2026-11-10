/* Account-scoped offline reads and an explicit, version-checked edit outbox. */
(() => {
    const nativeFetch=window.fetch.bind(window), DB='reloncavi-offline-v1';
    const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('reloncavi-account'):null;
    let account=null, unavailable=false, syncing=false, disconnected=!navigator.onLine;
    const emit=()=>window.dispatchEvent(new Event('pwa-state-changed'));
    const opening=new Promise((resolve,reject)=>{
        const request=indexedDB.open(DB,2);
        request.onupgradeneeded=()=>{
            for(const name of ['meta','reads','readinfo','outbox']) if(!request.result.objectStoreNames.contains(name)) request.result.createObjectStore(name,{keyPath:'key'});
        };
        request.onsuccess=()=>{
            request.result.onversionchange=()=>{request.result.close();unavailable=true;emit();};
            resolve(request.result);
        };
        request.onerror=()=>reject(request.error);
        request.onblocked=()=>reject(new Error('Cierra las otras pestañas para preparar el almacenamiento local.'));
    }).catch(()=>{unavailable=true;emit();return null;});
    async function storage(store,mode,action) {
        const db=await opening;
        if(!db) throw new Error('No se puede guardar en este dispositivo. Comprueba el espacio disponible.');
        return new Promise((resolve,reject)=>{
            const tx=db.transaction(store,mode), request=action(tx.objectStore(store));
            tx.oncomplete=()=>resolve(request?.result);
            tx.onerror=tx.onabort=()=>reject(tx.error||new Error('No se pudo conservar el cambio en el dispositivo.'));
        });
    }
    const get=(store,key)=>storage(store,'readonly',s=>s.get(key));
    const put=(store,value)=>storage(store,'readwrite',s=>s.put(value));
    const remove=(store,key)=>storage(store,'readwrite',s=>s.delete(key));
    const all=store=>storage(store,'readonly',s=>s.getAll());
    async function writeRead(row) {
        const db=await opening;
        if(!db) throw new Error('No se puede guardar en este dispositivo.');
        return new Promise((resolve,reject)=>{
            const tx=db.transaction(['reads','readinfo'],'readwrite');
            tx.objectStore('reads').put(row);
            tx.objectStore('readinfo').put({key:row.key,type:row.type,time:row.time,size:row.body.byteLength});
            tx.oncomplete=resolve;tx.onerror=tx.onabort=()=>reject(tx.error);
        });
    }
    async function removeRead(key) {await remove('reads',key);await remove('readinfo',key);}
    const ready=get('meta','session').then(row=>{account=row?.user||null;}).catch(()=>{});
    channel?.addEventListener('message',()=>{get('meta','session').then(row=>{account=row?.user||null;emit();}).catch(()=>{});});
    const key=url=>`${account.id}|${new URL(url,location.href).href}`;
    const eligible=path=>/^\/api\/(?:plans\/(?:\d+\/(?:drawing\.svg)?)?|pdf\/plans\/(?:\d+\/(?:source\.pdf)?)?|pdf\/workbooks\/(?:\d+\/(?:metadata\/|cells\/))?|projects\/|projects\/(?:pdf|dxf)\/\d+\/(?:sectors\/|version\/)|buttons\/(?:pdf|dxf)\/\d+\/details\/)$/u.test(path);
    const pending=async()=>account?(await all('outbox')).filter(row=>row.user===account.id):[];
    async function rememberSession(response) {
        const data=await response.clone().json();
        account=data.user||null;
        if(account) await put('meta',{key:'session',user:account});
        else {
            await remove('meta','session');
            await storage('reads','readwrite',s=>s.clear());
            await storage('readinfo','readwrite',s=>s.clear());
        }
        channel?.postMessage('session');
        emit();
    }
    async function remember(url,response,owner) {
        if(!account||account.id!==owner||!response.ok||response.status===206) return;
        const body=await response.clone().arrayBuffer();
        if(body.byteLength>40*1024*1024) return;
        // Inspect only lightweight metadata; never load every PDF to cache one cell tile.
        const rows=await all('readinfo');
        let total=rows.reduce((sum,row)=>sum+row.size,0);
        for(const row of rows.sort((a,b)=>a.time-b.time)) {
            if(total+body.byteLength<=80*1024*1024) break;
            await removeRead(row.key);total-=row.size;
        }
        await writeRead({key:`${owner}|${url}`,body,type:response.headers.get('Content-Type')||'',time:Date.now()});
    }
    function offlineResponse(data) {
        return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json','X-Reloncavi-Offline':'1'}});
    }
    async function overlay(url,response) {
        if(!account||!response.headers.get('Content-Type')?.includes('json')) return response;
        const rows=await pending();
        const sectorRows=rows.filter(row=>row.kind==='sector' && (url.pathname===row.url || url.pathname===row.detail));
        const workbook=rows.find(row=>row.kind==='workbook' && url.pathname===row.metadataUrl);
        if(!sectorRows.length&&!workbook) return response;
        const data=await response.json();
        if(Array.isArray(data.sectors)) {
            for(const row of sectorRows) {
                const id=row.body.sector?.id||row.body.id;
                data.sectors=data.sectors.filter(sector=>sector.id!==id);
                if(row.body.action==='save') data.sectors.push(row.body.sector);
            }
        }
        if(workbook) data.offline_pending=true;
        return offlineResponse(data);
    }
    window.fetch=async (input,options={})=>{
        const url=new URL(input instanceof Request?input.url:input,location.href);
        if(url.origin!==location.origin) return nativeFetch(input,options);
        const method=(options.method||(input instanceof Request?input.method:'GET')).toUpperCase();
        const auth=/^\/api\/auth\/(?:session|login|logout)\/$/.test(url.pathname);
        if(!navigator.onLine && !auth && method!=='GET') throw new TypeError('Esta acción requiere conexión. Los sectores y las celdas de Excel pueden conservarse como borradores.');
        if(!auth && (method!=='GET'||!eligible(url.pathname))) {
            try {
                const response=await nativeFetch(input,options);
                if(url.pathname.startsWith('/api/')) {disconnected=false;emit();}
                return response;
            } catch(error) {
                if(error.name!=='AbortError' && url.pathname.startsWith('/api/')) {disconnected=true;emit();}
                throw error;
            }
        }
        await ready;
        const owner=account?.id;
        let response;
        try { response=await nativeFetch(input,options); }
        catch(error) {
            if(error.name==='AbortError') throw error;
            disconnected=true;emit();
            if(auth && url.pathname==='/api/auth/session/' && account) return offlineResponse({user:account,csrf_token:'',offline:true});
            if(method==='GET' && account && eligible(url.pathname)) {
                const row=await get('reads',key(url));
                if(row) return overlay(url,new Response(row.body,{headers:{'Content-Type':row.type,'X-Reloncavi-Offline':'1'}}));
                throw new Error('Este contenido aún no está descargado. Ábrelo con conexión antes de consultarlo sin señal.');
            }
            throw error;
        }
        disconnected=false;
        if(auth && response.ok) await rememberSession(response).catch(()=>{unavailable=true;emit();});
        else if(account && owner===account.id) {
            if([401,403,404].includes(response.status)) await removeRead(key(url)).catch(()=>{});
            if(response.status===401) await rememberSession(offlineResponse({user:null})).catch(()=>{});
            if(response.ok && response.headers.get('X-Reloncavi-User')===String(owner)) await remember(url.href,response,owner).catch(()=>{unavailable=true;emit();});
        }
        emit();
        return response.ok && method==='GET' && !auth?overlay(url,response):response;
    };
    async function queue(kind,url,body,extra={}) {
        await ready;
        if(!account) throw new Error('Inicia sesión con conexión antes de guardar cambios sin señal.');
        const identifier=kind==='sector'?(body.sector?.id||body.id):'cells';
        const entryKey=`${account.id}|${url}|${identifier}`;
        const old=await get('outbox',entryKey);
        if(old?.conflict) throw new Error('Hay un conflicto pendiente. Exporta el borrador antes de continuar.');
        if(old && kind==='sector') body={...body,previous:old.body.previous};
        if(old && kind==='workbook') {
            if(old.body.revision!==body.revision) throw new Error('La versión del Excel cambió. Revisa los cambios pendientes.');
            const cells=new Map([...old.body.edits,...body.edits].map(edit=>[`${edit.sheet}:${edit.row}:${edit.col}`,edit]));
            body={...body,edits:[...cells.values()]};
        }
        await put('outbox',{key:entryKey,user:account.id,kind,url,body,time:old?.time||Date.now(),...extra});
        emit();return body;
    }
    function transportFailure(error) {return error instanceof TypeError||error.name==='NetworkError'||!navigator.onLine||(disconnected&&!error.status);}
    const enqueue=(...args)=>navigator.locks?navigator.locks.request('reloncavi-outbox',()=>queue(...args)):queue(...args);
    async function sectorRequest(context,body,send) {
        const rows=await pending();
        const alreadyQueued=rows.some(row=>row.url===context.plan.sectors_url && row.kind==='sector');
        if(!alreadyQueued) {
            try {return await send();} catch(error) {if(!transportFailure(error)) throw error;}
        }
        const path=new URL(context.plan.sectors_url,location.href).pathname;
        await enqueue('sector',path,body,{detail:new URL(context.plan.detail_url,location.href).pathname});
        const sectors=(context.plan.sectors||[]).filter(sector=>sector.id!==(body.sector?.id||body.id));
        if(body.action==='save') sectors.push(body.sector);
        disconnected=true;emit();
        return {sectors,offline_pending:true};
    }
    async function workbookRequest(metadata,body,send) {
        const url=metadata.save_url||`/api/pdf/workbooks/${metadata.id}/save/`;
        const rows=await pending();
        if(!rows.some(row=>row.url===url)) {
            try {return await send();} catch(error) {if(!transportFailure(error)) throw error;}
        }
        await enqueue('workbook',url,body,{metadataUrl:metadata.metadata_url||`/api/pdf/workbooks/${metadata.id}/metadata/`});
        disconnected=true;emit();
        return {...metadata,offline_pending:true};
    }
    async function applyResult(row,result) {
        for(const info of await all('readinfo')) {
            if(!info.key.startsWith(`${row.user}|`) || !info.type.includes('json')) continue;
            const url=new URL(info.key.slice(info.key.indexOf('|')+1));
            const relevant=row.kind==='sector'?[row.detail,row.url].includes(url.pathname):[row.metadataUrl,row.metadataUrl.replace('metadata/','cells/')].includes(url.pathname);
            if(!relevant) continue;
            const cached=await get('reads',info.key);
            if(!cached) continue;
            let value;
            if(row.kind==='sector' && [row.detail,row.url].includes(url.pathname)) {
                value=JSON.parse(new TextDecoder().decode(cached.body));value.sectors=result.sectors;
            } else if(row.kind==='workbook' && url.pathname===row.metadataUrl) value=result;
            else if(row.kind==='workbook' && url.pathname===row.metadataUrl.replace('metadata/','cells/')) {
                value=JSON.parse(new TextDecoder().decode(cached.body));value.revision=result.revision;
                for(const edit of row.body.edits) {
                    if(edit.sheet!==url.searchParams.get('sheet')) continue;
                    const firstRow=Number(url.searchParams.get('row')),firstCol=Number(url.searchParams.get('col'));
                    if(edit.row<firstRow||edit.row>=firstRow+Number(url.searchParams.get('rows'))||edit.col<firstCol||edit.col>=firstCol+Number(url.searchParams.get('cols'))) continue;
                    let cell=value.cells?.find(cell=>cell.row===edit.row&&cell.col===edit.col);
                    if(!cell) {cell={row:edit.row,col:edit.col};(value.cells||=[]).push(cell);}
                    cell.value=edit.value;cell.display=edit.value==null?'':String(edit.value);
                }
            }
            if(value) {cached.body=new TextEncoder().encode(JSON.stringify(value)).buffer;cached.time=Date.now();await writeRead(cached);}
        }
    }
    async function sync() {
        if(syncing||!navigator.onLine) return;
        await ready;
        if(!account||!(await pending()).length) return;
        const owner=account.id;
        syncing=true;emit();
        try {
            const session=await nativeFetch('/api/auth/session/',{credentials:'same-origin',cache:'no-store'});
            if(!session.ok) return;
            const data=await session.json();
            if(!account||data.user?.id!==account.id) throw new Error('Inicia sesión con la misma cuenta para sincronizar los borradores.');
            for(const row of (await pending()).sort((a,b)=>a.time-b.time)) {
                if(row.conflict) continue;
                if(account?.id!==owner) break;
                const response=await nativeFetch(row.url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':data.csrf_token,'X-Reloncavi-Expected-User':String(owner)},body:JSON.stringify(row.body)});
                if(!response.ok) {
                    const result=await response.json().catch(()=>({}));
                    if([400,403,404,409].includes(response.status)) {
                        row.conflict=result.error||'El contenido cambió o ya no tienes permiso. Conservamos tu borrador.';
                        await put('outbox',row);continue;
                    }
                    throw new Error(result.error||'La sincronización no se completó. El borrador se conserva.');
                }
                const result=await response.json();
                await applyResult(row,result);
                // Remove only the sent revision, never a newer edit from another tab.
                const latest=await get('outbox',row.key);
                if(JSON.stringify(latest?.body)===JSON.stringify(row.body)) await remove('outbox',row.key);
            }
            disconnected=false;
        } finally {syncing=false;emit();}
    }
    async function draftsFor(metadata) {
        const url=metadata.save_url||`/api/pdf/workbooks/${metadata.id}/save/`;
        return (await pending()).find(row=>row.url===url);
    }
    window.relOffline={
        nativeFetch,sectorRequest,workbookRequest,draftsFor,
        get offline(){return disconnected||!navigator.onLine;},
        get unavailable(){return unavailable;},get syncing(){return syncing;},
        async state(){await ready;return {user:account,count:(await pending()).length,rows:await pending(),syncing,unavailable};},
        sync:async()=>navigator.locks?navigator.locks.request('reloncavi-outbox',sync):sync(),
        async exportDrafts(){return JSON.stringify({app:'Reloncaví',exported:new Date().toISOString(),changes:await pending()},null,2);},
        async discard(){for(const row of await pending()) await remove('outbox',row.key);emit();},
        async canLogout(){
            if((await pending()).length){window.relPWA?.open();window.relPWA?.message('Hay cambios pendientes. Sincronízalos o exporta y descarta los borradores antes de cerrar sesión.');return false;}
            return true;
        },
        async downloadPlan(context) {
            if(!context) throw new Error('Abre primero el plano que quieres consultar sin señal.');
            if(!navigator.onLine) throw new Error('Conéctate para descargar el plano.');
            if(!window.isSecureContext||!('serviceWorker' in navigator)) throw new Error('Abre la app desde HTTPS para preparar el uso sin conexión.');
            await new Promise((resolve,reject)=>{
                const timer=setTimeout(()=>reject(new Error('La app todavía está preparando su primera descarga. Vuelve a intentarlo en unos segundos.')),30000);
                navigator.serviceWorker.ready.then(()=>{clearTimeout(timer);resolve();},error=>{clearTimeout(timer);reject(error);});
            });
            const {plan,kind}=context;
            for(const url of [kind==='pdf'?'/api/pdf/plans/':'/api/plans/',plan.detail_url,plan.sectors_url,plan.source_url||plan.svg_url]) {
                if(!url) continue;
                const started=Date.now();
                const response=await window.fetch(url,{credentials:'same-origin',cache:'no-store'});
                if(!response.ok) throw new Error('No se pudo descargar todo el plano. Vuelve a intentarlo.');
                const saved=await get('reads',key(url));
                if(!saved||saved.time<started) throw new Error('No se pudo conservar todo el plano en el dispositivo. Comprueba el espacio y el límite de 40 MiB por archivo.');
            }
            if(unavailable) throw new Error('No se completó el guardado local. Comprueba el espacio disponible.');
            await navigator.storage?.persist?.();
        },
    };
    window.addEventListener('online',()=>{disconnected=false;window.relOffline.sync().catch(error=>window.relPWA?.message(error.message));});
    window.addEventListener('offline',()=>{disconnected=true;emit();});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden && navigator.onLine) window.relOffline.sync().catch(()=>{});});
    window.addEventListener('load',()=>window.relOffline.sync().catch(()=>{}));
})();
