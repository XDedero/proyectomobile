/* Opacity belongs only to the open viewer, never to a plan or saved project. */
(() => {
    const dock = document.createElement('section');
    dock.className = 'plan-display-dock';
    dock.hidden = true;
    dock.innerHTML = `<label class="plan-opacity-control" for="planAnnotationOpacity">
      Opacidad <output id="planAnnotationOpacityValue" for="planAnnotationOpacity">100 %</output>
      <input id="planAnnotationOpacity" type="range" min="0" max="100" value="100" aria-label="Opacidad de botones y sectores">
    </label><label class="plan-opacity-control plan-size-control" hidden>
      Tamaño del botón <output>100 %</output>
      <input type="range" min="25" max="150" value="100" aria-label="Tamaño del botón seleccionado">
      <small role="status"></small>
    </label>`;
    const slider = dock.querySelector('input'), output = dock.querySelector('output');
    const sizeControl = dock.querySelector('.plan-size-control');
    const sizeSlider = sizeControl.querySelector('input'), sizeOutput = sizeControl.querySelector('output');
    const sizeStatus = sizeControl.querySelector('small');
    let current = null, opacity = 1, selected = null, saving = false, pending = Promise.resolve(true);
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
    function previewSize(context, button, size) {
        if (context.kind === 'pdf') context.viewer.buttons.get(button.id)?.style.setProperty('--button-scale', size);
        else context.viewer.resizePin(context.viewer.pins.get(button.id), size);
    }
    sizeSlider.addEventListener('input', () => {
        if (!selected) return;
        sizeOutput.value = `${sizeSlider.value} %`;
        previewSize(current, selected, Number(sizeSlider.value) / 100);
    });
    sizeSlider.addEventListener('change', () => {
        if (!selected || saving) return;
        const context = current, button = selected, size = Number(sizeSlider.value) / 100;
        saving = sizeSlider.disabled = true;
        sizeStatus.textContent = 'Guardando tamaño…';
        pending = (async () => {
            try {
                const response = await fetch(context.plan.display_url, {
                    method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()},
                    body: JSON.stringify({button_id: button.id, button_scale: size}),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || 'No se pudo guardar el tamaño.');
                button.button_scale = result.button_scale;
                if (context.kind === 'dxf') {
                    const pin = context.viewer.pins.get(button.id);
                    if (pin) pin.motor.button_scale = result.button_scale;
                }
                if (current?.plan === context.plan) window.projectWorkspace.dirty();
                sizeStatus.textContent = '';
                return true;
            } catch (error) {
                previewSize(context, button, button.button_scale ?? 1);
                sizeStatus.textContent = error.message;
                return false;
            } finally {
                saving = false;
                window.planDisplay.render(current, window.projectWorkspace?.selected);
            }
        })();
    });
    window.planDisplay = {
        flush: () => saving ? pending : true,
        render(context, selection) {
            if (!context) {
                dock.hidden = true;
                current = null;
                selected = null;
                opacity = 1;
                return;
            }
            if (current?.plan !== context.plan) opacity = 1;
            current = context;
            const frame = context.viewport.closest('.plan-view-frame') || context.viewport;
            if (dock.previousElementSibling !== frame) frame.after(dock);
            dock.hidden = false;
            preview();
            const button = (context.plan.markers || context.plan.motors)?.find(item => item.id === selection?.buttonId);
            if (selected !== button && !saving) sizeStatus.textContent = '';
            selected = button;
            sizeControl.hidden = !button || !context.plan.permissions?.can_assign;
            sizeSlider.disabled = saving;
            if (button && !saving) {
                sizeSlider.value = String(Math.round((button.button_scale ?? 1) * 100));
                sizeOutput.value = `${sizeSlider.value} %`;
            }
        },
    };
})();
