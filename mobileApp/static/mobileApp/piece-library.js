/* Saved motors are named originals. Buttons always use separate working copies. */
(() => {
    const byId = (id) => document.getElementById(id);
    const view = byId('viewSavedMotors');
    let target = null, registerContext = null, searchTimer = null, epoch = 0, nextOffset = null, canManage = false, refreshTimer;
    const registerModal = document.createElement('div');
    registerModal.className = 'modal fade motor-register-modal';
    registerModal.tabIndex = -1;
    registerModal.id = 'motorRegisterDialog';
    registerModal.setAttribute('aria-labelledby', 'motorRegisterTitle');
    registerModal.innerHTML = `<div class="modal-dialog modal-dialog-centered"><form id="motorRegisterForm" class="modal-content">
      <div class="modal-header"><h2 id="motorRegisterTitle" class="modal-title fs-5">Guardar pieza</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
      <div class="modal-body"><p class="small text-muted">Se conservará la ficha original para incorporarla a otros planos.</p>
      <label for="motorRegisterName" class="form-label">Nombre de la pieza</label><input id="motorRegisterName" class="form-control" maxlength="120" required autocomplete="off">
      <div id="pieceRegisterCategorySection"><label for="pieceRegisterCategory" class="form-label mt-2">Categoría</label><select id="pieceRegisterCategory" class="form-select" required></select></div>
      <p id="motorRegisterStatus" class="small text-danger mt-2" role="status"></p></div>
      <div class="modal-footer"><button type="button" class="btn btn-outline-secondary" data-bs-dismiss="modal">Cancelar</button><button type="submit" id="btnConfirmRegisterMotor" class="btn btn-success">Guardar pieza</button></div></form></div>`;
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
      <div class="modal-header"><h2 id="savedMotorPickerTitle" class="modal-title fs-5">Importar pieza guardada</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div>
      <div class="modal-body"><p id="savedMotorPickerStatus" class="small" role="status"></p><input type="search" id="savedMotorPickerSearch" class="form-control mb-2" placeholder="Buscar por nombre" aria-label="Buscar pieza guardada"><div id="savedMotorPickerList" class="saved-motors-list"></div></div></div></div>`;
    document.body.append(picker);
    const pickerController = new bootstrap.Modal(picker);

    async function request(url, options = {}) {
        const headers = new Headers(options.headers || {});
        if (options.method && options.method !== 'GET') headers.set('X-CSRFToken', csrfToken());
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options, headers });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            if (response.status === 401) window.appAuth?.expire();
            const error = new Error(data.error || 'No se pudo completar la operación con la pieza.');
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
        const meta = document.createElement('small');
        meta.textContent = `${record.category_name || 'Sin categoría'} · ${record.cloud_status === 'synced' ? 'Guardado en la base de datos y almacenado en la nube' : record.cloud_status === 'uploaded' ? 'Almacenado en la nube · Confirmando registro' : 'Guardado en este PC · Subida pendiente'}`;
        const actions = document.createElement('div');
        actions.className = 'saved-motor-actions';
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'btn btn-sm btn-outline-success';
        open.textContent = 'Ver ficha';
        open.onclick = async () => {
            if (pickerMode) {
                picker.addEventListener('hidden.bs.modal', () => window.workbookEditor.open({ ...record.workbook, context: 'saved-piece' }), { once: true });
                pickerController.hide();
            } else await window.workbookEditor.open({ ...record.workbook, context: 'saved-piece' });
        };
        actions.append(open);
        if (canManage) {
            const edit=document.createElement('button'); edit.type='button'; edit.className='btn btn-sm btn-outline-primary'; edit.textContent='Modificar';
            edit.onclick=async () => {
                if (!(await window.workbookEditor.requestClose())) return;
                edit.disabled=true;
                try {
                    const result=await post(record.edit_url);
                    if (pickerMode) {
                        picker.addEventListener('hidden.bs.modal',()=>window.workbookEditor.open(result.workbook),{once:true});pickerController.hide();
                    } else await window.workbookEditor.open(result.workbook);
                } catch(error) { byId(pickerMode?'savedMotorPickerStatus':'savedMotorsStatus').textContent=error.message; }
                finally { edit.disabled=false; }
            };
            const remove=document.createElement('button'); remove.type='button'; remove.className='btn btn-sm btn-outline-danger'; remove.textContent='Eliminar';
            remove.onclick=async () => {
                if (!(await window.confirmAppAction('Eliminar pieza',
                    `¿Eliminar «${record.name}» del catálogo, su registro de la base de datos y su hoja de la nube? Las fichas ya incorporadas a los planos conservarán sus copias.`,
                    'Eliminar pieza', { danger: true }))) return;
                remove.disabled=true; edit.disabled=true;
                try {
                    await post(record.delete_url,{revision:record.workbook.revision});
                    row.remove();
                    if (pickerMode) await refreshPicker(); else await refresh();
                    document.dispatchEvent(new CustomEvent('piece-library:changed'));
                } catch(error) { byId(pickerMode?'savedMotorPickerStatus':'savedMotorsStatus').textContent=error.message; }
                finally { remove.disabled=false;edit.disabled=false; }
            };
            actions.append(edit,remove);
        }
        if (pickerMode) {
            const use = document.createElement('button');
            use.type = 'button';
            use.className = 'btn btn-sm btn-success';
            use.textContent = 'Usar en botón seleccionado';
            use.disabled = !target;
            use.onclick = () => useRecord(record, pickerMode);
            actions.append(use);
        }
        row.append(title, meta, actions);
        return row;
    }

    async function refresh(append = false) {
        const version = ++epoch;
        const list = byId('savedMotorsList');
        const status = byId('savedMotorsStatus');
        const offset = append ? nextOffset || 0 : 0;
        status.textContent = 'Cargando piezas…';
        if (!append) list.replaceChildren();
        try {
            const data = await request(`/api/piece-library/?q=${encodeURIComponent(byId('savedMotorsSearch').value)}&offset=${offset}`);
            if (version !== epoch || !appState.user) return;
            canManage = data.can_manage === true;
            byId('btnImportMotorRecord').hidden = !canManage;
            for (const record of data.pieces) list.append(card(record));
            nextOffset = data.next_offset;
            byId('btnMoreSavedMotors').hidden = nextOffset === null;
            status.textContent = data.total ? `${data.total} piezas guardadas` : 'Todavía no hay piezas guardadas. Rellene una ficha y use Guardar pieza, o importe una ficha del dispositivo.';
            if (data.pending_deletions) status.textContent += ' Eliminando piezas de la nube y la base de datos…';
            if (data.cloud_error) status.textContent += ' '+data.cloud_error;
            clearTimeout(refreshTimer);
            if (data.pending_deletions || data.pieces.some(item=>item.cloud_status !== 'synced')) refreshTimer=setTimeout(()=>{
                if (view.classList.contains('active') && !window.workbookEditor.visible) refresh();
            },5000);
        } catch (error) { if (version === epoch) status.textContent = error.message; }
    }

    async function refreshPicker() {
        byId('savedMotorPickerStatus').textContent = target ? `Se vinculará la hoja de motor a ${target.name}.` : 'Seleccione un botón del plano para asignarle una pieza.';
        try {
            const data = await request(`/api/piece-library/?q=${encodeURIComponent(byId('savedMotorPickerSearch').value)}`);
            canManage = data.can_manage === true;
            const list = byId('savedMotorPickerList');
            list.replaceChildren();
            for (const record of data.pieces) list.append(card(record, true));
            if (!data.pieces.length) byId('savedMotorPickerStatus').textContent = 'No se encontraron piezas guardadas.';
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
        status.textContent = 'Vinculando la hoja de la pieza…';
        try {
            const result = await post(selected.assign_url, { saved_piece_id: record.id });
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
        if (!editor.pieceEdit && (editor.pieceRegistration || editor.metadata.motor_registration)?.registered) {
            editor.setStatus('Esta pieza ya existe en Piezas guardadas.');
            return;
        }
        registerContext = { editor, epoch: editor.epoch, id: editor.metadata.id, revision: editor.metadata.revision, sheet: editor.sheet, category:editor.pieceCategoryId, edit:editor.pieceEdit };
        byId('motorRegisterName').value = editor.pieceDraftName || (editor.context !== 'piece-import' ? target?.name : null) || editor.metadata.name.replace(/\.xlsx$/i, '');
        try { await fillCategories(byId('pieceRegisterCategory'), registerContext.category); }
        catch(error) { registerContext=null; editor.setStatus(error.message,'error'); return; }
        byId('pieceRegisterCategorySection').hidden = !!registerContext.category && !registerContext.edit;
        byId('motorRegisterStatus').textContent = '';
        registerController.show();
        [...document.querySelectorAll('.modal-backdrop')].at(-1)?.classList.add('motor-register-backdrop');
    }

    byId('motorRegisterForm').addEventListener('submit', async (event) => {
        event.preventDefault();
        const context = registerContext;
        if (!context) return;
        byId('btnConfirmRegisterMotor').disabled = true;
        byId('motorRegisterStatus').textContent = 'Guardando pieza…';
        try {
            const result = await post(context.edit?.update_url || '/api/piece-library/register/', { workbook_id: context.id, revision: context.revision, sheet: context.sheet,
                piece_revision:context.edit?.revision, category_id:context.edit ? byId('pieceRegisterCategory').value : context.category || byId('pieceRegisterCategory').value, name: byId('motorRegisterName').value });
            if (context.edit && context.editor.visible && context.editor.epoch === context.epoch) {
                context.editor.pieceEdit.revision=result.piece.workbook.revision;
                context.editor.pieceUnpublished=false;
                context.editor.pieceDraftName=result.piece.name;
                context.editor.pieceCategoryId=result.piece.category_id;
                context.editor.applyPermissions();context.editor.updatePending();
                context.editor.setStatus('Pieza actualizada en este PC. Sincronizando su hoja en piezas.xlsx…','success');
            }
            if (context.editor.visible && context.editor.epoch === context.epoch && result.workbook) {
                context.editor.metadata = result.workbook;
                context.editor.applyPermissions();
                context.editor.setStatus(`Pieza «${result.piece.name}» guardada en este PC. Subiendo a piezas.xlsx…`, 'success');
                context.editor.notifySaved(result.workbook);
            }
            registerController.hide();
            if (view.classList.contains('active')) refresh();
            document.dispatchEvent(new CustomEvent('piece-library:changed'));
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
    const create = document.createElement('div'); create.className='modal fade'; create.tabIndex=-1;
    create.innerHTML=`<div class="modal-dialog modal-dialog-centered"><div class="modal-content"><div class="modal-header"><h2 class="modal-title fs-5">Añadir pieza</h2><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Cerrar"></button></div><div class="modal-body">
      <label class="form-label" for="pieceCreateName">Nombre</label><input id="pieceCreateName" class="form-control mb-2" maxlength="120">
      <label class="form-label" for="pieceCreateCategory">Categoría</label><select id="pieceCreateCategory" class="form-select mb-3"></select>
      <button type="button" id="pieceCreateBlank" class="btn btn-outline-success">Formato predeterminado</button> <button type="button" id="pieceCreateImport" class="btn btn-success">Importar Excel o CSV</button>
      <p id="pieceCreateStatus" class="small mt-2" role="status"></p></div></div></div>`;
    document.body.append(create); const createController = new bootstrap.Modal(create);
    function categoryControls(select) {
        if (!select) return;
        const box = document.createElement('details');
        box.className = 'border rounded p-2 mb-3';
        box.hidden = true;
        const summary = document.createElement('summary'); summary.textContent = 'Crear categoría';
        const label = document.createElement('label'); label.textContent = 'Nombre de la categoría'; label.className = 'form-label small mt-2';
        const input = document.createElement('input'); input.type = 'text'; input.maxLength = 80; input.className = 'form-control mb-2';
        input.id = select.id + 'NewName'; label.htmlFor = input.id;
        const save = document.createElement('button'); save.type = 'button'; save.className = 'btn btn-sm btn-success'; save.textContent = 'Crear categoría';
        const feedback = document.createElement('p'); feedback.className = 'small mt-2 mb-0'; feedback.setAttribute('role', 'status');
        box.append(summary, label, input, save, feedback); select.after(box);
        const modal = select.closest('.modal');
        modal?.addEventListener('show.bs.modal', () => {
            box.hidden = !appState.user?.permissions?.can_create_category;
            box.open = false; input.value = ''; feedback.textContent = '';
        });
        save.onclick = async () => {
            if (!input.value.trim()) { feedback.textContent = 'Ingrese el nombre de la categoría.'; input.focus(); return; }
            save.disabled = true;
            try {
                const category = await post('/api/administration/categories/', { name: input.value.trim() });
                if (![...select.options].some(option => option.value === category.id)) select.append(new Option(category.name, category.id));
                select.value = category.id; select.dispatchEvent(new Event('change'));
                document.dispatchEvent(new CustomEvent('category:created', { detail: category }));
                input.value = ''; feedback.textContent = 'Categoría creada.';
            } catch (error) { feedback.textContent = error.message; }
            finally { save.disabled = false; }
        };
    }
    categoryControls(byId('pieceCreateCategory'));
    categoryControls(byId('newButtonCategory'));
    async function fillCategories(select, selected) {
        const data = await request('/api/piece-library/categories/');
        select.replaceChildren(new Option('Seleccione una categoría',''));
        for (const row of data.categories) select.append(new Option(row.name,row.id));
        select.value=selected || '';
    }
    function draft() {
        const name=byId('pieceCreateName').value.trim(), category=byId('pieceCreateCategory').value;
        if (!name || !category) throw new Error('Indique el nombre y la categoría.');
        return {name,category};
    }
    async function openDraft(result, data) {
        create.addEventListener('hidden.bs.modal', () => window.workbookEditor.open({...result.workbook,context:'piece-import',piece_name:data.name,piece_category_id:data.category}),{once:true});
        createController.hide();
    }
    byId('btnImportMotorRecord').addEventListener('click', async () => {
        byId('pieceCreateName').value=''; byId('pieceCreateStatus').textContent=''; createController.show();
        try { await fillCategories(byId('pieceCreateCategory')); } catch(error) {byId('pieceCreateStatus').textContent=error.message;}
    });
    byId('pieceCreateBlank').onclick=async () => {
        try { const data=draft(); const result=await post('/api/piece-library/import/',{blank:true,category_id:data.category}); await openDraft(result,data); }
        catch(error) {byId('pieceCreateStatus').textContent=error.message;}
    };
    byId('pieceCreateImport').onclick=() => {try {draft();byId('motorRecordFileInput').click();} catch(error) {byId('pieceCreateStatus').textContent=error.message;}};
    byId('motorRecordFileInput').addEventListener('change', async (event) => {
        const file = event.target.files[0];
        event.target.value = '';
        if (!file) return;
        byId('savedMotorsStatus').textContent = 'Abriendo ficha del dispositivo…';
        try {
            const body = new FormData();
            const data = draft(); body.append('category_id',data.category);
            body.append('file', await window.workbookFields.normalizeUpload(file));
            const result = await request('/api/piece-library/import/', { method: 'POST', body });
            await openDraft(result,data);
        } catch (error) { byId('savedMotorsStatus').textContent = byId('pieceCreateStatus').textContent = error.message; }
    });

    const replacementInput=document.createElement('input'); replacementInput.type='file'; replacementInput.accept='.xlsx,.csv'; replacementInput.hidden=true;
    document.body.append(replacementInput);
    let replacementContext=null;
    replacementInput.addEventListener('change',async () => {
        const file=replacementInput.files[0], context=replacementContext; replacementInput.value=''; replacementContext=null;
        const editor=window.workbookEditor;
        if (!file || !context || !editor.visible || editor.epoch !== context.epoch) return;
        if (!(await window.confirmAppAction('Reemplazar ficha',
            `Importar «${file.name}» reemplazará la ficha que está editando y descartará los cambios sin guardar. Al guardar la pieza, solo se conservará la hoja que seleccione.`,
            'Reemplazar ficha', { danger: true }))) return;
        if (!editor.visible || editor.epoch !== context.epoch || editor.busy) return;
        editor.setBusy(true);editor.setStatus('Abriendo el archivo de reemplazo…');
        try {
            const body=new FormData();body.append('category_id',context.category);body.append('file',await window.workbookFields.normalizeUpload(file));
            const result=await request('/api/piece-library/import/',{method:'POST',body});
            if (!editor.visible || editor.epoch !== context.epoch) return;
            editor.close(true);
            await editor.open({...result.workbook,context:'piece-edit',piece_edit:context.edit,piece_name:context.name,piece_category_id:context.category,piece_unpublished:true});
            editor.setStatus('Seleccione la hoja que desea conservar y pulse Guardar pieza. Las demás hojas no se incorporarán al catálogo.');
        } catch(error) {editor.setStatus(error.message,'error');}
        finally {if(editor.epoch===context.epoch) editor.setBusy(false);}
    });

    window.pieceLibrary = {
        registerCurrent, chooseForTarget,
        replaceCurrent(editor) {
            if (!editor.pieceEdit || editor.busy) return;
            replacementContext={epoch:editor.epoch,edit:{...editor.pieceEdit},category:editor.pieceCategoryId,name:editor.pieceDraftName};
            replacementInput.click();
        },
        setTarget(value) { target = value; },
        reset() { epoch++; clearTimeout(refreshTimer); target = null; registerContext = null; replacementContext=null; registerController.hide(); pickerController.hide(); byId('savedMotorsList').replaceChildren(); },
    };
})();
