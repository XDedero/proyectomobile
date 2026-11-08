/* Shared visual preferences; document coordinates never depend on opacity or zoom. */
(() => {
    const dock = document.createElement('section');
    dock.className = 'plan-display-dock'; dock.hidden = true;
    dock.innerHTML = `<details class="plan-display-controls"><summary>Vista de botones</summary>
      <label><input type="checkbox" data-setting="legend"> Mostrar leyenda lateral</label>
      <label>Opacidad <output>100 %</output><input type="range" min="0" max="100" value="100" data-setting="opacity"></label>
      <label><input type="checkbox" data-setting="hover" checked> Ampliar y mostrar nombre al pasar o tocar</label>
      <fieldset class="display-area" hidden><legend>Área del botón seleccionado</legend>
        <label><input type="checkbox" data-area="has_area"> Mostrar área</label>
        <label>Forma <select data-area="area_shape"><option value="ellipse">Circular / elíptica</option><option value="rectangle">Cuadrada / rectangular</option></select></label>
        <label>Ancho <input type="range" data-area="area_radius_x" min="0.1" max="100" step="0.1"></label>
        <label>Alto <input type="range" data-area="area_radius_y" min="0.1" max="100" step="0.1"></label>
      </fieldset><p class="small mb-0" role="status"></p></details><aside class="plan-color-legend" aria-label="Leyenda de botones" hidden></aside>`;
    let current = null, button = null, pending = Promise.resolve(), generation = 0;
    const layouts = new WeakMap();
    let peek = null, peekTimer, peekVersion = 0;
    function highlight(target) {
        const next = target?.closest?.('.enable-peek');
        if (peek === next) return;
        peek?.classList.remove('is-peeking'); peek = next; peek?.classList.add('is-peeking');
    }
    document.addEventListener('pointerover', event => highlight(event.target));
    document.addEventListener('pointermove', event => highlight(document.elementFromPoint(event.clientX,event.clientY)), {passive:true});
    document.addEventListener('pointerdown', event => { clearTimeout(peekTimer); highlight(event.target); }, {passive:true});
    document.addEventListener('pointerup', event => {
        highlight(document.elementFromPoint(event.clientX,event.clientY));
        peekTimer = setTimeout(() => highlight(null), 1200);
    }, {passive:true});
    const controls = dock.querySelector('details'), legend = dock.querySelector('aside'), message = dock.querySelector('[role=status]');
    function settings() { return {opacity: 1, hover: true, legend: false, ...current.plan.display_settings}; }
    function preview() {
        if (!current) return;
        const config = settings();
        current.viewer.setDisplaySettings?.(config);
        legend.hidden = !config.legend;
        dock.querySelector('output').textContent = `${Math.round(config.opacity * 100)} %`;
        if (current.kind === 'pdf') current.viewer.setMarkers(current.plan.markers || []);
        else {
            for (const row of current.plan.motors || []) {
                const pin = current.viewer.pins.get(row.id);
                if (pin) pin.motor = row;
                current.viewer.updateButtonAppearance(row);
            }
            current.viewer.updatePins();
        }
    }
    function save(values) {
        const context = current, version = generation;
        message.textContent = 'Guardando vista en este PC…';
        pending = pending.catch(() => {}).then(async () => {
            const response = await fetch(context.plan.display_url, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type':'application/json', 'X-CSRFToken':csrfToken()}, body:JSON.stringify(values)});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'No se pudo guardar la vista.');
            window.projectWorkspace?.dirty();
            if (version === generation) message.textContent = 'Vista guardada en este PC. Pulse Guardar plano para subirla.';
        }).catch(error => { if (version === generation) message.textContent = error.message; throw error; });
        pending.catch(() => {});
    }
    for (const control of dock.querySelectorAll('[data-setting]')) {
        control.addEventListener('input', () => {
            if (!current) return;
            current.plan.display_settings = {...settings(), [control.dataset.setting]: control.type === 'checkbox' ? control.checked : Number(control.value)/100};
            preview();
        });
        control.addEventListener('change', () => save({display_settings: settings()}));
    }
    for (const control of dock.querySelectorAll('[data-area]')) {
        control.addEventListener('input', () => {
            if (!button) return;
            button[control.dataset.area] = control.type === 'checkbox' ? control.checked : control.type === 'range' ? Number(control.value)/100 : control.value;
            preview();
        });
        control.addEventListener('change', () => button && save({button_id: button.id, has_area: !!button.has_area, area_shape: button.area_shape || 'ellipse', area_radius_x: button.area_radius_x || .05, area_radius_y: button.area_radius_y || .05}));
    }
    window.planDisplay = {
        clearPeek() {
            peekVersion++;
            clearTimeout(peekTimer);
            highlight(null);
        },
        peekButton(kind, id) {
            const version=++peekVersion;
            setTimeout(() => {
                if (version !== peekVersion) return;
                const selector=kind === 'pdf' ? `[data-pdf-marker-id="${Number(id)}"]` : `[data-motor-id="${Number(id)}"]`;
                clearTimeout(peekTimer); highlight(document.querySelector(selector));
                peekTimer=setTimeout(() => highlight(null),1200);
            },0);
        },
        restorePeek() {
            if (!peek || peek.isConnected) return;
            const pdf=peek.dataset.pdfMarkerId, cad=peek.dataset.motorId;
            const replacement=pdf ? document.querySelector(`[data-pdf-marker-id="${Number(pdf)}"]`) : cad ? document.querySelector(`[data-motor-id="${Number(cad)}"]`) : null;
            peek=replacement?.classList.contains('enable-peek') ? replacement : null;
            peek?.classList.add('is-peeking');
        },
        flush: () => pending,
        render(context, selected) {
            if (!context) { dock.hidden = true; return; }
            if (current?.plan !== context.plan) { generation++; controls.open = false; message.textContent = ''; }
            current = context;
            // The dock is outside the viewer, so it never intercepts document gestures.
            if (!context.viewport.closest('#planFullscreen')) {
                let layout=layouts.get(context.viewport);
                if (!layout) {
                    layout=document.createElement('div'); layout.className='plan-document-layout';
                    context.viewport.before(layout); layout.append(context.viewport); layouts.set(context.viewport,layout);
                }
                if (dock.parentNode !== layout) layout.append(dock);
            } else if (dock.previousElementSibling !== context.viewport) context.viewport.after(dock);
            dock.hidden = false;
            const config = settings(), rows = context.plan.markers || context.plan.motors || [];
            for (const control of dock.querySelectorAll('[data-setting]')) {
                if (control.type === 'checkbox') control.checked = config[control.dataset.setting];
                else control.value = config.opacity * 100;
                control.disabled = !context.plan.permissions?.can_assign;
            }
            button = rows.find(item => item.id === selected?.buttonId) || null;
            const fieldset = dock.querySelector('fieldset'); fieldset.hidden = !button;
            for (const control of fieldset.querySelectorAll('input,select')) {
                const value = button?.[control.dataset.area];
                if (control.type === 'checkbox') control.checked = !!value;
                else control.value = control.type === 'range' ? (value || .05)*100 : value || 'ellipse';
                control.disabled = !context.plan.permissions?.can_assign;
            }
            legend.replaceChildren();
            rows.forEach((row, index) => {
                const entry = document.createElement('button'); entry.type = 'button';
                const swatch = document.createElement('i'); swatch.style.backgroundColor = row.button_color || '#2563eb';
                swatch.style.opacity = config.opacity;
                const text = document.createElement('span'); text.textContent = `${index+1} - ${row.label || row.name || `Botón ${index+1}`}`;
                entry.append(swatch, text);
                entry.onclick = async () => {
                    if (context.kind === 'pdf') {
                        if (context.viewer.pageNumber !== row.page) await context.viewer.setPage(row.page);
                        await window.pdfWorkflow.select(row.id);
                    } else window.planWorkflow.select(row.id, true);
                };
                legend.append(entry);
            });
            preview();
        },
    };
})();
