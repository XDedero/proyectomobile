/* Browser-compatible hooks for a future Android host. No network dependency. */
(() => {
    const root = document.documentElement;
    let viewportFrame = 0;
    function updateViewport() {
        cancelAnimationFrame(viewportFrame);
        viewportFrame = requestAnimationFrame(() => {
            const viewport = window.visualViewport;
            // Preserve accessibility zoom; pinching must not reflow the app.
            if (viewport && Math.abs(viewport.scale - 1) > 0.01) return;
            root.style.setProperty('--viewport-height', `${viewport?.height || window.innerHeight}px`);
        });
    }
    window.addEventListener('resize', updateViewport);
    window.visualViewport?.addEventListener('resize', updateViewport);
    window.addEventListener('pageshow', updateViewport);
    updateViewport();

    function setInsets(insets = {}) {
        for (const side of ['top', 'right', 'bottom', 'left']) {
            const value = insets[side];
            if (Number.isFinite(value) && value >= 0) root.style.setProperty(`--safe-${side}`, `${value}px`);
            else root.style.removeProperty(`--safe-${side}`);
        }
    }

    // All exports already use authenticated same-origin URLs, rather than
    // blob/data URLs. Browsers keep their normal download behavior. Android
    // can optionally implement PlanosAndroid.download(url, suggestedName).
    document.addEventListener('click', (event) => {
        const link = event.target.closest?.('a[download]');
        if (!link?.href || typeof window.PlanosAndroid?.download !== 'function') return;
        const url = new URL(link.href, location.href);
        if (!['http:', 'https:'].includes(url.protocol) || url.origin !== location.origin) return;
        try {
            window.PlanosAndroid.download(url.href, link.download || '');
            event.preventDefault();
        } catch (_) { /* The host may use its standard DownloadListener instead. */ }
    }, true);

    // Bootstrap ignores hide() while a modal is still opening. Track public
    // lifecycle events so an immediate Back also waits for the transition.
    const openModals = new Set();
    const modalTransitions = new WeakMap();
    for (const [start, end] of [['show', 'shown'], ['hide', 'hidden']]) {
        document.addEventListener(`${start}.bs.modal`, (event) => {
            if (start === 'show') openModals.add(event.target);
            modalTransitions.set(event.target, new Promise((resolve) => {
                event.target.addEventListener(`${end}.bs.modal`, resolve, { once: true });
            }));
        });
    }
    document.addEventListener('hidden.bs.modal', (event) => openModals.delete(event.target));
    async function dismissModal(modal) {
        await modalTransitions.get(modal);
        bootstrap.Modal.getInstance(modal)?.hide();
        await modalTransitions.get(modal);
    }

    let navigatingBack = false;
    async function requestBack() {
        if (navigatingBack) return true;
        navigatingBack = true;
        try {
            // Registration dialogs can appear above the workbook editor.
            const modals = [...new Set([...openModals, ...document.querySelectorAll('.modal.show')])];
            modals.sort((a, b) => Number(getComputedStyle(b).zIndex) - Number(getComputedStyle(a).zIndex));
            const topModal = modals[0];
            if (topModal && Number(getComputedStyle(topModal).zIndex) > 2200) {
                await dismissModal(topModal);
                return true;
            }
            if (window.workbookEditor?.visible) {
                // A failed save or an operation in progress keeps the editor open.
                await window.workbookEditor.requestClose();
                return true;
            }
            if (topModal) {
                await dismissModal(topModal);
                return true;
            }
            const dialog = document.getElementById('pdfMarkerDialog');
            if (dialog && !dialog.hidden) {
                document.getElementById('btnPdfCloseMarkerDialog').click();
                return true;
            }
            const fullMenu = document.getElementById('fullscreenPlanMenu');
            if (fullMenu && !fullMenu.hidden) {
                document.getElementById('btnPlanFullscreenMenu').click();
                return true;
            }
            if (window.projectWorkspace?.full) {
                window.projectWorkspace.exitFullscreen();
                return true;
            }
            const dropdown = document.querySelector('[data-bs-toggle="dropdown"][aria-expanded="true"]');
            if (dropdown) {
                bootstrap.Dropdown.getInstance(dropdown)?.hide();
                return true;
            }
            const active = document.querySelector('.app-view.active');
            const backButton = {
                viewImportAutoCAD: 'btnBackToMainMenu',
                viewImportPDF: 'btnPdfBack',
                viewSavedMotors: 'btnSavedMotorsBack',
            }[active?.id];
            if (backButton) {
                document.getElementById(backButton).click();
                return true;
            }
            return false;
        } finally { navigatingBack = false; }
    }
    window.mobileApp = Object.freeze({ requestBack, setInsets });
})();
