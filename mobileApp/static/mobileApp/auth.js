/* Authentication and capabilities always come from Django, never from form rules. */
(() => {
    const loginButton = document.getElementById('btnLogin');
    const alert = document.getElementById('loginAlert');
    let authRevision = 0;

    async function authRequest(url, data) {
        const options = { credentials: 'same-origin', cache: 'no-store' };
        if (data !== undefined) {
            options.method = 'POST';
            options.headers = { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() };
            options.body = JSON.stringify(data);
        }
        const response = await fetch(url, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || 'No se pudo completar el acceso. Recargue la página e intente nuevamente.');
        return result;
    }

    function showLoginError(message) {
        document.getElementById('loginAlertText').textContent = message;
        alert.classList.remove('d-none');
    }

    function applyUser(user) {
        const previousId = appState.user?.id;
        if (previousId && previousId !== user?.id) {
            window.planWorkflow?.close?.();
            window.pdfWorkflow?.reset?.();
            window.projectWorkspace?.reset();
            window.motorLibrary?.reset();
        }
        appState.user = user;
        const permissions = user?.permissions || {};
        const canManagePlan = permissions.can_change_plan || permissions.can_change_pdf_plan
            || permissions.can_save_plan || permissions.can_save_pdf_plan
            || permissions.can_delete_plan || permissions.can_delete_pdf_plan;
        const role = permissions.can_administer ? 'Administrador' : permissions.can_change_excel ? 'Editor de Excel' : canManagePlan ? 'Gestor de planos' : 'Consulta';
        document.getElementById('dropdownUserName').textContent = user?.username || '';
        document.getElementById('dropdownUserRole').textContent = role;
        document.getElementById('welcomeUserText').textContent = user ? `Bienvenido, ${user.display_name}` : '';
        const permissionsSummary = [];
        if (permissions.can_import) permissionsSummary.push('importar planos');
        if (permissions.can_administer) {
            document.getElementById('permissionSummary').textContent = 'Puede administrar la aplicación y gestionar planos y motores.';
        } else if (!permissions.can_change_excel && !permissions.can_save_plan && !permissions.can_save_pdf_plan
            && !permissions.can_change_plan && !permissions.can_change_pdf_plan
            && !permissions.can_delete_plan && !permissions.can_delete_pdf_plan) {
            document.getElementById('permissionSummary').textContent = 'Puede importar planos y verlos junto con los Excel de sus motores.';
        } else {
            if (permissions.can_import) permissionsSummary.push('importar planos');
            if (permissions.can_assign && permissions.can_change_excel) permissionsSummary.push('asignar y editar Excel');
            else if (permissions.can_assign) permissionsSummary.push('agregar botones y asignar Excel');
            else if (permissions.can_change_excel) permissionsSummary.push('editar Excel');
            const canChangePlan = permissions.can_change_plan || permissions.can_change_pdf_plan;
            const canSavePlan = permissions.can_save_plan || permissions.can_save_pdf_plan;
            if (canChangePlan && canSavePlan) permissionsSummary.push('modificar y guardar planos');
            else if (canChangePlan) permissionsSummary.push('modificar planos');
            else if (canSavePlan) permissionsSummary.push('guardar planos');
            if (permissions.can_delete_plan || permissions.can_delete_pdf_plan) permissionsSummary.push('eliminar planos');
            document.getElementById('permissionSummary').textContent = permissionsSummary.length
                ? `Puede ${permissionsSummary.join(', ')}.` : '';
        }
        btnGoToImportSection.hidden = !permissions.can_view;
        menuItemPlanos.hidden = !permissions.can_view;
        document.getElementById('btnAdminister').hidden = !permissions.can_administer;
        document.getElementById('menuItemAdminister').hidden = !permissions.can_administer;
        window.planWorkflow?.permissionsChanged?.();
        window.pdfWorkflow?.permissionsChanged?.();
        document.getElementById('appAuthError').textContent = user && !window.planWorkflow
            ? 'La sesión está iniciada, pero no se pudo cargar el visor de planos. Recargue la página para volver a cargarlo.'
            : '';
        if (!user) {
            modalSavedPlans.hide();
            document.getElementById('savedPlansContainer').replaceChildren();
            loginForm.reset();
            switchView(viewLogin);
        } else if (previousId !== user.id || viewLogin.classList.contains('active')) {
            switchView(viewMainMenu);
            if (permissions.can_view) window.planWorkflow?.restore?.();
        }
    }

    function expire() {
        authRevision += 1;
        applyUser(null);
        showLoginError('Su sesión expiró. Inicie sesión nuevamente.');
    }

    async function refresh() {
        const revision = ++authRevision;
        const result = await authRequest('/api/auth/session/');
        if (revision === authRevision) applyUser(result.user);
    }

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const username = document.getElementById('usernameInput').value.trim();
        const password = document.getElementById('passwordInput').value;
        if (!username || !password) { showLoginError('Ingrese su nombre de usuario y contraseña.'); return; }
        loginButton.disabled = true;
        alert.classList.add('d-none');
        const revision = ++authRevision;
        try {
            const result = await authRequest('/api/auth/login/', { username, password });
            if (revision === authRevision) {
                document.getElementById('passwordInput').value = '';
                applyUser(result.user);
            }
        } catch (error) { showLoginError(error.message); }
        finally { loginButton.disabled = false; }
    });

    menuItemLogout.addEventListener('click', async (event) => {
        event.preventDefault();
        if (window.pdfWorkflow?.canLeave && !(await window.pdfWorkflow.canLeave())) return;
        const revision = ++authRevision;
        try {
            await authRequest('/api/auth/logout/', {});
            if (revision === authRevision) applyUser(null);
        } catch (error) {
            document.getElementById('appAuthError').textContent = `No se pudo cerrar la sesión: ${error.message}`;
        }
    });

    window.appAuth = { refresh, expire };
    loginButton.disabled = true;
    refresh().catch(() => showLoginError('No se pudo abrir el almacenamiento local de la aplicación. Cierre y vuelva a abrir la app.'))
        .finally(() => { appState.authReady = true; loginButton.disabled = false; });
})();
