/* Apply the server-verified company's appearance before revealing its account. */
window.companyAppearance = (() => {
    const root = document.documentElement;
    const companyTokens = ['primary', 'primary_rgb', 'hover', 'hover_rgb', 'pressed', 'ink', 'soft', 'surface', 'border', 'header'];
    const paletteTokens = {
        '--app-bg': 'hover', '--primary-accent': 'primary', '--primary-hover': 'hover',
        '--app-body-bg': 'background', '--bs-primary': 'primary', '--bs-primary-rgb': 'primary_rgb',
        '--bs-link-color': 'primary', '--bs-link-hover-color': 'hover',
    };

    async function enableTheme(theme) {
        let sheet = document.getElementById('companyThemeStyles');
        if (!theme) {
            if (sheet) sheet.disabled = true;
            return;
        }
        if (sheet?.sheet) {
            sheet.disabled = false;
            return;
        }
        if (!sheet) {
            sheet = document.createElement('link');
            sheet.id = 'companyThemeStyles';
            sheet.rel = 'stylesheet';
            sheet.href = root.dataset.companyThemeUrl;
        }
        await new Promise((resolve, reject) => {
            sheet.onload = resolve;
            sheet.onerror = () => reject(new Error('No se pudo cargar la apariencia de su empresa. Recargue la aplicación.'));
            sheet.disabled = false;
            if (!sheet.isConnected) document.head.append(sheet);
        });
    }

    async function apply(appearance, isCurrent) {
        if (!appearance) return;
        const identity = appearance.company_identity || '';
        const version = appearance.company_brand_version || '0';
        if (root.dataset.companyId === identity && root.dataset.brandVersion === version) return;
        await enableTheme(appearance.company_theme);
        if (!isCurrent()) return;
        const palette = appearance.company_palette;
        const theme = appearance.company_theme;
        document.getElementById('companyThemeTokens')?.remove();
        document.getElementById('companyPaletteStyle')?.remove();
        for (const token of companyTokens) {
            const name = '--company-' + token.replaceAll('_', '-');
            if (theme) root.style.setProperty(name, theme[token]);
            else root.style.removeProperty(name);
        }
        for (const [name, key] of Object.entries(paletteTokens)) {
            if (palette) root.style.setProperty(name, palette[key]);
            else root.style.removeProperty(name);
        }
        if (palette) {
            const rules = document.createElement('style');
            rules.id = 'companyPaletteStyle';
            rules.textContent = '.app-header{background:linear-gradient(110deg,var(--company-header-color),var(--app-body-bg))}' +
                '.welcome-card,.btn-modern-primary{background:linear-gradient(135deg,var(--primary-hover),var(--primary-accent))}' +
                '.btn-modern-primary:hover,.btn-modern-primary:active{background:var(--primary-hover)}' +
                '.welcome-copy p,.welcome-session{color:#fff}';
            root.style.setProperty('--company-header-color', palette.header);
            document.head.append(rules);
        }
        const title = appearance.company_name || 'Reloncaví';
        const subtitle = appearance.company_name || 'Reloncaví Valdivia';
        for (const node of document.querySelectorAll('[data-company-title]')) node.textContent = title + ' APP';
        for (const node of document.querySelectorAll('[data-company-name]')) node.textContent = subtitle;
        document.title = title + ' APP · ' + subtitle;
        document.querySelector('meta[name="theme-color"]').content = palette?.header || '#eaf4ff';
        document.querySelector('meta[name="apple-mobile-web-app-title"]').content = title;
        const logo = appearance.company_logo;
        for (const image of document.querySelectorAll('.company-logo-window > img')) {
            image.hidden = true;
            image.onload = () => { image.hidden = false; };
            image.src = logo || '/static/mobileApp/img/reloncavi-logo.png';
        }
        document.getElementById('companyLogoStyle').textContent = logo
            ? '.company-logo-window > img{inset:0;width:100%;height:100%;transform:none}' : '';
        defaultPlanButtonColor = theme?.primary || '#2563eb';
        if (theme) root.dataset.planButtonColor = theme.primary;
        else delete root.dataset.planButtonColor;
        root.dataset.companyId = identity;
        root.dataset.brandVersion = version;
    }

    return { apply };
})();
