/* Install and update controls work in Safari without beforeinstallprompt. */
(() => {
    const installed=()=>window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
    const ios=/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
    let installPrompt=null,registration=null,acceptingUpdate=false;
    const launch=document.createElement('button');
    launch.id='pwaOpen';launch.type='button';launch.className='pwa-launch';launch.textContent='Instalar app';
    launch.setAttribute('aria-label','Instalación y conexión de Reloncaví');
    document.querySelector('.app-shell').prepend(launch);
    const panel=document.createElement('dialog');panel.className='pwa-panel';panel.id='pwaPanel';
    panel.setAttribute('aria-labelledby','pwaTitle');
    panel.innerHTML=`<div class="pwa-panel-heading"><img src="/static/mobileApp/img/pwa/apple-touch-icon.png" alt="" width="48" height="48"><h2 id="pwaTitle">Reloncaví en tu dispositivo</h2><button type="button" class="pwa-close" aria-label="Cerrar">×</button></div>
      <p id="pwaConnection" role="status"></p>
      <div id="pwaInstallHelp"><p>Instala la app para abrir tus planos desde la pantalla de inicio.</p><ol><li>Abre esta dirección en Safari.</li><li>Toca Compartir y luego <strong>Agregar a pantalla de inicio</strong>.</li><li>Activa <strong>Abrir como app</strong> si aparece y toca Agregar.</li></ol></div>
      <button type="button" id="pwaInstall" class="btn btn-primary" hidden>Instalar Reloncaví</button>
      <hr><h3>Trabajo sin conexión</h3><p>Descarga el plano abierto antes de salir a terreno. Los datos y hojas de Excel que hayas consultado también se conservan en este dispositivo.</p>
      <p>Sin señal puedes consultar lo descargado y guardar cambios de sectores y celdas de Excel como pendientes. La sincronización se realiza al recuperar conexión con la app abierta. Importar, mover botones, cambiar la estructura del Excel y guardar en la nube requieren conexión.</p>
      <button type="button" id="pwaDownload" class="btn btn-outline-primary">Descargar plano abierto</button>
      <p id="pwaPending"></p><div class="pwa-actions"><button type="button" id="pwaSync" class="btn btn-primary">Sincronizar pendientes</button><button type="button" id="pwaExport" class="btn btn-outline-secondary">Exportar borradores</button><button type="button" id="pwaDiscard" class="btn btn-outline-danger">Descartar borradores</button></div>
      <ul id="pwaConflicts"></ul><hr><div id="pwaUpdateArea" hidden><p>Hay una nueva versión disponible.</p><button type="button" id="pwaUpdate" class="btn btn-primary">Actualizar app</button></div>
      <p id="pwaMessage" role="status"></p>`;
    document.body.append(panel);
    const $=id=>document.getElementById(id);
    const message=text=>{$('pwaMessage').textContent=text;};
    let stateEpoch=0;
    async function refresh() {
        const epoch=++stateEpoch;
        const state=await window.relOffline.state().catch(()=>({count:0,rows:[],unavailable:true}));
        if(epoch!==stateEpoch) return;
        const offline=window.relOffline.offline;
        launch.textContent=offline?'Sin conexión':state.count?`${state.count} pendientes`:registration?.waiting?'Actualizar app':installed()?'App y conexión':'Instalar app';
        launch.classList.toggle('is-offline',offline);launch.classList.toggle('has-pending',Boolean(state.count));
        $('pwaConnection').textContent=state.unavailable?'El almacenamiento local no está disponible. Los cambios necesitan conexión.':offline?'Sin conexión. Se muestra la última copia disponible.':'Con conexión';
        $('pwaPending').textContent=state.count?`${state.count} cambios guardados en este dispositivo, pendientes del servidor.`:'No hay cambios pendientes de sincronizar.';
        $('pwaSync').disabled=!state.count||offline||state.syncing;
        $('pwaExport').disabled=$('pwaDiscard').disabled=!state.count||state.syncing;
        $('pwaDownload').disabled=offline||!state.user;
        $('pwaInstallHelp').hidden=installed()||Boolean(installPrompt);
        if(!ios&&!installed()&&!installPrompt) $('pwaInstallHelp').innerHTML='<p>Usa la opción «Instalar app» o «Agregar a pantalla de inicio» del menú de tu navegador.</p>';
        $('pwaInstall').hidden=!installPrompt||installed();
        $('pwaUpdateArea').hidden=!registration?.waiting;
        $('pwaConflicts').replaceChildren();
        for(const row of state.rows.filter(row=>row.conflict)) {
            const item=document.createElement('li');item.textContent=`${row.kind==='sector'?'Sector':'Excel'}: ${row.conflict} Exporta el borrador para revisarlo antes de descartarlo.`;$('pwaConflicts').append(item);
        }
    }
    function open(){panel.showModal();refresh();}
    launch.onclick=open;panel.querySelector('.pwa-close').onclick=()=>panel.close();
    window.relPWA={open,message};
    $('pwaInstall').onclick=async()=>{
        if(!installPrompt) return;
        await installPrompt.prompt();await installPrompt.userChoice;installPrompt=null;refresh();
    };
    window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;refresh();});
    window.addEventListener('appinstalled',()=>{installPrompt=null;refresh();});
    $('pwaDownload').onclick=async()=>{
        $('pwaDownload').disabled=true;message('Descargando el plano…');
        try {await window.relOffline.downloadPlan(window.projectWorkspace?.active);message('Plano descargado para consulta sin conexión. Abre las hojas de Excel que necesitarás antes de desconectarte.');}
        catch(error){message(error.message);}finally{refresh();}
    };
    $('pwaSync').onclick=async()=>{
        try {await window.relOffline.sync();message('Revisión de pendientes terminada. Si no hay conflictos, vuelve a abrir el plano para ver los datos sincronizados.');}
        catch(error){message(error.message);}
    };
    $('pwaExport').onclick=async()=>{
        const text=await window.relOffline.exportDrafts(),file=new File([text],'reloncavi-borradores.json',{type:'application/json'});
        if(navigator.canShare?.({files:[file]})) {
            try {await navigator.share({files:[file],title:'Borradores Reloncaví'});} catch(error){if(error.name!=='AbortError') message(error.message);}
        } else {
            const url=URL.createObjectURL(file),link=document.createElement('a');link.href=url;link.download=file.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
        }
    };
    $('pwaDiscard').onclick=async()=>{
        if(confirm('¿Descartar los borradores locales? Exporta primero los que quieras conservar.')) {await window.relOffline.discard();message('Borradores descartados. Vuelve a abrir el plano para cargar los datos del servidor.');}
    };
    $('pwaUpdate').onclick=async()=>{
        const state=await window.relOffline.state();
        if(state.count||window.workbookEditor?.hasUnsavedChanges||window.planSectors?.editing||window.projectWorkspace?.active?.isBusy?.()||window.projectWorkspace?.active?.plan._projectDirty) {message('Guarda el plano, sincroniza tus cambios y termina la edición antes de actualizar.');return;}
        if(registration?.waiting) {acceptingUpdate=true;registration.waiting.postMessage({type:'ACTIVATE_UPDATE'});}
    };
    if('serviceWorker' in navigator && window.isSecureContext) {
        window.addEventListener('load',async()=>{
            try {
                registration=await navigator.serviceWorker.register('/service-worker.js',{scope:'/',updateViaCache:'none'});
                registration.addEventListener('updatefound',()=>{
                    registration.installing?.addEventListener('statechange',()=>refresh());
                });
                await navigator.serviceWorker.ready;refresh();
            } catch(error) {message('No se pudo preparar la instalación sin conexión. Revisa la conexión y vuelve a abrir la app.');console.warn('PWA:',error);}
        });
        navigator.serviceWorker.addEventListener('controllerchange',()=>{if(acceptingUpdate) location.reload();});
    } else if(!window.isSecureContext) {
        message('Para instalar y habilitar la apertura sin conexión, abre la app desde una dirección HTTPS.');
    }
    window.addEventListener('pwa-state-changed',()=>refresh());refresh();
})();
