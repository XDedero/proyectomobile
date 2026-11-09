/* Native SVG navigation: mouse/touch pan, wheel/pinch zoom and motor selection. */
class DxfViewer {
    constructor(container, onSelect) {
        this.container = container;
        this.onSelect = onSelect;
        this.pointers = new Map();
        this.pins = new Map();
        container.addEventListener('pointerdown', (event) => this.pointerDown(event));
        container.addEventListener('pointermove', (event) => this.pointerMove(event));
        container.addEventListener('pointerup', (event) => this.pointerUp(event));
        container.addEventListener('pointercancel', (event) => this.pointerUp(event, true));
        container.addEventListener('wheel', (event) => {
            if (!this.svg) return;
            event.preventDefault();
            this.zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.005), event.clientX, event.clientY);
        }, { passive: false });
        container.addEventListener('keydown', (event) => {
            const motor = event.target.closest('[data-motor-id]');
            const movement = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
            if (motor && this.moving && movement && this.onMove) {
                event.preventDefault();
                const pin = this.pins.get(Number(motor.dataset.motorId));
                const step = 10 / (this.svg.getScreenCTM()?.a || 1);
                this.onMove(pin.motor.id, { svg_x: pin.motor.svg_x + movement[0]*step, svg_y: pin.motor.svg_y + movement[1]*step });
                return;
            }
            if (motor && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                this.onSelect(Number(motor.dataset.motorId));
                return;
            }
            if (!this.svg) return;
            const deltas = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
            if (deltas[event.key]) {
                event.preventDefault();
                const [dx, dy] = deltas[event.key];
                this.view.x += dx * this.view.width * 0.1;
                this.view.y += dy * this.view.height * 0.1;
                this.update();
            } else if (event.key === '+' || event.key === '=') this.zoom(1.4);
            else if (event.key === '-') this.zoom(1 / 1.4);
            else if (event.key === '0') this.fit();
        });
        new ResizeObserver(() => this.updatePins()).observe(container);
    }

    load(source, motors) {
        const parsed = new DOMParser().parseFromString(source, 'image/svg+xml');
        if (parsed.querySelector('parsererror') || parsed.documentElement.localName !== 'svg') {
            throw new Error('No se pudo mostrar el plano DXF.');
        }
        this.clear();
        this.svg = document.importNode(parsed.documentElement, true);
        this.svg.removeAttribute('width');
        this.svg.removeAttribute('height');
        this.svg.setAttribute('aria-label', 'Plano DXF con motores seleccionables');
        this.container.replaceChildren(this.svg);
        const box = this.svg.viewBox.baseVal;
        this.initial = { x: box.x, y: box.y, width: box.width, height: box.height };
        this.view = { ...this.initial };
        const layer = this.element('g', { class: 'motor-layer' });
        this.svg.append(layer);
        for (const motor of motors) {
            const group = this.element('g', {
                'data-motor-id': motor.id, class: `motor-target${motor.excel ? ' has-excel' : ''}`, tabindex: '0',
                role: 'button', 'aria-label': `${motor.name}, X ${motor.x}, Y ${motor.y}`,
                'aria-pressed': 'false',
            });
            const title = this.element('title');
            title.textContent = `${motor.name} · X: ${motor.x} · Y: ${motor.y}`;
            const [x, y, width, height] = motor.svg_bounds;
            const bounds = this.element('rect', { x, y, width, height, class: 'motor-bounds' });
            const circle = this.element('circle', { cx: motor.svg_x, cy: motor.svg_y, class: 'motor-pin' });
            const label = this.element('text', { class: 'motor-label', 'font-size': 11, x: 0, y: 0 });
            const hover = this.element('text', {class: 'motor-hover-name', x: 0, y: 30, 'font-size': 12});
            const area = this.element(motor.area_shape === 'rectangle' ? 'rect' : 'ellipse', {class:'motor-coverage'});
            label.textContent = String(this.pins.size + 1);
            group.append(title, area, bounds, circle, label, hover);
            layer.append(group);
            this.pins.set(motor.id, { group, circle, label, hover, area, motor });
            this.updateButtonAppearance(motor);
        }
        this.update();
        window.planDisplay?.restorePeek();
    }

    updateButtonAppearance(motor) {
        const pin = this.pins.get(motor.id);
        if (!pin) return;
        pin.group.querySelector('title').textContent = `${motor.name} · X: ${motor.x} · Y: ${motor.y}`;
        pin.group.setAttribute('aria-label', `${motor.name}, X ${motor.x}, Y ${motor.y}`);
        pin.circle.style.fill = motor.button_color || '#2563eb';
        pin.hover.textContent = motor.name;
        pin.group.classList.toggle('enable-peek', this.displaySettings?.hover !== false);
        for (const node of [pin.circle, pin.label, pin.hover, pin.group.querySelector('.motor-bounds')]) node.style.opacity = this.displaySettings?.opacity ?? 1;
        pin.area.style.fill = motor.button_color || '#2563eb';
        pin.area.style.opacity = (this.displaySettings?.opacity ?? 1) * .18;
        pin.area.style.pointerEvents = 'none';
        pin.area.style.display = motor.has_area ? '' : 'none';
        if (pin.area.localName !== (motor.area_shape === 'rectangle' ? 'rect' : 'ellipse')) {
            const replacement = this.element(motor.area_shape === 'rectangle' ? 'rect' : 'ellipse', {class:'motor-coverage'});
            pin.area.replaceWith(replacement); pin.area = replacement; this.updateButtonAppearance(motor);
        }
    }

    setDisplaySettings(settings) { this.displaySettings = settings; }

    element(name, attributes = {}) {
        const element = document.createElementNS('http://www.w3.org/2000/svg', name);
        for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
        return element;
    }

    clear() {
        this.container.replaceChildren();
        this.pointers.clear();
        this.pins.clear();
        this.svg = null;
        this.gesture = false;
    }

    select(id) {
        for (const [motorId, pin] of this.pins) {
            pin.group.classList.toggle('selected', motorId === id);
            pin.group.setAttribute('aria-pressed', String(motorId === id));
            pin.group.classList.toggle('has-excel', Boolean(pin.motor.excel));
        }
        // Keep the selected motor visible when symbols overlap.
        const pin = this.pins.get(id);
        if (pin) pin.group.parentNode.append(pin.group);
    }

    focusMotor(id) {
        const pin = this.pins.get(id);
        if (!pin) return;
        this.view.x = pin.motor.svg_x - this.view.width / 2;
        this.view.y = pin.motor.svg_y - this.view.height / 2;
        this.update();
    }

    update() {
        if (!this.svg) return;
        this.svg.setAttribute('viewBox', `${this.view.x} ${this.view.y} ${this.view.width} ${this.view.height}`);
        this.updatePins();
    }

    updatePins() {
        if (!this.svg) return;
        this.container.dispatchEvent(new Event('plan-view-changed'));
        const scale = this.svg.getScreenCTM()?.a;
        if (!scale) return;
        for (const { circle, label, hover, area, motor } of this.pins.values()) {
            circle.setAttribute('r', 12 / scale);
            // Keep text in pixel-sized local coordinates: very large SVG font
            // sizes are clamped by browsers when the CAD viewBox uses 1e6 units.
            label.setAttribute('transform', `translate(${motor.svg_x} ${motor.svg_y}) scale(${1 / scale})`);
            hover.setAttribute('transform', `translate(${motor.svg_x} ${motor.svg_y}) scale(${1 / scale})`);
            const rx = (motor.area_radius_x || .05)*this.initial.width, ry = (motor.area_radius_y || .05)*this.initial.height;
            const attrs = motor.area_shape === 'rectangle' ? {x:motor.svg_x-rx,y:motor.svg_y-ry,width:rx*2,height:ry*2} : {cx:motor.svg_x,cy:motor.svg_y,rx,ry};
            for (const [key,value] of Object.entries(attrs)) area.setAttribute(key,value);
        }
    }

    fit() {
        if (!this.svg) return;
        this.view = { ...this.initial };
        this.update();
    }

    zoom(factor, clientX, clientY) {
        if (!this.svg) return;
        const rect = this.container.getBoundingClientRect();
        const point = this.svg.createSVGPoint();
        point.x = clientX ?? rect.left + rect.width / 2;
        point.y = clientY ?? rect.top + rect.height / 2;
        const anchor = point.matrixTransform(this.svg.getScreenCTM().inverse());
        const width = Math.max(this.initial.width / 100, Math.min(this.initial.width * 4, this.view.width / factor));
        const ratio = width / this.view.width;
        this.view.x = anchor.x - (anchor.x - this.view.x) * ratio;
        this.view.y = anchor.y - (anchor.y - this.view.y) * ratio;
        this.view.width = width;
        this.view.height *= ratio;
        this.update();
    }

    pointerDown(event) {
        if (!this.svg || event.button !== 0) return;
        event.preventDefault();
        const target = event.target.closest('[data-motor-id]');
        this.pointers.set(event.pointerId, {
            x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY,
            motorId: target ? Number(target.dataset.motorId) : null, moved: false,
        });
        this.container.setPointerCapture(event.pointerId);
        if (this.pointers.size > 1) this.gesture = true;
        this.container.classList.add('dragging');
    }

    pointerMove(event) {
        const pointer = this.pointers.get(event.pointerId);
        if (!pointer || !this.svg) return;
        const previous = [...this.pointers.values()].map((item) => ({ ...item }));
        pointer.moved ||= Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY) > 6;
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        if (this.moving && pointer.motorId !== null && this.pointers.size === 1 && !this.gesture) {
            const pin = this.pins.get(pointer.motorId);
            const scale = this.svg.getScreenCTM().a;
            pin?.group.setAttribute('transform', `translate(${(event.clientX-pointer.startX)/scale} ${(event.clientY-pointer.startY)/scale})`);
            return;
        }
        const current = [...this.pointers.values()];
        const center = (items) => ({
            x: items.reduce((sum, item) => sum + item.x, 0) / items.length,
            y: items.reduce((sum, item) => sum + item.y, 0) / items.length,
        });
        const before = center(previous);
        const after = center(current);
        const scale = this.svg.getScreenCTM().a;
        this.view.x -= (after.x - before.x) / scale;
        this.view.y -= (after.y - before.y) / scale;
        this.update();
        if (current.length === 2) {
            const distance = (items) => Math.hypot(items[0].x - items[1].x, items[0].y - items[1].y);
            const oldDistance = distance(previous);
            if (oldDistance > 0) this.zoom(distance(current) / oldDistance, after.x, after.y);
        }
    }

    pointerUp(event, cancelled = false) {
        const pointer = this.pointers.get(event.pointerId);
        if (!pointer) return;
        this.pointers.delete(event.pointerId);
        if (this.container.hasPointerCapture(event.pointerId)) this.container.releasePointerCapture(event.pointerId);
        const pin = this.pins.get(pointer.motorId);
        pin?.group.removeAttribute('transform');
        if (!cancelled && pointer.moved && !this.gesture && this.moving && pin && this.onMove) {
            const scale = this.svg.getScreenCTM().a;
            this.onMove(pointer.motorId, { svg_x: pin.motor.svg_x + (event.clientX-pointer.startX)/scale, svg_y: pin.motor.svg_y + (event.clientY-pointer.startY)/scale });
        }
        if (!cancelled && !pointer.moved && !this.gesture) {
            if (this.placing && this.onPlace) {
                const point = this.svg.createSVGPoint();
                point.x = event.clientX;
                point.y = event.clientY;
                const position = point.matrixTransform(this.svg.getScreenCTM().inverse());
                this.onPlace(position.x, position.y);
            } else if (pointer.motorId !== null) this.onSelect(pointer.motorId);
        }
        if (!this.pointers.size) {
            this.gesture = false;
            this.container.classList.remove('dragging');
        }
    }
}
window.DxfViewer = DxfViewer;
