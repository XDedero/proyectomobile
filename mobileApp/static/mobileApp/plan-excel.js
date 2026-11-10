/* A drawing owns one workbook. Buttons and listings select its worksheets. */
(() => {
    const byId = (id) => document.getElementById(id);
    let epoch = 0, bookTarget = null, assignment = null, listingTarget = null;
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
          <button type="button" id="btnPlanExcelImport" class="btn btn-success"><i class="bi bi-folder2-open" aria-hidden="true"></i> Importar Excel completo</button>
        </div>
        <input type="file" id="planWorkbookInput" accept=".xlsx" hidden>
        <p class="small text-muted mt-2">Puedes elegir una hoja como listado desde «Añadir listado».</p>
        <a id="downloadPlanWorkbook" class="btn btn-outline-secondary w-100" download>Exportar Excel completo</a>`);
    const sheetModal = modal('excelSheetAssignment', 'Configurar botón', `
        <p id="sheetAssignmentName" class="small fw-semibold text-break"></p>
        <div class="d-flex flex-wrap gap-2 mb-3">
          <button type="button" id="btnButtonNameOptions" class="btn btn-outline-secondary btn-sm">Cambiar nombre</button>
          <button type="button" id="btnButtonAppearanceOptions" class="btn btn-outline-secondary btn-sm">Apariencia</button>
          <button type="button" id="btnCopyPlanButton" class="btn btn-outline-primary btn-sm">Copiar botón</button>
        </div>
        <div class="border rounded p-2 mb-3">
          <div class="form-check form-switch mb-2">
            <input type="checkbox" class="form-check-input" id="sheetButtonDescriptionEnabled">
            <label class="form-check-label" for="sheetButtonDescriptionEnabled">Mostrar descripción (opcional)</label>
          </div>
          <button type="button" id="btnButtonDetailsOptions" class="btn btn-outline-secondary btn-sm">Editar descripciones</button>
        </div>
        <label class="form-label small" for="sheetButtonCategory">Categoría del botón</label>
        <div class="input-group mb-3">
          <select id="sheetButtonCategory" class="form-select"></select>
          <button type="button" id="btnSaveButtonCategory" class="btn btn-outline-success">Guardar categoría</button>
        </div>
        <label class="form-label small" for="planMotorSheet">Hoja del Excel del plano</label>
        <select id="planMotorSheet" class="form-select mb-2"></select>
        <button type="button" id="btnAssignWorkbookSheet" class="btn btn-success w-100 mb-3">Vincular hoja</button>
        <div class="excel-choice-actions">
          <button type="button" id="btnAssignBlankSheet" class="btn btn-outline-success">Ficha vacía</button>
          <button type="button" id="btnAssignSavedMotorSheet" class="btn btn-outline-success">Pieza guardada</button>
          <button type="button" id="btnAssignmentImportWorkbook" class="btn btn-outline-secondary">Importar Excel o CSV</button>
        </div>
        <input type="file" id="buttonWorkbookInput" accept=".xlsx,.csv" hidden>
        <div id="buttonWorkbookImport" class="border rounded-2 p-2 mt-3" hidden>
          <p id="buttonWorkbookFilename" class="small fw-semibold text-break mb-2"></p>
          <label class="form-label small" for="buttonWorkbookSheet">Hoja del archivo importado</label>
          <select id="buttonWorkbookSheet" class="form-select mb-2"></select>
          <div class="table-responsive mb-2" style="max-height: 10rem">
            <table class="table table-sm table-bordered small mb-0" aria-label="Vista previa de la hoja elegida">
              <tbody id="buttonWorkbookPreview"></tbody>
            </table>
          </div>
          <button type="button" id="btnConfirmButtonWorkbook" class="btn btn-success">Incorporar y vincular hoja</button>
        </div>
        <p id="sharedMotorSheetHint" class="small text-muted mt-2"></p>
        `);
    const listingsModal = modal('excelListingManager', 'Listados de motores', `
        <div id="planListingsRows" class="excel-listing-rows mb-3"></div>
        <button type="button" id="btnAddPlanListing" class="btn btn-success mb-3">Añadir listado</button>
        <div id="planListingOptions" class="mb-3" hidden>
          <p class="small fw-semibold mb-2">Origen del listado</p>
          <div class="excel-choice-actions">
            <button type="button" id="btnDefaultPlanListing" class="btn btn-outline-success">Formato predeterminado</button>
            <button type="button" id="btnImportPlanListing" class="btn btn-success">Importar Excel o CSV</button>
          </div>
          <input type="file" id="planListingFileInput" accept=".xlsx,.csv" hidden>
          <div id="planListingImport" class="mt-3" hidden>
            <label class="form-label small" for="planListingSourceSheet">Hoja que se añadirá como listado</label>
            <select id="planListingSourceSheet" class="form-select mb-2"></select>
            <button type="button" id="btnConfirmPlanListing" class="btn btn-success">Añadir hoja</button>
          </div>
        </div>
        <label class="form-label small" for="planAdditionalListingSheet">Otra hoja del libro como listado</label>
        <div class="input-group mb-3"><select id="planAdditionalListingSheet" class="form-select"></select><button type="button" id="btnAddExistingListing" class="btn btn-outline-success">Agregar hoja</button></div>
        <label class="form-label small" for="planPrimaryListingSheet">Hoja del listado original</label>
        <div class="input-group"><select id="planPrimaryListingSheet" class="form-select"></select><button type="button" id="btnChangePrimaryListing" class="btn btn-outline-secondary">Cambiar</button></div>`);
    const newButtonModal = modal('newPlanButton', 'Añadir botón', `
        <label class="form-label" for="newButtonCategory">Categoría</label>
        <select id="newButtonCategory" class="form-select mb-3" required></select>
        <label class="form-label" for="newButtonName">Nombre <small class="text-muted">(opcional)</small></label>
        <input id="newButtonName" class="form-control mb-3" maxlength="120" placeholder="Se usará el número del botón">
        <label class="form-label" for="newButtonColor">Color del botón</label>
        <input type="color" id="newButtonColor" class="form-control form-control-color mb-3" value="#2563eb">
        <div class="form-check form-switch mb-2">
          <input type="checkbox" class="form-check-input" id="newButtonDescriptionEnabled">
          <label class="form-check-label" for="newButtonDescriptionEnabled">Añadir descripción al botón</label>
        </div>
        <div id="newButtonDescriptionOptions" class="border rounded p-2 mb-3" hidden>
          <div id="newButtonDescriptions"></div>
          <button type="button" id="btnNewButtonAddDescription" class="btn btn-outline-secondary btn-sm">Añadir campo</button>
        </div>
        <label class="form-label" for="newButtonOrigin">Origen de la ficha Excel</label>
        <select id="newButtonOrigin" class="form-select mb-3">
          <option value="blank">Formato predeterminado vacío</option>
          <option value="upload">Importar Excel o CSV</option>
          <option value="sheet">Hoja del Excel del plano</option>
          <option value="piece">Pieza guardada</option>
          <option value="category">Excel permanente de la categoría</option>
        </select>
        <div id="newButtonUploadOptions" class="mb-3" hidden>
          <label for="newButtonFile" class="form-label">Archivo Excel o CSV</label>
          <input type="file" id="newButtonFile" class="form-control" accept=".xlsx,.csv">
        </div>
        <div id="newButtonSheetOptions" class="mb-3" hidden>
          <label for="newButtonSheet" class="form-label">Hoja del plano</label>
          <select id="newButtonSheet" class="form-select"></select>
        </div>
        <div id="newButtonPieceOptions" class="mb-3" hidden>
          <label for="newButtonPiece" class="form-label">Pieza guardada</label>
          <select id="newButtonPiece" class="form-select"></select>
        </div>
        <div id="newButtonCategoryTemplates" class="mb-3" hidden>
          <label for="newButtonTemplate" class="form-label">Excel permanente</label>
          <select id="newButtonTemplate" class="form-select"></select>
        </div>
        <p class="small text-muted">Configura el botón aquí y después toca su posición en el plano.</p>
        <button type="button" id="btnConfirmNewButton" class="btn btn-success w-100">Colocar botón en el plano</button>`);
    newButtonModal.element.querySelector('.modal-dialog').classList.add('modal-dialog-scrollable');
    let newButton = null;

    function message(dialog, text, error = false) {
        const node = dialog.element.querySelector('.excel-manager-status');
        node.textContent = text;
        node.classList.toggle('text-danger', error);
    }
    function busy(dialog, value) {
        dialog.element.querySelectorAll('button,select,input,textarea').forEach(node => { node.disabled = value; });
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
            const body = new FormData();
            body.append('file', data ? file : await window.workbookFields.normalizeUpload(file));
            if (data?.origin === 'upload') body.append('placement', JSON.stringify(data));
            else for (const [key, value] of Object.entries(data || {})) body.append(key, value);
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
        const keys = ['workbook', 'workbook_mode', 'sheet_options', 'button_categories', 'listings', 'listing', 'binding_counts'];
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

    async function newButtonOptions() {
        if (newButton || !(await window.workbookEditor.requestClose())) return null;
        const context = target();
        if (!context) return null;
        const selection = new Promise(resolve => { newButton = { context, resolve, result: null }; });
        byId('newButtonName').value = '';
        byId('newButtonName').maxLength = context.kind === 'dxf' ? 255 : 120;
        byId('newButtonColor').value = '#2563eb';
        byId('newButtonDescriptionEnabled').checked = false;
        byId('newButtonDescriptionOptions').hidden = true;
        byId('newButtonDescriptions').replaceChildren();
        byId('newButtonFile').value = '';
        byId('newButtonOrigin').value = 'blank';
        for (const id of ['UploadOptions', 'SheetOptions', 'PieceOptions', 'CategoryTemplates']) byId('newButton' + id).hidden = true;
        byId('newButtonCategory').replaceChildren(new Option('Elija una categoría', ''));
        message(newButtonModal, '');
        newButtonModal.controller.show();
        busy(newButtonModal, true);
        try {
            const data = await load(context);
            if (newButton?.context === context) {
                newButton.categories = data.button_categories;
                for (const item of data.button_categories) byId('newButtonCategory').append(new Option(item.name, item.id));
                byId('newButtonSheet').replaceChildren(new Option('Elija una hoja', ''), ...data.sheet_options.filter(row => !row.is_listing).map(row => new Option(row.name, row.name)));
                if (!data.button_categories.length) message(newButtonModal, 'Cree una categoría para añadir botones.', true);
            }
        } catch (error) {
            if (newButton?.context === context) message(newButtonModal, error.message, true);
        } finally {
            if (newButton?.context === context) busy(newButtonModal, false);
        }
        return selection;
    }

    document.addEventListener('category:created', event => {
        if (newButton) newButton.categories.push(event.detail);
    });

    async function chooseNewButtonSource() {
        const draft = newButton;
        if (!draft || !valid(draft.context)) return;
        const origin = byId('newButtonOrigin').value;
        const file = origin === 'upload' ? byId('newButtonFile').files[0] : undefined;
        if (!byId('newButtonCategory').value) return message(newButtonModal, 'Seleccione una categoría.', true);
        if (origin === 'upload' && !file) return message(newButtonModal, 'Seleccione un archivo Excel o CSV.', true);
        if (origin === 'piece' && !byId('newButtonPiece').value) return message(newButtonModal, 'Seleccione una pieza.', true);
        if (origin === 'category' && !byId('newButtonTemplate').value) return message(newButtonModal, 'Esta categoría no tiene un Excel permanente seleccionado.', true);
        if (origin === 'sheet' && !byId('newButtonSheet').value) return message(newButtonModal, 'Seleccione una hoja.', true);
        const descriptions = [...byId('newButtonDescriptions').children].map(row => ({key: row.querySelector('input').value.trim(), value: row.querySelector('textarea').value}));
        if (descriptions.some(row => !row.key) || new Set(descriptions.map(row => row.key.toLocaleLowerCase())).size !== descriptions.length) return message(newButtonModal, 'Cada descripción necesita una clave distinta.', true);
        busy(newButtonModal, true);
        try {
            const upload = file ? await window.workbookFields.normalizeUpload(file) : undefined;
            if (newButton !== draft || !valid(draft.context)) return;
            draft.result = { origin, saved_piece_id:origin === 'piece' ? Number(byId('newButtonPiece').value) : undefined,
                template_id: origin === 'category' ? Number(byId('newButtonTemplate').value) : undefined,
                sheet: origin === 'sheet' ? byId('newButtonSheet').value : undefined,
                category_id: byId('newButtonCategory').value, show_description: byId('newButtonDescriptionEnabled').checked,
                button_color: byId('newButtonColor').value, descriptions: descriptions.length ? descriptions : undefined,
                name: byId('newButtonName').value.trim() || undefined, label: byId('newButtonName').value.trim() || undefined, file: upload };
            await hide(newButtonModal);
        } catch (error) {
            if (newButton === draft) message(newButtonModal, error.message, true);
        } finally {
            if (newButton === draft) busy(newButtonModal, false);
        }
    }

    function sourceSelected(id, sheet, result) {
        const current = active();
        const items = current?.kind === 'pdf' ? current.plan.markers : current?.plan.motors;
        for (const item of items || []) {
            const workbook = current.kind === 'pdf' ? item.workbook : item.excel;
            if (workbook?.id !== id) continue;
            workbook.preferred_sheet = item.source_sheet = sheet;
            const updated=result?.marker || result?.motor;
            if (updated?.id === item.id) Object.assign(item,updated);
        }
        window.projectWorkspace?.dirty();
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

    async function manage() {
        if (!(await window.workbookEditor.requestClose())) return;
        bookTarget = target();
        if (!bookTarget) return;
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
            await hide(bookModal);
            await reopen(context, result.plan);
            window.projectWorkspace.dirty();
        } catch (error) { message(bookModal, error.message, true); }
        finally { busy(bookModal, false); }
    }

    async function assign(kind, plan, marker) {
        if (!(await window.workbookEditor.requestClose())) return;
        assignment = { ...target(), kind, plan, marker };
        if (!valid(assignment)) return;
        byId('buttonWorkbookImport').hidden = true;
        message(sheetModal, ''); sheetModal.controller.show(); busy(sheetModal, true);
        byId('sheetAssignmentName').textContent = marker.label || marker.name || `Botón ${marker.id}`;
        byId('sheetButtonDescriptionEnabled').checked = marker.show_description === true;

        try {
            const data = await load(assignment);
            const select = byId('planMotorSheet');
            select.replaceChildren(new Option('Elija una hoja de motor', ''));
            for (const sheet of data.sheet_options.filter(sheet => !sheet.is_listing)) {
                const count = data.binding_counts?.[sheet.name] || 0;
                select.append(new Option(sheet.name + (count > 1 ? ` · ${count} botones` : ''), sheet.name));
            }
            select.value = marker.sheet_name || '';
            const category = byId('sheetButtonCategory');
            category.replaceChildren(new Option('Sin categoría', ''));
            for (const item of data.button_categories) category.append(new Option(item.name, item.id));
            category.value = marker.category_id || '';
            byId('sharedMotorSheetHint').textContent = 'Los botones que usan la misma hoja comparten sus datos.';
        } catch (error) { message(sheetModal, error.message, true); }
        finally { busy(sheetModal, false); }
    }
    async function linkSheet(values, file) {
        const context = assignment;
        if (!valid(context)) return;
        busy(sheetModal, true); message(sheetModal, 'Vinculando hoja…');
        try {
            await request(context.marker.assign_url, values, file);
            const plan = await request(context.plan.detail_url);
            if (!valid(context)) return;
            await hide(sheetModal);
            const marker = await reopen(context, plan, context.marker.id);
            window.projectWorkspace.dirty();
            const book = context.kind === 'pdf' ? marker?.workbook : marker?.excel;
            if (book && values.action !== 'set_category') await window.workbookEditor.open({ ...book, removeMarker: window.projectWorkspace.selected?.remove });
        } catch (error) { message(sheetModal, error.message, true); }
        finally { busy(sheetModal, false); }
    }

    async function previewButtonFile(file, sourceSheet = '') {
        const context = assignment;
        if (!valid(context)) return;
        busy(sheetModal, true);
        message(sheetModal, 'Leyendo hojas del archivo…');
        byId('buttonWorkbookImport').hidden = true;
        try {
            const upload = sourceSheet ? file : await window.workbookFields.normalizeUpload(file);
            if (context !== assignment || !valid(context)) return;
            const data = await request(context.marker.assign_url, { action: 'inspect', source_sheet: sourceSheet }, upload);
            if (context !== assignment || !valid(context)) return;
            context.upload = upload;
            const select = byId('buttonWorkbookSheet');
            select.replaceChildren(...data.sheets.map(name => new Option(name, name)));
            select.value = data.source_sheet;
            byId('buttonWorkbookFilename').textContent = file.name;
            const preview = byId('buttonWorkbookPreview');
            preview.replaceChildren(...data.rows.map(row => {
                const line = document.createElement('tr');
                for (const value of row) line.insertCell().textContent = value;
                return line;
            }));
            byId('buttonWorkbookImport').hidden = false;
            message(sheetModal, 'Solo se incorporará la hoja que seleccione.');
        } catch (error) {
            if (context === assignment) message(sheetModal, error.message, true);
        } finally {
            if (context === assignment) busy(sheetModal, false);
        }
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
                remove.onclick = async () => {
                    if (await window.confirmAppAction('Eliminar listado', `¿Eliminar el listado «${listing.name}»?`, 'Eliminar listado', { danger: true })) {
                        if (context === listingTarget) await changeListing({ action: 'delete', listing_id: listing.id });
                    }
                };
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
        const context = listingTarget;
        byId('planListingImport').hidden = true;
        byId('planListingOptions').hidden = Boolean(context.plan.listings?.length);
        byId('btnAddPlanListing').hidden = !byId('planListingOptions').hidden;
        message(listingsModal, ''); listingsModal.controller.show(); busy(listingsModal, true);
        try { renderListings(context, await load(context)); }
        catch (error) { message(listingsModal, error.message, true); }
        finally { busy(listingsModal, false); }
    }
    async function previewListing(file) {
        const context = listingTarget;
        if (!valid(context)) return;
        busy(listingsModal, true);
        byId('planListingImport').hidden = true;
        try {
            const result = await request(context.plan.assign_listing_url, { action: 'inspect' }, file);
            if (context !== listingTarget || !valid(context)) return;
            context.upload = file;
            byId('planListingSourceSheet').replaceChildren(...result.sheets.map(name => new Option(name, name)));
            byId('planListingImport').hidden = false;
            message(listingsModal, '');
        } catch (error) { message(listingsModal, error.message, true); }
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
    async function openWorkbook() {
        const context = target();
        if (!context) return;
        try {
            const data = await load(context);
            if (valid(context)) await window.workbookEditor.open({ ...data.workbook, context: 'plan' });
        } catch (error) { window.projectWorkspace.reportError(error.message); }
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
    byId('btnAssignSavedMotorSheet').onclick = async () => { await hide(sheetModal); window.pieceLibrary.chooseForTarget(); };
    byId('btnSaveButtonCategory').onclick = () => linkSheet({ action: 'set_category', category_id: byId('sheetButtonCategory').value });
    newButtonModal.element.addEventListener('hidden.bs.modal', () => {
        const draft = newButton;
        newButton = null;
        draft?.resolve(valid(draft.context) ? draft.result : null);
    });
    function updateNewButtonTemplates() {
        const category = newButton?.categories?.find(item => item.id === byId('newButtonCategory').value);
        byId('newButtonTemplate').replaceChildren();
        for (const item of category?.templates || []) byId('newButtonTemplate').append(new Option(`${item.name} · ${item.sheet}`, item.id));
    }
    byId('newButtonCategory').onchange = updateNewButtonTemplates;
    byId('newButtonDescriptionEnabled').onchange = event => {
        byId('newButtonDescriptionOptions').hidden = !event.target.checked;
    };
    byId('btnNewButtonAddDescription').onclick = () => {
        const rows = byId('newButtonDescriptions');
        if (rows.children.length >= 64) return;
        const row = document.createElement('div'); row.className = 'button-details-edit-row mb-2';
        row.innerHTML = '<input class="form-control form-control-sm" maxlength="100" placeholder="Clave" aria-label="Clave de descripción"><textarea class="form-control form-control-sm" maxlength="2000" rows="1" placeholder="Valor" aria-label="Valor de descripción"></textarea><button type="button" class="btn btn-outline-danger btn-sm" aria-label="Quitar descripción">Quitar</button>';
        row.querySelector('button').onclick = () => row.remove(); rows.append(row); row.querySelector('input').focus();
    };
    byId('newButtonOrigin').onchange = async () => {
        const origin = byId('newButtonOrigin').value;
        for (const [id, value] of [['UploadOptions','upload'], ['SheetOptions','sheet'], ['PieceOptions','piece'], ['CategoryTemplates','category']]) byId('newButton' + id).hidden = origin !== value;
        updateNewButtonTemplates();
        if (origin !== 'piece') return;
        const draft=newButton;if(!draft) return;
        busy(newButtonModal,true);
        try {
            const data=await request('/api/piece-library/');
            if(newButton !== draft) return;
            byId('newButtonPiece').replaceChildren(new Option('Elija una pieza',''));
            for(const piece of data.pieces) byId('newButtonPiece').append(new Option(`${piece.category_name} - ${piece.name}`,piece.id));
            // Fetch remaining pages so all saved pieces are available.
            let offset=data.next_offset;
            while(offset !== null && offset !== undefined) {
                const more=await request(`/api/piece-library/?offset=${offset}`);
                if(newButton !== draft) return;
                for(const piece of more.pieces) byId('newButtonPiece').append(new Option(`${piece.category_name} - ${piece.name}`,piece.id));
                offset=more.next_offset;
            }
            message(newButtonModal,data.total ? '' : 'Todavía no hay piezas guardadas.');
        } catch(error) {if(newButton===draft) message(newButtonModal,error.message,true);}
        finally {if(newButton===draft) busy(newButtonModal,false);}
    };
    byId('btnConfirmNewButton').onclick = chooseNewButtonSource;
    byId('btnAssignmentImportWorkbook').onclick = () => byId('buttonWorkbookInput').click();
    byId('buttonWorkbookInput').onchange = event => {
        const file = event.target.files[0];
        event.target.value = '';
        if (file) previewButtonFile(file);
    };
    byId('buttonWorkbookSheet').onchange = event => {
        if (assignment?.upload) previewButtonFile(assignment.upload, event.target.value);
    };
    byId('btnConfirmButtonWorkbook').onclick = () => {
        if (assignment?.upload) linkSheet({ action: 'stage', source_sheet: byId('buttonWorkbookSheet').value }, assignment.upload);
    };
    byId('sheetButtonDescriptionEnabled').onchange = async event => {
        const context = assignment;
        if (!valid(context)) return;
        const enabled = event.target.checked;
        busy(sheetModal, true);
        try {
            const previous = await request(context.marker.details_url);
            if (!valid(context)) return;
            const result = await request(context.marker.details_url, {
                name: previous.name, color: previous.color, show_description: enabled,
            });
            if (!valid(context)) return;
            Object.assign(context.marker, result.button);
            window.projectWorkspace.dirty();
            message(sheetModal, (enabled ? 'Descripción activada.' : 'Descripción desactivada. Sus datos se conservan.') + ' Pulsa «Guardar plano» para publicarlo.');
        } catch (error) {
            if (valid(context)) {
                event.target.checked = context.marker.show_description === true;
                message(sheetModal, error.message, true);
            }
        } finally { if (valid(context)) busy(sheetModal, false); }
    };
    async function editButtonOption(mode) {
        const context = assignment;
        if (!valid(context)) return;
        await hide(sheetModal);
        if (!valid(context)) return;
        if (context.kind === 'pdf') window.pdfWorkflow.editButtonDetails(context.marker, mode);
        else window.planWorkflow.editButtonDetails(mode);
    }
    byId('btnButtonNameOptions').onclick = () => editButtonOption('name');
    byId('btnButtonAppearanceOptions').onclick = () => editButtonOption('appearance');
    byId('btnButtonDetailsOptions').onclick = () => editButtonOption('description');
    async function copyButton(context, marker, dialog = null) {
        if (!marker || !valid(context) || !context.plan.permissions?.can_assign || active().isBusy?.() || window.planSectors?.editing) return;
        if (window.planDisplay && !(await window.planDisplay.flush())) return;
        if (!valid(context)) return;
        const name = marker.label || marker.name || window.projectWorkspace.selected?.name || 'Botón';
        const info = [marker.category_name, marker.source_sheet || marker.sheet_name].filter(Boolean).join(' · ');
        if (!(await window.confirmAppAction('Copiar botón',
            `¿Copiar «${name}»${info ? ` (${info})` : ''}? Se copiarán su ficha, descripción y apariencia.`, 'Copiar botón'))) return;
        if (dialog) await hide(dialog);
        if (!valid(context)) return;
        await active().setMode('add', { origin: 'copy', copy_button_id: marker.id });
    }
    byId('btnCopyPlanButton').onclick = () => copyButton(assignment, assignment?.marker, sheetModal);
    function copySelectedButton() {
        const context = target();
        if (!context) return;
        const buttons = context.kind === 'pdf' ? context.plan.markers : context.plan.motors;
        return copyButton(context, buttons.find(button => button.id === window.projectWorkspace.selected?.buttonId));
    }
    byId('btnAddPlanListing').onclick = () => {
        byId('planListingOptions').hidden = false;
        byId('btnAddPlanListing').hidden = true;
    };
    byId('btnDefaultPlanListing').onclick = () => changeListing({ action: 'add' });
    byId('btnImportPlanListing').onclick = () => byId('planListingFileInput').click();
    byId('planListingFileInput').onchange = event => { const file = event.target.files[0]; event.target.value = ''; if (file) previewListing(file); };
    byId('btnConfirmPlanListing').onclick = () => {
        if (listingTarget?.upload) changeListing({ action: 'import', source_sheet: byId('planListingSourceSheet').value }, listingTarget.upload);
    };
    byId('btnChangePrimaryListing').onclick = () => { const sheet = byId('planPrimaryListingSheet').value; if (sheet) changeListing({ action: 'set_primary', sheet }); };
    byId('btnAddExistingListing').onclick = () => { const sheet = byId('planAdditionalListingSheet').value; if (sheet) changeListing({ action: 'add_sheet', sheet }); };
    for (const prefix of ['Cad', 'Pdf']) {
        const button = document.createElement('button'); button.type = 'button'; button.id = `btn${prefix}Workbook`;
        button.className = 'btn btn-outline-success btn-sm'; button.innerHTML = '<i class="bi bi-file-earmark-excel" aria-hidden="true"></i> Excel del plano';
        byId(`btn${prefix}ExportProject`).before(button);
        button.onclick = () => manage();
        const view = document.createElement('button');
        view.type = 'button'; view.className = 'btn btn-outline-success btn-sm';
        view.id = `btn${prefix}WorkbookSheets`;
        view.textContent = 'Ver hojas de Excel'; view.onclick = openWorkbook;
        button.before(view);
        byId(`btn${prefix}AssignListing`).textContent = 'Añadir listado';
        byId(`btn${prefix}OpenListing`).textContent = 'Ver listados';
    }
    const fullButton = document.createElement('button'); fullButton.type = 'button'; fullButton.textContent = 'Excel del plano'; fullButton.id = 'fullscreenManageWorkbook';
    fullButton.onclick = () => { byId('btnPlanFullscreenMenu').click(); manage(); };
    byId('fullscreenPlanMenu').prepend(fullButton);
    const fullSheets = document.createElement('button');
    fullSheets.type = 'button'; fullSheets.textContent = 'Ver hojas de Excel';
    fullSheets.onclick = () => { byId('btnPlanFullscreenMenu').click(); openWorkbook(); };
    fullButton.before(fullSheets);
    window.planExcel = { manage, assign, listings, openListings, newButtonOptions, sourceSelected, copySelectedButton,
        createButton(url, position, { file, ...options }) {
            const values = Object.fromEntries(Object.entries(options).filter(([, value]) => value !== undefined));
            return request(url, { ...position, ...values }, file);
        },
        reset() {
            epoch++; bookTarget = assignment = listingTarget = null;
            newButton?.resolve(null);
            newButton = null;
            for (const dialog of [bookModal, sheetModal, listingsModal, newButtonModal]) dialog.controller.hide();
        },
    };
})();
