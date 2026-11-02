/* PDF and DXF share one project, saved-plan library and fullscreen workspace. */
(() => {
    const byId = (id) => document.getElementById(id);
    let active = null, saving = false, full = false, placeholder = null, originalViewport = null;
    let previousOverflow = '', fullscreenOwned = false;
    let pickingFile = false;
    const overlay = document.createElement('section');
    overlay.id = 'planFullscreen';
    overlay.className = 'plan-fullscreen';
    overlay.hidden = true;
    overlay.setAttribute('aria-label', 'Plano en pantalla completa');
    overlay.innerHTML = `<header class="plan-fullscreen-toolbar">
        <button type="button" id="btnExitPlanFullscreen" aria-label="Salir de pantalla completa">← Volver</button>
        <span id="fullscreenPlanName"></span>
        <select id="fullscreenPage" aria-label="Página del plano" hidden></select>
        <button type="button" id="btnPlanFullscreenMenu" aria-label="Menú del plano" aria-expanded="false" aria-controls="fullscreenPlanMenu">⋮</button>
        </header>
        <nav id="fullscreenPlanMenu" class="fullscreen-plan-menu" aria-label="Acciones del plano" hidden>
          <button type="button" data-project-action="explore">Explorar plano</button>
          <button type="button" data-project-action="add">Agregar botón</button>
          <button type="button" data-project-action="move" id="fullscreenMoveMarker">Mover botón</button>
          <button type="button" data-project-action="saved-motors">Importar motor guardado</button>
          <button type="button" data-project-action="assign-listing">Asignar listado</button>
          <button type="button" data-project-action="open-listing" id="fullscreenOpenListing">Ver listado</button>
          <button type="button" data-project-action="save">Guardar plano</button>
          <button type="button" data-project-action="export">Exportar proyecto</button>
          <div class="fullscreen-zoom-actions"><button type="button" data-project-action="zoom-out" aria-label="Alejar">−</button><button type="button" data-project-action="fit">Ajustar</button><button type="button" data-project-action="zoom-in" aria-label="Ampliar">+</button></div>
        </nav>
        <div id="fullscreenPlanViewport" class="fullscreen-plan-viewport"></div>
        <div id="fullscreenSelection" class="fullscreen-selection" hidden><span id="fullscreenSelectionName"></span><button type="button" id="fullscreenAssignExcel">Asignar Excel</button><button type="button" id="fullscreenOpenExcel">Ver ficha</button></div>
        <p id="fullscreenPlanStatus" class="fullscreen-plan-status" role="status"></p>`;
    document.body.append(overlay);
    const fullMenu = byId('fullscreenPlanMenu');

    const listingModal = document.createElement('div');
    listingModal.className = 'modal fade';
    listingModal.tabIndex = -1;
    listingModal.setAttribute('aria-labelledby', 'planListingTitle');
    listingModal.innerHTML = `<div class="modal-dialog modal-dialog-centered"><div class="modal-content">
      <div class="modal-header"><h2 class="modal-title fs-5" id="planListingTitle">Listado del plano</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
      <div class="modal-body"><p class="small text-muted">El listado pertenece al plano completo. Use la plantilla con número de motor, especificaciones y sector, o asigne un Excel.</p>
      <button type="button" id="btnCreatePlanListing" class="btn btn-success w-100 mb-2">Crear listado de motores</button>
      <button type="button" id="btnUploadPlanListing" class="btn btn-outline-success w-100 mb-3">Importar listado .xlsx</button>
      <input type="file" id="planListingFileInput" accept=".xlsx" hidden>
      <label for="planListingLibrary" class="small">Reutilizar un Excel asignado</label><select id="planListingLibrary" class="form-select mb-2"></select>
      <button type="button" id="btnAssignPlanListingLibrary" class="btn btn-outline-secondary w-100">Asignar Excel seleccionado</button>
      <p id="planListingStatus" class="small mt-2" role="status"></p></div></div></div>`;
    document.body.append(listingModal);
    const listingController = new bootstrap.Modal(listingModal);

    async function request(url, options = {}) {
        const headers = new Headers(options.headers || {});
        if (options.method && options.method !== 'GET') headers.set('X-CSRFToken', csrfToken());
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth?.expire();
            const error = new Error(data.error || 'No se pudo completar la operación del proyecto.');
            error.status = response.status;
            throw error;
        }
        return data;
    }
    const post = (url, values = {}) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
    function status(message, error = false) {
        const id = active?.kind === 'pdf' ? 'pdfProjectSaveStatus' : 'cadProjectSaveStatus';
        byId(id).textContent = message;
        byId(id).classList.toggle('text-danger', error);
        byId('fullscreenPlanStatus').textContent = message;
        byId('fullscreenPlanStatus').classList.toggle('has-error', error);
    }
    function canMove() { return Boolean(appState.user?.permissions[active?.kind === 'pdf' ? 'can_change_pdf_plan' : 'can_change_plan']); }
    function update() {
        byId('cadProjectActions').hidden = !appState.currentPlan;
        for (const [kind, prefix] of [['pdf', 'Pdf'], ['dxf', 'Cad']]) {
            const listing = active?.kind === kind ? active.plan.listing : null;
            if (active?.kind === kind) byId(`btn${prefix}OpenListing`).hidden = !listing;
            byId(`btn${prefix}SavePlan`).disabled = saving;
        }
        if (!active) return;
        byId('fullscreenPlanName').textContent = `${active.plan.name} · ${active.kind.toUpperCase()}`;
        byId('fullscreenOpenListing').hidden = !active.plan.listing;
        byId('fullscreenMoveMarker').hidden = !canMove();
        const pageSelect = byId('fullscreenPage');
        pageSelect.hidden = active.kind !== 'pdf';
        if (active.kind === 'pdf') {
            pageSelect.replaceChildren();
            for (let index = 1; index <= active.plan.pages.length; index++) pageSelect.append(new Option(`${index} / ${active.plan.pages.length}`, String(index)));
            pageSelect.value = String(active.viewer.pageNumber || 1);
        }
    }
    function activate(configuration) {
        if (!active || active.kind !== configuration.kind || active.plan.id !== configuration.plan.id) selection(null);
        if (full && active && (active.kind !== configuration.kind || active.plan.id !== configuration.plan.id)) exitFullscreen();
        active = configuration;
        update();
        if (!full) status(active.plan.saved_at ? `Plano guardado · ${new Date(active.plan.saved_at).toLocaleString('es-CL')}` : 'Plano sin guardar. Pulse Guardar plano para añadirlo a sus planos guardados.');
    }
    function deactivate(kind) {
        if (active?.kind !== kind) return;
        exitFullscreen();
        active = null;
        update();
    }
    function dirty() { if (active) status('Hay cambios en el proyecto. Pulse Guardar plano.'); }

    async function saveActive() {
        if (!active || saving) return false;
        const current = active;
        saving = true;
        update();
        status('Guardando plano, botones, fichas y listado…');
        try {
            const editor = window.workbookEditor;
            if (editor?.visible) {
                editor.commitAll();
                if (editor.hasUnsavedChanges && !(await editor.save())) {
                    status('El plano no se guardó. Revise los cambios de la ficha abierta.', true);
                    return false;
                }
            }
            const data = await post(current.plan.save_project_url);
            current.plan.saved_at = data.saved_at;
            if (active === current) status('Plano guardado con sus botones, fichas Excel y listado.');
            if (editor?.visible) editor.setStatus('Plano guardado con todos sus archivos Excel.', 'success');
            return true;
        } catch (error) {
            status(error.message, true);
            if (window.workbookEditor?.visible) window.workbookEditor.setStatus(error.message, 'error');
            return false;
        } finally { saving = false; update(); }
    }
    function download(url) {
        const link = document.createElement('a');
        link.href = url;
        link.download = '';
        document.body.append(link);
        link.click();
        link.remove();
    }
    async function exportActive() {
        const current = active;
        if (current && await saveActive()) download(current.plan.export_project_url);
    }

    function setMenu(open) {
        fullMenu.hidden = !open;
        byId('btnPlanFullscreenMenu').setAttribute('aria-expanded', String(open));
    }
    async function enterFullscreen() {
        if (!active || full) return;
        full = true;
        originalViewport = active.viewport;
        placeholder = document.createComment('plan-viewport');
        originalViewport.before(placeholder);
        byId('fullscreenPlanViewport').append(originalViewport);
        previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        overlay.hidden = false;
        update();
        try {
            if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
                await document.documentElement.requestFullscreen();
                fullscreenOwned = true;
            }
        } catch (_) { /* The full-window workspace also works without this API. */ }
        active?.viewer.fit();
        byId('btnPlanFullscreenMenu').focus();
    }
    function exitFullscreen() {
        if (!full) return;
        full = false;
        setMenu(false);
        if (placeholder?.parentNode && originalViewport) placeholder.replaceWith(originalViewport);
        placeholder = originalViewport = null;
        overlay.hidden = true;
        if (window.workbookEditor?.visible) {
            window.workbookEditor.previousBodyOverflow = previousOverflow;
            document.body.style.overflow = 'hidden';
        } else document.body.style.overflow = previousOverflow;
        if (fullscreenOwned && document.fullscreenElement) document.exitFullscreen().catch(() => {});
        fullscreenOwned = false;
        active?.viewer.fit();
    }
    function selection(configuration) {
        window.motorLibrary?.setTarget(configuration?.target || null);
        byId('fullscreenSelection').hidden = !configuration;
        if (!configuration) return;
        byId('fullscreenSelectionName').textContent = configuration.name;
        byId('fullscreenAssignExcel').onclick = configuration.assign;
        byId('fullscreenOpenExcel').hidden = !configuration.workbook;
        byId('fullscreenOpenExcel').onclick = configuration.open;
    }

    async function openListing() {
        if (active?.plan.listing) await window.workbookEditor.open({ ...active.plan.listing, context: 'listing', preferred_sheet: 'Listado' });
    }
    async function configureListing() {
        if (!active) return;
        byId('planListingStatus').textContent = '';
        const library = byId('planListingLibrary');
        library.replaceChildren(new Option('Cargando…', ''));
        listingController.show();
        try {
            const data = await request('/api/pdf/workbooks/');
            library.replaceChildren(new Option('Seleccione un Excel', ''));
            for (const book of data.workbooks) library.append(new Option(book.name, String(book.id)));
        } catch (error) { byId('planListingStatus').textContent = error.message; }
    }
    async function assignListing(values, file) {
        if (!active) return;
        const current = active;
        const buttons = [...listingModal.querySelectorAll('button')];
        buttons.forEach((button) => { button.disabled = true; });
        byId('planListingStatus').textContent = 'Asignando listado al plano…';
        try {
            let result;
            if (file) {
                const body = new FormData();
                body.append('file', file);
                result = await request(current.plan.assign_listing_url, { method: 'POST', body });
            } else result = await post(current.plan.assign_listing_url, values);
            current.plan.listing = result.listing;
            dirty();
            update();
            listingModal.addEventListener('hidden.bs.modal', () => { if (active === current) openListing(); }, { once: true });
            listingController.hide();
        } catch (error) { byId('planListingStatus').textContent = error.message; }
        finally { buttons.forEach((button) => { button.disabled = false; }); }
    }

    async function listSaved() {
        const container = byId('savedPlansContainer');
        container.textContent = 'Cargando planos guardados…';
        try {
            const data = await request('/api/projects/');
            byId('savedCountBadge').textContent = data.plans.length;
            container.replaceChildren();
            if (!data.plans.length) container.textContent = 'No hay planos guardados. Abra un PDF o DXF y pulse Guardar plano.';
            for (const saved of data.plans) {
                const row = document.createElement('div');
                row.className = 'list-group-item d-flex align-items-center gap-2 rounded-3';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn text-start flex-grow-1 text-break';
                const name = document.createElement('span');
                name.textContent = saved.name;
                const badge = document.createElement('small');
                badge.className = 'project-kind-badge';
                badge.textContent = saved.kind.toUpperCase();
                button.append(name, badge);
                button.addEventListener('click', async () => {
                    try {
                        if (!(await window.workbookEditor.requestClose())) return;
                        exitFullscreen();
                        modalSavedPlans.hide();
                        const plan = await request(saved.detail_url);
                        await openPlan(saved.kind, plan);
                    } catch (error) { container.textContent = error.message; }
                });
                const exportButton = document.createElement('a');
                exportButton.className = 'btn btn-sm btn-outline-primary';
                exportButton.textContent = 'Exportar';
                exportButton.href = saved.export_url;
                exportButton.download = '';
                row.append(button, exportButton);
                container.append(row);
            }
        } catch (error) { container.textContent = error.message; }
    }
    function showSaved() { modalSavedPlans.show(); listSaved(); }
    async function openPlan(kind, plan) {
        if (kind === 'pdf') await window.pdfWorkflow.open(plan);
        else await window.planWorkflow.open(plan);
    }

    byId('btnPlanFullscreenMenu').addEventListener('click', () => setMenu(fullMenu.hidden));
    byId('btnExitPlanFullscreen').addEventListener('click', exitFullscreen);
    byId('fullscreenPage').addEventListener('change', async (event) => {
        try { await active?.viewer.setPage(Number(event.target.value)); } catch (error) { status(error.message, true); }
    });
    fullMenu.addEventListener('click', (event) => {
        const action = event.target.closest('[data-project-action]')?.dataset.projectAction;
        if (!action || !active) return;
        setMenu(false);
        if (['explore', 'add', 'move'].includes(action)) {
            if (action === 'move' && !canMove()) return;
            active.setMode(action);
            status(action === 'add' ? 'Toque el plano donde desea agregar el botón.' : action === 'move' ? 'Arrastre el botón que desea mover.' : 'Arrastre para recorrer el plano.');
        } else if (action === 'assign-listing') configureListing();
        else if (action === 'saved-motors') window.motorLibrary?.chooseForTarget();
        else if (action === 'open-listing') openListing();
        else if (action === 'save') saveActive();
        else if (action === 'export') exportActive();
        else if (action === 'fit') active.viewer.fit();
        else if (action === 'zoom-in') active.viewer.zoom(1.4);
        else if (action === 'zoom-out') active.viewer.zoom(1/1.4);
    });
    document.addEventListener('click', (event) => { if (full && event.target.matches('input[type="file"]')) pickingFile = true; }, true);
    document.addEventListener('change', (event) => { if (event.target.matches('input[type="file"]')) pickingFile = false; });
    document.addEventListener('cancel', (event) => { if (event.target.matches('input[type="file"]')) pickingFile = false; }, true);
    document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement && full && fullscreenOwned) {
            // Mobile file pickers can leave native fullscreen. Keep the same
            // workspace open while the user chooses a document.
            if (pickingFile) fullscreenOwned = false;
            else exitFullscreen();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !full || window.workbookEditor.visible || !byId('pdfMarkerDialog').hidden || listingModal.classList.contains('show')) return;
        event.preventDefault();
        if (!fullMenu.hidden) setMenu(false); else exitFullscreen();
    });
    overlay.addEventListener('pointerdown', (event) => { if (!fullMenu.contains(event.target) && !byId('btnPlanFullscreenMenu').contains(event.target)) setMenu(false); });
    for (const prefix of ['Cad', 'Pdf']) {
        byId(`btn${prefix}Fullscreen`).addEventListener('click', enterFullscreen);
        byId(`btn${prefix}SavePlan`).addEventListener('click', saveActive);
        byId(`btn${prefix}ExportProject`).addEventListener('click', exportActive);
        byId(`btn${prefix}AssignListing`).addEventListener('click', configureListing);
        byId(`btn${prefix}OpenListing`).addEventListener('click', openListing);
    }
    byId('btnSavedPlans').addEventListener('click', showSaved);
    byId('btnCreatePlanListing').addEventListener('click', () => assignListing({ create_template: true }));
    byId('btnUploadPlanListing').addEventListener('click', () => byId('planListingFileInput').click());
    byId('planListingFileInput').addEventListener('change', (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (file) assignListing(null, file);
    });
    byId('btnAssignPlanListingLibrary').addEventListener('click', () => {
        const id = Number(byId('planListingLibrary').value);
        if (id) assignListing({ workbook_id: id });
        else byId('planListingStatus').textContent = 'Seleccione un Excel.';
    });
    byId('btnImportProject').addEventListener('click', () => byId('projectFileInput').click());
    byId('projectFileInput').addEventListener('change', async (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        byId('btnImportProject').disabled = true;
        byId('pdfStatus').textContent = 'Importando proyecto, botones y Excel…';
        try {
            const body = new FormData();
            body.append('file', file);
            const data = await request('/api/projects/import/', { method: 'POST', body });
            await openPlan(data.kind, data.plan);
            status('Proyecto importado con sus botones y archivos. Pulse Guardar plano.');
        } catch (error) { byId('pdfError').textContent = error.message; byId('pdfError').hidden = false; }
        finally { byId('btnImportProject').disabled = false; }
    });
    document.addEventListener('workbook:saved', dirty);
    window.projectWorkspace = {
        get active() { return active; }, get full() { return full; },
        activate, deactivate, selection, dirty, update, saveActive, exportActive, listSaved, showSaved, exitFullscreen,
        reportError(message) { status(message, true); },
        reset() { exitFullscreen(); active = null; listingController.hide(); update(); },
    };
})();
