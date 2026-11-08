/* The server validates administrator access on every request. */
(() => {
    const $ = id => document.getElementById(id);
    let state, roleId = null, groupId = null, categoryId = null;
    const initialControls = [...document.querySelectorAll('form input, form button')];
    initialControls.forEach(control => { control.disabled = true; });
    const token = () => decodeURIComponent(document.cookie.split('; ').find(x => x.startsWith('csrftoken='))?.slice(10) || '');
    function status(message, error = false) { $('managementStatus').textContent = message; $('managementStatus').className = error ? 'text-danger' : 'text-success'; }
    async function request(url, data) {
        const options = {credentials: 'same-origin', cache: 'no-store'};
        if (data !== undefined) {
            options.method = 'POST'; options.headers = {'X-CSRFToken': token()};
            if (data instanceof FormData) options.body = data;
            else { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data); }
        }
        const response = await fetch(url, options);
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'No se pudo guardar el cambio.');
        return result;
    }
    function checks(id, items, selected = [], label = item => item.name) {
        $(id).replaceChildren();
        for (const item of items) {
            const row = document.createElement('label'); row.className = 'permission-option';
            const input = document.createElement('input'); input.type = 'checkbox'; input.value = item.id; input.checked = selected.includes(item.id);
            row.append(input, document.createTextNode(label(item))); $(id).append(row);
        }
        if (!items.length) $(id).textContent = 'Todavía no hay elementos disponibles.';
    }
    const selected = (id, numeric = true) => [...$(id).querySelectorAll('input:checked')].map(input => numeric ? Number(input.value) : input.value);
    function action(text, callback, danger = false) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = text;
        button.className = `btn btn-sm me-2 btn-outline-${danger ? 'danger' : 'primary'}`;
        button.onclick = async () => { try { await callback(); } catch (error) { status(error.message, true); } };
        return button;
    }
    function rows(id, items, detail, edit, remove) {
        $(id).replaceChildren();
        for (const item of items) {
            const row = document.createElement('div'); row.className = 'management-row';
            const title = document.createElement('strong'); title.textContent = item.name;
            const hint = document.createElement('p'); hint.textContent = detail(item);
            row.append(title, hint, action('Editar', () => edit(item)), action('Eliminar', async () => {
                if (!confirm(`¿Eliminar «${item.name}»?`)) return;
                await remove(item); await load(); status('Elemento eliminado.');
            }, true)); $(id).append(row);
        }
        if (!items.length) $(id).textContent = 'Crea el primer elemento para comenzar.';
    }
    const userLabel = user => `${user.name}${user.administrator ? ' · Administrador' : ''}`;
    function editRole(role) {
        roleId = role?.id || null; $('roleForm').reset(); $('roleFormTitle').textContent = role ? 'Editar rol' : 'Crear rol';
        $('roleName').value = role?.name || ''; $('roleDescription').value = role?.description || '';
        for (const [id, field] of [['roleView','can_view'], ['roleExport','can_export'], ['roleCreate','can_create'], ['roleAll','all_plans']]) $(id).checked = role ? role[field] : true;
        $('rolePlanSelection').hidden = $('roleAll').checked;
        checks('roleUsers', state.users, role?.users, userLabel);
        checks('rolePlans', state.plans, role?.plans, plan => `${plan.name} · ${plan.kind}`);
    }
    function editGroup(group) {
        groupId = group?.id || null; $('groupForm').reset(); $('groupFormTitle').textContent = group ? 'Editar grupo' : 'Crear grupo';
        $('groupName').value = group?.name || '';
        checks('groupRoles', state.roles, group?.roles); checks('groupUsers', state.users, group?.users, userLabel);
    }
    function editCategory(category) {
        categoryId = category?.id || null; $('categoryForm').reset(); $('categoryFormTitle').textContent = category ? 'Editar categoría' : 'Crear categoría';
        $('categoryName').value = category?.name || ''; $('categoryTemplates').replaceChildren();
        for (const item of category?.templates || []) {
            const row = document.createElement('div'); row.className = 'management-row mb-2';
            const name = document.createElement('p'); name.textContent = `${item.name} · Hoja: ${item.sheet}`;
            row.append(name, action('Quitar Excel', async () => {
                if (!confirm(`¿Quitar «${item.name}» de esta categoría? Los botones existentes conservarán su copia.`)) return;
                await request('/api/administration/categories/', {id: categoryId, action: 'delete_template', template_id: item.id});
                await load(); status('Excel retirado de la categoría.');
            }, true)); $('categoryTemplates').append(row);
        }
    }
    async function load() {
        state = await request('/api/administration/'); $('managementSummary').replaceChildren();
        for (const [label, count] of [['Usuarios', state.users.length], ['Roles', state.roles.length], ['Grupos', state.groups.length], ['Categorías', state.categories.length]]) {
            const card = document.createElement('div'); card.className = 'summary-item';
            const number = document.createElement('strong'); number.textContent = count;
            const text = document.createElement('span'); text.textContent = label; card.append(number, text); $('managementSummary').append(card);
        }
        rows('roleRows', state.roles, role => `${role.all_plans ? 'Todos los planos' : role.plans.length + ' planos'} · ${role.users.length} usuarios directos${role.description ? ' · ' + role.description : ''}`, editRole,
            role => request('/api/administration/roles/', {id: role.id, action: 'delete'}));
        rows('groupRows', state.groups, group => `${group.roles.length} roles · ${group.users.length} miembros`, editGroup,
            group => request('/api/administration/groups/', {id: group.id, action: 'delete'}));
        rows('categoryRows', state.categories, category => `${category.templates.length} Excel permanentes`, editCategory,
            category => request('/api/administration/categories/', {id: category.id, action: 'delete'}));
        editRole(state.roles.find(item => item.id === roleId)); editGroup(state.groups.find(item => item.id === groupId)); editCategory(state.categories.find(item => item.id === categoryId));
    }
    async function save(form, operation) {
        const buttons = [...form.querySelectorAll('button')]; buttons.forEach(button => button.disabled = true);
        try { await operation(); await load(); status('Cambios guardados.'); }
        catch (error) { status(error.message, true); }
        finally { buttons.forEach(button => button.disabled = false); }
    }
    $('roleForm').onsubmit = event => { event.preventDefault(); save(event.target, async () => {
        const result = await request('/api/administration/roles/', {id: roleId, name: $('roleName').value, description: $('roleDescription').value,
            can_view: $('roleView').checked, can_export: $('roleExport').checked, can_create: $('roleCreate').checked, all_plans: $('roleAll').checked,
            users: selected('roleUsers'), plans: selected('rolePlans', false)}); roleId = result.id;
    }); };
    $('groupForm').onsubmit = event => { event.preventDefault(); save(event.target, async () => {
        const result = await request('/api/administration/groups/', {id: groupId, name: $('groupName').value, roles: selected('groupRoles'), users: selected('groupUsers')}); groupId = result.id;
    }); };
    $('categoryForm').onsubmit = event => { event.preventDefault(); save(event.target, async () => {
        const data = new FormData(); if (categoryId) data.set('id', categoryId);
        data.set('name', $('categoryName').value); data.set('sheet', $('categorySheet').value);
        const file = $('categoryFile').files[0]; if (file) data.set('file', file);
        const result = await request('/api/administration/categories/', file ? data : {id: categoryId, name: $('categoryName').value}); categoryId = result.id;
    }); };
    $('roleAll').onchange = () => { $('rolePlanSelection').hidden = $('roleAll').checked; };
    for (const id of ['newRole', 'cancelRole']) $(id).onclick = () => editRole();
    for (const id of ['newGroup', 'cancelGroup']) $(id).onclick = () => editGroup();
    for (const id of ['newCategory', 'cancelCategory']) $(id).onclick = () => editCategory();
    for (const button of document.querySelectorAll('[data-tab]')) button.onclick = () => {
        for (const item of document.querySelectorAll('[data-tab]')) { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', active); $(item.dataset.tab + 'Panel').hidden = !active; }
    };
    status('Cargando administración…');
    load().then(() => { initialControls.forEach(control => { control.disabled = false; }); status(''); })
        .catch(error => status(error.message, true));
})();
