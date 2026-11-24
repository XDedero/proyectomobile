(() => {
    const color = document.getElementById('companyColor');
    const custom = document.getElementById('companyCustomColor');
    const toggle = document.getElementById('companyPaletteButton');
    const toggleLabel = document.getElementById('companyPaletteLabel');
    const palette = document.getElementById('companyPalette');
    const swatches = palette.querySelectorAll('[data-color]');
    const defaultColor = custom.value;

    function updateSelection() {
        const value = color.value.toLowerCase();
        const selected = /^#[0-9a-f]{6}$/.test(value) ? value : '';
        custom.value = selected || defaultColor;
        toggle.style.setProperty('--selected-color', selected || defaultColor);
        for (const swatch of swatches) {
            swatch.setAttribute('aria-pressed', String(swatch.dataset.color === selected));
        }
    }

    function select(value) {
        color.value = value;
        updateSelection();
    }

    toggle.addEventListener('click', () => {
        const expanded = palette.hidden;
        palette.hidden = !expanded;
        toggle.setAttribute('aria-expanded', String(expanded));
        toggleLabel.textContent = expanded ? 'Ocultar paleta de colores' : 'Mostrar paleta de colores';
    });
    color.addEventListener('input', updateSelection);
    for (const swatch of swatches) {
        swatch.addEventListener('click', () => select(swatch.dataset.color));
    }
    custom.addEventListener('input', () => select(custom.value));
    custom.addEventListener('change', () => select(custom.value));
    document.getElementById('companyResetColor').addEventListener('click', () => select(''));
    updateSelection();
    toggle.hidden = false;
})();
