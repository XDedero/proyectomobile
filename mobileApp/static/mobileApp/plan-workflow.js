/* The Django API persists the drawing and each motor's Excel independently. */
(() => {
    const status = document.getElementById('planStatus');
    const viewerSection = document.getElementById('dxfViewerSection');
    const motorList = document.getElementById('motorList');
    const motorCard = document.getElementById('selectedMotorCard');
    const assignMotor = document.getElementById('btnAssignMotorExcel');
    const originalImportLabel = btnImportAutoCAD.innerHTML;
    let busy = false;
    let revision = 0;
    let pendingAssignment = null;
    const viewer = new DxfViewer(document.getElementById('dxfViewport'), id => {
        if (busy || viewer.moving) return;
        activateMotor(id);
    });
    const activeKey = () => `dxf.activePlan.${appState.user?.id || 'anonymous'}`;
    const permissions = () => ({...appState.user?.permissions, ...appState.currentPlan?.permissions});
    let placing = false;

    function remember(id) {
        try {
            if (id) localStorage.setItem(activeKey(), String(id));
            else localStorage.removeItem(activeKey());
        } catch (_) { /* Browser storage is optional; server storage remains available. */ }
    }

    function showError(message) {
        document.getElementById('errorNoPlanText').textContent = message;
        errorNoPlanAlert.style.display = 'flex';
        if (window.projectWorkspace?.full && window.projectWorkspace.active?.kind === 'dxf') window.projectWorkspace.reportError(message);
    }

    function setBusy(value, message = '') {
        busy = value;
        btnImportAutoCAD.disabled = value || !permissions().can_import;
        btnAssignExcelCorner.disabled = value || !permissions().can_assign;
        assignMotor.disabled = value || !permissions().can_assign;
        btnRemovePlan.disabled = value;
        btnImportAutoCAD.innerHTML = value ? '<span class="spinner-border spinner-border-sm" aria-hidden="true"></span> Procesando…' : originalImportLabel;
        if (message) status.textContent = message;
        document.getElementById('btnDeleteCadMarker').disabled = value;
        window.projectWorkspace?.update();
    }

    async function request(url, file, values) {
        if (location.protocol === 'file:') throw new Error('Abra la aplicación completa para acceder a sus archivos locales.');
        const options = { credentials: 'same-origin', cache: 'no-store' };
        if (file) {
            const token = document.cookie.split('; ').find((entry) => entry.startsWith('csrftoken='))?.slice(10);
            const body = new FormData();
            body.append('file', await window.workbookFields.normalizeUpload(file));
            options.method = 'POST';
            options.body = body;
            options.headers = { 'X-CSRFToken': decodeURIComponent(token || '') };
        } else if (values !== undefined) {
            options.method = 'POST';
            options.body = JSON.stringify(values);
            options.headers = { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() };
        }
        let response;
        try { response = await fetch(url, options); }
        catch (_) { throw new Error('No se pudo acceder a los datos de la aplicación. Intente nuevamente.'); }
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth.expire();
            if (response.status === 403) await window.appAuth.refresh().catch(() => {});
            throw new Error(data.error || 'No se pudo completar la operación.');
        }
        return data;
    }

    function validateFile(file, extension) {
        if (!file.name.toLowerCase().endsWith(extension)) throw new Error(`Solo se admiten archivos ${extension}.`);
        if (!file.size || file.size > 20 * 1024 * 1024) throw new Error('Seleccione un archivo con contenido y de hasta 20 MB.');
    }

    function validatePlanFile(file) {
        const lower = file.name.toLowerCase();
        if (!lower.endsWith('.dxf')) {
            throw new Error('Solo se admiten archivos .dxf.');
        }
        if (!file.size || file.size > 20 * 1024 * 1024) {
            throw new Error('Seleccione un archivo con contenido y de hasta 20 MB.');
        }
    }

    function selectedMotor() {
        return appState.currentPlan?.motors.find((motor) => motor.id === appState.selectedMotorId);
    }

    function updateMotorCard() {
        const motor = selectedMotor();
        motorCard.hidden = !motor;
        excelLinkedSection.style.display = motor?.excel ? 'block' : 'none';
        if (window.projectWorkspace?.active?.kind === 'dxf') window.projectWorkspace.selection(motor ? {
            buttonId: motor.id, name: motor.name, workbook: motor.excel, assign: chooseExcel, open: showPreview, remove: deleteSelectedMarker,
            target: { category_id: motor.category_id, name: motor.name, assign_url: motor.assign_url, apply(result) {
                const current = appState.currentPlan?.motors.find((item) => item.id === result.motor.id);
                if (!current) return null;
                Object.assign(current, result.motor);
                appState.selectedMotorId = current.id;
                switchView(viewImportAutoCAD);
                updatePlanCardUI();
                viewer.select(current.id);
                window.projectWorkspace.dirty();
                return { ...current.excel, removeMarker: permissions().can_delete_cad_marker ? deleteSelectedMarker : null };
            } },
        } : null);
        if (!motor) return;
        document.getElementById('selectedMotorName').textContent = [motor.name, motor.category_name].filter(Boolean).join(' · ');
        const number = (value) => value.toLocaleString('es-CL', { maximumFractionDigits: 6 });
        document.getElementById('selectedMotorCoordinates').textContent = `X: ${number(motor.x)} · Y: ${number(motor.y)} · Z: ${number(motor.z)} (unidades del dibujo)`;
        document.getElementById('selectedMotorHint').textContent = motor.excel
            ? 'Hoja del Excel del plano. Puede vincularla también a otros botones.'
            : 'Elija una hoja, una ficha vacía o un motor guardado.';
        if (motor.excel) {
            excelFileName.textContent = motor.sheet_name || motor.excel.name;
            excelFileName.href = motor.excel.url;
            document.getElementById('excelSheets').textContent = `Hojas: ${motor.excel.sheets.join(', ')}`;
        } else excelFileName.removeAttribute('href');
    }

    function selectMotor(id, center = false) {
        const motor = appState.currentPlan?.motors.find((item) => item.id === id);
        if (!motor) return;
        resetPreview();
        appState.selectedMotorId = id;
        viewer.select(id);
        if (center) viewer.focusMotor(id);
        for (const button of motorList.querySelectorAll('button')) button.setAttribute('aria-pressed', String(Number(button.dataset.motorId) === id));
        updateMotorCard();
        errorNoPlanAlert.style.display = 'none';
        status.textContent = `${motor.name} seleccionado. Pulse de nuevo para abrir su ficha.`;
    }

    function activateMotor(id, center = false) {
        if (busy || viewer.moving || !appState.currentPlan?.motors.some(item => item.id === id)) return;
        const alreadySelected = appState.selectedMotorId === id;
        selectMotor(id, center);
        if (alreadySelected) openSelectedButton();
    }

    function deselectMotor() {
        appState.selectedMotorId = null;
        viewer.select(null);
        for (const button of motorList.querySelectorAll('button')) button.setAttribute('aria-pressed', 'false');
        updateMotorCard();
        status.textContent = '';
    }

    function updatePlanCardUI() {
        const plan = appState.currentPlan;
        loadedPlanCard.style.display = plan ? 'block' : 'none';
        viewerSection.hidden = !plan;
        if (plan) {
            planFileName.textContent = plan.name;
            planFileSize.textContent = `${(plan.size / 1024).toLocaleString('es-CL', { maximumFractionDigits: 1 })} KB`;
            planFileDate.textContent = new Date(plan.date).toLocaleDateString('es-CL');
            document.getElementById('motorCount').textContent = `${plan.motors.length} motores`;
            document.getElementById('noMotorsMessage').hidden = plan.motors.length !== 0;
            document.getElementById('noMotorsMessage').textContent = `No se encontró la palabra ${plan.motor_keyword} en el plano. Puede añadir botones manualmente.`;
        }
        motorList.replaceChildren();
        for (const [index, motor] of (plan?.motors || []).entries()) {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.motorId = motor.id;
            button.setAttribute('aria-pressed', String(motor.id === appState.selectedMotorId));
            button.textContent = `${index + 1}. ${motor.name} · ${motor.sheet_name || 'Sin hoja asignada'}`;
            button.addEventListener('click', () => activateMotor(motor.id, true));
            motorList.append(button);
        }
        updateMotorCard();
        updateActions();
        activateProject();
    }

    async function openPlan(plan, currentRevision) {
        plan = await window.planVersions.beforeOpen(plan);
        if (!plan || currentRevision !== revision) return false;
        const response = await fetch(plan.svg_url, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('No se pudo cargar la vista del plano. Puede volver a abrirlo desde Planos guardados.');
        const drawing = await response.text();
        if (currentRevision !== revision) return;
        resetPreview();
        const previous = appState.currentPlan;
        appState.currentPlan = plan;
        appState.selectedMotorId = null;
        updatePlanCardUI();
        try { viewer.load(drawing, plan.motors); }
        catch (error) {
            appState.currentPlan = previous;
            updatePlanCardUI();
            throw error;
        }
        remember(plan.id);
        setMode('explore');
        activateProject();
        status.textContent = plan.motors.length ? 'Pulse un motor para seleccionarlo y vuelva a pulsarlo para abrir su ficha.' : 'Plano listo. Puede desplazarse y ampliar el dibujo.';
        errorNoPlanAlert.style.display = 'none';
        return true;
    }

    function close() {
        window.projectWorkspace?.deactivate('dxf');
        revision += 1;
        appState.currentPlan = null;
        appState.selectedMotorId = null;
        pendingAssignment = null;
        placing = viewer.placing = false;
        resetPreview();
        autocadFileInput.value = '';
        excelFileInput.value = '';
        remember(null);
        viewer.clear();
        updatePlanCardUI();
        status.textContent = '';
    }

    btnImportAutoCAD.addEventListener('click', () => { if (!busy) autocadFileInput.click(); });
    autocadFileInput.addEventListener('change', async () => {
        const file = autocadFileInput.files[0];
        autocadFileInput.value = '';
        if (!file || busy || !permissions().can_import) return;
        const currentRevision = ++revision;
        try {
            validatePlanFile(file);
            setBusy(true, 'Leyendo plano y buscando motores…');
            const plan = await request('/api/plans/import/', file);
            if (currentRevision === revision) await openPlan(plan, currentRevision);
        } catch (error) { if (currentRevision === revision) { showError(error.message); status.textContent = ''; } }
        finally { setBusy(false); }
    });

    function chooseExcel() {
        if (busy) return;
        if (!permissions().can_assign) { showError('Su usuario no tiene permiso para modificar el Excel de los motores.'); return; }
        if (!appState.currentPlan) { showError('Importe un plano DXF antes de asignar un Excel.'); return; }
        const motor = selectedMotor();
        if (!motor) { showError('Toque un motor en el plano o selecciónelo en la lista antes de asignar su Excel.'); return; }
        if (window.planExcel) { window.planExcel.assign('dxf', appState.currentPlan, motor); return; }
        pendingAssignment = { planId: appState.currentPlan.id, motorId: motor.id, url: motor.assign_url };
        excelFileInput.value = '';
        excelFileInput.click();
    }

    btnAssignExcelCorner.addEventListener('click', chooseExcel);
    assignMotor.addEventListener('click', chooseExcel);
    excelFileInput.addEventListener('change', async () => {
        const file = excelFileInput.files[0];
        const assignment = pendingAssignment;
        excelFileInput.value = '';
        pendingAssignment = null;
        if (!file || !assignment || busy || !permissions().can_assign) return;
        const currentRevision = revision;
        try {
            validateFile(file, '.xlsx');
            setBusy(true, 'Validando y guardando el Excel del motor…');
            const { motor } = await request(assignment.url, file);
            if (currentRevision !== revision || appState.currentPlan?.id !== assignment.planId) return;
            const currentMotor = appState.currentPlan.motors.find((item) => item.id === assignment.motorId);
            Object.assign(currentMotor, motor);
            resetPreview();
            viewer.select(appState.selectedMotorId);
            updatePlanCardUI();
            status.textContent = `${motor.excel.name} guardado para ${motor.name}.`;
            window.projectWorkspace?.dirty();
            errorNoPlanAlert.style.display = 'none';
        } catch (error) { if (currentRevision === revision) { showError(error.message); status.textContent = ''; } }
        finally { setBusy(false); }
    });

    btnRemovePlan.addEventListener('click', () => { if (!busy) close(); });
    document.getElementById('btnZoomIn').addEventListener('click', () => viewer.zoom(1.4));
    document.getElementById('btnZoomOut').addEventListener('click', () => viewer.zoom(1 / 1.4));
    document.getElementById('btnFitPlan').addEventListener('click', () => viewer.fit());

    async function listSaved() {
        return window.projectWorkspace?.listSaved();
    }

    async function restore() {
        let id;
        try { id = localStorage.getItem(activeKey()); } catch (_) { return; }
        if (!id || !/^\d+$/.test(id) || busy || appState.currentPlan) return;
        const currentRevision = ++revision;
        setBusy(true);
        try { await openPlan(await request(`/api/plans/${id}/`), currentRevision); }
        catch (_) { remember(null); }
        finally { setBusy(false); }
    }

    function resetPreview() {}

    async function openSelectedButton() {
        const motor = selectedMotor();
        if (motor?.show_description) await showButtonDetails();
        else if (motor?.excel) await showPreview();
        else if (motor && !busy && permissions().can_assign) chooseExcel();
    }

    async function showButtonDetails(edit = false) {
        const motor = selectedMotor(), plan = appState.currentPlan;
        if (!motor) return;
        await window.buttonDetails.open(motor, {
            kind: 'dxf', name: motor.name, edit, defaultName: `Botón ${plan.motors.indexOf(motor) + 1}`,
            openExcel: motor.excel ? showPreview : null,
            assign: permissions().can_assign ? chooseExcel : null,
            onSaved(updated) {
                if (appState.currentPlan !== plan) return;
                Object.assign(motor, updated);
                viewer.updateButtonAppearance(motor);
                updatePlanCardUI();
                window.projectWorkspace?.dirty();
            },
        });
    }

    async function showPreview() {
        const motor = selectedMotor();
        if (!motor?.excel || !permissions().can_view) return;
        await window.workbookEditor.open({ ...motor.excel, removeMarker: permissions().can_delete_cad_marker ? deleteSelectedMarker : null });
    }

    function updateActions() {
        const plan = appState.currentPlan;
        document.getElementById('btnRenameCadPlan').hidden = !plan || !permissions().can_change_plan;
        document.getElementById('btnDeleteCadPlan').hidden = !plan || !permissions().can_delete_plan;
        document.getElementById('btnAddCadMarker').hidden = !permissions().can_assign;
        document.getElementById('btnAddCadMarker').disabled = busy || !permissions().can_assign;
        btnAssignExcelCorner.hidden = assignMotor.hidden = !permissions().can_assign;
        document.getElementById('btnMoveCadMarker').hidden = !permissions().can_change_plan;
        document.getElementById('btnDeleteCadMarker').hidden = !permissions().can_delete_cad_marker;
    }

    let placementOptions = null;
    viewer.onPlace = async (svg_x, svg_y) => {
        const plan = appState.currentPlan;
        if (!plan || busy || !placing || !permissions().can_assign) return;
        const currentRevision = revision;
        const fromPiece = placementOptions?.origin === 'piece';
        setBusy(true, 'Guardando botón…');
        try {
            const position = { svg_x, svg_y, name: `Botón ${plan.motors.length + 1}` };
            const { motor } = placementOptions
                ? await window.planExcel.createButton(plan.markers_url, position, placementOptions)
                : await request(plan.markers_url, null, position);
            if (currentRevision !== revision || appState.currentPlan?.id !== plan.id) return;
            const previousView = { ...viewer.view };
            plan.motors.push(motor);
            await openPlan(plan, currentRevision);
            viewer.view = previousView;
            viewer.update();
            selectMotor(motor.id);
            setMode('explore');
            status.textContent = 'Botón creado y seleccionado. Pulse de nuevo para abrir su ficha.';
            window.projectWorkspace?.dirty();
        } catch (error) { showError(error.message); }
        finally { setBusy(false); updateActions(); }
        if (fromPiece && currentRevision === revision && appState.currentPlan?.id === plan.id) await showPreview();
    };
    async function setMode(mode) {
        if (mode === 'move' && !permissions().can_change_plan) return;
        if (mode === 'add' && window.planExcel) {
            const current = appState.currentPlan;
            const options = await window.planExcel.newButtonOptions();
            if (!options || current !== appState.currentPlan) return;
            placementOptions = options;
        } else {
            placementOptions = null;
        }
        placing = viewer.placing = mode === 'add';
        viewer.moving = mode === 'move';
        viewer.container.classList.toggle('moving-markers', viewer.moving);
        document.getElementById('btnAddCadMarker').setAttribute('aria-pressed', String(placing));
        document.getElementById('btnMoveCadMarker').setAttribute('aria-pressed', String(viewer.moving));
        window.projectWorkspace?.modeChanged(mode);
        status.textContent = placing ? 'Toque el plano donde desea añadir el botón.' : viewer.moving ? 'Arrastre un botón hasta su nueva ubicación.' : 'Puede recorrer el plano.';
    }
    document.getElementById('btnAddCadMarker').addEventListener('click', () => setMode(placing ? 'explore' : 'add'));
    document.getElementById('btnMoveCadMarker').addEventListener('click', () => setMode(viewer.moving ? 'explore' : 'move'));
    document.getElementById('btnExploreCad').addEventListener('click', () => setMode('explore'));
    viewer.onMove = async (id, position) => {
        const motor = appState.currentPlan?.motors.find((item) => item.id === id);
        if (!motor || busy || !permissions().can_change_plan) return;
        const currentRevision = revision;
        setBusy(true);
        try {
            const { motor: moved } = await request(motor.move_url, null, position);
            if (currentRevision !== revision) return;
            Object.assign(motor, moved);
            const pin = viewer.pins.get(id);
            if (pin) {
                pin.circle.setAttribute('cx', motor.svg_x);
                pin.circle.setAttribute('cy', motor.svg_y);
                const bounds = pin.group.querySelector('.motor-bounds');
                for (const [index, name] of ['x', 'y', 'width', 'height'].entries()) bounds.setAttribute(name, motor.svg_bounds[index]);
            }
            viewer.update();
            selectMotor(id);
            window.projectWorkspace?.dirty();
        } catch (error) { showError(error.message); }
        finally { setBusy(false); }
    };
    function activateProject() {
        if (appState.currentPlan && viewImportAutoCAD.classList.contains('active')) {
            window.projectWorkspace?.activate({ kind: 'dxf', plan: appState.currentPlan, viewport: viewer.container, viewer, setMode, refresh: updateMotorCard, isBusy: () => busy });
            updateMotorCard();
        }
    }
    document.getElementById('btnRenameCadPlan').addEventListener('click', async () => {
        const plan = appState.currentPlan;
        if (!plan || busy) return;
        const name = window.prompt('Nombre del plano guardado', plan.name);
        if (name === null || name === plan.name) return;
        setBusy(true);
        try {
            const data = await request(plan.update_url, null, { name });
            plan.name = data.name;
            updatePlanCardUI();
        } catch (error) { showError(error.message); }
        finally { setBusy(false); }
    });
    async function deleteSelectedMarker() {
        const plan = appState.currentPlan;
        const motor = selectedMotor();
        if (!plan || !motor || busy || !permissions().can_delete_cad_marker) return;
        if (!window.confirm(`¿Eliminar el botón «${motor.name}» del plano?`)) return;
        if (!(await window.workbookEditor.requestClose())) return;
        const currentRevision = revision;
        setBusy(true);
        try {
            await request(motor.delete_url, null, {});
            if (currentRevision !== revision || appState.currentPlan !== plan) return;
            plan.motors = plan.motors.filter((item) => item.id !== motor.id);
            viewer.pins.get(motor.id)?.group.remove();
            viewer.pins.delete(motor.id);
            [...viewer.pins.values()].forEach((pin, index) => { pin.label.textContent = String(index + 1); });
            appState.selectedMotorId = null;
            setMode('explore');
            updatePlanCardUI();
            viewer.update();
            window.projectWorkspace.dirty();
            status.textContent = 'Botón eliminado.';
        }
        catch (error) { showError(error.message); }
        finally { setBusy(false); }
    }
    document.getElementById('btnDeleteCadMarker').addEventListener('click', deleteSelectedMarker);
    window.addEventListener('workbook-saved', (event) => {
        for (const motor of appState.currentPlan?.motors || []) {
            if (motor.excel?.id === event.detail.id) {
                const book = event.detail;
                const metadataUrl = motor.excel.metadata_url;
                const sourceUrl = motor.excel.select_source_url;
                Object.assign(motor.excel, book, { preferred_sheet: motor.source_sheet || motor.sheet_name,
                    metadata_url: metadataUrl, url: book.download_url, select_source_url: sourceUrl,
                    context: sourceUrl ? 'button-source' : 'motor' });
            }
        }
        updateMotorCard();
    });

    function permissionsChanged() {
        btnAssignExcelCorner.hidden = !permissions().can_assign;
        assignMotor.hidden = !permissions().can_assign;
        btnImportAutoCAD.hidden = !permissions().can_import;
        updateActions();
        if (!permissions().can_view && appState.currentPlan) {
            close();
            if (appState.user) switchView(viewMainMenu);
        }
        setBusy(busy);
        updateMotorCard();
    }

    document.getElementById('btnPreviewExcel').addEventListener('click', () => showPreview());
    window.planWorkflow = { close, listSaved, restore, permissionsChanged, activate: activateProject,
        get currentPlan() { return appState.currentPlan; },
        select: selectMotor,
        deselect: deselectMotor,
        editButtonDetails(mode = 'description') { return showButtonDetails(mode); },
        removeDeleted(id) {
            let remembered;
            try { remembered = localStorage.getItem(activeKey()); } catch (_) {}
            if (appState.currentPlan?.id === id) {
                const documentId = window.workbookEditor?.metadata?.id;
                const current = appState.currentPlan;
                if (documentId && (current.workbook?.id === documentId || current.listing?.id === documentId || current.motors.some(motor => motor.excel?.id === documentId))) window.workbookEditor.close(true);
                close();
            } else if (remembered === String(id)) remember(null);
        },
        async open(plan) {
            switchView(viewImportAutoCAD);
            return openPlan(plan, ++revision);
        },
    };
    window.updatePlanCardUI = updatePlanCardUI;
})();
