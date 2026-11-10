/* Every administration action is authorized again by the server. */
(() => {
    const $ = id => document.getElementById(id);
    const roles = {viewer: 'Espectador', worker: 'Trabajador', administrator: 'Administrador'};
    const roleDescriptions = {
        viewer: 'Puede consultar e importar planos para verlos. No puede modificarlos ni guardar un historial propio.',
        worker: 'Puede crear y modificar planos, asociar Excel, guardar en Drive y crear categorías. Solo puede eliminar sus propios planos.',
        administrator: 'Puede administrar usuarios y categorías, y gestionar todos los planos. Al recibir este rol debe cerrar sesión y volver a entrar.',
    };
    let state = {users: [], requests: [], categories: []}, userId = null, categoryId = null, busy = false, dataRevision = 0;
    const initialControls = [...document.querySelectorAll('main form input, main form select, main form button, #userSearch')];
    initialControls.forEach(control => { control.disabled = true; });
    const token = () => decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('csrftoken='))?.slice(10) || '');
    function status(message, error = false) {
        $('managementStatus').textContent = message;
        $('managementStatus').className = error ? 'text-danger' : 'text-success';
    }
    async function request(url, data) {
        const options = {credentials: 'same-origin', cache: 'no-store'};
        if (data !== undefined) {
            options.method = 'POST'; options.headers = {'X-CSRFToken': token()};
            if (data instanceof FormData) options.body = data;
            else { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data); }
        }
        const response = await fetch(url, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación. Recarga el panel e intenta otra vez.');
        return result;
    }
    function confirmAction(title, description = '', label = 'Aceptar') {
        const dialog = $('managementConfirm');
        if (dialog.open) return Promise.resolve(false);
        $('managementConfirmTitle').textContent = title;
        $('managementConfirmDescription').textContent = description;
        $('confirmManagementLabel').textContent = label;
        dialog.returnValue = '';
        dialog.showModal();
        return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'accept'), {once: true}));
    }
    async function mutate(operation, message = 'Cambios guardados.') {
        if (busy) return;
        busy = true;
        let saved = false, result;
        document.querySelectorAll('main button, main input, main select').forEach(control => { control.disabled = true; });
        try {
            result = await operation(); saved = true;
            await load();
            status(result?.message || message);
        } catch (error) { status(saved ? `${result?.message || message} No se pudo actualizar el panel: ${error.message}` : error.message, true); }
        finally {
            busy = false;
            document.querySelectorAll('main button, main input, main select').forEach(control => { control.disabled = false; });
            applyRolePermissions();
        }
    }
    function action(label, callback, {danger = false, decision, dataAction} = {}) {
        const button = document.createElement('button'); button.type = 'button';
        if (decision) {
            button.className = `decision-button decision-${decision}`;
            const icon = document.createElement('i'); icon.className = `bi bi-${decision === 'accept' ? 'check-lg' : 'x-lg'}`; icon.setAttribute('aria-hidden', 'true');
            button.append(icon, document.createTextNode(label));
        } else { button.className = `btn btn-sm btn-outline-${danger ? 'danger' : 'primary'}`; button.textContent = label; }
        if (dataAction) button.dataset.action = dataAction;
        button.onclick = async () => { if (busy) return; try { await callback(); } catch (error) { status(error.message, true); } };
        return button;
    }
    function empty(container, text) {
        const message = document.createElement('p'); message.className = 'empty-state mb-0'; message.textContent = text; container.append(message);
    }
    const date = value => value ? new Date(value).toLocaleString('es-CL', {dateStyle: 'short', timeStyle: 'short'}) : '';
    const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    const hasLegacyUsername = user => !/^cuenta_[a-f0-9]{32}$/.test(user.username || '');
    function summary() {
        const pending = state.requests.filter(item => item.status === 'pending').length;
        $('managementSummary').replaceChildren();
        for (const [label, count] of [['Solicitudes pendientes', pending], ['Usuarios', state.users.length], ['Categorías', state.categories.length]]) {
            const card = document.createElement('div'); card.className = 'summary-item';
            const number = document.createElement('strong'); number.textContent = count;
            const caption = document.createElement('span'); caption.textContent = label;
            card.append(number, caption); $('managementSummary').append(card);
        }
        $('pendingCount').textContent = pending; $('pendingCount').hidden = !pending;
    }
    function renderRequests() {
        const container = $('requestRows'); container.replaceChildren();
        for (const item of state.requests) {
            const row = document.createElement('article'); row.className = 'management-row request-row';
            const copy = document.createElement('div'); copy.className = 'request-copy';
            const title = document.createElement('strong'); title.textContent = item.name || `${item.first_name} ${item.last_name}`;
            const details = document.createElement('p'); details.textContent = `RUT ${item.rut} · Solicitado ${date(item.created_at)}`;
            const badge = document.createElement('span'); badge.className = 'request-state pending';
            badge.textContent = 'Pendiente';
            copy.append(title, details, badge);
            row.append(copy);
            const controls = document.createElement('div'); controls.className = 'request-actions';
            controls.append(action('Aceptar', () => mutate(() => request('/api/administration/requests/', {id: item.id, action: 'approve'}), 'Solicitud aceptada. La cuenta ya puede iniciar sesión como espectador.'), {decision: 'accept', dataAction: 'approve'}));
            controls.append(action('Eliminar', async () => {
                if (await confirmAction('¿Seguro que desea eliminar esta solicitud?', 'La solicitud se eliminará definitivamente.', 'Eliminar')) {
                    await mutate(() => request('/api/administration/requests/', {id: item.id, action: 'reject'}), 'Solicitud eliminada.');
                }
            }, {decision: 'reject', dataAction: 'reject'}));
            row.append(controls);
            container.append(row);
        }
        if (!state.requests.length) empty(container, 'No hay solicitudes pendientes.');
    }
    function renderUsers() {
        const container = $('userRows'); container.replaceChildren();
        const term = normalize($('userSearch').value);
        const items = state.users.filter(user => normalize(`${user.name} ${user.first_name} ${user.last_name} ${user.rut} ${user.username}`).includes(term));
        for (const user of items) {
            const row = document.createElement('div'); row.className = `management-row${user.id === userId ? ' selected' : ''}`;
            const title = document.createElement('strong'); title.textContent = user.name || user.username;
            const badge = document.createElement('span'); badge.className = 'role-badge'; badge.textContent = user.primary_administrator ? 'Administrador principal' : roles[user.role] || 'Espectador';
            const details = document.createElement('p'); details.textContent = `RUT ${user.rut || 'Sin asignar'}${user.rut_is_test ? ' (de prueba)' : ''}${hasLegacyUsername(user) ? ' · ' + user.username : ''}`;
            row.append(title, badge, details);
            if (user.can_edit) row.append(action('Editar', () => { editUser(user); renderUsers(); $('userFormTitle').scrollIntoView({behavior: 'smooth', block: 'nearest'}); }));
            container.append(row);
        }
        if (!items.length) empty(container, term ? 'No se encontraron usuarios con esa búsqueda.' : 'No hay usuarios disponibles.');
    }
    function roleHelp() {
        const user = state.users.find(item => item.id === userId);
        $('userRoleHelp').textContent = user?.primary_administrator
            ? 'Es la única cuenta que puede crear, modificar y eliminar otros administradores. Debe conservar su rol.'
            : roleDescriptions[$('userRole').value];
    }
    function applyRolePermissions() {
        const user = state.users.find(item => item.id === userId);
        $('userRole').querySelector('[value="administrator"]').disabled = !state.can_manage_administrators && user?.role !== 'administrator';
        $('userRole').disabled = Boolean(user?.primary_administrator);
    }
    function editUser(user) {
        if (user && !user.can_edit) user = null;
        userId = user?.id || null;
        $('userForm').reset(); $('userForm').hidden = !user; $('userSelectionHint').hidden = Boolean(user);
        if (!user) return;
        $('userFormTitle').textContent = `Editar ${user.name || user.username}`;
        $('userAccountName').textContent = hasLegacyUsername(user) ? `Usuario de acceso: ${user.username}. También puede ingresar con su RUT.` : 'Inicia sesión con su RUT y contraseña.';
        $('userFirstName').value = user.first_name || ''; $('userLastName').value = user.last_name || '';
        $('userRut').value = user.rut || ''; $('userRole').value = user.role || 'viewer'; roleHelp();
        $('deleteUser').hidden = !user.can_delete;
        applyRolePermissions();
    }
    function editCategory(category) {
        categoryId = category?.id || null; $('categoryForm').reset();
        $('categoryFormTitle').textContent = category ? 'Editar categoría' : 'Crear categoría';
        $('categoryName').value = category?.name || ''; $('categoryTemplates').replaceChildren();
        for (const item of category?.templates || []) {
            const row = document.createElement('div'); row.className = 'management-row mb-2';
            const name = document.createElement('p'); name.textContent = `${item.name} · Hoja: ${item.sheet}`;
            row.append(name, action('Quitar Excel', async () => {
                if (await confirmAction('¿Seguro que desea quitar este Excel?', `Los botones existentes conservarán su copia de «${item.name}».`, 'Quitar')) {
                    await mutate(() => request('/api/administration/categories/', {id: categoryId, action: 'delete_template', template_id: item.id}), 'Excel retirado de la categoría.');
                }
            }, {danger: true})); $('categoryTemplates').append(row);
        }
    }
    function renderCategories() {
        const container = $('categoryRows'); container.replaceChildren();
        for (const category of state.categories) {
            const row = document.createElement('div'); row.className = 'management-row';
            const title = document.createElement('strong'); title.textContent = category.name;
            const detail = document.createElement('p'); detail.textContent = `${category.templates.length} Excel permanentes`;
            const controls = document.createElement('div'); controls.className = 'form-actions';
            controls.append(action('Editar', () => editCategory(category)), action('Eliminar', async () => {
                if (await confirmAction('¿Seguro que desea eliminar esta categoría?', `Se eliminará «${category.name}».`, 'Eliminar')) {
                    await mutate(() => request('/api/administration/categories/', {id: category.id, action: 'delete'}), 'Categoría eliminada.');
                }
            }, {danger: true})); row.append(title, detail, controls); container.append(row);
        }
        if (!state.categories.length) empty(container, 'Crea la primera categoría para comenzar.');
    }
    async function load() {
        dataRevision += 1;
        state = await request('/api/administration/');
        summary(); renderRequests(); renderUsers(); renderCategories();
        editUser(state.users.find(user => user.id === userId));
        editCategory(state.categories.find(category => category.id === categoryId));
    }
    $('userForm').onsubmit = async event => {
        event.preventDefault(); if (busy || !userId) return;
        const current = state.users.find(user => user.id === userId);
        if ($('userRole').value === 'administrator' && current?.role !== 'administrator') {
            if (!await confirmAction('¿Seguro que desea otorgar el rol de administrador?', 'El usuario deberá cerrar sesión y volver a entrar para utilizar sus nuevos permisos.', 'Otorgar rol')) return;
        }
        const data = {id: userId, action: 'update', first_name: $('userFirstName').value.trim(), last_name: $('userLastName').value.trim(), rut: $('userRut').value.trim(), role: $('userRole').value};
        if ($('userPassword').value) data.password = $('userPassword').value;
        await mutate(() => request('/api/administration/users/', data));
    };
    $('deleteUser').onclick = async () => {
        const user = state.users.find(item => item.id === userId); if (!user || busy) return;
        if (await confirmAction('¿Seguro que desea eliminar este usuario?', `La cuenta de ${user.name || user.username} dejará de tener acceso a la aplicación.`, 'Eliminar')) {
            await mutate(async () => { const result = await request('/api/administration/users/', {id: userId, action: 'delete'}); userId = null; return result; }, 'Usuario eliminado.');
        }
    };
    $('categoryForm').onsubmit = event => {
        event.preventDefault();
        mutate(async () => {
            const data = new FormData(); if (categoryId) data.set('id', categoryId);
            data.set('name', $('categoryName').value); data.set('sheet', $('categorySheet').value);
            const file = $('categoryFile').files[0]; if (file) data.set('file', file);
            const result = await request('/api/administration/categories/', file ? data : {id: categoryId, name: $('categoryName').value});
            categoryId = result.id;
        }, 'Categoría guardada.');
    };
    $('userSearch').oninput = renderUsers; $('userRole').onchange = roleHelp;
    $('cancelUser').onclick = () => { editUser(); renderUsers(); };
    for (const id of ['newCategory', 'cancelCategory']) $(id).onclick = () => editCategory();
    for (const button of document.querySelectorAll('[data-tab]')) button.onclick = () => {
        for (const item of document.querySelectorAll('[data-tab]')) {
            const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', active); $(item.dataset.tab + 'Panel').hidden = !active;
        }
        if (button.dataset.tab === 'users') {
            $('userSearch').value = '';
            renderUsers();
            refreshLists().catch(error => status(error.message, true));
        }
    };
    status('Cargando administración…');
    load().then(() => { initialControls.forEach(control => { control.disabled = false; }); applyRolePermissions(); status(''); }).catch(error => status(error.message, true));
    // Refresh lists without replacing unsaved user or category forms.
    async function refreshLists() {
        if (busy) return;
        const revision = dataRevision;
        const latest = await request('/api/administration/');
        if (busy || revision !== dataRevision) return;
        state.users = latest.users; state.requests = latest.requests;
        if (userId && !state.users.some(user => user.id === userId && user.can_edit)) editUser();
        summary(); renderUsers(); renderRequests();
    }
    setInterval(() => {
        if (document.hidden || $('managementConfirm').open) return;
        refreshLists().catch(() => { /* The next explicit action displays any connection error. */ });
    }, 15000);
})();
