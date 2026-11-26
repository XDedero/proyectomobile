/* Every administration action is authorized again by the server. */
(() => {
    const $ = id => document.getElementById(id);
    const roles = {viewer: 'Espectador', worker: 'Trabajador', administrator: 'Administrador'};
    const roleDescriptions = {
        viewer: 'Puede consultar e importar planos para verlos. No puede modificarlos ni guardar un historial propio.',
        worker: 'Puede crear y modificar planos, asociar Excel, guardar en Drive y crear categorías. Solo puede eliminar sus propios planos.',
        administrator: 'Puede administrar usuarios y categorías, y gestionar todos los planos. Al recibir este rol debe cerrar sesión y volver a entrar.',
    };
    const state = {users: [], categories: []};
    let userId = null, creatingUser = false, categoryId = null, busy = false, dataRevision = 0;
    let ready = false, refreshingDetails = false;
    const initialControls = [...document.querySelectorAll('main form input, main form select, main form button, #userSearch, #newUser')];
    initialControls.forEach(control => { control.disabled = true; });
    const token = () => decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('csrftoken='))?.slice(10) || '');
    function status(message, error = false) {
        $('managementStatus').textContent = message;
        $('managementStatus').className = error ? 'text-danger' : 'text-success';
    }
    async function request(url, data) {
        const identity = document.documentElement.dataset.sessionUser;
        const options = {credentials: 'same-origin', cache: 'no-store', headers:{}};
        if (identity) options.headers['X-Reloncavi-Expected-User'] = identity;
        if (data !== undefined) {
            options.method = 'POST';
            Object.assign(options.headers, {'X-CSRFToken': token(), 'Content-Type': 'application/json'});
            options.body = JSON.stringify(data);
        }
        const response = await fetch(url, options);
        if (identity && response.headers.has('X-Reloncavi-User') && response.headers.get('X-Reloncavi-User') !== identity) {
            location.replace('/');
            throw new Error('La sesión cambió. Se está abriendo la empresa de la cuenta activa.');
        }
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
            await load();
            status(result?.message || message);
        } catch (error) { status(saved ? `${result?.message || message} No se pudo actualizar el panel: ${error.message}` : error.message, true); }
        finally {
            busy = false;
            document.querySelectorAll('main button, main input, main select').forEach(control => { control.disabled = false; });
            if (!ready) initialControls.forEach(control => { control.disabled = true; });
            applyRolePermissions();
        }
    }
    function action(label, callback, {danger = false} = {}) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = `btn btn-sm btn-outline-${danger ? 'danger' : 'primary'}`;
        button.textContent = label;
        button.onclick = async () => { if (busy) return; try { await callback(); } catch (error) { status(error.message, true); } };
        return button;
    }
    function empty(container, text) {
        const message = document.createElement('p'); message.className = 'empty-state mb-0'; message.textContent = text; container.append(message);
    }
    const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
    function summary() {
        $('managementSummary').replaceChildren();
        for (const [label, count] of [['Usuarios', state.users.length], ['Categorías', state.categories.length]]) {
            const card = document.createElement('div'); card.className = 'summary-item';
            const number = document.createElement('strong'); number.textContent = count;
            const caption = document.createElement('span'); caption.textContent = label;
            card.append(number, caption); $('managementSummary').append(card);
        }
    }
    function renderUsers() {
        const container = $('userRows'); container.replaceChildren();
        const term = normalize($('userSearch').value);
        const items = state.users.filter(user => normalize(`${user.name} ${user.first_name} ${user.last_name} ${user.rut}`).includes(term));
        for (const user of items) {
            const row = document.createElement('div'); row.className = `management-row${user.id === userId ? ' selected' : ''}`;
            const title = document.createElement('strong'); title.textContent = user.name || user.username;
            const badge = document.createElement('span'); badge.className = 'role-badge'; badge.textContent = user.primary_administrator ? 'Administrador principal' : roles[user.role] || 'Espectador';
            const details = document.createElement('p'); details.textContent = `RUT ${user.rut || 'Sin asignar'}${user.rut_is_test ? ' (de prueba)' : ''}`;
            row.append(title, badge, details);
            if (user.can_edit) row.append(action('Editar', () => { editUser(user); renderUsers(); $('userFormTitle').scrollIntoView({behavior: 'smooth', block: 'nearest'}); }));
            container.append(row);
        }
        if (!items.length) empty(container, term ? 'No se encontraron usuarios con esa búsqueda.' : 'No hay usuarios disponibles.');
    }
    function roleHelp() {
        const user = state.users.find(item => item.id === userId);
        $('userRoleHelp').textContent = user?.primary_administrator
            ? 'Puede crear cuentas y administrar otros administradores. Debe conservar su rol.'
            : roleDescriptions[$('userRole').value];
    }
    function applyRolePermissions() {
        const user = state.users.find(item => item.id === userId);
        $('userRole').querySelector('[value="administrator"]').disabled = !state.can_manage_administrators && user?.role !== 'administrator';
        $('userRole').disabled = Boolean(user?.primary_administrator);
    }
    function editUser(user, create = false) {
        if (user && !user.can_edit) user = null;
        creatingUser = create && state.can_create_users;
        userId = user?.id || null;
        const show = Boolean(user || creatingUser);
        $('userForm').reset(); $('userForm').hidden = !show; $('userSelectionHint').hidden = show;
        if (!show) return;
        $('userFormTitle').textContent = creatingUser ? 'Crear usuario' : `Editar ${user.name}`;
        $('userAccountName').textContent = 'La cuenta inicia sesión con su RUT y contraseña.';
        $('userFirstName').value = user?.first_name || ''; $('userLastName').value = user?.last_name || '';
        $('userRut').value = user?.rut || ''; $('userRole').value = user?.role || 'viewer'; roleHelp();
        for (const id of ['userFirstName', 'userLastName', 'userPassword']) $(id).required = creatingUser;
        $('userPasswordLabel').textContent = creatingUser ? 'Contraseña inicial' : 'Nueva contraseña (opcional)';
        $('userPasswordHelp').textContent = creatingUser
            ? 'Usa al menos 8 caracteres y evita claves comunes, solo números o datos personales.'
            : 'Deja este campo vacío para conservar la contraseña.';
        $('saveUser').textContent = creatingUser ? 'Crear cuenta' : 'Guardar cambios';
        $('deleteUser').hidden = !user?.can_delete;
        applyRolePermissions();
    }
    function editCategory(category) {
        categoryId = category?.id || null; $('categoryForm').reset();
        $('categoryFormTitle').textContent = category ? 'Editar categoría' : 'Crear categoría';
        $('categoryName').value = category?.name || '';
        $('categoryMaintenanceRequired').value = category?.maintenance_required === false ? 'no' : 'yes';
    }
    function renderCategories() {
        const container = $('categoryRows'); container.replaceChildren();
        for (const category of state.categories) {
            const row = document.createElement('div'); row.className = 'management-row';
            const title = document.createElement('strong'); title.textContent = category.name;
            const policy = document.createElement('div'); policy.className = 'small text-secondary';
            policy.textContent = category.maintenance_required ? 'Mantención obligatoria' : 'Mantención opcional';
            const info = document.createElement('div'); info.append(title, policy);
            const controls = document.createElement('div'); controls.className = 'form-actions';
            controls.append(action('Editar', () => editCategory(category)), action('Eliminar', async () => {
                if (await confirmAction('¿Seguro que desea eliminar esta categoría?', `Se eliminará «${category.name}».`, 'Eliminar')) {
                    await mutate(() => request('/api/administration/categories/', {id: category.id, action: 'delete'}), 'Categoría eliminada.');
                }
            }, {danger: true})); row.append(info, controls); container.append(row);
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
        state.can_create_users = latest.can_create_users;
        if ($('newUser')) $('newUser').hidden = !state.can_create_users;
        ready = true;
        summary(); renderUsers(); renderCategories();
        const user = state.users.find(item => item.id === userId && item.can_edit);
        if (resetForms || (userId && !user)) editUser(user);
        if (resetForms) editCategory(state.categories.find(category => category.id === categoryId));
        if (!busy) initialControls.forEach(control => { control.disabled = false; });
        applyRolePermissions();
    }
    $('userForm').onsubmit = async event => {
        event.preventDefault(); if (busy || (!userId && !creatingUser)) return;
        const current = state.users.find(user => user.id === userId);
        if (!creatingUser && $('userRole').value === 'administrator' && current?.role !== 'administrator') {
            if (!await confirmAction('¿Seguro que desea otorgar el rol de administrador?', 'El usuario deberá cerrar sesión y volver a entrar para utilizar sus nuevos permisos.', 'Otorgar rol')) return;
        }
        const data = {id: userId, action: creatingUser ? 'create' : 'update', first_name: $('userFirstName').value.trim(), last_name: $('userLastName').value.trim(), rut: $('userRut').value.trim(), role: $('userRole').value};
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
            const result = await request('/api/administration/categories/', {
                id: categoryId, name: $('categoryName').value, maintenance_required: $('categoryMaintenanceRequired').value === 'yes',
            });
            categoryId = result.id;
        }, 'Categoría guardada.');
    };
    $('userSearch').oninput = renderUsers; $('userRole').onchange = roleHelp;
    $('newUser')?.addEventListener('click', () => { editUser(null, true); renderUsers(); $('userFirstName').focus(); });
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
    function resume() {
        if (!ready || !$('usersPanel').hidden) refreshDetails();
    }
    window.addEventListener('focus', resume);
    window.addEventListener('online', resume);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) resume(); });
    resume();
})();
