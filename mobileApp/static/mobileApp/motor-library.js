/* Saved motors are named originals. Buttons always use separate working copies. */
(() => {
    const byId = (id) => document.getElementById(id);
    const view = byId('viewSavedMotors');
    let target = null, registerContext = null, searchTimer = null, epoch = 0, nextOffset = null;
    const registerModal = document.createElement('div');
    registerModal.className = 'modal fade motor-register-modal';
    registerModal.tabIndex = -1;
    registerModal.id = 'motorRegisterDialog';
    registerModal.setAttribute('aria-labelledby', 'motorRegisterTitle');
    registerModal.innerHTML = `<div class="modal-dialog modal-dialog-centered"><form id="motorRegisterForm" class="modal-content">
      <div class="modal-header"><h2 id="motorRegisterTitle" class="modal-title fs-5">Guardar motor</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
      <div class="modal-body"><p class="small text-muted">Se conservará la ficha original para incorporarla a otros planos.</p>
      <label for="motorRegisterName" class="form-label">Nombre del motor</label><input id="motorRegisterName" class="form-control" maxlength="120" required autocomplete="off">
      <p id="motorRegisterStatus" class="small text-danger mt-2" role="status"></p></div>
      <div class="modal-footer"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancelar</button><button type="submit" id="btnConfirmRegisterMotor" class="btn btn-success">Guardar motor</button></div></form></div>`;
    document.body.append(registerModal);
    const registerController = new bootstrap.Modal(registerModal);
    registerModal.addEventListener('keydown', (event) => event.stopPropagation());
    registerModal.addEventListener('hidden.bs.modal', () => { registerContext = null; });
    registerModal.addEventListener('shown.bs.modal', () => byId('motorRegisterName').focus());

    const picker = document.createElement('div');
    picker.className = 'modal fade';
    picker.tabIndex = -1;
    picker.id = 'savedMotorPicker';
    picker.setAttribute('aria-labelledby', 'savedMotorPickerTitle');
    picker.innerHTML = `<div class="modal-dialog modal-dialog-centered modal-dialog-scrollable"><div class="modal-content">
      <div class="modal-header"><h2 id="savedMotorPickerTitle" class="modal-title fs-5">Importar motor guardado</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
      <div class="modal-body"><p id="savedMotorPickerStatus" class="small" role="status"></p><input type="search" id="savedMotorPickerSearch" class="form-control mb-2" placeholder="Buscar por nombre" aria-label="Buscar motor guardado"><div id="savedMotorPickerList" class="saved-motors-list"></div></div></div></div>`;
    document.body.append(picker);
    const pickerController = new bootstrap.Modal(picker);

    async function request(url, options = {}) {
        const headers = new Headers(options.headers || {});
        if (options.method && options.method !== 'GET') headers.set('X-CSRFToken', csrfToken());
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth?.expire();
            const error = new Error(data.error || 'No se pudo completar la operación con el motor.');
            error.data = data;
            error.status = response.status;
            throw error;
        }
        return data;
    }
    const post = (url, data) => request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

    function card(record, pickerMode = false) {
        const row = document.createElement('article');
        row.className = 'saved-motor-card';
        const title = document.createElement('strong');
        title.textContent = record.name;
        const actions = document.createElement('div');
        actions.className = 'saved-motor-actions';
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'btn btn-sm btn-outline-success';
        open.textContent = 'Ver ficha';
        open.onclick = async () => {
            if (pickerMode) {
                picker.addEventListener('hidden.bs.modal', () => window.workbookEditor.open({ ...record.workbook, context: 'saved-motor' }), { once: true });
                pickerController.hide();
            } else await window.workbookEditor.open({ ...record.workbook, context: 'saved-motor' });
        };
        actions.append(open);
        if (pickerMode) {
            const use = document.createElement('button');
            use.type = 'button';
            use.className = 'btn btn-sm btn-success';
            use.textContent = 'Usar en botón seleccionado';
            use.disabled = !target;
            use.onclick = () => useRecord(record, pickerMode);
            actions.append(use);
        }
        row.append(title, actions);
        return row;
    }

    async function refresh(append = false) {
        const version = ++epoch;
        const list = byId('savedMotorsList');
        const status = byId('savedMotorsStatus');
        const offset = append ? nextOffset || 0 : 0;
        status.textContent = 'Cargando motores…';
        if (!append) list.replaceChildren();
        try {
            const data = await request(`/api/motor-library/?q=${encodeURIComponent(byId('savedMotorsSearch').value)}&offset=${offset}`);
            if (version !== epoch || !appState.user) return;
            for (const record of data.motors) list.append(card(record));
            nextOffset = data.next_offset;
            byId('btnMoreSavedMotors').hidden = nextOffset === null;
            status.textContent = data.total ? `${data.total} motores guardados` : 'Todavía no hay motores guardados. Rellene una ficha y use Guardar motor, o importe una ficha del dispositivo.';
        } catch (error) { if (version === epoch) status.textContent = error.message; }
    }

    async function refreshPicker() {
        byId('savedMotorPickerStatus').textContent = target ? `Se vinculará la hoja de motor a ${target.name}.` : 'Seleccione un botón del plano para asignarle un motor.';
        try {
            const data = await request(`/api/motor-library/?q=${encodeURIComponent(byId('savedMotorPickerSearch').value)}`);
            const list = byId('savedMotorPickerList');
            list.replaceChildren();
            for (const record of data.motors) list.append(card(record, true));
            if (!data.motors.length) byId('savedMotorPickerStatus').textContent = 'No se encontraron motores guardados.';
        } catch (error) { byId('savedMotorPickerStatus').textContent = error.message; }
    }

    async function chooseForTarget() {
        if (!(await window.workbookEditor.requestClose())) return;
        byId('savedMotorPickerSearch').value = '';
        pickerController.show();
        refreshPicker();
    }

    async function useRecord(record, pickerMode) {
        const selected = target;
        if (!selected) return;
        const status = byId(pickerMode ? 'savedMotorPickerStatus' : 'savedMotorsStatus');
        status.textContent = 'Vinculando la hoja del motor…';
        try {
            const result = await post(selected.assign_url, { saved_motor_id: record.id });
            const workbook = selected.apply(result);
            if (!workbook) return;
            if (pickerMode) {
                picker.addEventListener('hidden.bs.modal', () => window.workbookEditor.open(workbook), { once: true });
                pickerController.hide();
            } else await window.workbookEditor.open(workbook);
        } catch (error) { status.textContent = error.message; }
    }

    async function registerCurrent(editor) {
        if (registerContext || editor.busy || !editor.visible) return;
        editor.commitAll();
        if (editor.hasUnsavedChanges && !(await editor.save())) return;
        if (editor.metadata.motor_registration?.registered) {
            editor.setStatus('Este motor ya existe en Motores guardados.');
            return;
        }
        if (!editor.metadata.motor_registration?.has_data) {
            editor.setStatus('Rellene datos del motor antes de registrarlo. Una ficha vacía no se puede guardar como motor.', 'error');
            return;
        }
        registerContext = { editor, epoch: editor.epoch, id: editor.metadata.id, revision: editor.metadata.revision, sheet: editor.sheet };
        byId('motorRegisterName').value = (editor.context !== 'motor-import' ? target?.name : null) || editor.metadata.name.replace(/\.xlsx$/i, '');
        byId('motorRegisterStatus').textContent = '';
        registerController.show();
        [...document.querySelectorAll('.modal-backdrop')].at(-1)?.classList.add('motor-register-backdrop');
    }

    byId('motorRegisterForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        const context = registerContext;
        if (!context) return;
        byId('btnConfirmRegisterMotor').disabled = true;
        byId('motorRegisterStatus').textContent = 'Guardando motor…';
        try {
            const result = await post('/api/motor-library/register/', { workbook_id: context.id, revision: context.revision, sheet: context.sheet, name: byId('motorRegisterName').value });
            if (context.editor.visible && context.editor.epoch === context.epoch && result.workbook) {
                context.editor.metadata = result.workbook;
                context.editor.applyPermissions();
                context.editor.setStatus(`Motor «${result.motor.name}» guardado.`, 'success');
                context.editor.notifySaved(result.workbook);
            }
            registerController.hide();
            if (view.classList.contains('active')) refresh();
            document.dispatchEvent(new CustomEvent('motor-library:changed'));
        } catch (error) {
            byId('motorRegisterStatus').textContent = error.message;
            if (error.data?.existing && context.editor.metadata) {
                context.editor.metadata.motor_registration = { is_motor: true, registered: true, name: error.data.existing.name, can_register: false, has_data: true };
                context.editor.applyPermissions();
            }
        } finally { byId('btnConfirmRegisterMotor').disabled = false; }
    });

    byId('btnSavedMotors').addEventListener('click', () => {
        if (!appState.user) return;
        switchView(view);
        refresh();
    });
    byId('btnSavedMotorsBack').addEventListener('click', () => switchView(viewMainMenu));
    byId('savedMotorsSearch').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => refresh(), 180); });
    byId('savedMotorPickerSearch').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(refreshPicker, 180); });
    byId('btnMoreSavedMotors').addEventListener('click', () => refresh(true));
    byId('btnImportMotorRecord').addEventListener('click', () => byId('motorRecordFileInput').click());
    byId('motorRecordFileInput').addEventListener('change', async (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        byId('savedMotorsStatus').textContent = 'Abriendo ficha del dispositivo…';
        try {
            const body = new FormData();
            body.append('file', file);
            const result = await request('/api/motor-library/import/', { method: 'POST', body });
            await window.workbookEditor.open({ ...result.workbook, context: 'motor-import' });
        } catch (error) { byId('savedMotorsStatus').textContent = error.message; }
    });

    window.motorLibrary = {
        registerCurrent, chooseForTarget,
        setTarget(value) { target = value; },
        reset() { epoch++; target = null; registerContext = null; registerController.hide(); pickerController.hide(); byId('savedMotorsList').replaceChildren(); },
    };
})();
