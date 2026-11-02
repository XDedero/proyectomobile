/* A drawing owns one workbook. Buttons and listings select its worksheets. */
(() => {
    const byId = (id) => document.getElementById(id);
    let epoch = 0, bookTarget = null, assignment = null, listingTarget = null, resumeMarker = null;
    const active = () => window.projectWorkspace?.active;
    function modal(id, title, body) {
        const element = document.createElement('div');
        element.className = 'modal fade';
        element.id = id;
        element.tabIndex = -1;
        element.setAttribute('aria-labelledby', id + 'Title');
        element.innerHTML = `<div class="modal-dialog modal-dialog-centered"><div class="modal-content">
          <div class="modal-header"><h2 class="modal-title fs-5" id="${id}Title">${title}</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
          <div class="modal-body">${body}<p class="excel-manager-status small mt-3 mb-0" role="status" aria-live="polite"></p></div></div></div>`;
        document.body.append(element);
        const result = { element, controller: new bootstrap.Modal(element), ready: Promise.resolve() };
        element.addEventListener('show.bs.modal', () => { result.ready = new Promise(resolve => element.addEventListener('shown.bs.modal', resolve, { once: true })); });
        return result;
    }
    const bookModal = modal('excelBookManager', 'Excel del plano', `
        <p id="planWorkbookName" class="small text-secondary text-break"></p>
        <div class="excel-choice-actions">
          <button type="button" id="btnPlanExcelTemplate" class="btn btn-outline-success"><i class="bi bi-file-earmark-plus" aria-hidden="true"></i> Formato vacío</button>
          <button type="button" id="btnPlanExcelImport" class="btn btn-success"><i class="bi bi-folder2-open" aria-hidden="true"></i> Importar Excel</button>
        </div>
        <input type="file" id="planWorkbookInput" accept=".xlsx" hidden>
        <p class="small text-muted mt-2">Se conservan los datos y el formato. La primera hoja será el listado.</p>
        <a id="downloadPlanWorkbook" class="btn btn-outline-secondary w-100" download>Exportar Excel completo</a>`);
    const sheetModal = modal('excelSheetAssignment', 'Asignar hoja al botón', `
        <p id="sheetAssignmentName" class="small fw-semibold text-break"></p>
        <label class="form-label small" for="planMotorSheet">Hoja del Excel del plano</label>
        <select id="planMotorSheet" class="form-select mb-2"></select>
        <button type="button" id="btnAssignWorkbookSheet" class="btn btn-success w-100 mb-3">Vincular hoja</button>
        <div class="excel-choice-actions">
          <button type="button" id="btnAssignBlankSheet" class="btn btn-outline-success">Ficha vacía</button>
          <button type="button" id="btnAssignSavedMotorSheet" class="btn btn-outline-success">Motor guardado</button>
          <button type="button" id="btnAssignmentImportWorkbook" class="btn btn-outline-secondary">Importar Excel</button>
        </div>
        <p id="sharedMotorSheetHint" class="small text-muted mt-2"></p>
        <div id="sheetMarkerRename" class="border-top pt-3 mt-3" hidden>
          <label class="form-label small" for="sheetMarkerLabel">Nombre del botón</label>
          <div class="input-group"><input id="sheetMarkerLabel" class="form-control" maxlength="120"><button type="button" id="btnSheetMarkerRename" class="btn btn-outline-secondary">Cambiar</button></div>
        </div>`);
    const listingsModal = modal('excelListingManager', 'Listados de motores', `
        <div id="planListingsRows" class="excel-listing-rows mb-3"></div>
        <div class="excel-choice-actions mb-3">
          <button type="button" id="btnAddPlanListing" class="btn btn-success">Agregar listado</button>
          <button type="button" id="btnImportPlanListing" class="btn btn-outline-success">Importar listado</button>
        </div>
        <input type="file" id="planListingFileInput" accept=".xlsx" hidden>
        <label class="form-label small" for="planAdditionalListingSheet">Otra hoja del libro como listado</label>
        <div class="input-group mb-3"><select id="planAdditionalListingSheet" class="form-select"></select><button type="button" id="btnAddExistingListing" class="btn btn-outline-success">Agregar hoja</button></div>
        <label class="form-label small" for="planPrimaryListingSheet">Hoja del listado original</label>
        <div class="input-group"><select id="planPrimaryListingSheet" class="form-select"></select><button type="button" id="btnChangePrimaryListing" class="btn btn-outline-secondary">Cambiar</button></div>`);

    function message(dialog, text, error = false) {
        const node = dialog.element.querySelector('.excel-manager-status');
        node.textContent = text;
        node.classList.toggle('text-danger', error);
    }
    function busy(dialog, value) {
        dialog.element.querySelectorAll('button,select,input').forEach(node => { node.disabled = value; });
    }
    async function hide(dialog) {
        await dialog.ready;
        if (!dialog.element.classList.contains('show')) return;
        const hidden = new Promise(resolve => dialog.element.addEventListener('hidden.bs.modal', resolve, { once: true }));
        dialog.controller.hide();
        await hidden;
    }
    async function request(url, data, file) {
        const options = { credentials: 'same-origin', cache: 'no-store' };
        if (file) {
            const body = new FormData(); body.append('file', file);
            Object.assign(options, { method: 'POST', headers: { 'X-CSRFToken': csrfToken() }, body });
        } else if (data !== undefined) {
            Object.assign(options, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() }, body: JSON.stringify(data) });
        }
        const response = await fetch(url, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth?.expire();
            throw new Error(result.error || 'No se pudo actualizar el Excel del plano.');
        }
        return result;
    }
    function target() {
        const current = active();
        return current ? { kind: current.kind, plan: current.plan, user: appState.user.id, epoch } : null;
    }
    function valid(context) {
        return context && context.epoch === epoch && context.user === appState.user?.id && active()?.kind === context.kind && active()?.plan.id === context.plan.id;
    }
    function apply(context, data) {
        if (!valid(context)) return false;
        const keys = ['workbook', 'workbook_mode', 'sheet_options', 'listings', 'listing', 'binding_counts'];
        for (const key of keys) if (key in data) context.plan[key] = data[key];
        Object.assign(active().plan, Object.fromEntries(keys.filter(key => key in data).map(key => [key, data[key]])));
        window.projectWorkspace.update();
        return true;
    }
    async function load(context) {
        const data = await request(context.plan.workbook_url);
        apply(context, data);
        return data;
    }
    async function reopen(context, plan, markerId) {
        if (!valid(context)) return null;
        if (context.kind === 'pdf') await window.pdfWorkflow.open(plan);
        else await window.planWorkflow.open(plan);
        context.plan = active().plan;
        if (markerId) {
            if (context.kind === 'pdf') await window.pdfWorkflow.select(markerId);
            else window.planWorkflow.select(markerId);
        }
        return (context.kind === 'pdf' ? context.plan.markers : context.plan.motors).find(item => item.id === markerId);
    }

    async function manage(resume = null) {
        if (!(await window.workbookEditor.requestClose())) return;
        bookTarget = target();
        if (!bookTarget) return;
        resumeMarker = resume;
        message(bookModal, ''); bookModal.controller.show(); busy(bookModal, true);
        try {
            const data = await load(bookTarget);
            byId('planWorkbookName').textContent = data.workbook.name;
            byId('downloadPlanWorkbook').href = data.workbook.download_url;
            byId('downloadPlanWorkbook').download = data.workbook.name;
        } catch (error) { message(bookModal, error.message, true); }
        finally { busy(bookModal, false); }
    }
    async function replaceWorkbook(data, file) {
        const context = bookTarget;
        if (!valid(context)) return;
        busy(bookModal, true); message(bookModal, 'Preparando Excel…');
        try {
            const result = await request(context.plan.workbook_url, data, file);
            if (!valid(context)) return;
            const markerId = resumeMarker;
            await hide(bookModal);
            const marker = await reopen(context, result.plan, markerId);
            window.projectWorkspace.dirty();
            if (marker) await assign(context.kind, context.plan, marker);
        } catch (error) { message(bookModal, error.message, true); }
        finally { busy(bookModal, false); }
    }

    async function assign(kind, plan, marker) {
        if (!(await window.workbookEditor.requestClose())) return;
        assignment = { ...target(), kind, plan, marker };
        if (!valid(assignment)) return;
        message(sheetModal, ''); sheetModal.controller.show(); busy(sheetModal, true);
        byId('sheetAssignmentName').textContent = marker.label || marker.name || `Botón ${marker.id}`;
        byId('sheetMarkerRename').hidden = kind !== 'pdf';
        byId('sheetMarkerLabel').value = marker.label || '';
        try {
            const data = await load(assignment);
            const select = byId('planMotorSheet');
            select.replaceChildren(new Option('Elija una hoja de motor', ''));
            for (const sheet of data.sheet_options.filter(sheet => !sheet.is_listing)) {
                const count = data.binding_counts?.[sheet.name] || 0;
                select.append(new Option(sheet.name + (count > 1 ? ` · ${count} botones` : ''), sheet.name));
            }
            select.value = marker.sheet_name || '';
            byId('sharedMotorSheetHint').textContent = 'Los botones que usan la misma hoja comparten sus datos.';
        } catch (error) { message(sheetModal, error.message, true); }
        finally { busy(sheetModal, false); }
    }
    async function linkSheet(values) {
        const context = assignment;
        if (!valid(context)) return;
        busy(sheetModal, true); message(sheetModal, 'Vinculando hoja…');
        try {
            await request(context.marker.assign_url, values);
            const plan = await request(context.plan.detail_url);
            if (!valid(context)) return;
            await hide(sheetModal);
            const marker = await reopen(context, plan, context.marker.id);
            window.projectWorkspace.dirty();
            const book = context.kind === 'pdf' ? marker?.workbook : marker?.excel;
            if (book) await window.workbookEditor.open({ ...book, removeMarker: window.projectWorkspace.selected?.remove });
        } catch (error) { message(sheetModal, error.message, true); }
        finally { busy(sheetModal, false); }
    }

    function renderListings(context, data) {
        const container = byId('planListingsRows');
        container.replaceChildren();
        if (!data.listings.length) container.textContent = 'Agregue un listado para comenzar.';
        for (const listing of data.listings) {
            const row = document.createElement('div'); row.className = 'excel-listing-row';
            const open = document.createElement('button'); open.type = 'button'; open.className = 'btn btn-light text-start flex-grow-1';
            open.textContent = listing.name + (listing.is_primary ? ' · Original' : listing.is_protected ? ' · Protegido' : '');
            open.onclick = async () => { await hide(listingsModal); await openListings(listing.name); };
            row.append(open);
            if (listing.can_delete) {
                const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'btn btn-outline-danger';
                remove.setAttribute('aria-label', `Eliminar ${listing.name}`); remove.title = 'Eliminar listado'; remove.dataset.listingId = listing.id;
                remove.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i>';
                remove.onclick = () => { if (window.confirm(`¿Eliminar el listado «${listing.name}»?`)) changeListing({ action: 'delete', listing_id: listing.id }); };
                row.append(remove);
            }
            container.append(row);
        }
        const primary = data.listings.find(row => row.is_primary);
        const select = byId('planPrimaryListingSheet');
        select.replaceChildren(new Option('Elija una hoja', ''));
        for (const sheet of data.sheet_options) {
            if (sheet.name === primary?.name || (!sheet.is_listing && !data.binding_counts?.[sheet.name])) select.append(new Option(sheet.name, sheet.name));
        }
        select.value = primary?.name || '';
        const additional = byId('planAdditionalListingSheet');
        additional.replaceChildren(new Option('Elija una hoja libre', ''));
        for (const sheet of data.sheet_options.filter(sheet => !sheet.is_listing && !data.binding_counts?.[sheet.name])) additional.append(new Option(sheet.name, sheet.name));
    }
    async function listings() {
        if (!(await window.workbookEditor.requestClose())) return;
        listingTarget = target(); if (!listingTarget) return;
        message(listingsModal, ''); listingsModal.controller.show(); busy(listingsModal, true);
        try { renderListings(listingTarget, await load(listingTarget)); }
        catch (error) { message(listingsModal, error.message, true); }
        finally { busy(listingsModal, false); }
    }
    async function changeListing(values, file) {
        const context = listingTarget; if (!valid(context)) return;
        busy(listingsModal, true); message(listingsModal, 'Actualizando listados…');
        try {
            const data = await request(context.plan.assign_listing_url, values, file);
            if (!apply(context, data)) return;
            window.projectWorkspace.dirty(); renderListings(context, data);
            message(listingsModal, 'Listados actualizados.');
            if (values?.action === 'add' || file) {
                await hide(listingsModal);
                await openListings(data.listings.at(-1)?.name);
            }
        } catch (error) { message(listingsModal, error.message, true); }
        finally { busy(listingsModal, false); }
    }
    async function openListings(name) {
        const context = target(); if (!context) return;
        try {
            const data = await load(context);
            if (!data.listing) { await listings(); return; }
            await window.workbookEditor.open({ ...data.listing, context: 'listing', preferred_sheet: name || data.listing.preferred_sheet });
        } catch (error) { window.projectWorkspace.reportError(error.message); }
    }

    byId('btnPlanExcelTemplate').onclick = () => replaceWorkbook({ action: 'template' });
    byId('btnPlanExcelImport').onclick = () => byId('planWorkbookInput').click();
    byId('planWorkbookInput').onchange = event => { const file = event.target.files[0]; event.target.value = ''; if (file) replaceWorkbook(undefined, file); };
    byId('btnAssignWorkbookSheet').onclick = () => { const sheet = byId('planMotorSheet').value; if (sheet) linkSheet({ sheet }); else message(sheetModal, 'Elija una hoja.', true); };
    byId('btnAssignBlankSheet').onclick = () => linkSheet({ blank: true });
    byId('btnAssignSavedMotorSheet').onclick = async () => { await hide(sheetModal); window.motorLibrary.chooseForTarget(); };
    byId('btnAssignmentImportWorkbook').onclick = async () => { const id = assignment?.marker.id; await hide(sheetModal); await manage(id); };
    byId('btnSheetMarkerRename').onclick = async () => {
        if (!valid(assignment)) return;
        try {
            const result = await request(assignment.marker.update_url, { label: byId('sheetMarkerLabel').value });
            Object.assign(assignment.marker, result.marker);
            await window.pdfWorkflow.select(assignment.marker.id);
            window.projectWorkspace.dirty(); message(sheetModal, 'Nombre actualizado.');
        } catch (error) { message(sheetModal, error.message, true); }
    };
    byId('btnAddPlanListing').onclick = () => changeListing({ action: 'add' });
    byId('btnImportPlanListing').onclick = () => byId('planListingFileInput').click();
    byId('planListingFileInput').onchange = event => { const file = event.target.files[0]; event.target.value = ''; if (file) changeListing(undefined, file); };
    byId('btnChangePrimaryListing').onclick = () => { const sheet = byId('planPrimaryListingSheet').value; if (sheet) changeListing({ action: 'set_primary', sheet }); };
    byId('btnAddExistingListing').onclick = () => { const sheet = byId('planAdditionalListingSheet').value; if (sheet) changeListing({ action: 'add_sheet', sheet }); };
    for (const prefix of ['Cad', 'Pdf']) {
        const button = document.createElement('button'); button.type = 'button'; button.id = `btn${prefix}Workbook`;
        button.className = 'btn btn-outline-success btn-sm'; button.innerHTML = '<i class="bi bi-file-earmark-excel" aria-hidden="true"></i> Excel del plano';
        byId(`btn${prefix}ExportProject`).before(button);
        button.onclick = () => manage();
        byId(`btn${prefix}AssignListing`).textContent = 'Gestionar listados';
        byId(`btn${prefix}OpenListing`).textContent = 'Ver listados';
    }
    const fullButton = document.createElement('button'); fullButton.type = 'button'; fullButton.textContent = 'Excel del plano'; fullButton.id = 'fullscreenManageWorkbook';
    fullButton.onclick = () => { byId('btnPlanFullscreenMenu').click(); manage(); };
    byId('fullscreenPlanMenu').prepend(fullButton);
    window.planExcel = { manage, assign, listings, openListings, reset() {
        epoch++; bookTarget = assignment = listingTarget = resumeMarker = null;
        for (const dialog of [bookModal, sheetModal, listingsModal]) dialog.controller.hide();
    } };
})();
