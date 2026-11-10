/* PDF and DXF share one project, saved-plan library and fullscreen workspace. */
(() => {
    const byId = (id) => document.getElementById(id);
    let active = null, saving = false, deleting = false, full = false, placeholder = null, originalViewport = null;
    let selectedActions = null;
    let previousOverflow = '', fullscreenOwned = false;
    let pickingFile = false;
    let libraryRequestVersion = 0, libraryLoading = false, librarySignature = null;
    let localRows = [], cloudRows = [];
    let ownHistory = false;
    let cloudRequestVersion = 0, cloudLoading = false, cloudOpening = false, cloudCheckedAt = 0;
    let localCheckedAt = 0;
    const cloudMessages = {
        local: 'Guardado en este equipo',
        pending: 'Guardado en este equipo. Subiendo a la nube…',
        uploaded: 'Archivos almacenados en la nube. Confirmando el registro en la base de datos…',
        synced: 'Guardado en la base de datos y almacenado en la nube',
        partial: 'Archivos almacenados en la nube. Confirmando el registro en la base de datos…',
        error: 'Guardado en este equipo. No se pudo almacenar en la nube',
    };
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
          <button type="button" data-project-action="saved-motors">Importar pieza guardada</button>
          <button type="button" data-project-action="assign-listing">Añadir listado</button>
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
        <div id="fullscreenSelection" class="fullscreen-selection" hidden><span id="fullscreenSelectionName"></span><button type="button" id="fullscreenAdjustArea" hidden>Ajustar radio</button></div>
        <p id="fullscreenPlanStatus" class="fullscreen-plan-status" role="status"></p>`;
    document.body.append(overlay);
    const fullMenu = byId('fullscreenPlanMenu');
    const uploadStages = { queued: 'Guardado en este PC. Esperando turno para subir…', preparing: 'Preparando los archivos para la nube…',
        pdf: 'Subiendo el PDF a la nube…', excel: 'Subiendo el Excel y sus hojas a la nube…',
        database: 'Guardando el plano, las hojas y los botones en la base de datos…', confirming: 'Confirmando el guardado completo…' };
    for (const [id, anchor] of [['pdfProjectUploadProgress', 'pdfProjectSaveStatus'], ['cadProjectUploadProgress', 'cadProjectSaveStatus'], ['fullscreenUploadProgress', 'fullscreenPlanStatus']]) {
        const element = document.createElement('section');
        element.id = id; element.className = 'project-upload-progress'; element.hidden = true;
        element.innerHTML = '<p class="upload-stage mb-1" role="status" aria-live="polite"></p><progress max="100" aria-label="Etapas del guardado en la nube"></progress><small class="upload-hint"></small>';
        byId(anchor).after(element);
    }
    function renderUploadProgress() {
        const plan = active?.plan, pending = saving || plan?.cloud_status === 'pending';
        for (const id of ['pdfProjectUploadProgress', 'cadProjectUploadProgress', 'fullscreenUploadProgress']) {
            const element = byId(id);
            element.hidden = !plan || (id === 'fullscreenUploadProgress' ? !full : id.startsWith('pdf') !== (active.kind === 'pdf')) ||
                (!saving && !plan._saveRequestError && !['pending', 'uploaded', 'synced', 'partial', 'error'].includes(plan.cloud_status));
            if (element.hidden) continue;
            const state = saving ? 'pending' : plan._saveRequestError ? 'error' : plan.cloud_status;
            const message = saving ? 'Guardando los cambios en este PC…' : plan._cloudPollDelayed && pending ? 'Esperando conexión para confirmar la subida…' :
                state === 'pending' ? uploadStages[plan.cloud_stage] || cloudMessages.pending : state === 'synced' ? cloudMessages.synced :
                ['uploaded', 'partial'].includes(state) ? 'Archivos almacenados en la nube' : 'No se completó la subida.';
            const label = element.querySelector('.upload-stage');
            if (label.textContent !== message) label.textContent = message;
            element.dataset.state = state;
            const bar = element.querySelector('progress');
            if (saving) bar.removeAttribute('value');
            else bar.value = ['synced', 'uploaded', 'partial'].includes(state) ? 100 : Math.min(95, Math.max(0, plan.cloud_progress || 5));
            bar.setAttribute('aria-valuetext', message);
            element.querySelector('.upload-hint').textContent = pending ? 'La subida todavía no ha terminado.' : state === 'synced' ? '' : ['uploaded', 'partial'].includes(state) ? 'El registro en la base de datos se confirma automáticamente.' : plan._saveRequestError || plan.cloud_error || '';
        }
    }
    function applyCloudState(plan, saved) {
        for (const key of ['cloud_status', 'cloud_error', 'cloud_stage', 'cloud_progress']) plan[key] = saved[key];
        plan._cloudPollDelayed = false;
    }

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
    function rights(kind = active?.kind, plan = null) { return plan?.permissions || (active && active.kind === kind ? active.plan.permissions : null) || {}; }
    function canMove() { return Boolean(rights()[active?.kind === 'pdf' ? 'can_change_pdf_plan' : 'can_change_plan']); }
    function canSave(kind = active?.kind) { return Boolean(rights(kind)[kind === 'pdf' ? 'can_save_pdf_plan' : 'can_save_plan']); }
    function canDelete(kind = active?.kind, plan = null) { return Boolean(rights(kind, plan)[kind === 'pdf' ? 'can_delete_pdf_plan' : 'can_delete_plan'] || appState.user?.permissions?.can_administer); }
    function canDeleteMarker() { return Boolean(rights()[active?.kind === 'pdf' ? 'can_delete_pdf_marker' : 'can_delete_cad_marker']); }
    function isBusy() { return saving || deleting || active?.plan.cloud_status === 'pending' || Boolean(active?.isBusy?.()); }
    function update() {
        window.planSectors?.render(active);
        window.planDisplay?.render(active, selectedActions);
        byId('cadProjectActions').hidden = !appState.currentPlan;
        for (const button of byId('savedPlansContainer').querySelectorAll('[data-delete-plan]')) button.disabled = isBusy();
        for (const [kind, prefix] of [['pdf', 'Pdf'], ['dxf', 'Cad']]) {
            const listing = active?.kind === kind ? active.plan.listing : null;
            if (active?.kind === kind) byId(`btn${prefix}OpenListing`).hidden = !listing;
            byId(`btn${prefix}SavePlan`).hidden = !canSave(kind);
            byId(`btn${prefix}SavePlan`).disabled = isBusy();
            byId(`btnDelete${prefix}Plan`).disabled = isBusy();
        }
        const editable = Boolean(rights().can_assign);
        byId('fullscreenAddMarker').hidden = !editable;
        if (byId('fullscreenManageWorkbook')) byId('fullscreenManageWorkbook').hidden = !editable;
        fullMenu.querySelector('[data-project-action="export"]').hidden = rights().can_export === false;
        for (const action of ['add', 'saved-motors', 'assign-listing']) fullMenu.querySelector(`[data-project-action="${action}"]`).hidden = !editable;
        for (const prefix of ['Pdf', 'Cad']) {
            const kind = prefix === 'Pdf' ? 'pdf' : 'dxf';
            byId(`btn${prefix}AssignListing`).hidden = !rights(kind).can_assign;
            if (byId(`btn${prefix}Workbook`)) byId(`btn${prefix}Workbook`).hidden = !rights(kind).can_assign;
            byId(`btn${prefix}ExportProject`).hidden = rights(kind).can_export === false;
        }
        byId('fullscreenSavePlan').hidden = byId('fullscreenSaveMenu').hidden = !active || !canSave();
        byId('fullscreenDeletePlan').hidden = !active || !canDelete();
        byId('fullscreenMoveMarkerQuick').hidden = !active || !canMove();
        renderSidebarActions();
        for (const id of ['fullscreenSavePlan', 'fullscreenSaveMenu', 'fullscreenDeletePlan', 'fullscreenAddMarker', 'fullscreenMoveMarkerQuick']) byId(id).disabled = isBusy();
        renderUploadProgress();
        if (!active) return;
        const saveState = saving ? 'Guardando…' : active.plan._saveRequestError ? 'Guardado pendiente' : active.plan.cloud_status === 'pending' ? 'Subiendo a la nube…' : !active.plan.saved_at ? 'Sin guardar' : active.plan._projectDirty ? 'Cambios pendientes' : active.plan.cloud_status === 'synced' ? 'Almacenado en la nube' : active.plan.cloud_status === 'uploaded' ? 'En la nube · Confirmando registro…' : 'Guardado en este PC';
        const badge = byId(active.kind === 'pdf' ? 'pdfSaveBadge' : 'cadSaveBadge');
        badge.textContent = saveState;
        badge.classList.toggle('has-changes', saving || Boolean(active.plan._saveRequestError) || active.plan._projectDirty || active.plan.cloud_status !== 'synced');
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
        mountButtonList(configuration);
        update();
        if (!full) status(active.plan._projectDirty ? 'Cambios pendientes' : active.plan.saved_at ? cloudMessages[active.plan.cloud_status] || cloudMessages.local : 'Plano sin guardar');
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
        current.plan._saveRequestError = '';
        saving = true;
        update();
        status('Guardando plano, botones, fichas y listado…');
        try {
            const editor = window.workbookEditor;
            if (editor?.visible) {
                if (editor.busy) {
                    status('Espere a que termine la operación de Excel antes de guardar.', true);
                    return false;
                }
                editor.commitAll();
                if (editor.hasUnsavedChanges && !(await editor.save())) {
                    status('El plano no se guardó. Revise los cambios de la ficha abierta.', true);
                    return false;
                }
            }
            if (window.planDisplay && !(await window.planDisplay.flush())) return false;
            if (current.applyArea && !(await current.applyArea())) return false;
            const data = await post(current.plan.save_project_url);
            if (data.plan && active === current) {
                if (editor?.visible) editor.close(true);
                Object.assign(current.plan, data.plan);
                current.refresh?.();
            }
            current.plan.saved_at = data.saved_at;
            applyCloudState(current.plan, data.saved);
            current.plan.project_version = data.saved_at;
            window.planVersions?.remember(current.plan);
            current.plan._projectDirty = false;
            if (active === current) status(data.saved.cloud_error || cloudMessages[data.saved.cloud_status] || cloudMessages.local, data.saved.cloud_status === 'error');
            return true;
        } catch (error) {
            current.plan._saveRequestError = error.message;
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
        if (!target || isBusy() || !canDelete(target.kind, target.plan)) return;
        const { kind, plan } = target;
        if (!(await window.confirmAppAction('Eliminar plano',
            `¿Eliminar «${plan.name}» de la aplicación? Se quitarán sus botones y desaparecerá de Planos guardados.`,
            'Eliminar plano', { danger: true }))) return;
        if (isBusy() || !(await window.workbookEditor.requestClose())) return;
        deleting = true;
        libraryRequestVersion += 1;
        libraryLoading = false;
        update();
        try {
            try { await post(plan.delete_url); }
            catch (error) { if (error.status !== 404) throw error; }
            libraryRequestVersion += 1;
            libraryLoading = false;
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

    function mountButtonList({kind, viewport}) {
        if (viewport.parentElement.classList.contains('plan-view-frame')) return;
        const list = byId(kind === 'pdf' ? 'pdfMarkerList' : 'motorList');
        const frame = document.createElement('div');
        frame.className = 'plan-view-frame';
        const sidebar = document.createElement('aside');
        sidebar.className = 'plan-button-sidebar';
        sidebar.setAttribute('aria-label', 'Botones del plano');
        sidebar.innerHTML = `<div class="plan-button-sidebar-content">
            <div class="plan-button-sidebar-header">
                <button type="button" class="plan-button-list-toggle" aria-expanded="true">⋮</button>
                <strong>Botones</strong>
            </div>
            <div class="plan-button-sidebar-actions">
                <button type="button" class="plan-button-sidebar-action" data-button-action="assign" aria-label="Opciones del botón" title="Opciones del botón" aria-haspopup="dialog" hidden><i class="bi bi-gear" aria-hidden="true"></i></button>
                <button type="button" class="plan-button-sidebar-action" data-button-action="remove" aria-label="Eliminar botón" title="Eliminar botón" aria-haspopup="dialog" hidden><i class="bi bi-trash" aria-hidden="true"></i></button>
            </div>
        </div>`;
        const toggle = sidebar.querySelector('.plan-button-list-toggle');
        const heading = sidebar.querySelector('strong');
        toggle.setAttribute('aria-controls', list.id);
        function collapse(collapsed) {
            sidebar.classList.toggle('is-collapsed', collapsed);
            list.hidden = heading.hidden = collapsed;
            toggle.setAttribute('aria-expanded', String(!collapsed));
            toggle.setAttribute('aria-label', collapsed ? 'Mostrar listado de botones' : 'Minimizar listado de botones');
            toggle.title = toggle.getAttribute('aria-label');
        }
        toggle.onclick = () => collapse(!list.hidden);
        for (const button of sidebar.querySelectorAll('[data-button-action]')) {
            button.onclick = () => selectedActions?.[button.dataset.buttonAction]?.();
        }
        collapse(false);
        sidebar.firstElementChild.append(list);
        viewport.before(frame);
        frame.append(sidebar, viewport);
    }

    function renderSidebarActions() {
        const sidebar = active?.viewport.closest('.plan-view-frame')?.querySelector('.plan-button-sidebar');
        if (!sidebar) return;
        const busy = isBusy();
        for (const [action, allowed] of [['assign', rights().can_assign], ['remove', canDeleteMarker()]]) {
            const button = sidebar.querySelector(`[data-button-action="${action}"]`);
            const available = Boolean(selectedActions?.[action]);
            button.hidden = !allowed || !available;
            button.disabled = busy;
        }
    }

    async function enterFullscreen() {
        if (!active || full) return;
        resetOutsideClicks();
        full = true;
        originalViewport = active.viewport.parentElement;
        placeholder = document.createComment('plan-viewport');
        originalViewport.before(placeholder);
        byId('fullscreenPlanViewport').append(originalViewport);
        if (active.areaPanel) byId('fullscreenPlanViewport').before(active.areaPanel);
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
        resetOutsideClicks();
        full = false;
        setMenu(false);
        if (placeholder?.parentNode && originalViewport) {
            const areaPanel = overlay.querySelector('#pdfAreaPanel');
            if (areaPanel) placeholder.before(areaPanel);
            placeholder.replaceWith(originalViewport);
        }
        placeholder = originalViewport = null;
        overlay.hidden = true;
        if (window.workbookEditor?.visible) {
            window.workbookEditor.previousBodyOverflow = previousOverflow;
            document.body.style.overflow = 'hidden';
        } else document.body.style.overflow = previousOverflow;
        if (fullscreenOwned && document.fullscreenElement) document.exitFullscreen().catch(() => {});
        fullscreenOwned = false;
        active?.viewer.fit();
        window.planDisplay?.render(active, selectedActions);
    }
    function selection(configuration) {
        if (selectedActions?.buttonId !== configuration?.buttonId) resetOutsideClicks();
        selectedActions = configuration;
        for (const [kind, id] of [['pdf', 'btnPdfCopyButton'], ['dxf', 'btnCadCopyButton']]) {
            byId(id).disabled = active?.kind !== kind || !configuration || !active.plan.permissions?.can_assign;
        }
        window.pieceLibrary?.setTarget(configuration?.target || null);
        window.planDisplay?.render(active, configuration);
        renderSidebarActions();
        byId('fullscreenSelection').hidden = !configuration;
        if (!configuration) return;
        byId('fullscreenSelectionName').textContent = configuration.name;
        byId('fullscreenAdjustArea').hidden = !configuration.adjustArea;
        byId('fullscreenAdjustArea').onclick = configuration.adjustArea;
    }

    const outsideClickInterval = 500;
    const clickMovementLimit = 6;
    let outsidePress = null, lastOutsideClick = null, pendingDeselection = null;
    function canDeselectAt(target) {
        if (!active || !selectedActions || !(target instanceof Element)) return false;
        if (target.closest('[data-pdf-marker-id], [data-motor-id]')) return false;
        if (active.viewport.contains(target) &&
            (active.viewer.placing || window.planSectors?.editing || target.closest('.sector-selectable, [data-sector-handle]'))) return false;
        // Keep controls that operate on the current button usable while editing.
        if (window.workbookEditor?.visible || document.querySelector('.modal.show, .app-confirm[open]') || !byId('pdfMarkerDialog').hidden) return false;
        return !(target.closest('button, input, select, textarea, label, a') && target.closest(
            '#fullscreenSelection, #pdfAreaPanel, .plan-button-sidebar, #btnPdfAdjustArea, #btnMoveCadMarker, #btnPdfCopyButton, #btnCadCopyButton, .plan-size-control'
        ));
    }
    function resetOutsideClicks() {
        outsidePress = lastOutsideClick = pendingDeselection = null;
    }
    function movedFromPress(event, press) {
        return Math.hypot(event.clientX - press.x, event.clientY - press.y) > clickMovementLimit;
    }
    function finishOutsideDeselection(press) {
        if (!press || pendingDeselection !== press) return;
        pendingDeselection = null;
        if (press.plan !== active?.plan || press.buttonId !== selectedActions?.buttonId) return;
        const workflow = active.kind === 'pdf' ? window.pdfWorkflow : window.planWorkflow;
        workflow?.deselect();
    }
    document.addEventListener('pointerdown', event => {
        pendingDeselection = null;
        if (event.button !== 0 || !event.isPrimary || !canDeselectAt(event.target)) {
            resetOutsideClicks();
            return;
        }
        outsidePress = {id: event.pointerId, x: event.clientX, y: event.clientY,
            plan: active.plan, buttonId: selectedActions.buttonId, type: event.pointerType};
    }, true);
    document.addEventListener('pointermove', event => {
        if (outsidePress?.id === event.pointerId && movedFromPress(event, outsidePress)) resetOutsideClicks();
    }, true);
    document.addEventListener('pointerup', event => {
        const press = outsidePress;
        if (!press || press.id !== event.pointerId) return;
        outsidePress = null;
        const target = document.elementFromPoint(event.clientX, event.clientY);
        if (movedFromPress(event, press) || press.plan !== active?.plan || press.buttonId !== selectedActions?.buttonId || !canDeselectAt(target)) {
            resetOutsideClicks();
            return;
        }
        const now = performance.now();
        if (lastOutsideClick?.plan === press.plan && lastOutsideClick.buttonId === press.buttonId &&
            lastOutsideClick.type === press.type && now - lastOutsideClick.time <= outsideClickInterval) {
            resetOutsideClicks();
            pendingDeselection = press;
            // Let the tap finish before removing controls that can shift the view.
            setTimeout(() => finishOutsideDeselection(press), 350);
        } else {
            lastOutsideClick = {...press, time: now};
        }
    }, true);
    document.addEventListener('click', () => finishOutsideDeselection(pendingDeselection));
    document.addEventListener('pointercancel', resetOutsideClicks, true);
    document.addEventListener('wheel', resetOutsideClicks, {capture: true, passive: true});
    for (const id of ['btnPdfCopyButton', 'btnCadCopyButton']) byId(id).onclick = () => window.planExcel?.copySelectedButton();

    async function openListing() { await window.planExcel?.openListings(); }
    async function configureListing() { await window.planExcel?.listings(); }

    async function listSaved({ quiet = false } = {}) {
        const userId = appState.user?.id;
        if (!userId) return;
        const version = ++libraryRequestVersion;
        const parameters = new URLSearchParams();
        if (ownHistory) parameters.set('mine', '1');
        for (const [kind, workflow] of [['pdf', window.pdfWorkflow], ['dxf', window.planWorkflow]]) {
            if (workflow?.currentPlan) parameters.set(kind, workflow.currentPlan.id);
        }
        const container = byId('savedPlansContainer');
        if (!quiet) {
            byId('savedPlansFeedback').textContent = '';
            librarySignature = null;
            if (!localRows.length && !cloudRows.length) container.textContent = 'Cargando planos guardados…';
        }
        libraryLoading = true;
        try {
            const data = await request(`/api/projects/?${parameters}`);
            if (version !== libraryRequestVersion || appState.user?.id !== userId) return;
            for (const [kind, state] of Object.entries(data.availability || {})) {
                if (state.exists !== false || isBusy()) continue;
                const workflow = kind === 'pdf' ? window.pdfWorkflow : window.planWorkflow;
                if (workflow?.currentPlan?.id !== state.id) continue;
                workflow.removeDeleted(state.id);
                window.planExcel?.reset();
                status('Este plano fue eliminado de la aplicación.', false, kind);
            }
            byId('savedCountBadge').textContent = data.plans.length;
            const currentSaved = data.plans.find(saved => saved.kind === active?.kind && saved.plan_id === active?.plan.id);
            if (currentSaved && !saving && (!active.plan.saved_at || Date.parse(currentSaved.date) >= Date.parse(active.plan.saved_at))) {
                if (!active.plan.saved_at || Date.parse(currentSaved.date) > Date.parse(active.plan.saved_at)) active.plan._saveRequestError = '';
                applyCloudState(active.plan, currentSaved);
                if (!active.plan._projectDirty && !active.plan._saveRequestError) status(currentSaved.cloud_error || cloudMessages[currentSaved.cloud_status] || cloudMessages.local, currentSaved.cloud_status === 'error');
                update();
            }
            if (quiet && !byId('modalSavedPlans').classList.contains('show')) return;
            localRows = [...data.plans, ...(data.cached || [])];
            renderLibrary();
        } catch (error) {
            // Connection failures never mean a plan was deleted. Keep the last
            // confirmed state until this installation can be reached again.
            if (version === libraryRequestVersion && appState.user?.id === userId && !quiet) container.textContent = error.message;
            if (active?.plan.cloud_status === 'pending') { active.plan._cloudPollDelayed = true; renderUploadProgress(); }
        } finally { if (version === libraryRequestVersion) libraryLoading = false; }
    }
    const titleKey = (name) => name.replace(/\.pdf$/i, '').trim().toLocaleLowerCase('es');
    function libraryRows() {
        const entries = cloudRows.map(cloud => ({ name: cloud.title || cloud.name, cloud, copies: [] }));
        for (const copy of localRows) {
            const match = entries.find(({ cloud, copies }) => (copy.cloud_origin && copies.some(local => local.cloud_origin === copy.cloud_origin)) || (cloud && copy.kind === 'pdf' && (
                copy.cloud_origin === cloud.id || (cloud.cloud_key && copy.cloud_key === cloud.cloud_key &&
                (cloud.cloud_key.startsWith('project_') || titleKey(copy.name) === titleKey(cloud.title || cloud.name))))));
            if (match) match.copies.push(copy);
            else entries.push({ name: copy.name, cloud: null, copies: [copy] });
        }
        const filter = byId('savedDeviceFilter').value;
        const order = byId('savedCloudOrder').value;
        const day = byId('savedCloudDate').value;
        const localDate = new Intl.DateTimeFormat('sv-SE', { year: 'numeric', month: '2-digit', day: '2-digit' });
        const date = entry => Date.parse(entry.cloud?.date || entry.copies[0]?.date || '') || 0;
        return entries.filter(entry => (filter === 'all' || Boolean(entry.copies.length) === (filter === 'local')) &&
            (!day || (entry.cloud?.date && Number.isFinite(Date.parse(entry.cloud.date)) && localDate.format(new Date(entry.cloud.date)) === day)))
            .sort((a, b) => (order === 'device' ? Number(Boolean(b.copies.length)) - Number(Boolean(a.copies.length)) : 0) ||
                (order === 'oldest' ? date(a) - date(b) : date(b) - date(a)));
    }
    function libraryAction(label, icon, style, callback) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn btn-sm btn-outline-${style}`;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.innerHTML = `<i class="bi ${icon}" aria-hidden="true"></i>`;
        button.addEventListener('click', callback);
        return button;
    }
    function renderLibrary() {
        const rows = libraryRows();
        const signature = JSON.stringify(rows);
        if (signature === librarySignature) return;
        librarySignature = signature;
        const container = byId('savedPlansContainer');
        container.replaceChildren();
        if (!rows.length) container.textContent = 'No hay planos para mostrar con estos filtros.';
        for (const entry of rows) {
            const { copies, cloud, name } = entry;
            const local = copies[0];
            const kind = local?.kind || 'pdf';
            const row = document.createElement('div');
            row.className = 'list-group-item saved-plan-row rounded-3';
            const open = document.createElement('button');
            open.type = 'button';
            open.className = 'btn text-start flex-grow-1 text-break';
            const title = document.createElement('span');
            title.textContent = name;
            const badge = document.createElement('small');
            badge.className = 'project-kind-badge';
            badge.textContent = [kind.toUpperCase(), local ? 'En este PC' : 'Sin copia en este PC',
                local?.cloud_status === 'pending' ? 'Subiendo a la nube…' : cloud && 'En la nube',
                local?.cloud_status === 'partial' && 'Confirmación pendiente',
                local?.cloud_status === 'uploaded' && 'Archivos en la nube · Confirmando registro',
                (cloud?.date || local?.date) && new Date(cloud?.date || local.date).toLocaleString('es-CL')].filter(Boolean).join(' · ');
            open.append(title, badge);
            open.addEventListener('click', () => openLibraryEntry(entry, !local));
            row.append(open);
            if (cloud && local) row.append(libraryAction('Abrir versión de la nube', 'bi-cloud-arrow-down', 'primary', () => openLibraryEntry(entry, true)));
            if (local?.export_url && local.permissions?.can_export !== false) row.append(libraryAction('Exportar proyecto', 'bi-download', 'primary', () => download(local.export_url)));
            if (copies.length && copies.every(copy => copy.clear_url)) {
                const clear = libraryAction('Limpiar copia de este PC', 'bi-pc-display', 'secondary', () => removeLibraryEntry(entry, true));
                clear.dataset.deletePlan = local.plan_id;
                row.append(clear);
            }
            if ((local ? canDelete(kind, local) : appState.user?.permissions?.can_administer) && (cloud?.delete_url || local?.delete_url)) {
                const remove = libraryAction('Eliminar proyecto completo', 'bi-trash', 'danger', () => removeLibraryEntry(entry, false));
                remove.dataset.deletePlan = local?.plan_id || cloud.id;
                row.append(remove);
            }
            container.append(row);
        }
        update();
    }
    async function openLibraryEntry(entry, remote) {
        if (isBusy() || cloudOpening || !(await window.workbookEditor.requestClose())) return;
        const user = appState.user?.id;
        const local = entry.copies[0];
        const feedback = byId('savedPlansFeedback');
        cloudOpening = true;
        feedback.textContent = remote ? 'Descargando el plano, sus hojas y botones…' : 'Abriendo copia de este PC…';
        try {
            const result = remote ? await post(entry.cloud.open_url) : { plan: await request(local.detail_url) };
            if (appState.user?.id !== user) return;
            exitFullscreen();
            const opened = await openPlan(remote ? 'pdf' : local.kind, result.plan);
            if (opened === false) return;
            modalSavedPlans.hide();
            if (remote) status(result.warning || 'Plano y Excel abiertos desde la nube.', Boolean(result.warning));
        } catch (error) {
            feedback.textContent = error.message;
            if (error.status === 404) await listSaved({quiet: true});
        } finally { cloudOpening = false; }
    }
    async function removeLibraryEntry(entry, localOnly) {
        if (isBusy() || cloudOpening) return;
        const { cloud, copies, name } = entry;
        if (!localOnly && !cloud) return deletePlan({ kind: copies[0].kind, plan: { ...copies[0], id: copies[0].plan_id } });
        const message = localOnly ? `¿Limpiar «${name}» de este PC? ${cloud ? 'La copia en la nube seguirá disponible.' : 'Se quitará la copia guardada en este PC.'}` :
            `¿Eliminar «${name}» de todos los equipos? Se retirarán el PDF y el Excel de la nube y su registro de la base de datos.`;
        const action = localOnly ? 'Limpiar copia local' : 'Eliminar plano';
        if (!(await window.confirmAppAction(action, message, action, { danger: true }))) return;
        if (isBusy() || cloudOpening || !(await window.workbookEditor.requestClose())) return;
        deleting = true;
        update();
        try {
            if (localOnly) {
                for (const copy of copies) {
                    await post(copy.clear_url);
                    (copy.kind === 'pdf' ? window.pdfWorkflow : window.planWorkflow).removeDeleted(copy.plan_id);
                }
            } else {
                const result = await post(cloud.delete_url);
                for (const id of result.local_ids || []) window.pdfWorkflow.removeDeleted(id);
                cloudRows = cloudRows.filter(item => item.id !== cloud.id);
            }
        } catch (error) { byId('savedPlansFeedback').textContent = error.message; }
        finally {
            deleting = false;
            update();
            await listSaved({ quiet: true });
            if (!localOnly) await showCloudProjects({ quiet: true });
        }
    }
    function showSaved({ mine = false } = {}) {
        ownHistory = mine && Boolean(appState.user?.permissions?.can_history);
        libraryRequestVersion++;
        cloudRequestVersion++;
        localRows = []; cloudRows = []; librarySignature = null;
        byId('savedDeviceFilter').value = 'all';
        byId('savedCloudDate').value = '';
        byId('modalSavedPlans').querySelector('.modal-title').textContent = ownHistory ? 'Historial de mis planos' : 'Planos guardados';
        byId('cloudProjectsStatus').textContent = '';
        for (const id of ['savedDeviceFilter', 'savedCloudDate', 'btnCloudProjects']) {
            byId(id).closest('.col-6').hidden = ownHistory;
        }
        modalSavedPlans.show(); listSaved();
        if (!ownHistory) showCloudProjects();
    }
    async function showCloudProjects({ quiet = false } = {}) {
        const user = appState.user?.id;
        if (!user || cloudLoading || ownHistory) return;
        const version = ++cloudRequestVersion;
        cloudLoading = true;
        cloudCheckedAt = Date.now();
        const control = byId('btnCloudProjects');
        const feedback = byId('cloudProjectsStatus');
        control.disabled = true;
        if (!quiet) feedback.textContent = 'Consultando la nube…';
        try {
            const { plans } = await request('/api/projects/cloud/');
            if (appState.user?.id !== user || version !== cloudRequestVersion) return;
            cloudRows = plans;
            feedback.textContent = '';
            renderLibrary();
        } catch (error) {
            if (appState.user?.id === user && version === cloudRequestVersion) feedback.textContent = error.message;
        } finally {
            cloudLoading = false;
            control.disabled = false;
            if (version !== cloudRequestVersion && appState.user?.id === user && byId('modalSavedPlans').classList.contains('show')) showCloudProjects();
        }
    }
    byId('btnCloudProjects').addEventListener('click', () => showCloudProjects());
    for (const id of ['btnMyPlans', 'menuItemMyPlans']) byId(id)?.addEventListener('click', event => {
        event.preventDefault(); showSaved({ mine: true });
    });
    for (const id of ['savedDeviceFilter', 'savedCloudOrder', 'savedCloudDate']) byId(id).addEventListener('change', renderLibrary);
    byId('modalSavedPlans').addEventListener('hidden.bs.modal', () => {
        ownHistory = false;
        libraryRequestVersion++;
        cloudRequestVersion++;
        cloudRows = [];
        localRows = [];
        librarySignature = null;
    });
    function synchronize() {
        if (!appState.user || document.hidden || saving || deleting || active?.isBusy?.() || libraryLoading) return;
        localCheckedAt = Date.now();
        listSaved({ quiet: true });
        if (byId('modalSavedPlans').classList.contains('show') && Date.now() - cloudCheckedAt > 15000) showCloudProjects({ quiet: true });
    }
    window.addEventListener('focus', synchronize);
    window.addEventListener('pageshow', synchronize);
    window.addEventListener('online', synchronize);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) synchronize(); });
    setInterval(() => {
        if ((active?.plan.cloud_status === 'pending' || Date.now() - localCheckedAt >= 5000) &&
            (window.pdfWorkflow?.currentPlan || window.planWorkflow?.currentPlan || byId('modalSavedPlans').classList.contains('show'))) synchronize();
    }, 1000);
    async function openPlan(kind, plan) {
        if (kind === 'pdf') return window.pdfWorkflow.open(plan);
        return window.planWorkflow.open(plan);
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
    function modeChanged(mode, copying = false) {
        for (const [id, value] of [['fullscreenAddMarker', 'add'], ['fullscreenMoveMarkerQuick', 'move']]) byId(id).setAttribute('aria-pressed', String(mode === value));
        if (copying) status('Elige dónde colocar la copia del botón.');
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
        else if (action === 'saved-motors') window.pieceLibrary?.chooseForTarget();
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
        if (event.key !== 'Escape' || !full || window.workbookEditor.visible || window.planSectors?.editing || !byId('pdfMarkerDialog').hidden || document.querySelector('.modal.show')) return;
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
        reset() {
            libraryRequestVersion += 1;
            libraryLoading = false;
            librarySignature = null;
            exitFullscreen(); active = null; selection(null); window.planExcel?.reset(); update();
        },
    };
})();
