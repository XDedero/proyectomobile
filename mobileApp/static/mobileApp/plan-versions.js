/* Check the published version before rendering a downloaded drawing. */
(() => {
    const dialog = document.createElement('div');
    dialog.id = 'planUpdateDialog';
    dialog.className = 'plan-update-overlay';
    dialog.hidden = true;
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-labelledby', 'planUpdateTitle');
    dialog.innerHTML = `<section class="plan-update-card">
      <div class="plan-update-warning" role="alert"><i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i>
        <h2 id="planUpdateTitle">Este plano está desactualizado</h2>
        <p>La versión de este plano está desactualizada. Hay actualizaciones pendientes por descargar.</p>
      </div>
      <p id="planUpdateName" class="fw-semibold text-break"></p>
      <p>Actualiza para abrir la versión más reciente. La descarga reemplazará la copia antigua, con sus botones y Excel.</p>
      <p id="planUpdateStatus" role="status" aria-live="polite"></p>
      <div class="d-flex flex-wrap gap-2"><button type="button" id="btnUpdateStalePlan" class="btn btn-danger">Actualizar</button>
      <button type="button" id="btnCancelStalePlan" class="btn btn-outline-secondary">Cancelar</button></div>
    </section>`;
    document.body.append(dialog);
    const $ = id => document.getElementById(id);
    let pending = null;
    const key = plan => `plan.version.${appState.user?.id}.${plan.kind}.${plan.cloud_key || plan.id}`;
    function remembered(plan) { try { return localStorage.getItem(key(plan)); } catch (_) { return null; } }
    function remember(plan) { try { if (plan.project_version) localStorage.setItem(key(plan), plan.project_version); localStorage.removeItem(key(plan) + '.stale'); } catch (_) {} }
    async function request(url, post = false) {
        const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...(post ? {method: 'POST', headers: {'X-CSRFToken': csrfToken()}} : {})});
        const data = await response.json().catch(() => ({}));
        if (!response.ok) { if (response.status === 401) window.appAuth?.expire(); throw new Error(data.error || 'No se pudo comprobar o actualizar el plano.'); }
        return data;
    }
    function finish(value) {
        if (!pending) return;
        const current = pending; pending = null; dialog.hidden = true;
        document.body.style.overflow = current.overflow;
        current.focus?.focus(); current.resolve(value);
    }
    async function beforeOpen(plan) {
        if (!plan.version_url) return plan;
        const user = appState.user?.id;
        const known = remembered(plan) || plan.project_version || '';
        const url = new URL(plan.version_url, location.href); url.searchParams.set('version', known);
        const result = await request(url);
        if (appState.user?.id !== user) return null;
        try {
            if (!result.verified && localStorage.getItem(key(plan) + '.stale')) result.stale = true;
            if (result.stale) localStorage.setItem(key(plan) + '.stale', '1');
        } catch (_) {}
        if (!result.stale) { remember(plan); return plan; }
        if (pending) finish(null);
        $('planUpdateName').textContent = plan.name;
        $('planUpdateStatus').textContent = '';
        $('btnUpdateStalePlan').disabled = $('btnCancelStalePlan').disabled = false;
        const promise = new Promise(resolve => { pending = {resolve, plan, user, refresh: result.refresh_url, overflow: document.body.style.overflow, focus: document.activeElement}; });
        (document.querySelector('.modal.show') || document.body).append(dialog);
        document.body.style.overflow = 'hidden'; dialog.hidden = false; $('btnUpdateStalePlan').focus();
        return promise;
    }
    $('btnCancelStalePlan').onclick = () => finish(null);
    $('btnUpdateStalePlan').onclick = async () => {
        const current = pending; if (!current) return;
        $('btnUpdateStalePlan').disabled = $('btnCancelStalePlan').disabled = true;
        $('planUpdateStatus').textContent = 'Descargando y comprobando la versión más reciente…';
        try {
            const result = await request(current.refresh, true);
            if (pending !== current) return;
            if (appState.user?.id !== current.user) { finish(null); return; }
            remember(result.plan);
            finish(result.plan);
            window.projectWorkspace?.listSaved({quiet: true});
        } catch (error) {
            if (pending === current) {
                $('planUpdateStatus').textContent = `${error.message} La copia anterior se conserva hasta completar la actualización.`;
                $('planUpdateStatus').className = 'text-danger';
                $('btnUpdateStalePlan').disabled = $('btnCancelStalePlan').disabled = false;
            }
        }
    };
    dialog.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !$('btnCancelStalePlan').disabled) { event.preventDefault(); finish(null); }
        if (event.key === 'Tab') {
            const buttons = [...dialog.querySelectorAll('button:not(:disabled)')];
            if (!buttons.length) { event.preventDefault(); return; }
            if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons.at(-1).focus(); }
            else if (!event.shiftKey && document.activeElement === buttons.at(-1)) { event.preventDefault(); buttons[0].focus(); }
        }
    });
    window.planVersions = {beforeOpen, remember, reset: () => finish(null)};
})();
