/* Every administration action is authorized again by the server. */
(() => {
    const $ = id => document.getElementById(id);
    const roles = {viewer: 'Espectador', worker: 'Trabajador', administrator: 'Administrador'};
    const roleDescriptions = {
        viewer: 'Puede consultar e importar planos para verlos. No puede modificarlos ni guardar un historial propio.',
        worker: 'Puede crear y modificar planos, asociar Excel, guardar en Drive y crear categorías. Solo puede eliminar sus propios planos.',
        administrator: 'Puede administrar usuarios y categorías, y gestionar todos los planos. Al recibir este rol debe cerrar sesión y volver a entrar.',
    };
    const initialRequests = JSON.parse($('initialRegistrationRequests').textContent);
    const state = {users: [], requests: initialRequests, categories: []};
    let userId = null, categoryId = null, busy = false, dataRevision = 0;
    let ready = false, refreshingRequests = false, refreshingDetails = false;
    const requestRoles = new Map();
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
            options.method = 'POST';
            options.headers = {'X-CSRFToken': token(), 'Content-Type': 'application/json'};
            options.body = JSON.stringify(data);
        }
        const response = await fetch(url, options);
        const result = await response.json().catch(() => null);
        if (!response.ok || !result) throw new Error(result?.error || 'No se pudo completar la operación. Recarga el panel e intenta otra vez.');
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
        dataRevision += 1;
        let saved = false, result;
        document.querySelectorAll('main button, main input, main select').forEach(control => { control.disabled = true; });
        try {
            result = await operation(); saved = true;
            const updates = await Promise.allSettled([load(), loadRequests()]);
            const failed = updates.find(update => update.status === 'rejected');
            if (failed) throw failed.reason;
            status(result?.message || message);
        } catch (error) { status(saved ? `${result?.message || message} No se pudo actualizar el panel: ${error.message}` : error.message, true); }
        finally {
            busy = false;
            document.querySelectorAll('main button, main input, main select').forEach(control => { control.disabled = false; });
            if (!ready) initialControls.forEach(control => { control.disabled = true; });
            $('refreshRequests').disabled = refreshingRequests;
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
        for (const id of requestRoles.keys()) if (!state.requests.some(item => item.id === id)) requestRoles.delete(id);
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
            const label = document.createElement('label'); label.className = 'request-role'; label.textContent = 'Rol de la cuenta';
            const select = document.createElement('select'); select.className = 'form-select form-select-sm';
            select.dataset.requestRole = item.id;
            for (const [value, name] of Object.entries(roles)) {
                const option = document.createElement('option'); option.value = value; option.textContent = name; select.append(option);
            }
            select.value = requestRoles.get(item.id) || 'viewer';
            select.onchange = () => requestRoles.set(item.id, select.value);
            label.append(select); controls.append(label);
            controls.append(action('Aceptar', async () => {
                const role = select.value;
                if (role === 'administrator' && !await confirmAction('¿Seguro que desea otorgar el rol de administrador?', `${item.name} podrá gestionar solicitudes, usuarios y categorías.`, 'Aceptar y otorgar rol')) return;
                await mutate(() => request('/api/administration/requests/', {id: item.id, action: 'approve', role}));
            }, {decision: 'accept', dataAction: 'approve'}));
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
            ? 'Puede modificar y eliminar otros administradores. Debe conservar su rol. Todos los administradores pueden aprobar solicitudes y otorgar roles.'
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
        $('categoryName').value = category?.name || '';
    }
    function renderCategories() {
        const container = $('categoryRows'); container.replaceChildren();
        for (const category of state.categories) {
            const row = document.createElement('div'); row.className = 'management-row';
            const title = document.createElement('strong'); title.textContent = category.name;
            const controls = document.createElement('div'); controls.className = 'form-actions';
            controls.append(action('Editar', () => editCategory(category)), action('Eliminar', async () => {
                if (await confirmAction('¿Seguro que desea eliminar esta categoría?', `Se eliminará «${category.name}».`, 'Eliminar')) {
                    await mutate(() => request('/api/administration/categories/', {id: category.id, action: 'delete'}), 'Categoría eliminada.');
                }
            }, {danger: true})); row.append(title, controls); container.append(row);
        }
        if (!state.categories.length) empty(container, 'Crea la primera categoría para comenzar.');
    }
    async function load({resetForms = true} = {}) {
        const revision = dataRevision;
        const latest = await request('/api/administration/');
        if (revision !== dataRevision) return;
        state.users = latest.users;
        state.categories = latest.categories;
        state.can_manage_administrators = latest.can_manage_administrators;
        ready = true;
        summary(); renderUsers(); renderCategories();
        const user = state.users.find(item => item.id === userId && item.can_edit);
        if (resetForms || (userId && !user)) editUser(user);
        if (resetForms) editCategory(state.categories.find(category => category.id === categoryId));
        if (!busy) initialControls.forEach(control => { control.disabled = false; });
        applyRolePermissions();
    }
    async function loadRequests() {
        const revision = dataRevision;
        try {
            const {requests} = await request('/api/administration/requests/pending/');
            if (revision !== dataRevision) return;
            if (!Array.isArray(requests)) throw new Error('La respuesta del servidor no incluye la lista de solicitudes.');
            if (JSON.stringify(state.requests) !== JSON.stringify(requests)) {
                state.requests = requests;
                summary(); renderRequests();
            }
            $('requestRefreshStatus').textContent = 'Solicitudes actualizadas. Se comprueban automáticamente cada 5 segundos.';
        } catch (error) {
            if (revision !== dataRevision) return;
            $('requestRefreshStatus').textContent = `No se pudieron actualizar las solicitudes: ${error.message} Pulsa Actualizar solicitudes para reintentar.`;
            throw error;
        }
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
            const result = await request('/api/administration/categories/', {id: categoryId, name: $('categoryName').value});
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
            refreshDetails();
        }
        if (button.dataset.tab === 'requests') refreshRequests();
        if (button.dataset.tab === 'categories' && !ready) refreshDetails();
    };
    async function refreshDetails() {
        if (busy || refreshingDetails) return;
        refreshingDetails = true;
        const revision = dataRevision;
        try {
            await load({resetForms: !ready});
            if (revision === dataRevision) status('');
        } catch (error) {
            if (revision === dataRevision) status(`No se pudieron actualizar los usuarios y las categorías: ${error.message}`, true);
        } finally {
            refreshingDetails = false;
        }
    }
    async function refreshRequests() {
        if (busy || refreshingRequests || $('managementConfirm').open) return;
        refreshingRequests = true;
        $('refreshRequests').disabled = true;
        try {
            await loadRequests();
        } catch {
            // loadRequests leaves the last confirmed list visible and reports the error.
        } finally {
            refreshingRequests = false;
            $('refreshRequests').disabled = busy;
        }
    }
    function resume() {
        refreshRequests();
        if (!ready || !$('usersPanel').hidden) refreshDetails();
    }
    $('refreshRequests').onclick = refreshRequests;
    window.addEventListener('focus', resume);
    window.addEventListener('online', resume);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) resume(); });
    setInterval(() => { if (!document.hidden) refreshRequests(); }, 5000);
    summary(); renderRequests();
    resume();
})();
