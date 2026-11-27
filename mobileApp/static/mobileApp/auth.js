/* Authentication and capabilities always come from Django, never from form rules. */
(() => {
    const loginButton = document.getElementById('btnLogin');
    const alert = document.getElementById('loginAlert');
    const appScreen = document.getElementById('appScreen');
    const sessionLoading = document.getElementById('sessionLoading');
    let authRevision = 0;
    let authBusy = false;
    let refreshFlight = null;
    let refreshTimer = null;
    let redirectingToCompany = false;
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
            ? 'Puede gestionar usuarios, categorías y planos.'
            : user?.role === 'worker'
                ? 'Puede crear y modificar planos, asociar Excel y guardar su trabajo. Consulte sus planos en el historial.'
                : 'Puede consultar los planos guardados e importar planos para verlos.';
        menuItemPlanos.hidden = !permissions.can_view;
        document.getElementById('btnAdminister').hidden = !permissions.can_administer;
        document.getElementById('menuItemAdminister').hidden = !permissions.can_administer;
        document.getElementById('btnMyPlans').hidden = !permissions.can_history;
        document.getElementById('menuItemMyPlans').hidden = !permissions.can_history;
        window.planWorkflow?.permissionsChanged?.();
        window.pdfWorkflow?.permissionsChanged?.();
        document.getElementById('appAuthError').textContent = user && !window.pdfWorkflow
            ? 'La sesión está iniciada, pero no se pudo cargar el visor de planos. Recargue la página para volver a cargarlo.'
            : '';
        if (!user) {
            modalSavedPlans.hide();
            document.getElementById('savedPlansContainer').replaceChildren();
            loginForm.reset();
            loginForm.hidden = false;
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

    function applySession(result, revision) {
        if (revision !== authRevision) return false;
        if (result.company_url) {
            redirectingToCompany = true;
            appScreen.hidden = true;
            sessionLoading.hidden = false;
            applyUser(null);
            window.location.replace(result.company_url);
            return true;
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
        return true;
    }

    function refresh() {
        if (authBusy) return Promise.resolve();
        if (refreshFlight) return refreshFlight;
        const revision = ++authRevision;
        refreshFlight = authRequest('/api/auth/session/').then(result => applySession(result, revision), error => {
            if (revision === authRevision) throw error;
            return false;
        })
            .finally(() => { refreshFlight = null; });
        return refreshFlight;
    }

    function refreshWhenVisible() {
        if (!appState.authReady || redirectingToCompany || document.hidden || authBusy) return;
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

    function sessionChanged() {
        if (!appState.authReady && refreshFlight) {
            authRevision += 1;
            return;
        }
        refreshWhenVisible();
    }

    async function openApp() {
        try {
            // If the account changed in another tab, wait for a fresh check before showing a screen.
            while (!(await refresh())) { /* The previous response was superseded. */ }
        } catch (_) {
            applyUser(null);
            showLoginError('No se pudo abrir el almacenamiento local de la aplicación. Cierre y vuelva a abrir la app.');
        }
        if (redirectingToCompany) return;
        appState.authReady = true;
        loginButton.disabled = false;
        sessionLoading.hidden = true;
        appScreen.hidden = false;
    }

    loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const rut = document.getElementById('usernameInput').value.trim();
        const password = document.getElementById('passwordInput').value;
        if (!rut || !password) { showLoginError('Ingrese su RUT y contraseña.'); return; }
        loginButton.disabled = true;
        authBusy = true;
        alert.classList.add('d-none');
        const revision = ++authRevision;
        try {
            const result = await authRequest('/api/auth/login/', { rut, password });
            announceSessionChange();
            if (revision === authRevision) {
                document.getElementById('passwordInput').value = '';
                applySession(result, revision);
            }
        } catch (error) { showLoginError(error.message); }
        finally { authBusy = false; loginButton.disabled = false; }
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

    sessionChannel?.addEventListener('message', sessionChanged);
    window.addEventListener('storage', event => { if (event.key === 'planos.auth.change') sessionChanged(); });
    window.addEventListener('focus', refreshWhenVisible);
    window.addEventListener('pageshow', event => { if (event.persisted) refreshWhenVisible(); });
    window.addEventListener('online', refreshWhenVisible);
    window.addEventListener('app-session-changed', sessionChanged);
    document.addEventListener('visibilitychange', refreshWhenVisible);

    window.appAuth = { refresh, expire };
    openApp();
})();
