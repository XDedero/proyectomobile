/* Account-scoped offline reads and an explicit, version-checked edit outbox. */
(() => {
    const nativeFetch=window.fetch.bind(window), DB='reloncavi-offline-v1';
    const MiB=1024*1024, maxFile=40*MiB, maxDownloads=240*MiB;
    const channel=typeof BroadcastChannel==='function'?new BroadcastChannel('reloncavi-account'):null;
    let account=null, unavailable=false, syncing=false, disconnected=!navigator.onLine;
    const emit=()=>window.dispatchEvent(new Event('pwa-state-changed'));
    const opening=new Promise((resolve,reject)=>{
        const request=indexedDB.open(DB,3);
        request.onupgradeneeded=()=>{
            const db=request.result;
            for(const name of ['meta','reads','readinfo','outbox']) if(!db.objectStoreNames.contains(name)) db.createObjectStore(name,{keyPath:'key'});
            if(!db.objectStoreNames.contains('downloads')) {
                db.createObjectStore('downloads',{keyPath:'key'}).createIndex('user','user');
                db.createObjectStore('downloadFiles',{keyPath:'key'}).createIndex('accountUrl','accountUrl');
            }
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
    async function updateStores(names,action) {
        const db=await opening;
        if(!db) throw new Error('No se puede guardar en este dispositivo.');
        return new Promise((resolve,reject)=>{
            const tx=db.transaction(names,'readwrite');
            let failure;
            tx.oncomplete=resolve;
            tx.onabort=()=>reject(failure||tx.error||new Error('No se pudo actualizar el almacenamiento local.'));
            action(tx,message=>{failure=new Error(message);tx.abort();});
        });
    }
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
    function removeRead(key) {
        return updateStores(['reads','readinfo'],tx=>{
            tx.objectStore('reads').delete(key);tx.objectStore('readinfo').delete(key);
        });
    }
    async function purgeTransientCaches(owner) {
        if(!owner) return;
        const paths=new Set(), obsolete=[];
        function include(plan) {
            for(const url of [plan.detail_url,plan.source_url,plan.svg_url,plan.sectors_url,plan.version_url,
                ...[plan.workbook,...(plan.markers||plan.motors||[]).map(row=>row.workbook||row.excel)]
                    .filter(Boolean).flatMap(book=>[book.metadata_url,book.cells_url,book.download_url])]) {
                if(url) paths.add(new URL(url,location.href).pathname);
            }
        }
        for(const copy of await all('downloads')) if(copy.user===owner) {
            const file=await downloadedFile(owner,copy.detail_url);
            const data=file?await file.body.text().then(JSON.parse).catch(()=>null):null;
            if(data?.is_draft) {include(data);obsolete.push(copy);}
        }
        const reads=(await all('readinfo')).filter(row=>row.key.startsWith(owner+'|'));
        for(const row of reads) {
            const path=new URL(row.key.slice(String(owner).length+1)).pathname;
            if(!/^\/api\/(?:pdf\/)?plans\/\d+\/$/.test(path)) continue;
            const cached=await get('reads',row.key);
            const data=cached?await Promise.resolve().then(()=>JSON.parse(new TextDecoder().decode(cached.body))).catch(()=>null):null;
            if(data?.is_draft) include(data);
        }
        if(!paths.size&&!obsolete.length) return;
        await updateStores(['reads','readinfo','downloads','downloadFiles'],tx=>{
            for(const row of reads) {
                const path=new URL(row.key.slice(String(owner).length+1)).pathname;
                if([...paths].some(prefix=>path===prefix||(prefix.endsWith('/')&&path.startsWith(prefix)))) {
                    tx.objectStore('reads').delete(row.key);tx.objectStore('readinfo').delete(row.key);
                }
            }
            for(const copy of obsolete) {
                tx.objectStore('downloads').delete(copy.key);
                for(const url of copy.urls) tx.objectStore('downloadFiles').delete(copy.key+'|'+url);
            }
        });
    }
    const ready=get('meta','session').then(async row=>{
        account=row?.user||null;
        await purgeTransientCaches(account?.id);
    }).catch(()=>{});
    channel?.addEventListener('message',event=>{
        if(event.data==='downloads') window.dispatchEvent(new Event('pwa-downloads-changed'));
        else get('meta','session').then(row=>{account=row?.user||null;emit();}).catch(()=>{});
    });
    const key=url=>`${account.id}|${new URL(url,location.href).href}`;
    const eligible=path=>/^\/api\/(?:plans\/(?:\d+\/(?:drawing\.svg)?)?|pdf\/plans\/(?:\d+\/(?:source\.pdf)?)?|pdf\/workbooks\/(?:\d+\/(?:metadata\/|cells\/|download\/))?|projects\/|projects\/(?:pdf|dxf)\/\d+\/(?:sectors\/|version\/)|buttons\/(?:pdf|dxf)\/\d+\/details\/)$/u.test(path);
    const proxyUnavailable=response=>[502,503,504].includes(response.status)&&!response.headers.has('X-Reloncavi-User');
    const pending=async()=>account?(await all('outbox')).filter(row=>row.user===account.id):[];
    const downloadedFile=(owner,url)=>storage('downloadFiles','readonly',
        store=>store.index('accountUrl').get(owner+'|'+new URL(url,location.href).href));
    async function downloadedPlans() {
        await ready;
        const owner=account?.id;
        if(!owner) return [];
        const rows=await storage('downloads','readonly',store=>store.index('user').getAll(owner));
        // Older releases downloaded private editing copies. They must not be
        // presented as saved drawings or revive buttons that were never saved.
        const published=[];
        for(const row of rows) {
            const detail=await downloadedFile(owner,row.detail_url);
            const data=detail?await detail.body.text().then(JSON.parse).catch(()=>null):null;
            if(data && !data.is_draft && !data.has_pending_changes) published.push(row);
        }
        return account?.id===owner?published:[];
    }
    async function writeDownload(plan,files) {
        await updateStores(['meta','downloads','downloadFiles'],(tx,abort)=>{
            const session=tx.objectStore('meta').get('session');
            session.onsuccess=()=>{
                if(session.result?.user.id!==plan.user) {
                    abort('La sesión cambió durante la descarga.');return;
                }
                const downloads=tx.objectStore('downloads'), objects=tx.objectStore('downloadFiles');
                const listing=downloads.getAll();
                listing.onsuccess=()=>{
                    const samePlan=row=>row.user===plan.user&&row.kind===plan.kind&&(
                        row.key===plan.key||row.plan_id===plan.plan_id||(row.cloud_key&&row.cloud_key===plan.cloud_key)
                    );
                    const previous=listing.result.filter(samePlan);
                    if(previous.some(row=>row.time>plan.time)) {
                        abort('Ya hay una descarga más reciente de este plano.');return;
                    }
                    const total=listing.result.reduce((bytes,row)=>bytes+(samePlan(row)?0:row.size),plan.size);
                    if(total>maxDownloads) {
                        abort('Libera espacio quitando una copia descargada antes de guardar este plano.');return;
                    }
                    for(const copy of previous) {
                        for(const url of copy.urls) objects.delete(copy.key+'|'+url);
                        downloads.delete(copy.key);
                    }
                    for(const {url,body} of files) objects.put({key:plan.key+'|'+url,accountUrl:plan.user+'|'+url,time:plan.time,body});
                    downloads.put(plan);
                };
            };
        });
        channel?.postMessage('downloads');
        window.dispatchEvent(new Event('pwa-downloads-changed'));
    }
    async function clearDownloadedPlan(copy) {
        await ready;
        const owner=account?.id;
        if(!owner||copy.user!==owner) throw new Error('Abre la sesión de esta copia para quitarla.');
        const pending=copiesInProgress.get(copy.key);
        if(pending) pending.cancelled=true;
        await updateStores(['downloads','downloadFiles','reads','readinfo'],tx=>{
            const downloads=tx.objectStore('downloads'), query=downloads.get(copy.key);
            query.onsuccess=()=>{
                const row=query.result;
                if(!row||row.user!==owner) return;
                const objects=tx.objectStore('downloadFiles');
                for(const url of row.urls) objects.delete(row.key+'|'+url);
                downloads.delete(row.key);
                const info=tx.objectStore('readinfo'), reads=tx.objectStore('reads');
                const cursor=info.openCursor(IDBKeyRange.bound(owner+'|',owner+'|\uffff'));
                cursor.onsuccess=()=>{
                    const item=cursor.result;
                    if(!item) return;
                    const pathname=new URL(item.key.slice(String(owner).length+1)).pathname;
                    if(row.paths.some(path=>pathname===path||(path.endsWith('/')&&pathname.startsWith(path)))) {
                        reads.delete(item.key);item.delete();
                    }
                    item.continue();
                };
            };
        });
        channel?.postMessage('downloads');
        window.dispatchEvent(new Event('pwa-downloads-changed'));
    }
    async function forgetUnavailable(owner,url) {
        const rows=await downloadedPlans();
        for(const row of rows) {
            if(row.user===owner && [row.detail_url,row.source_url].some(item=>new URL(item,location.href).pathname===url.pathname)) {
                await clearDownloadedPlan(row);
            }
        }
    }
    async function rememberSession(response) {
        const data=await response.clone().json();
        const previous=account?.id;
        account=data.user||null;
        await updateStores(['meta','reads','readinfo','downloads','downloadFiles'],tx=>{
            if(account) tx.objectStore('meta').put({key:'session',user:account});
            else tx.objectStore('meta').delete('session');
            if(!account||(previous&&previous!==account.id)) {
                for(const store of ['reads','readinfo','downloads','downloadFiles']) tx.objectStore(store).clear();
            }
        });
        channel?.postMessage('session');
        emit();
    }
    async function remember(url,response,owner) {
        if(!account||account.id!==owner||!response.ok||response.status===206||response.headers.get('X-Reloncavi-Transient')==='1') return;
        if(/^\/api\/(?:pdf\/)?plans\/\d+\/$/.test(new URL(url).pathname) && (await response.clone().json()).is_draft) return;
        const body=await response.clone().arrayBuffer();
        if(body.byteLength>maxFile) return;
        // Inspect only lightweight metadata; never load every PDF to cache one cell tile.
        const rows=await all('readinfo');
        let total=rows.reduce((sum,row)=>sum+row.size,0);
        for(const row of rows.sort((a,b)=>a.time-b.time)) {
            if(total+body.byteLength<=80*MiB) break;
            await removeRead(row.key);total-=row.size;
        }
        await writeRead({key:`${owner}|${url}`,body,type:response.headers.get('Content-Type')||'',time:Date.now()});
    }
    function offlineResponse(data) {
        return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json','X-Reloncavi-Offline':'1'}});
    }
    async function offlineRead(owner,url) {
        const saved=await downloadedFile(owner,url);
        const row=saved||await get('reads',owner+'|'+url.href);
        if(!row||account?.id!==owner) return null;
        let response=new Response(row.body,{headers:{
            'Content-Type':row.type||row.body.type,'X-Reloncavi-Offline':'1',
        }});
        const planDetail=/^\/api\/(?:pdf\/)?plans\/\d+\/$/.test(url.pathname);
        const workbookMetadata=/^\/api\/pdf\/workbooks\/\d+\/metadata\/$/.test(url.pathname);
        if(planDetail || workbookMetadata) {
            const data=await response.clone().json();
            if(data.is_draft) return null;
            if(planDetail) {
                // Offline copies are confirmed snapshots, never editing sessions.
                data.permissions={...data.permissions};
                for(const key of new Set([...Object.keys(data.permissions),'can_assign','can_change_excel','can_save_plan','can_save_pdf_plan','can_copy_button'])) {
                    if(key.startsWith('can_') && !['can_view','can_export'].includes(key)) data.permissions[key]=false;
                }
                response=offlineResponse(data);
            } else response=offlineResponse({...data,can_edit:false,can_export:data.can_export===true});
        }
        return overlay(url,response);
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
        const statusOnly=url.pathname==='/api/projects/'&&url.searchParams.get('status')==='1';
        if(!navigator.onLine && !auth && method!=='GET') throw new TypeError('Esta acción requiere conexión. Los sectores y las celdas de Excel pueden conservarse como borradores.');
        if(!auth && (method!=='GET'||statusOnly||!eligible(url.pathname))) {
            try {
                const response=await nativeFetch(input,options);
                if(url.pathname.startsWith('/api/')) {disconnected=proxyUnavailable(response);emit();}
                return response;
            } catch(error) {
                if(error.name!=='AbortError' && url.pathname.startsWith('/api/')) {disconnected=true;emit();}
                throw error;
            }
        }
        await ready;
        const owner=account?.id;
        let response;
        try {
            response=await nativeFetch(input,options);
            if(proxyUnavailable(response)) throw new TypeError('La aplicación está temporalmente sin conexión.');
        }
        catch(error) {
            if(error.name==='AbortError') throw error;
            disconnected=true;emit();
            if(auth && url.pathname==='/api/auth/session/' && account) return offlineResponse({user:account,csrf_token:'',offline:true});
            if(method==='GET' && owner && account?.id===owner && eligible(url.pathname)) {
                const cached=await offlineRead(owner,url);
                if(cached) return cached;
                throw new Error('Este contenido aún no está descargado. Ábrelo con conexión antes de consultarlo sin señal.');
            }
            throw error;
        }
        disconnected=false;
        if(auth && response.ok) await rememberSession(response).catch(()=>{unavailable=true;emit();});
        else if(account && owner===account.id) {
            if([401,403,404].includes(response.status)) {
                await removeRead(key(url)).catch(()=>{});
                if(response.status!==401) await forgetUnavailable(owner,url).catch(()=>{});
            }
            if(response.status===401) await rememberSession(offlineResponse({user:null})).catch(()=>{});
            if(response.ok && !url.pathname.endsWith('/download/') && response.headers.get('X-Reloncavi-User')===String(owner)) await remember(url.href,response,owner).catch(()=>{unavailable=true;emit();});
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
        if(context.plan.is_draft) {
            try {return await send();} catch(error) {
                if(!transportFailure(error)) throw error;
                throw new Error('Se requiere conexión para editar el plano. No se guardó ninguna copia local de estos cambios.');
            }
        }
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
        if(metadata.is_draft) {
            try {return await send();} catch(error) {
                if(!transportFailure(error)) throw error;
                throw new Error('Se requiere conexión para editar este Excel. Sus cambios siguen abiertos; el plano se confirma con Guardar plano.');
            }
        }
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
        if(row.kind==='sector') {
            const detail=await downloadedFile(row.user,row.detail);
            if(detail) {
                const data=JSON.parse(await detail.body.text());
                data.sectors=result.sectors;
                await updateStores(['downloadFiles'],tx=>{
                    const files=tx.objectStore('downloadFiles');
                    for(const [url,value] of [[row.detail,data],[row.url,{sectors:result.sectors}]]) {
                        const query=files.index('accountUrl').get(row.user+'|'+new URL(url,location.href).href);
                        query.onsuccess=()=>{
                            if(query.result?.time===detail.time) {
                                files.put({...query.result,body:new Blob([JSON.stringify(value)],{type:'application/json'})});
                            }
                        };
                    }
                });
            }
        }
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
                await applyResult(row,result).catch(()=>{unavailable=true;});
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
    async function checkedDownload(url,owner,{optionalExport=false}={}) {
        const target=new URL(url,location.href);
        if(target.origin!==location.origin||!eligible(target.pathname)) throw new Error('El archivo no pertenece a este plano.');
        const response=await nativeFetch(target,{credentials:'same-origin',cache:'no-store'});
        if(optionalExport&&response.status===403&&response.headers.get('X-Reloncavi-User')===String(owner)) return null;
        if(!response.ok||response.status===206||response.headers.get('X-Reloncavi-User')!==String(owner)) {
            throw new Error('No se pudo descargar el plano completo. Comprueba tu conexión y sesión.');
        }
        return response;
    }
    async function fileBody(response) {
        if(Number(response.headers.get('Content-Length'))>maxFile) {
            await response.body?.cancel();
            throw new Error('La copia admite archivos de hasta 40 MiB.');
        }
        if(!response.body) throw new Error('El archivo descargado está vacío.');
        const reader=response.body.getReader(), chunks=[];
        let size=0;
        for(;;) {
            const {done,value}=await reader.read();
            if(done) break;
            size+=value.byteLength;
            if(size>maxFile) {
                await reader.cancel();
                throw new Error('La copia admite archivos de hasta 40 MiB.');
            }
            chunks.push(value);
        }
        if(!size) throw new Error('El archivo descargado está vacío.');
        return new Blob(chunks,{type:response.headers.get('Content-Type')||''});
    }
    const savedPlan=plan=>Boolean(plan.saved_at||plan.cloud_origin);
    const copyKey=(owner,plan)=>[owner,plan.kind,plan.cloud_origin||plan.cloud_key||plan.id].join('|');
    const copyRevision=plan=>JSON.stringify([plan.id,plan.project_version,plan.workbook?.revision,plan.permissions?.can_export]);
    const copiesInProgress=new Map();

    async function currentCopy(owner,plan) {
        const copy=await get('downloads',copyKey(owner,plan));
        return copy?.revision===copyRevision(plan) ? copy : null;
    }

    async function cachedSource(owner,plan) {
        const copy=await get('downloads',copyKey(owner,plan));
        if(!copy || copy.user!==owner) return null;
        // A new workbook revision need not invalidate the immutable PDF bytes.
        const samePdf=plan.kind==='pdf'&&plan.source_revision&&copy.source_revision===plan.source_revision;
        if(copy.revision!==copyRevision(plan)&&!samePdf) return null;
        const file=await get('downloadFiles',copy.key+'|'+new URL(copy.source_url,location.href).href);
        return account?.id===owner ? file?.body : null;
    }

    async function planSource(plan) {
        if(!savedPlan(plan)) return null;
        const owner=appState.user?.id;
        await ready;
        if(!owner||owner!==account?.id) return null;
        const source=plan.source_url||plan.svg_url;
        const cached=await cachedSource(owner,plan);
        if(cached) return cached;
        if(!navigator.onLine||disconnected||account?.id!==owner) return null;
        const running=copiesInProgress.get(copyKey(owner,plan));
        if(!running?.cancelled && running?.revision===copyRevision(plan) && running.sourceBody) return running.sourceBody;
        return fileBody(await checkedDownload(source,owner));
    }

    async function keepPlan(plan,sourceBody) {
        if(plan.is_draft||plan.has_pending_changes||plan._projectDirty||!savedPlan(plan)||!navigator.onLine||disconnected) return;
        const owner=appState.user?.id;
        const detail=JSON.parse(JSON.stringify(plan));
        await ready;
        if(!owner||owner!==account?.id||await currentCopy(owner,detail)) return;
        if((await pending()).some(row=>row.detail===detail.detail_url)) return;
        const key=copyKey(owner,detail), revision=copyRevision(detail);
        const running=copiesInProgress.get(key);
        if(running?.revision===revision) return running.result;
        if(running) running.cancelled=true;
        const copy={revision,sourceBody,cancelled:false};
        copiesInProgress.set(key,copy);
        copy.result=storePlan(owner,detail,sourceBody,copy);
        try { return await copy.result; }
        finally { if(copiesInProgress.get(key)===copy) copiesInProgress.delete(key); }
    }

    async function storePlan(owner,detail,sourceBody,copy) {
        navigator.storage?.persist?.().catch(()=>{});
        const started=Date.now();
        const urls=[], files=[];
        function append(url,body) {
            const absolute=new URL(url,location.href).href;
            urls.push(absolute);files.push({url:absolute,body});
        }
        const json=data=>new Blob([JSON.stringify(data)],{type:'application/json'});
        const source=detail.source_url||detail.svg_url;
        append(detail.detail_url,json(detail));
        append(detail.sectors_url,json({sectors:detail.sectors||[]}));
        append(source,sourceBody||await cachedSource(owner,detail)||await fileBody(await checkedDownload(source,owner)));
        const book=detail.workbook;
        let workbookUrl;
        if(book?.download_url&&detail.permissions?.can_export===true) {
            const response=await checkedDownload(book.download_url,owner,{optionalExport:true}).catch(()=>null);
            if(response?.headers.get('X-Reloncavi-Workbook-Revision')===String(book.revision)) {
                const body=await fileBody(response).catch(()=>null);
                if(body) {
                    append(book.download_url,body);
                    workbookUrl=book.download_url;
                }
            } else await response?.body?.cancel().catch(()=>{});
        }
        const buttons=detail.markers||detail.motors||[];
        const paths=[detail.detail_url,detail.sectors_url,detail.version_url,source,
            ...(book?[book.metadata_url,book.cells_url,book.download_url]:[]),
            ...buttons.flatMap(button=>{
                const book=button.workbook||button.excel;
                return [button.details_url,book?.metadata_url,book?.cells_url];
            })];
        const download={
            key:copyKey(owner,detail),revision:copy.revision,
            user:owner,kind:detail.kind,plan_id:detail.id,name:detail.name,version:detail.project_version,
            date:detail.saved_at||detail.date,time:started,cloud_key:detail.cloud_key,cloud_origin:detail.cloud_origin,
            detail_url:detail.detail_url,source_url:source,workbook_url:workbookUrl,workbook_name:book?.name,
            source_revision:detail.source_revision,
            urls,paths:[...new Set(paths.filter(Boolean).map(url=>new URL(url,location.href).pathname))],
            size:files.reduce((size,file)=>size+file.body.size,0),
        };
        if(copy.cancelled) return;
        try {
            await writeDownload(download,files);
        } catch(error) {
            if(error.name==='QuotaExceededError') throw new Error('El navegador se quedó sin espacio. Quita una copia de este dispositivo y vuelve a abrir el plano.');
            throw error;
        }
    }
    window.relOffline={
        nativeFetch,sectorRequest,workbookRequest,draftsFor,planSource,keepPlan,downloadedPlans,clearDownloadedPlan,
        get offline(){return disconnected||!navigator.onLine;},
        get unavailable(){return unavailable;},get syncing(){return syncing;},
        async state(){
            await ready;
            const rows=await pending();
            return {user:account,count:rows.length,rows,syncing,unavailable};
        },
        sync:async()=>navigator.locks?navigator.locks.request('reloncavi-outbox',sync):sync(),
        async exportDrafts(){return JSON.stringify({app:'Reloncaví',exported:new Date().toISOString(),changes:await pending()},null,2);},
        async discard(){for(const row of await pending()) await remove('outbox',row.key);emit();},
        async canLogout(){
            if((await pending()).length){window.relPWA?.open();window.relPWA?.message('Hay cambios pendientes. Sincronízalos o exporta y descarta los borradores antes de cerrar sesión.');return false;}
            return true;
        },
    };
    window.addEventListener('online',()=>{disconnected=false;window.relOffline.sync().catch(error=>window.relPWA?.message(error.message));});
    window.addEventListener('offline',()=>{disconnected=true;emit();});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden && navigator.onLine) window.relOffline.sync().catch(()=>{});});
    window.addEventListener('load',()=>window.relOffline.sync().catch(()=>{}));
})();
