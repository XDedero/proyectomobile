/* The button opens a short description first, with the workbook one step away. */
(() => {
    const dialog = document.createElement('div');
    dialog.id = 'buttonDetailsModal';
    dialog.className = 'modal fade button-details';
    dialog.tabIndex = -1;
    dialog.setAttribute('aria-labelledby', 'buttonDetailsTitle');
    dialog.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content rounded-4 shadow">
          <div class="modal-header">
            <div class="min-w-0"><h2 class="modal-title fs-5 text-break" id="buttonDetailsTitle">Descripción del botón</h2>
            <p id="buttonDetailsCategory" class="small text-secondary mb-0"></p></div>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar descripción"></button>
          </div>
          <div class="modal-body">
            <div id="buttonDetailsAppearance" class="mb-3" hidden>
              <div id="buttonDetailsNameSection">
              <label class="form-label" for="buttonDetailsName">Nombre del botón <small class="text-muted">(opcional)</small></label>
              <input type="text" class="form-control mb-2" id="buttonDetailsName" maxlength="120">
              <p class="small text-muted">Deja el nombre vacío para mostrar el número del botón.</p>
              </div>
              <div id="buttonDetailsDescriptionSection" class="form-check form-switch mb-2">
                <input type="checkbox" class="form-check-input" id="buttonDetailsEnabled">
                <label class="form-check-label" for="buttonDetailsEnabled">Mostrar descripción al abrir el botón</label>
              </div>
              <div id="buttonDetailsColorSection"><div class="d-flex align-items-center gap-2 flex-wrap">
                <input type="checkbox" class="form-check-input mt-0" id="buttonDetailsCustomColor">
                <label for="buttonDetailsCustomColor">Color personalizado</label>
                <input type="color" class="form-control form-control-color" id="buttonDetailsColor" aria-label="Color del botón" value="#2563eb">
              </div></div>
            </div>
            <div id="buttonDetailsRows"></div>
            <button type="button" class="btn btn-outline-secondary btn-sm mt-2" id="buttonDetailsAdd" hidden>Añadir descripción</button>
            <p id="buttonDetailsStatus" class="small mb-0" role="status"></p>
          </div>
          <div class="modal-footer justify-content-between">
            <div class="d-flex flex-wrap gap-2">
              <button type="button" class="btn btn-outline-secondary btn-sm" id="buttonDetailsEdit" hidden>Editar</button>
              <button type="button" class="btn btn-success btn-sm" id="buttonDetailsSave" hidden>Guardar descripción</button>
              <button type="button" class="btn btn-outline-secondary btn-sm" id="buttonDetailsCancel" hidden>Cancelar</button>
              <button type="button" class="btn btn-outline-secondary btn-sm" id="buttonDetailsAssign" hidden>Asignar hoja</button>
            </div>
            <button type="button" class="btn btn-primary btn-sm" id="buttonDetailsExcel" disabled>Ver más detalles</button>
          </div>
        </div>
      </div>`;
    document.body.append(dialog);
    const modal = bootstrap.Modal.getOrCreateInstance(dialog);
    const byId = id => document.getElementById('buttonDetails' + id);
    const controls = ['Edit', 'Save', 'Cancel', 'Add', 'Assign', 'Excel'];
    let current = null;

    function status(message, error = false) {
        byId('Status').textContent = message;
        byId('Status').classList.toggle('text-danger', error);
        byId('Status').classList.toggle('mt-2', Boolean(message));
    }

    function isCurrent(context) { return context === current && appState.user?.id === context.user; }

    async function request(url, data) {
        const options = { credentials: 'same-origin', cache: 'no-store' };
        if (data) Object.assign(options, {
            method: 'POST', headers: { 'X-CSRFToken': csrfToken(), 'Content-Type': 'application/json' }, body: JSON.stringify(data),
        });
        const response = await fetch(url, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth?.expire();
            throw new Error(result.error || 'No se pudo consultar la descripción del botón.');
        }
        return result;
    }

    function renderRows(context) {
        const container = byId('Rows');
        container.replaceChildren();
        if (!context.data) return;
        if (!context.editing) {
            const list = document.createElement('dl');
            list.className = 'button-details-list mb-0';
            for (const row of context.data.descriptions) {
                const entry = document.createElement('div');
                const key = document.createElement('dt'), value = document.createElement('dd');
                key.textContent = row.key;
                value.textContent = row.value.trim() ? row.value : 'Vacío';
                value.classList.toggle('text-muted', !row.value.trim());
                entry.append(key, value);
                list.append(entry);
            }
            if (list.childElementCount) container.append(list);
            else container.textContent = 'Este botón aún no tiene descripción.';
            return;
        }
        for (const [index, row] of context.draft.entries()) {
            const entry = document.createElement('div');
            entry.className = 'button-details-edit-row';
            const key = document.createElement('input');
            key.className = 'form-control form-control-sm'; key.maxLength = 100; key.value = row.key;
            key.placeholder = 'Clave'; key.setAttribute('aria-label', 'Clave ' + (index + 1));
            key.oninput = () => { row.key = key.value; };
            const value = document.createElement('textarea');
            value.className = 'form-control form-control-sm'; value.maxLength = 2000; value.value = row.value;
            value.rows = 1; value.placeholder = 'Valor'; value.setAttribute('aria-label', 'Valor ' + (index + 1));
            value.oninput = () => { row.value = value.value; };
            const remove = document.createElement('button');
            remove.type = 'button'; remove.className = 'btn btn-outline-danger btn-sm'; remove.textContent = 'Quitar';
            remove.setAttribute('aria-label', 'Quitar descripción ' + (index + 1));
            remove.onclick = () => { context.draft.splice(index, 1); render(context); };
            entry.append(key, value, remove);
            container.append(entry);
        }
    }

    function render(context) {
        const { data, editing, saving, options } = context;
        const mode = context.mode || 'description';
        const titles = { name: 'Cambiar nombre', appearance: 'Apariencia del botón', description: 'Descripción del botón' };
        byId('Title').textContent = editing ? titles[mode] : data ? data.name || options.defaultName || 'Botón' : options.name || 'Descripción del botón';
        byId('Category').textContent = context.button.category_name || '';
        byId('Appearance').hidden = !editing;
        byId('NameSection').hidden = mode !== 'name';
        byId('ColorSection').hidden = mode !== 'appearance';
        byId('DescriptionSection').hidden = mode !== 'description';
        byId('Rows').hidden = editing && mode !== 'description';
        byId('Edit').hidden = !data?.can_edit || editing;
        byId('Add').hidden = byId('Save').hidden = byId('Cancel').hidden = !editing;
        byId('Add').hidden = !editing || mode !== 'description';
        byId('Save').textContent = mode === 'description' ? 'Guardar descripción' : 'Guardar cambios';
        byId('Excel').hidden = editing;
        byId('Assign').hidden = !options.assign || Boolean(options.openExcel) || editing;
        renderRows(context);
        for (const id of controls) byId(id).disabled = saving;
        byId('Add').disabled = saving || (context.draft?.length || 0) >= 64;
        byId('Excel').disabled = saving || editing || !options.openExcel || !data;
        for (const control of byId('Appearance').querySelectorAll('input')) control.disabled = saving;
        for (const control of byId('Rows').querySelectorAll('input,textarea,button')) control.disabled = saving;
        byId('Color').disabled = saving || !byId('CustomColor').checked;
    }

    async function open(button, options) {
        if (!button.details_url || !(await window.workbookEditor.requestClose())) return;
        const context = current = { button, options, user: appState.user?.id, data: null, editing: false, saving: false };
        status('Cargando descripción…');
        render(context);
        modal.show();
        try {
            const data = await request(button.details_url);
            if (!isCurrent(context)) return;
            context.data = data;
            render(context);
            status(options.openExcel ? '' : 'Este botón todavía no tiene una hoja vinculada.');
            if (options.edit && data.can_edit) startEdit();
        } catch (error) { if (isCurrent(context)) status(error.message, true); }
    }

    function afterClose(action) {
        if (!action) return;
        dialog.addEventListener('hidden.bs.modal', action, { once: true });
        modal.hide();
    }

    byId('Excel').onclick = () => afterClose(current?.options.openExcel);
    byId('Assign').onclick = () => afterClose(current?.options.assign);
    function startEdit() {
        if (!current?.data?.can_edit) return;
        current.editing = true;
        current.mode = typeof current.options.edit === 'string' ? current.options.edit : 'description';
        current.draft = current.data.descriptions.map(row => ({ ...row }));
        byId('Name').value = current.data.name;
        byId('Name').maxLength = current.options.kind === 'dxf' ? 255 : 120;
        byId('CustomColor').checked = Boolean(current.data.color);
        byId('Enabled').checked = current.data.show_description;
        byId('Color').value = current.data.color || '#2563eb';
        render(current);
        status('');
        byId(current.mode === 'name' ? 'Name' : current.mode === 'appearance' ? 'CustomColor' : 'Enabled').focus();
    }
    byId('Edit').onclick = startEdit;
    byId('CustomColor').onchange = () => { byId('Color').disabled = !byId('CustomColor').checked; };
    byId('Add').onclick = () => {
        if (!current?.editing || current.draft.length >= 64) return;
        current.draft.push({ key: '', value: '' });
        render(current);
        byId('Rows').lastElementChild?.querySelector('input')?.focus();
    };
    byId('Cancel').onclick = () => {
        if (!current) return;
        if (current.mode !== 'description') { modal.hide(); return; }
        current.editing = false; render(current); status('');
    };
    byId('Save').onclick = async () => {
        const context = current;
        if (!context?.editing || context.saving) return;
        const data = { name: context.data.name, color: context.data.color, show_description: context.data.show_description };
        if (context.mode === 'name') data.name = byId('Name').value;
        if (context.mode === 'appearance') data.color = byId('CustomColor').checked ? byId('Color').value : '';
        if (context.mode === 'description') {
            data.show_description = byId('Enabled').checked;
            if (JSON.stringify(context.draft) !== JSON.stringify(context.data.descriptions)) data.descriptions = context.draft;
        }
        context.saving = true;
        render(context);
        status('Guardando…');
        try {
            const result = await request(context.button.details_url, data);
            if (!isCurrent(context)) return;
            context.data = result;
            context.editing = false;
            context.options.onSaved(result.button);
            status('Cambios guardados. Pulsa «Guardar plano» para publicarlos.');
        } catch (error) { if (isCurrent(context)) status(error.message, true); }
        finally {
            context.saving = false;
            if (isCurrent(context)) {
                render(context);
                if (!context.editing && context.mode !== 'description') modal.hide();
            }
        }
    };
    dialog.addEventListener('hide.bs.modal', event => { if (current?.saving) event.preventDefault(); });
    dialog.addEventListener('hidden.bs.modal', () => { current = null; });
    window.buttonDetails = { open };
})();
