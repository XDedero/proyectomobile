/* Apply the server-verified company's appearance before revealing its account. */
window.companyAppearance = (() => {
    const root = document.documentElement;
    const product = JSON.parse(document.getElementById('productAppearance').textContent);
    const companyTokens = [
        'primary', 'primary_rgb', 'hover', 'hover_rgb', 'pressed', 'ink', 'ink_rgb',
        'muted', 'soft', 'surface', 'border', 'background', 'header',
    ];

    function apply(appearance) {
        if (!appearance) return;
        const identity = appearance.company_identity || '';
        const version = appearance.company_brand_version || '0';
        const isProduct = Boolean(appearance.is_product_brand);
        if (root.hasAttribute('data-product-brand') === isProduct &&
                root.dataset.companyId === identity && root.dataset.brandVersion === version) return;
        const theme = appearance.company_theme || appearance.company_palette;
        document.getElementById('companyThemeTokens')?.remove();
        for (const token of companyTokens) {
            const name = '--company-' + token.replaceAll('_', '-');
            if (theme?.[token]) root.style.setProperty(name, theme[token]);
            else root.style.removeProperty(name);
        }
        root.toggleAttribute('data-company-themed', Boolean(theme));
        const title = appearance.company_name || (isProduct ? product.company_name : 'Reloncaví');
        for (const node of document.querySelectorAll('[data-company-title]')) node.textContent = title;
        document.title = title;
        document.querySelector('meta[name="theme-color"]').content = theme?.header || '#eaf4ff';
        const fallback = identity || isProduct ? product.company_logo : '/static/mobileApp/img/reloncavi-logo.png';
        for (const image of document.querySelectorAll('[data-company-logo]')) {
            image.hidden = true;
            image.classList.toggle('brand-logo-full', Boolean(appearance.company_logo || identity || isProduct));
            image.onload = () => { image.hidden = false; };
            image.onerror = () => {
                image.onerror = null;
                image.classList.add('brand-logo-full');
                image.src = product.company_logo;
            };
            image.src = appearance.company_logo || fallback;
            if (image.complete && image.naturalWidth) image.hidden = false;
        }
        defaultPlanButtonColor = theme?.primary || '#2563eb';
        if (theme) root.dataset.planButtonColor = theme.primary;
        else delete root.dataset.planButtonColor;
        root.dataset.companyId = identity;
        root.dataset.brandVersion = version;
        root.toggleAttribute('data-product-brand', isProduct);
    }

    return { apply, reset: () => apply(product) };
})();
