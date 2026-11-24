/* Apply the server-verified company's appearance before revealing its account. */
window.companyAppearance = (() => {
    const root = document.documentElement;
    const companyTokens = [
        'primary', 'primary_rgb', 'hover', 'hover_rgb', 'pressed', 'ink', 'ink_rgb',
        'muted', 'soft', 'surface', 'border', 'background', 'header',
    ];

    function apply(appearance) {
        if (!appearance) return;
        const identity = appearance.company_identity || '';
        const version = appearance.company_brand_version || '0';
        if (root.dataset.companyId === identity && root.dataset.brandVersion === version) return;
        const theme = appearance.company_theme || appearance.company_palette;
        document.getElementById('companyThemeTokens')?.remove();
        for (const token of companyTokens) {
            const name = '--company-' + token.replaceAll('_', '-');
            if (theme?.[token]) root.style.setProperty(name, theme[token]);
            else root.style.removeProperty(name);
        }
        root.toggleAttribute('data-company-themed', Boolean(theme));
        const title = appearance.company_name || 'Reloncaví';
        const subtitle = appearance.company_name || 'Reloncaví Valdivia';
        for (const node of document.querySelectorAll('[data-company-title]')) node.textContent = title + ' APP';
        for (const node of document.querySelectorAll('[data-company-name]')) node.textContent = subtitle;
        document.title = title + ' APP · ' + subtitle;
        document.querySelector('meta[name="theme-color"]').content = theme?.header || '#eaf4ff';
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
