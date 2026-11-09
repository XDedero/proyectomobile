/* Opacity belongs only to the open viewer, never to a plan or saved project. */
(() => {
    const dock = document.createElement('section');
    dock.className = 'plan-display-dock';
    dock.hidden = true;
    dock.innerHTML = `<label class="plan-opacity-control" for="planAnnotationOpacity">
      Opacidad <output id="planAnnotationOpacityValue" for="planAnnotationOpacity">100 %</output>
      <input id="planAnnotationOpacity" type="range" min="0" max="100" value="100" aria-label="Opacidad de botones y sectores">
    </label>`;
    const slider = dock.querySelector('input'), output = dock.querySelector('output');
    let current = null, opacity = 1;
    function preview() {
        if (!current) return;
        current.viewport.style.setProperty('--plan-annotation-opacity', String(opacity));
        current.viewport.classList.toggle('plan-annotations-hidden', opacity === 0);
        slider.value = String(Math.round(opacity * 100));
        output.value = `${Math.round(opacity * 100)} %`;
    }
    slider.addEventListener('input', () => {
        opacity = Number(slider.value) / 100;
        preview();
    });
    window.planDisplay = {
        render(context) {
            if (!context) {
                dock.hidden = true;
                current = null;
                opacity = 1;
                return;
            }
            if (current?.plan !== context.plan) opacity = 1;
            current = context;
            // Keep the drawing's full width; the only control is the slider.
            if (dock.previousElementSibling !== context.viewport) context.viewport.after(dock);
            dock.hidden = false;
            preview();
        },
    };
})();
