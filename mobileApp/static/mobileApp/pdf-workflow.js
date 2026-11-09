/* Configurable PDF plans and the shared, editable workbook library. */
(() => {
    const byId = (id) => document.getElementById(id);
    const view = byId('viewImportPDF');
    const viewport = byId('pdfViewport');
    const dialog = byId('pdfMarkerDialog');
    const pageSelect = byId('pdfPageSelect');
    const markerList = byId('pdfMarkerList');
    const library = byId('pdfWorkbookLibrary');
    let plan = null;
    const getPermissions = () => ({...appState.user?.permissions, ...plan?.permissions});
    let selectedId = null;
    let dialogMarkerId = null;
    let page = 1;
    let busy = false;
    let revision = 0;
    let mode = 'explore';
    let placementOptions = null;
    let focusBeforeDialog = null;

    async function request(url, options = {}) {
        const method = options.method || 'GET';
        const headers = new Headers(options.headers || {});
        if (!['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())) headers.set('X-CSRFToken', csrfToken());
        let response;
        try {
            response = await fetch(url, { ...options, credentials: 'same-origin', cache: 'no-store', headers });
        } catch (_) { throw new Error('No se pudo acceder a los datos de la aplicación. Intente nuevamente.'); }
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth?.expire();
            const error = new Error(data.error || 'No se pudo completar la operación.');
            error.status = response.status;
            error.data = data;
            throw error;
        }
        return data;
    }

    const post = (url, data) => request(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    });
    const upload = async (url, file) => {
        const body = new FormData();
        body.append('file', await window.workbookFields.normalizeUpload(file));
        return request(url, { method: 'POST', body });
    };
    const editor = window.workbookEditor = new WorkbookEditor({
        request,
        onSaved(workbook) {
            window.dispatchEvent(new CustomEvent('workbook-saved', { detail: workbook }));
            if (!plan) return;
            for (const marker of plan.markers) {
                if (marker.workbook?.id === workbook.id) {
                    const previous = marker.workbook;
                    marker.workbook = { ...workbook, preferred_sheet: marker.source_sheet || marker.sheet_name,
                        metadata_url: previous.metadata_url, select_source_url: previous.select_source_url, context: previous.context || 'motor' };
                }
            }
            updateMarkers();
        },
    });
    const viewer = new PdfPlanViewer(viewport, {
        pdfjsUrl: viewport.dataset.pdfjsUrl, workerUrl: viewport.dataset.workerUrl,
        onPlace: placeMarker,
        onSelect: (id) => selectMarker(id),
        onMove: moveMarker,
        onPageChange(detail) {
            if (page !== detail.page) closeAreaControls();
            page = detail.page;
            pageSelect.value = String(page);
            byId('btnPdfPreviousPage').disabled = page <= 1;
            byId('btnPdfNextPage').disabled = page >= detail.pageCount;
            renderMarkerList();
            window.projectWorkspace?.update();
        },
        onError: showError,
    });

    function status(message) { byId('pdfStatus').textContent = message; }
    function showError(error) {
        byId('pdfError').textContent = error?.message || String(error);
        byId('pdfError').hidden = false;
        if (window.projectWorkspace?.full && window.projectWorkspace.active?.kind === 'pdf') window.projectWorkspace.reportError(error?.message || String(error));
    }
    function clearError() { byId('pdfError').hidden = true; }
    function setBusy(value) {
        busy = value;
        areaToggle.disabled = value;
        for (const id of ['btnImportPdf', 'btnPdfClosePlan', 'btnPdfAddMarker', 'btnPdfMoveMarker']) byId(id).disabled = value;
        for (const control of areaPanel.querySelectorAll('button,input')) control.disabled = value;
        for (const button of dialog.querySelectorAll('button')) button.disabled = value;
        updatePermissions();
        byId('btnPdfDeleteSelectedMarker').disabled = value;
        window.projectWorkspace?.update();
    }
    function selectedMarker() { return plan?.markers.find((marker) => marker.id === selectedId); }
    function markerName(marker) {
        return marker.label || `Botón ${plan?.markers.findIndex((item) => item.id === marker.id) + 1}`;
    }
    async function setMode(next) {
        if (next === 'add' && window.planExcel) {
            const current = plan;
            const options = await window.planExcel.newButtonOptions();
            if (!options || current !== plan) return;
            placementOptions = options;
        } else {
            placementOptions = null;
        }
        window.projectWorkspace?.modeChanged(next);
        mode = next;
        viewer.setPlacing(next === 'add');
        viewer.setMoving(next === 'move');
        for (const [id, value] of [['btnPdfExplore', 'explore'], ['btnPdfAddMarker', 'add'], ['btnPdfMoveMarker', 'move']]) {
            byId(id).classList.toggle('active', value === next);
            byId(id).setAttribute('aria-pressed', String(value === next));
        }
        byId('pdfModeHint').textContent = next === 'add'
            ? 'Toque el punto del plano donde desea colocar el botón.'
            : next === 'move' ? 'Arrastre un botón hasta su nueva posición. También puede moverlo con las flechas del teclado.'
            : 'Pulse un botón para seleccionarlo y vuelva a pulsarlo para abrir su ficha.';
    }

    function renderMarkerList() {
        markerList.replaceChildren();
        for (const marker of plan?.markers.filter((item) => item.page === page) || []) {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.markerId = marker.id;
            button.textContent = [markerName(marker), marker.category_name, marker.sheet_name || marker.source_sheet || 'Sin hoja'].filter(Boolean).join(' · ');
            button.setAttribute('aria-pressed', String(marker.id === selectedId));
            button.addEventListener('click', () => selectMarker(marker.id));
            markerList.append(button);
        }
        if (plan && !markerList.childElementCount) {
            const empty = document.createElement('p');
            empty.className = 'small text-muted mb-0';
            empty.textContent = 'Esta página todavía no tiene botones. Pulse «Añadir botón» para colocar el primero.';
            markerList.append(empty);
        }
    }

    function updateSelection() {
        const marker = selectedMarker();
        if (areaMarkerId !== marker?.id) closeAreaControls();
        const canAdjust = marker?.page === page && getPermissions().can_change_pdf_plan;
        areaToggle.hidden = !canAdjust;
        areaToggle.disabled = busy;
        byId('pdfSelectedMarker').hidden = !marker;
        if (window.projectWorkspace?.active?.kind === 'pdf') window.projectWorkspace.selection(marker ? {
            buttonId: marker.id, name: markerName(marker), workbook: marker.workbook, adjustArea: canAdjust ? openAreaControls : null, assign: configureMarker, open: () => openWorkbook(marker.workbook), remove: deleteSelectedMarker,
            target: { category_id: marker.category_id, name: markerName(marker), assign_url: marker.assign_url, apply(result) {
                const index = plan.markers.findIndex((item) => item.id === result.marker.id);
                if (index < 0) return null;
                plan.markers[index] = result.marker;
                selectedId = result.marker.id;
                switchView(view);
                activateProject();
                updateMarkers();
                window.projectWorkspace.dirty();
                return { ...result.marker.workbook, removeMarker: getPermissions().can_delete_pdf_marker ? deleteSelectedMarker : null };
            } },
        } : null);
        if (!marker) return;
        byId('pdfSelectedMarkerName').textContent = markerName(marker);
        byId('pdfSelectedWorkbookName').textContent = marker.sheet_name || marker.source_sheet || 'Elija una hoja del Excel del plano.';
        byId('btnPdfOpenWorkbook').hidden = !marker.workbook;
    }

    function updateMarkers() {
        viewer.setMarkers(plan?.markers || []);
        viewer.select(selectedId);
        renderMarkerList();
        updateSelection();
    }

    async function selectMarker(id, open = true) {
        if (busy || !plan || !appState.user) return;
        const marker = plan.markers.find((item) => item.id === Number(id));
        if (!marker) return;
        const alreadySelected = selectedId === marker.id;
        selectedId = marker.id;
        updateMarkers();
        if (!open || !alreadySelected || mode === 'move') return;
        await openSelectedButton(marker);
    }

    function deselectMarker() {
        selectedId = null;
        updateMarkers();
    }

    async function openSelectedButton(marker) {
        if (marker.show_description) await showButtonDetails(marker);
        else if (marker.workbook) await openWorkbook(marker.workbook);
        else await configureMarker();
    }

    async function showButtonDetails(marker, edit = false) {
        closeAreaControls();
        const currentPlan = plan;
        await window.buttonDetails.open(marker, {
            kind: 'pdf', name: markerName(marker), edit,
            defaultName: `Botón ${plan.markers.findIndex(item => item.id === marker.id) + 1}`,
            openExcel: marker.workbook ? () => openWorkbook(marker.workbook) : null,
            assign: getPermissions().can_assign ? configureMarker : null,
            onSaved(updated) {
                if (plan !== currentPlan) return;
                Object.assign(marker, updated);
                updateMarkers();
                window.projectWorkspace?.dirty();
            },
        });
    }

    async function openWorkbook(workbook) {
        closeAreaControls();
        hideDialog();
        try { await editor.open({ ...workbook, removeMarker: getPermissions().can_delete_pdf_marker ? deleteSelectedMarker : null }); }
        catch (error) { showError(error); }
    }

    async function configureMarker() {
        closeAreaControls();
        const marker = selectedMarker();
        if (!marker) return;
        if (window.planExcel) { await window.planExcel.assign('pdf', plan, marker); return; }
        focusBeforeDialog = document.activeElement;
        dialogMarkerId = marker.id;
        byId('pdfMarkerLabel').value = marker.label;

        byId('pdfMarkerCurrentWorkbook').textContent = marker.workbook ? `Excel actual: ${marker.workbook.name}` : 'Este botón todavía no tiene un Excel.';
        byId('btnPdfUnlinkWorkbook').hidden = !marker.workbook;
        updatePermissions();
        byId('pdfMarkerDialogStatus').textContent = '';
        library.replaceChildren(new Option('Cargando biblioteca…', ''));
        dialog.hidden = false;
        byId('pdfMarkerLabel').focus();
        try {
            const data = await request('/api/piece-library/');
            if (dialogMarkerId !== marker.id || dialog.hidden) return;
            library.replaceChildren(new Option('Seleccione una pieza guardada', ''));
            for (const motor of data.pieces) library.append(new Option(motor.name, String(motor.id)));
            if (!data.pieces.length) library.options[0].textContent = 'No hay piezas guardadas. Puede usar la ficha vacía o importar del dispositivo.';
        } catch (error) { if (dialogMarkerId === marker.id) byId('pdfMarkerDialogStatus').textContent = error.message; }
    }

    function hideDialog() {
        dialog.hidden = true;
        dialogMarkerId = null;
        if (focusBeforeDialog?.isConnected) focusBeforeDialog.focus();
    }

    async function markerAction(action, success, openExcel = false) {
        const marker = selectedMarker();
        if (!marker || busy) return;
        const currentRevision = revision;
        clearError();
        setBusy(true);
        byId('pdfMarkerDialogStatus').textContent = 'Guardando…';
        try {
            const result = await action(marker);
            if (currentRevision !== revision || !appState.user) return;
            if (result.marker) {
                const index = plan.markers.findIndex((item) => item.id === marker.id);
                plan.markers[index] = result.marker;
            }
            if (result.deleted) {
                plan.markers = plan.markers.filter((item) => item.id !== marker.id);
                selectedId = null;
                hideDialog();
            }
            updateMarkers();
            byId('pdfMarkerDialogStatus').textContent = success;
            status(success);
            window.projectWorkspace?.dirty();
            if (openExcel && result.marker?.workbook) await openWorkbook(result.marker.workbook);
            return true;
        } catch (error) {
            byId('pdfMarkerDialogStatus').textContent = error.message;
            showError(error);
        } finally { setBusy(false); }
    }

    async function placeMarker(position) {
        if (!plan || busy || !appState.user) return;
        const currentRevision = revision;
        const fromPiece = placementOptions?.origin === 'piece';
        let pieceWorkbook = null;
        setBusy(true);
        clearError();
        try {
            const { marker } = placementOptions
                ? await window.planExcel.createButton(plan.markers_url, position, placementOptions)
                : await post(plan.markers_url, position);
            if (currentRevision !== revision) return;
            plan.markers.push(marker);
            selectedId = marker.id;
            updateMarkers();
            setMode('explore');
            status('Botón creado y seleccionado. Pulse de nuevo para abrir su ficha.');
            window.projectWorkspace?.dirty();
            if (fromPiece) pieceWorkbook = marker.workbook;
        } catch (error) { showError(error); }
        finally { setBusy(false); }
        if (pieceWorkbook && currentRevision === revision) await openWorkbook(pieceWorkbook);
    }

    async function moveMarker(id, position) {
        const marker = plan?.markers.find((item) => item.id === Number(id));
        if (!marker || busy || !appState.user) return;
        const currentRevision = revision;
        setBusy(true);
        try {
            const result = await post(marker.update_url, position);
            if (currentRevision !== revision) return;
            Object.assign(marker, result.marker);
            selectedId = marker.id;
            status('Posición del botón guardada.');
            window.projectWorkspace?.dirty();
        } catch (error) { showError(error); }
        finally { if (currentRevision === revision) updateMarkers(); setBusy(false); }
    }

    async function openPlan(nextPlan, currentRevision) {
        nextPlan = await window.planVersions.beforeOpen(nextPlan);
        if (!nextPlan) return false;
        if (currentRevision !== revision || !appState.user) return;
        closeAreaControls();
        hideDialog();
        editor.close(true);
        plan = nextPlan;
        selectedId = null;
        page = 1;
        byId('pdfPlanSection').hidden = false;
        byId('pdfSelectedMarker').hidden = true;
        byId('pdfPlanName').textContent = plan.name;
        pageSelect.replaceChildren();
        for (let index = 1; index <= plan.pages.length; index += 1) pageSelect.append(new Option(`Página ${index} / ${plan.pages.length}`, String(index)));
        setMode('explore');
        setBusy(true);
        activateProject();
        try {
            const loaded = await viewer.load(plan.source_url, plan.markers);
            if (currentRevision !== revision || !loaded) return;
            updateMarkers();
            activateProject();
            status('Plano listo. Puede recorrer sus páginas y añadir botones.');
            clearError();
            return true;
        } finally { if (currentRevision === revision) setBusy(false); }
    }

    function reset({ closeEditor = true } = {}) {
        window.projectWorkspace?.deactivate('pdf');
        revision += 1;
        closeAreaControls();
        hideDialog();
        if (closeEditor) editor.close(true);
        viewer.clear();
        plan = null;
        selectedId = null;
        page = 1;
        byId('pdfPlanSection').hidden = true;
        byId('pdfSelectedMarker').hidden = true;
        byId('pdfSavedList').replaceChildren();
        byId('pdfSavedList').hidden = true;
        status('');
        clearError();
        setBusy(false);
    }

    byId('btnGoToPdfSection').addEventListener('click', () => {
        if (!appState.user) return;
        switchView(view);
        activateProject();
        if (!plan) status('Importe un PDF o abra uno guardado para comenzar.');
        else viewer.fit();
    });
    byId('btnPdfBack').addEventListener('click', () => { hideDialog(); switchView(viewMainMenu); });
    byId('btnPdfSaved').addEventListener('click', () => window.projectWorkspace?.showSaved());
    byId('btnImportPdf').addEventListener('click', () => { if (!busy) byId('pdfFileInput').click(); });
    byId('pdfFileInput').addEventListener('change', async (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file || busy || !appState.user) return;
        if (!file.name.toLowerCase().endsWith('.pdf')) { showError('Solo se admiten archivos .pdf.'); return; }
        if (!file.size || file.size > 20 * 1024 * 1024) { showError('Seleccione un PDF con contenido y de hasta 20 MB.'); return; }
        const currentRevision = ++revision;
        setBusy(true);
        status('Importando y dibujando el PDF…');
        clearError();
        try { await openPlan(await upload('/api/pdf/plans/import/', file), currentRevision); }
        catch (error) { if (currentRevision === revision) { showError(error); status(''); } }
        finally { setBusy(false); }
    });
    byId('btnPdfClosePlan').addEventListener('click', reset);
    byId('btnPdfExplore').addEventListener('click', () => setMode('explore'));
    byId('btnPdfAddMarker').addEventListener('click', () => setMode('add'));
    byId('btnPdfMoveMarker').addEventListener('click', () => setMode('move'));
    pageSelect.addEventListener('change', () => { viewer.setPage(Number(pageSelect.value)).catch(showError); });
    byId('btnPdfPreviousPage').addEventListener('click', () => { viewer.setPage(page - 1).catch(showError); });
    byId('btnPdfNextPage').addEventListener('click', () => { viewer.setPage(page + 1).catch(showError); });
    byId('btnPdfZoomIn').addEventListener('click', () => viewer.zoom(1.35));
    byId('btnPdfZoomOut').addEventListener('click', () => viewer.zoom(1 / 1.35));
    byId('btnPdfFit').addEventListener('click', () => viewer.fit());
    byId('btnPdfConfigureMarker').addEventListener('click', configureMarker);
    byId('btnPdfOpenWorkbook').addEventListener('click', () => {
        if (selectedMarker()?.workbook) openWorkbook(selectedMarker().workbook);
    });
    byId('btnPdfCloseMarkerDialog').addEventListener('click', hideDialog);
    byId('btnPdfSaveMarkerLabel').addEventListener('click', () => markerAction(
        (marker) => post(marker.update_url, { label: byId('pdfMarkerLabel').value }), 'Nombre del botón guardado.',
    ));

    const areaPanel = byId('pdfAreaPanel');
    const areaToggle = byId('btnPdfAdjustArea');
    const areaEnabled = byId('pdfMarkerHasArea');
    const radiusFields = byId('pdfMarkerAreaSection');
    const radiusX = byId('pdfMarkerRadiusX'), radiusY = byId('pdfMarkerRadiusY');
    const lockRadius = byId('pdfMarkerLockRadius');
    let areaMarkerId = null;

    function closeAreaControls() {
        if (areaPanel.hidden) return;
        viewer.setMarkers(plan?.markers || []);
        viewer.select(selectedId);
        areaMarkerId = null;
        areaPanel.hidden = true;
        areaToggle.setAttribute('aria-expanded', 'false');
    }

    function previewArea() {
        if (areaPanel.hidden) return;
        radiusFields.hidden = !areaEnabled.checked;
        byId('pdfMarkerRadiusXVal').textContent = radiusX.value + '%';
        byId('pdfMarkerRadiusYVal').textContent = radiusY.value + '%';
        viewer.previewMarkerArea(areaMarkerId, areaEnabled.checked, Number(radiusX.value) / 100, Number(radiusY.value) / 100);
    }

    function openAreaControls() {
        const marker = selectedMarker();
        if (!marker || marker.page !== page || busy || !getPermissions().can_change_pdf_plan) return;
        if (!areaPanel.hidden) { closeAreaControls(); return; }
        areaMarkerId = marker.id;
        radiusX.value = Math.round((marker.area_radius_x || .05) * 1000) / 10;
        radiusY.value = Math.round((marker.area_radius_y || marker.area_radius_x || .05) * 1000) / 10;
        lockRadius.checked = radiusX.value === radiusY.value;
        areaEnabled.checked = true;
        byId('pdfAreaMarkerName').textContent = markerName(marker);
        areaPanel.hidden = false;
        areaToggle.setAttribute('aria-expanded', 'true');
        previewArea();
        areaPanel.scrollIntoView({ block: 'nearest' });
    }

    async function saveAreaControls() {
        if (areaPanel.hidden) return true;
        if (areaMarkerId !== selectedId || busy || !getPermissions().can_change_pdf_plan) return false;
        const saved = await markerAction(marker => post(marker.update_url, {
            has_area: areaEnabled.checked, area_radius_x: Number(radiusX.value) / 100, area_radius_y: Number(radiusY.value) / 100,
        }), 'Radio actualizado en el plano.');
        if (saved) closeAreaControls();
        return Boolean(saved);
    }

    areaToggle.addEventListener('click', openAreaControls);
    areaEnabled.addEventListener('change', previewArea);
    lockRadius.addEventListener('change', () => {
        if (lockRadius.checked) radiusY.value = radiusX.value;
        previewArea();
    });
    for (const [input, other] of [[radiusX, radiusY], [radiusY, radiusX]]) {
        input.addEventListener('input', () => {
            if (lockRadius.checked) other.value = input.value;
            previewArea();
        });
    }
    byId('btnPdfCancelMarkerArea').addEventListener('click', closeAreaControls);
    byId('btnPdfSaveMarkerArea').addEventListener('click', saveAreaControls);
    byId('btnPdfAssignLibrary').addEventListener('click', () => {
        const id = library.value;
        if (!id) { byId('pdfMarkerDialogStatus').textContent = 'Seleccione un Excel de la biblioteca.'; return; }
        markerAction((marker) => post(marker.assign_url, { saved_motor_id: Number(id) }), 'Copia del motor guardado asignada.', true);
    });
    byId('btnPdfAssignDevice').addEventListener('click', () => {
        byId('pdfExcelFileInput').value = '';
        byId('pdfExcelFileInput').click();
    });
    byId('pdfExcelFileInput').addEventListener('change', (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        if (!file.name.toLowerCase().endsWith('.xlsx') || !file.size || file.size > 20 * 1024 * 1024) {
            byId('pdfMarkerDialogStatus').textContent = 'Seleccione un Excel .xlsx válido de hasta 20 MB.';
            return;
        }
        markerAction((marker) => upload(marker.assign_url, file), 'Ficha asignada. Se incluye al guardar el plano.', true);
    });
    byId('btnPdfUnlinkWorkbook').addEventListener('click', () => markerAction(
        (marker) => post(marker.update_url, { workbook_id: null }), 'Nueva ficha vacía asignada al botón.', true,
    ));
    async function deleteSelectedMarker() {
        const marker = selectedMarker();
        if (!marker || busy || !getPermissions().can_delete_pdf_marker) return;
        if (!window.confirm(`¿Eliminar el botón «${markerName(marker)}» del plano?`)) return;
        if (!(await editor.requestClose())) return;
        await markerAction(() => post(marker.delete_url, {}), 'Botón eliminado.');
        setMode('explore');
    }
    byId('btnPdfDeleteMarker').addEventListener('click', deleteSelectedMarker);
    byId('btnPdfDeleteSelectedMarker').addEventListener('click', deleteSelectedMarker);
    function updatePermissions() {
        const permissions = getPermissions();
        byId('btnGoToPdfSection').hidden = !appState.user;
        byId('btnPdfMoveMarker').hidden = !permissions.can_change_pdf_plan;
        byId('btnPdfAddMarker').hidden = !permissions.can_assign;
        byId('btnPdfDeleteMarker').hidden = !permissions.can_delete_pdf_marker;
        byId('btnPdfDeleteSelectedMarker').hidden = !permissions.can_delete_pdf_marker;
        byId('btnRenamePdfPlan').hidden = !plan || !permissions.can_change_pdf_plan;
        byId('btnDeletePdfPlan').hidden = !plan || !permissions.can_delete_pdf_plan;
        byId('btnPdfOpenWorkbook').querySelector('span').textContent = permissions.can_change_excel ? 'Abrir y editar Excel' : 'Ver Excel';
        if (!permissions.can_change_pdf_plan && mode === 'move') setMode('explore');
    }
    byId('btnRenamePdfPlan').addEventListener('click', async () => {
        if (!plan || busy) return;
        const name = window.prompt('Nombre del plano guardado', plan.name);
        if (name === null || name === plan.name) return;
        setBusy(true);
        try {
            const updated = await post(plan.update_url, { name });
            plan.name = updated.name;
            byId('pdfPlanName').textContent = updated.name;
        } catch (error) { showError(error); }
        finally { setBusy(false); }
    });
    dialog.addEventListener('click', (event) => { if (event.target === dialog && !busy) hideDialog(); });
    dialog.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !busy) { event.preventDefault(); hideDialog(); }
        if (event.key !== 'Tab') return;
        const controls = [...dialog.querySelectorAll('button,input,select')].filter((element) => !element.disabled && element.getClientRects().length);
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    window.pdfWorkflow = {
        reset,
        get currentPlan() { return plan; },
        select(id) { return selectMarker(id, false); },
        deselect: deselectMarker,
        editButtonDetails(marker, mode = 'description') { return showButtonDetails(marker, mode); },
        removeDeleted(id) {
            if (plan?.id !== id) return;
            const documentId = editor.metadata?.id;
            const closeEditor = Boolean(documentId && (plan.workbook?.id === documentId || plan.listing?.id === documentId || plan.markers.some(marker => marker.workbook?.id === documentId)));
            reset({ closeEditor });
        },
        permissionsChanged: updatePermissions,
        canLeave() { return editor.requestClose(); },
        async open(nextPlan) {
            switchView(view);
            return openPlan(nextPlan, ++revision);
        },
    };

    function activateProject() {
        if (plan && view.classList.contains('active')) {
            window.projectWorkspace?.activate({ kind: 'pdf', plan, viewport, viewer, setMode, areaPanel, applyArea: saveAreaControls, refresh: updateMarkers, isBusy: () => busy });
            updateSelection();
        }
    }
})();
