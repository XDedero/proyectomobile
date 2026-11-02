/* PDF and DXF share one project, saved-plan library and fullscreen workspace. */
(() => {
    const byId = (id) => document.getElementById(id);
    let active = null, saving = false, deleting = false, full = false, placeholder = null, originalViewport = null;
    let selectedActions = null;
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
          <button type="button" data-project-action="assign-listing">Gestionar listados</button>
          <button type="button" data-project-action="open-listing" id="fullscreenOpenListing">Ver listados</button>
          <button type="button" data-project-action="save" id="fullscreenSaveMenu" hidden>Guardar plano</button>
          <button type="button" data-project-action="export">Exportar proyecto</button>
          <div class="fullscreen-zoom-actions"><button type="button" data-project-action="zoom-out" aria-label="Alejar">−</button><button type="button" data-project-action="fit">Ajustar</button><button type="button" data-project-action="zoom-in" aria-label="Ampliar">+</button></div>
        </nav>
        <div id="fullscreenPlanViewport" class="fullscreen-plan-viewport"></div>
        <div class="plan-quick-actions" aria-label="Acciones del plano">
          <button type="button" id="fullscreenAddMarker" aria-label="Añadir botón al plano" title="Añadir botón al plano" aria-pressed="false"><i class="bi bi-plus-circle" aria-hidden="true"></i><span>Botón</span></button>
          <button type="button" id="fullscreenMoveMarkerQuick" aria-pressed="false" hidden><i class="bi bi-arrows-move" aria-hidden="true"></i><span>Mover</span></button>
          <button type="button" id="fullscreenSavePlan" class="quick-save" hidden><i class="bi bi-floppy" aria-hidden="true"></i><span>Guardar plano</span></button>
          <button type="button" id="fullscreenDeletePlan" class="quick-danger" hidden><i class="bi bi-trash" aria-hidden="true"></i><span>Eliminar plano</span></button>
        </div>
        <div id="fullscreenSelection" class="fullscreen-selection" hidden><span id="fullscreenSelectionName"></span><button type="button" id="fullscreenAssignExcel">Asignar hoja</button><button type="button" id="fullscreenOpenExcel">Ver ficha</button><button type="button" id="fullscreenDeleteMarker" class="quick-danger" hidden><i class="bi bi-trash" aria-hidden="true"></i> Eliminar botón</button></div>
        <p id="fullscreenPlanStatus" class="fullscreen-plan-status" role="status"></p>`;
    document.body.append(overlay);
    const fullMenu = byId('fullscreenPlanMenu');

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
    function status(message, error = false, kind = active?.kind) {
        const id = kind === 'pdf' ? 'pdfProjectSaveStatus' : 'cadProjectSaveStatus';
        byId(id).textContent = message;
        byId(id).classList.toggle('text-danger', error);
        byId('fullscreenPlanStatus').textContent = message;
        byId('fullscreenPlanStatus').classList.toggle('has-error', error);
    }
    function canMove() { return Boolean(appState.user?.permissions?.[active?.kind === 'pdf' ? 'can_change_pdf_plan' : 'can_change_plan']); }
    function canSave(kind = active?.kind) { return Boolean(appState.user?.permissions?.[kind === 'pdf' ? 'can_save_pdf_plan' : 'can_save_plan']); }
    function canDelete(kind = active?.kind) { return Boolean(appState.user?.permissions?.[kind === 'pdf' ? 'can_delete_pdf_plan' : 'can_delete_plan']); }
    function canDeleteMarker() { return Boolean(appState.user?.permissions?.[active?.kind === 'pdf' ? 'can_delete_pdf_marker' : 'can_delete_cad_marker']); }
    function isBusy() { return saving || deleting || Boolean(active?.isBusy?.()); }
    function update() {
        byId('cadProjectActions').hidden = !appState.currentPlan;
        for (const button of byId('savedPlansContainer').querySelectorAll('[data-delete-plan]')) button.disabled = isBusy();
        for (const [kind, prefix] of [['pdf', 'Pdf'], ['dxf', 'Cad']]) {
            const listing = active?.kind === kind ? active.plan.listing : null;
            if (active?.kind === kind) byId(`btn${prefix}OpenListing`).hidden = !listing;
            byId(`btn${prefix}SavePlan`).hidden = !canSave(kind);
            byId(`btn${prefix}SavePlan`).disabled = isBusy();
            byId(`btnDelete${prefix}Plan`).disabled = isBusy();
        }
        byId('fullscreenSavePlan').hidden = byId('fullscreenSaveMenu').hidden = !active || !canSave();
        byId('fullscreenDeletePlan').hidden = !active || !canDelete();
        byId('fullscreenMoveMarkerQuick').hidden = !active || !canMove();
        byId('fullscreenDeleteMarker').hidden = !selectedActions?.remove || !canDeleteMarker();
        for (const id of ['fullscreenSavePlan', 'fullscreenSaveMenu', 'fullscreenDeletePlan', 'fullscreenDeleteMarker', 'fullscreenAddMarker', 'fullscreenMoveMarkerQuick']) byId(id).disabled = isBusy();
        if (!active) return;
        const saveState = !active.plan.saved_at ? 'Sin guardar' : active.plan._projectDirty ? 'Cambios pendientes' : 'Guardado';
        const badge = byId(active.kind === 'pdf' ? 'pdfSaveBadge' : 'cadSaveBadge');
        badge.textContent = saveState;
        badge.classList.toggle('has-changes', saveState !== 'Guardado');
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
        if (!full) status(active.plan._projectDirty ? 'Cambios pendientes' : active.plan.saved_at ? 'Plano guardado' : 'Plano sin guardar');
    }
    function deactivate(kind) {
        if (active?.kind !== kind) return;
        exitFullscreen();
        active = null;
        selection(null);
        update();
    }
    function dirty() { if (active) { active.plan._projectDirty = true; status('Cambios pendientes'); update(); } }

    async function saveActive() {
        if (!active || isBusy() || !canSave()) return false;
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
            current.plan._projectDirty = false;
            if (active === current) status('Plano guardado');
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
        if (!current || isBusy()) return;
        // Read-only users may export without creating/updating a saved plan.
        if (canSave() && !(await saveActive())) return;
        download(current.plan.export_project_url);
    }

    async function deletePlan(target = active) {
        if (!target || isBusy() || !canDelete(target.kind)) return;
        const { kind, plan } = target;
        if (!window.confirm(`¿Eliminar «${plan.name}» de la aplicación? Se quitarán sus botones y desaparecerá de Planos guardados.`)) return;
        if (!(await window.workbookEditor.requestClose())) return;
        deleting = true;
        update();
        try {
            await post(plan.delete_url);
            if (kind === 'pdf') window.pdfWorkflow.removeDeleted(plan.id);
            else window.planWorkflow.removeDeleted(plan.id);
            await listSaved();
            status('Plano eliminado', false, kind);
        } catch (error) {
            status(error.message, true, kind);
            byId('savedPlansFeedback').textContent = error.message;
        }
        finally { deleting = false; update(); }
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
        selectedActions = configuration;
        window.motorLibrary?.setTarget(configuration?.target || null);
        byId('fullscreenSelection').hidden = !configuration;
        byId('fullscreenDeleteMarker').hidden = !configuration?.remove || !canDeleteMarker();
        if (!configuration) return;
        byId('fullscreenSelectionName').textContent = configuration.name;
        byId('fullscreenAssignExcel').onclick = configuration.assign;
        byId('fullscreenOpenExcel').hidden = !configuration.workbook;
        byId('fullscreenOpenExcel').onclick = configuration.open;
        byId('fullscreenDeleteMarker').onclick = configuration.remove;
    }

    async function openListing() { await window.planExcel?.openListings(); }
    async function configureListing() { await window.planExcel?.listings(); }

    async function listSaved() {
        const container = byId('savedPlansContainer');
        byId('savedPlansFeedback').textContent = '';
        container.textContent = 'Cargando planos guardados…';
        try {
            const data = await request('/api/projects/');
            byId('savedCountBadge').textContent = data.plans.length;
            container.replaceChildren();
            if (!data.plans.length) container.textContent = 'No hay planos guardados. Abra un PDF o DXF y pulse Guardar plano.';
            for (const saved of data.plans) {
                const row = document.createElement('div');
                row.className = 'list-group-item saved-plan-row rounded-3';
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
                exportButton.innerHTML = '<i class="bi bi-download" aria-hidden="true"></i>';
                exportButton.title = 'Exportar proyecto';
                exportButton.setAttribute('aria-label', `Exportar ${saved.name}`);
                exportButton.href = saved.export_url;
                exportButton.download = '';
                row.append(button, exportButton);
                if (canDelete(saved.kind)) {
                    const deleteButton = document.createElement('button');
                    deleteButton.type = 'button';
                    deleteButton.className = 'btn btn-sm btn-outline-danger';
                    deleteButton.dataset.deletePlan = saved.plan_id;
                    deleteButton.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i>';
                    deleteButton.title = 'Eliminar plano';
                    deleteButton.setAttribute('aria-label', `Eliminar ${saved.name}`);
                    deleteButton.disabled = deleting;
                    deleteButton.addEventListener('click', () => deletePlan({ kind: saved.kind, plan: { ...saved, id: saved.plan_id } }));
                    row.append(deleteButton);
                }
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
    byId('fullscreenSavePlan').addEventListener('click', saveActive);
    byId('fullscreenDeletePlan').addEventListener('click', () => deletePlan());
    byId('fullscreenAddMarker').addEventListener('click', () => {
        if (active && !isBusy()) active.setMode(byId('fullscreenAddMarker').getAttribute('aria-pressed') === 'true' ? 'explore' : 'add');
    });
    byId('fullscreenMoveMarkerQuick').addEventListener('click', () => {
        if (active && !isBusy() && canMove()) active.setMode(byId('fullscreenMoveMarkerQuick').getAttribute('aria-pressed') === 'true' ? 'explore' : 'move');
    });
    function modeChanged(mode) {
        for (const [id, value] of [['fullscreenAddMarker', 'add'], ['fullscreenMoveMarkerQuick', 'move']]) byId(id).setAttribute('aria-pressed', String(mode === value));
    }
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
        if (event.key !== 'Escape' || !full || window.workbookEditor.visible || !byId('pdfMarkerDialog').hidden || document.querySelector('#excelListingManager.show, #excelBookManager.show, #excelSheetAssignment.show')) return;
        event.preventDefault();
        if (!fullMenu.hidden) setMenu(false); else exitFullscreen();
    });
    overlay.addEventListener('pointerdown', (event) => { if (!fullMenu.contains(event.target) && !byId('btnPlanFullscreenMenu').contains(event.target)) setMenu(false); });
    for (const prefix of ['Cad', 'Pdf']) {
        byId(`btn${prefix}Fullscreen`).addEventListener('click', enterFullscreen);
        byId(`btn${prefix}SavePlan`).addEventListener('click', saveActive);
        byId(`btnDelete${prefix}Plan`).addEventListener('click', () => deletePlan());
        byId(`btn${prefix}ExportProject`).addEventListener('click', exportActive);
        byId(`btn${prefix}AssignListing`).addEventListener('click', configureListing);
        byId(`btn${prefix}OpenListing`).addEventListener('click', openListing);
    }
    byId('btnSavedPlans').addEventListener('click', showSaved);
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
        get active() { return active; }, get full() { return full; }, get selected() { return selectedActions; },
        activate, deactivate, selection, dirty, update, saveActive, exportActive, listSaved, showSaved, exitFullscreen, deletePlan, modeChanged,
        reportError(message) { status(message, true); },
        reset() { exitFullscreen(); active = null; selection(null); window.planExcel?.reset(); update(); },
    };
})();
