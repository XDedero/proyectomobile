/* Authentication and capabilities always come from Django, never from form rules. */
(() => {
    const loginButton = document.getElementById('btnLogin');
    const alert = document.getElementById('loginAlert');
    const registrationPanel = document.getElementById('registrationPanel');
    const registrationForm = document.getElementById('registrationForm');
    const registrationStatus = document.getElementById('registrationStatus');
    const registrationButton = document.getElementById('btnSubmitRegistration');
    const registrationCompany = document.getElementById('registrationCompany');
    const requestRegistrationButton = document.getElementById('btnRequestRegistration');
    let authRevision = 0;
    let registrationRevision = 0;
    let registrationBusy = false;
    let authBusy = false;
    let refreshFlight = null;
    let refreshTimer = null;
    const sessionChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('planos-auth-session') : null;

    function announceSessionChange() {
        sessionChannel?.postMessage('changed');
        try { localStorage.setItem('planos.auth.change', String(Date.now()) + Math.random()); }
        catch (_) { /* Returning to a tab also verifies its current session. */ }
    }

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
            window.planVersions?.reset();
            window.projectWorkspace?.reset();
            window.pieceLibrary?.reset();
        }
        appState.user = user;
        const permissions = user?.permissions || {};
        const role = permissions.can_administer ? (user?.primary_administrator ? 'Administrador principal' : 'Administrador') : user?.role === 'worker' ? 'Trabajador' : 'Espectador';
        document.getElementById('dropdownUserName').textContent = user?.display_name || user?.rut || '';
        document.getElementById('dropdownUserRole').textContent = role;
        document.getElementById('welcomeUserText').textContent = user ? `Bienvenido, ${user.display_name}` : '';
        document.getElementById('permissionSummary').textContent = permissions.can_administer
            ? 'Puede gestionar solicitudes, usuarios, categorías y todos los planos.'
            : user?.role === 'worker'
                ? 'Puede crear y modificar planos, asociar Excel y guardar su trabajo. Consulte sus planos en el historial.'
                : 'Puede consultar los planos guardados e importar planos para verlos.';
        btnGoToImportSection.hidden = !permissions.can_view;
        menuItemPlanos.hidden = !permissions.can_view;
        document.getElementById('btnAdminister').hidden = !permissions.can_administer;
        document.getElementById('menuItemAdminister').hidden = !permissions.can_administer;
        document.getElementById('btnMyPlans').hidden = !permissions.can_history;
        document.getElementById('menuItemMyPlans').hidden = !permissions.can_history;
        window.planWorkflow?.permissionsChanged?.();
        window.pdfWorkflow?.permissionsChanged?.();
        document.getElementById('appAuthError').textContent = user && !window.planWorkflow
            ? 'La sesión está iniciada, pero no se pudo cargar el visor de planos. Recargue la página para volver a cargarlo.'
            : '';
        if (!user) {
            modalSavedPlans.hide();
            document.getElementById('savedPlansContainer').replaceChildren();
            loginForm.reset();
            loginForm.hidden = false;
            registrationPanel.hidden = true;
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

    async function applySession(result, revision) {
        if (revision !== authRevision) return;
        if (result.company_url) {
            applyUser(null);
            window.location.replace(result.company_url);
            return;
        }
        const changingUser = appState.user?.id !== result.user?.id;
        const appearance = result.user?.appearance || result.appearance;
        if (result.user && !appearance && result.user.company_id &&
                result.user.company_id !== document.documentElement.dataset.companyId) {
            applyUser(null);
            throw new Error('Conéctese para cargar la apariencia de su empresa.');
        }
        document.documentElement.classList.add('app-identity-updating');
        try {
            if (changingUser) applyUser(null);
            window.companyAppearance.apply(appearance);
            if (revision === authRevision) applyUser(result.user);
        } catch (error) {
            if (revision === authRevision) applyUser(null);
            throw error;
        } finally {
            if (revision === authRevision) document.documentElement.classList.remove('app-identity-updating');
        }
    }

    function refresh() {
        if (authBusy) return Promise.resolve();
        if (refreshFlight) return refreshFlight;
        const revision = ++authRevision;
        refreshFlight = authRequest('/api/auth/session/').then(result => applySession(result, revision))
            .finally(() => { refreshFlight = null; });
        return refreshFlight;
    }

    function refreshWhenVisible() {
        if (document.hidden || authBusy) return;
        clearTimeout(refreshTimer);
        if (refreshFlight) {
            authRevision += 1;
            refreshFlight.then(refreshWhenVisible, refreshWhenVisible);
            return;
        }
        refreshTimer = setTimeout(() => refresh().catch(error => {
            if (!appState.user) showLoginError(error.message);
        }), 50);
    }

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const username = document.getElementById('usernameInput').value.trim();
        const password = document.getElementById('passwordInput').value;
        if (!username || !password) { showLoginError('Ingrese su nombre de usuario y contraseña.'); return; }
        loginButton.disabled = true;
        authBusy = true;
        alert.classList.add('d-none');
        const revision = ++authRevision;
        try {
            const result = await authRequest('/api/auth/login/', { username, password });
            announceSessionChange();
            if (revision === authRevision) {
                document.getElementById('passwordInput').value = '';
                await applySession(result, revision);
            }
        } catch (error) { showLoginError(error.message); }
        finally { authBusy = false; loginButton.disabled = false; }
    });

    function registrationMessage(message, error = false) {
        registrationStatus.hidden = false;
        registrationStatus.textContent = message;
        registrationStatus.className = error ? 'small text-danger' : 'small text-success';
    }

    function validRut(value) {
        const rut = value.trim().toUpperCase();
        if (!/^[1-9]\d{6,7}-[\dK]$/.test(rut)) return false;
        const [number, digit] = rut.split('-');
        let total = 0, factor = 2;
        for (const char of [...number].reverse()) { total += Number(char) * factor; factor = factor === 7 ? 2 : factor + 1; }
        const expected = 11 - total % 11;
        return Number(number) > 0 && digit === (expected === 11 ? '0' : expected === 10 ? 'K' : String(expected));
    }

    requestRegistrationButton.onclick = async () => {
        const revision = ++registrationRevision;
        loginForm.hidden = true; registrationPanel.hidden = false;
        alert.classList.add('d-none'); registrationStatus.hidden = true; registrationButton.hidden = false;
        document.getElementById('passwordInput').value = '';
        document.getElementById('registrationFirstName').focus();
        registrationCompany.disabled = true;
        registrationButton.disabled = true;
        registrationCompany.replaceChildren(new Option('Cargando empresas…', ''));
        try {
            const { companies } = await authRequest('/api/auth/register/companies/');
            if (revision !== registrationRevision || registrationPanel.hidden) return;
            if (!companies.length) {
                registrationCompany.replaceChildren(new Option('No hay empresas disponibles', ''));
                registrationMessage('Por ahora no hay empresas habilitadas para recibir solicitudes.', true);
                return;
            }
            const options = companies.map(company => new Option(company.name, company.id));
            if (options.length > 1) options.unshift(new Option('Seleccione su empresa', ''));
            registrationCompany.replaceChildren(...options);
            registrationCompany.disabled = false;
            registrationButton.disabled = false;
        } catch (_) {
            if (revision !== registrationRevision || registrationPanel.hidden) return;
            registrationCompany.replaceChildren(new Option('No se pudo cargar la lista', ''));
            registrationMessage('Vuelva al inicio de sesión y abra de nuevo el formulario para reintentar.', true);
        }
    };
    document.getElementById('btnBackToLogin').onclick = () => {
        if (registrationBusy) return;
        registrationRevision += 1;
        registrationPanel.hidden = true; loginForm.hidden = false;
        registrationForm.reset(); registrationStatus.hidden = true;
        document.getElementById('usernameInput').focus();
    };
    registrationForm.addEventListener('submit', async event => {
        event.preventDefault(); if (registrationBusy || registrationCompany.disabled) return;
        const data = {
            company_id: registrationCompany.value,
            first_name: document.getElementById('registrationFirstName').value.trim(),
            last_name: document.getElementById('registrationLastName').value.trim(),
            rut: document.getElementById('registrationRut').value.trim(),
            password: document.getElementById('registrationPassword').value,
            confirm_password: document.getElementById('registrationConfirmPassword').value,
        };
        if (!data.company_id) { registrationMessage('Seleccione su empresa.', true); return; }
        if (!data.first_name || !data.last_name) { registrationMessage('Ingrese su nombre y apellido.', true); return; }
        if (!validRut(data.rut)) { registrationMessage('Ingrese un RUT válido sin puntos y con guion, por ejemplo 12345678-5.', true); return; }
        if (data.password !== data.confirm_password) { registrationMessage('Las contraseñas no coinciden.', true); return; }
        if (data.password.length < 8 || /^\d+$/.test(data.password)) { registrationMessage('Use una contraseña de al menos 8 caracteres que no contenga solo números.', true); return; }
        registrationBusy = true; registrationStatus.hidden = true;
        const controls = [...registrationForm.querySelectorAll('input, select, button')]; controls.forEach(control => { control.disabled = true; });
        document.getElementById('btnBackToLogin').disabled = true;
        try {
            const result = await authRequest('/api/auth/register/', data);
            registrationForm.reset(); registrationButton.hidden = true;
            registrationMessage(result.message || 'Solicitud enviada. Un administrador debe aprobarla antes de que pueda iniciar sesión con su RUT y contraseña.');
        } catch (error) { registrationMessage(error.message, true); }
        finally {
            registrationBusy = false; controls.forEach(control => { control.disabled = false; });
            document.getElementById('btnBackToLogin').disabled = false;
        }
    });

    menuItemLogout.addEventListener('click', async (event) => {
        event.preventDefault();
        if (window.pdfWorkflow?.canLeave && !(await window.pdfWorkflow.canLeave())) return;
        if (window.relOffline && !(await window.relOffline.canLogout())) return;
        const revision = ++authRevision;
        authBusy = true;
        try {
            const company = appState.user?.company_id;
            await authRequest('/api/auth/logout/', {});
            announceSessionChange();
            if (revision === authRevision) {
                if (company) window.location.replace('/');
                else applyUser(null);
            }
        } catch (error) {
            document.getElementById('appAuthError').textContent = `No se pudo cerrar la sesión: ${error.message}`;
        }
        finally { authBusy = false; }
    });

    sessionChannel?.addEventListener('message', refreshWhenVisible);
    window.addEventListener('storage', event => { if (event.key === 'planos.auth.change') refreshWhenVisible(); });
    window.addEventListener('focus', refreshWhenVisible);
    window.addEventListener('pageshow', event => { if (event.persisted) refreshWhenVisible(); });
    window.addEventListener('online', refreshWhenVisible);
    window.addEventListener('app-session-changed', refreshWhenVisible);
    document.addEventListener('visibilitychange', refreshWhenVisible);

    window.appAuth = { refresh, expire };
    loginButton.disabled = true;
    requestRegistrationButton.disabled = true;
    refresh().catch(() => showLoginError('No se pudo abrir el almacenamiento local de la aplicación. Cierre y vuelva a abrir la app.'))
        .finally(() => { appState.authReady = true; loginButton.disabled = false; requestRegistrationButton.disabled = false; });
})();
