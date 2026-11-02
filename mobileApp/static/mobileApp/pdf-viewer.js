/* PDF.js canvas rendering with plan coordinates independent of zoom and scrolling. */
class PdfPlanViewer {
    constructor(container, options = {}) {
        this.container = container;
        this.options = options;
        this.markers = [];
        this.buttons = new Map();
        this.pointers = new Map();
        this.version = 0;
        this.pageVersion = 0;
        this.renderVersion = 0;
        this.pageNumber = 0;
        this.pageCount = 0;
        this.scale = 1;
        this.resolution = 1280;
        this.maxZoom = 32;
        this.detailCanvas = null;
        this.detailRegion = null;
        this.operationBounds = null;
        this.overviewPromise = null;
        this.placing = false;
        this.moving = false;
        this.selectedId = null;
        this.isFit = true;
        this.events = new AbortController();
        this.container.classList.add('pdf-plan-viewport');
        this.container.setAttribute('tabindex', '0');
        this.container.setAttribute('aria-label', 'Plano PDF. Arrastre para desplazarse y use dos dedos para ampliar.');
        this.content = document.createElement('div');
        this.content.className = 'pdf-plan-content';
        this.stage = document.createElement('div');
        this.stage.className = 'pdf-plan-page';
        this.stage.hidden = true;
        this.layer = document.createElement('div');
        this.layer.className = 'pdf-plan-marker-layer';
        this.stage.append(this.layer);
        this.content.append(this.stage);
        this.message = document.createElement('div');
        this.message.className = 'pdf-plan-message';
        this.message.setAttribute('role', 'status');
        this.message.textContent = 'Importe un plano PDF para comenzar.';
        this.container.replaceChildren(this.content, this.message);

        const signal = this.events.signal;
        this.container.addEventListener('pointerdown', (event) => this.pointerDown(event), { signal });
        this.container.addEventListener('pointermove', (event) => this.pointerMove(event), { signal });
        this.container.addEventListener('pointerup', (event) => this.pointerUp(event), { signal });
        this.container.addEventListener('pointercancel', (event) => this.pointerUp(event, true), { signal });
        this.container.addEventListener('lostpointercapture', (event) => {
            if (this.pointers.has(event.pointerId)) this.pointerUp(event, true);
        }, { signal });
        this.container.addEventListener('wheel', (event) => {
            if (!this.page || (!event.ctrlKey && !event.metaKey)) return;
            event.preventDefault();
            this.zoom(Math.exp(-Math.max(-100, Math.min(100, event.deltaY)) * 0.007), event.clientX, event.clientY);
        }, { passive: false, signal });
        this.container.addEventListener('click', (event) => {
            // Pointer selection is handled on release after checking for a drag.
            const button = event.target.closest('[data-pdf-marker-id]');
            if (button && event.detail === 0) this.emit('onSelect', Number(button.dataset.pdfMarkerId));
        }, { signal });
        this.container.addEventListener('keydown', (event) => this.keyDown(event), { signal });
        this.container.addEventListener('scroll', () => this.scheduleRender(), { passive: true, signal });
        this.resizeObserver = new ResizeObserver(() => {
            if (!this.page || !this.container.clientWidth || !this.container.clientHeight) return;
            if (this.isFit) this.fit();
            else { this.layoutStage(); this.scheduleRender(); }
        });
        this.resizeObserver.observe(this.container);
    }

    async library() {
        if (!this.libraryPromise) {
            const url = this.options.pdfjsUrl || '/static/mobileApp/vendor/pdfjs/pdf-entry.mjs';
            this.libraryPromise = import(url).then((pdfjs) => {
                this.pdfjsVersion = pdfjs.version;
                pdfjs.GlobalWorkerOptions.workerSrc = this.options.workerUrl || '/static/mobileApp/vendor/pdfjs/pdf.worker-entry.mjs';
                return pdfjs;
            }).catch((error) => { this.libraryPromise = null; throw error; });
        }
        return this.libraryPromise;
    }

    viewerError(error, phase) {
        if (error.name === 'PdfViewerError') return error;
        const detail = String(error.message || '');
        let message;
        if (error.status === 401 || error.status === 403) message = 'No se pudo acceder al PDF con esta sesión. Inicie sesión nuevamente.';
        else if (error.status === 404 || error.name === 'MissingPDFException') message = 'El archivo PDF no está disponible en esta instalación. Vuelva a importar el plano si no hay una copia guardada válida.';
        else if (error.name === 'PasswordException') message = 'Este PDF está protegido con contraseña. Importe una copia sin protección.';
        else if (error.name === 'InvalidPDFException') message = 'El archivo recibido no es un PDF válido o está incompleto. Vuelva a importar el original.';
        else if (/version.*does not match/i.test(detail)) message = 'Los componentes del visor PDF tienen versiones distintas. Recargue la página para actualizarlos.';
        else if (/is not a function|is not defined|unsupported|unexpected token/i.test(detail) || error.name === 'SyntaxError') message = 'Este navegador no puede iniciar el visor PDF. Actualice iOS o el navegador y vuelva a abrir la aplicación.';
        else if (phase === 'library' || /worker/i.test(detail)) message = 'No se pudo cargar el visor PDF. Compruebe la conexión y recargue la página.';
        else if (phase === 'render') message = 'El dispositivo no pudo dibujar esta página del PDF. Cierre otras pestañas e intente nuevamente.';
        else message = 'No se pudo descargar el PDF. Compruebe la conexión y vuelva a abrir el plano.';
        console.error('[Visor PDF]', { phase, name: error.name, detail, version: this.pdfjsVersion }, error);
        const failure = new Error(message, { cause: error });
        failure.name = 'PdfViewerError';
        failure.status = error.status;
        failure.phase = phase;
        return failure;
    }

    async load(sourceUrl, markers = [], initialPage = 1) {
        this.clear();
        const version = this.version;
        this.showMessage('Abriendo plano PDF…');
        let phase = 'library';
        try {
            const pdfjs = await this.library();
            if (version !== this.version) return false;
            phase = 'document';
            const assetBase = new URL('.', new URL(this.options.pdfjsUrl || '/static/mobileApp/vendor/pdfjs/pdf-entry.mjs', location.href)).href;
            const loadingTask = pdfjs.getDocument({
                url: sourceUrl,
                withCredentials: true,
                cMapUrl: `${assetBase}cmaps/`, cMapPacked: true,
                standardFontDataUrl: `${assetBase}standard_fonts/`,
                wasmUrl: `${assetBase}wasm/`,
                isEvalSupported: false,
            });
            this.loadingTask = loadingTask;
            const document = await loadingTask.promise;
            if (version !== this.version) { await document.destroy(); return false; }
            this.document = document;
            this.pageCount = document.numPages;
            this.setMarkers(markers);
            phase = 'render';
            await this.setPage(initialPage);
            return version === this.version;
        } catch (error) {
            if (version !== this.version) return false;
            const failure = this.viewerError(error, phase);
            this.showMessage(failure.message);
            this.emit('onError', failure);
            throw failure;
        }
    }

    async setPage(pageNumber) {
        if (!this.document) return;
        pageNumber = Math.max(1, Math.min(this.pageCount, Number(pageNumber) || 1));
        if (this.page && pageNumber === this.pageNumber) return this.renderPage();
        const version = this.version;
        const pageVersion = ++this.pageVersion;
        this.renderVersion += 1;
        this.cancelRender();
        this.overviewPromise = null;
        const previousPage = this.page;
        this.page = null;
        this.releaseCanvases();
        this.operationBounds = null;
        this.stage.hidden = true;
        if (previousPage) {
            previousPage.recordedBBoxes = null;
            previousPage.cleanup();
        }
        this.showMessage(`Cargando página ${pageNumber}…`);
        try {
            const page = await this.document.getPage(pageNumber);
            if (version !== this.version || pageVersion !== this.pageVersion) return;
            this.page = page;
            this.pageNumber = pageNumber;
            this.baseViewport = page.getViewport({ scale: 1 });
            this.isFit = true;
            this.scale = this.fitScale();
            this.stage.hidden = false;
            this.stage.dataset.pdfPage = pageNumber;
            this.container.dataset.pdfPage = pageNumber;
            this.layoutStage();
            this.renderMarkers();
            this.container.scrollLeft = 0;
            this.container.scrollTop = 0;
            await this.renderPage();
            if (version !== this.version || pageVersion !== this.pageVersion || page !== this.page) return;
            this.emit('onPageChange', { page: this.pageNumber, pageCount: this.pageCount });
        } catch (error) {
            if (version !== this.version || pageVersion !== this.pageVersion || error.name === 'RenderingCancelledException') return;
            const failure = this.viewerError(error, 'render');
            this.showMessage(failure.message);
            this.emit('onError', failure);
            throw failure;
        }
    }

    fitScale() {
        if (!this.baseViewport) return 1;
        const width = Math.max(80, (this.container.clientWidth || 360) - 40);
        const height = Math.max(80, (this.container.clientHeight || 360) - 40);
        return Math.max(0.00001, Math.min(width / this.baseViewport.width, height / this.baseViewport.height));
    }

    layoutStage() {
        if (!this.baseViewport) return;
        this.stage.style.width = `${this.baseViewport.width * this.scale}px`;
        this.stage.style.height = `${this.baseViewport.height * this.scale}px`;
        this.container.dataset.pdfZoom = (this.scale / this.fitScale()).toFixed(4);
    }

    releaseCanvas(canvas) {
        if (!canvas) return;
        canvas.remove();
        // Removing a canvas alone does not immediately release its bitmap.
        canvas.width = canvas.height = 0;
    }

    releaseCanvases() {
        this.releaseCanvas(this.canvas);
        this.releaseCanvas(this.detailCanvas);
        this.canvas = this.detailCanvas = null;
        this.detailRegion = null;
    }

    visibleRegion() {
        if (!this.page || !this.container.clientWidth || !this.container.clientHeight) return null;
        const stage = this.stage.getBoundingClientRect();
        const container = this.container.getBoundingClientRect();
        const left = container.left + this.container.clientLeft;
        const top = container.top + this.container.clientTop;
        const x = Math.max(0, left - stage.left);
        const y = Math.max(0, top - stage.top);
        const right = Math.min(stage.width, left + this.container.clientWidth - stage.left);
        const bottom = Math.min(stage.height, top + this.container.clientHeight - stage.top);
        if (right <= x || bottom <= y) return null;
        return { x, y, width: right - x, height: bottom - y, pageWidth: stage.width, pageHeight: stage.height, scale: this.scale };
    }

    operationFilter(region) {
        const bounds = this.operationBounds;
        if (!bounds || typeof bounds.isEmpty !== 'function') return null;
        const minX = region.x / region.pageWidth, minY = region.y / region.pageHeight;
        const maxX = (region.x + region.width) / region.pageWidth;
        const maxY = (region.y + region.height) / region.pageHeight;
        // PDF.js records both drawing bounds and the state dependencies they
        // need. Use the same normalized bounds as its own detail-view renderer.
        return index => index >= bounds.length || (!bounds.isEmpty(index) &&
            bounds.minX(index) <= maxX && bounds.maxX(index) >= minX &&
            bounds.minY(index) <= maxY && bounds.maxY(index) >= minY);
    }

    async renderSurface(page, viewport, width, height, generation, { transform = null, overview = false, operationsFilter = null } = {}) {
        const version = this.version;
        const canvas = document.createElement('canvas');
        canvas.className = overview ? 'pdf-plan-canvas' : 'pdf-plan-canvas pdf-plan-detail';
        canvas.width = Math.max(1, Math.min(this.resolution, Math.round(width)));
        canvas.height = Math.max(1, Math.min(this.resolution, Math.round(height)));
        if (overview) {
            canvas.setAttribute('role', 'img');
            canvas.setAttribute('aria-label', `Página ${this.pageNumber} del plano PDF`);
            // A cancelled overview must never leave partial operation bounds.
            page.recordedBBoxes = null;
        } else canvas.setAttribute('aria-hidden', 'true');
        let accepted = false;
        let task;
        try {
            task = page.render({ canvas, viewport, transform, background: '#ffffff', recordOperations: overview, operationsFilter });
        } catch (error) {
            this.releaseCanvas(canvas);
            throw error;
        }
        this.renderTask = task;
        try {
            await task.promise;
            if (version !== this.version || generation !== this.renderVersion || page !== this.page) return null;
            accepted = true;
            return canvas;
        } catch (error) {
            if (error.name !== 'RenderingCancelledException' && version === this.version && generation === this.renderVersion) throw error;
            return null;
        } finally {
            if (this.renderTask === task) this.renderTask = null;
            if (!accepted) this.releaseCanvas(canvas);
        }
    }

    async renderPage() {
        if (!this.page || !this.container.clientWidth || !this.container.clientHeight) return;
        // The overview is independent of zoom. Finish it once instead of
        // restarting a heavy page while the user moves the viewport.
        if (this.overviewPromise) return this.overviewPromise;
        const page = this.page;
        const generation = ++this.renderVersion;
        this.cancelRender();
        if (!this.canvas) {
            const scale = this.resolution / Math.max(this.baseViewport.width, this.baseViewport.height);
            const viewport = page.getViewport({ scale });
            const pending = this.renderSurface(page, viewport, viewport.width, viewport.height, generation, { overview: true });
            this.overviewPromise = pending;
            let canvas;
            try { canvas = await pending; }
            finally { if (this.overviewPromise === pending) this.overviewPromise = null; }
            if (!canvas) return;
            if (page !== this.page || generation !== this.renderVersion) { this.releaseCanvas(canvas); return; }
            this.canvas = canvas;
            this.stage.prepend(canvas);
            this.operationBounds = page.recordedBBoxes;
            this.message.hidden = true;
        }
        if (this.pointers.size || page !== this.page || generation !== this.renderVersion) return;
        const region = this.visibleRegion();
        if (!region) return;
        const entirePage = region.width >= region.pageWidth - .5 && region.height >= region.pageHeight - .5;
        if (entirePage) {
            this.releaseCanvas(this.detailCanvas);
            this.detailCanvas = this.detailRegion = null;
            return;
        }
        const old = this.detailRegion;
        if (old && Math.abs(old.scale - region.scale) < 1e-8 &&
            ['x', 'y', 'width', 'height'].every(key => Math.abs(old[key] - region[key]) < .5)) return;
        // Rasterize only this window of the page. Even at the zoom limit, no
        // backing bitmap grows beyond 1280 pixels on either side.
        const outputScale = this.resolution / Math.max(region.width, region.height);
        const viewport = page.getViewport({ scale: this.scale });
        const canvas = await this.renderSurface(page, viewport, region.width * outputScale, region.height * outputScale, generation, {
            transform: [outputScale, 0, 0, outputScale, -region.x * outputScale, -region.y * outputScale],
            operationsFilter: this.operationFilter(region),
        });
        if (!canvas) return;
        if (page !== this.page || generation !== this.renderVersion) { this.releaseCanvas(canvas); return; }
        Object.assign(canvas.style, {
            left: `${region.x / region.pageWidth * 100}%`, top: `${region.y / region.pageHeight * 100}%`,
            width: `${region.width / region.pageWidth * 100}%`, height: `${region.height / region.pageHeight * 100}%`,
        });
        this.releaseCanvas(this.detailCanvas);
        this.detailCanvas = canvas;
        this.detailRegion = region;
        this.layer.before(canvas);
    }

    scheduleRender() {
        clearTimeout(this.renderTimer);
        if (!this.page || this.overviewPromise) return;
        if (this.renderTask) {
            this.renderVersion += 1;
            this.cancelRender();
        }
        // Pan and pinch use the current canvases; only the final view is drawn.
        if (this.pointers.size) return;
        this.renderTimer = setTimeout(() => {
            this.renderTimer = null;
            this.renderPage().catch(error => this.emit('onError', error));
        }, 160);
    }

    cancelRender() {
        clearTimeout(this.renderTimer);
        this.renderTimer = null;
        if (this.renderTask) {
            this.renderTask.cancel();
            this.renderTask = null;
        }
    }

    zoom(factor, clientX, clientY) {
        if (!this.page || !Number.isFinite(factor) || factor <= 0) return;
        const containerRect = this.container.getBoundingClientRect();
        clientX ??= containerRect.left + this.container.clientWidth / 2;
        clientY ??= containerRect.top + this.container.clientHeight / 2;
        const before = this.stage.getBoundingClientRect();
        const anchorX = (clientX - before.left) / before.width;
        const anchorY = (clientY - before.top) / before.height;
        const fitScale = this.fitScale();
        const scale = Math.max(fitScale / 4, Math.min(fitScale * this.maxZoom, this.scale * factor));
        if (Math.abs(scale - this.scale) < 1e-10) return;
        this.scale = scale;
        this.isFit = false;
        this.layoutStage();
        const after = this.stage.getBoundingClientRect();
        this.container.scrollLeft += after.left + anchorX * after.width - clientX;
        this.container.scrollTop += after.top + anchorY * after.height - clientY;
        this.scheduleRender();
    }

    fit() {
        if (!this.page) return;
        const scale = this.fitScale();
        const changed = Math.abs(scale - this.scale) > 0.000001;
        this.scale = scale;
        this.isFit = true;
        this.layoutStage();
        this.container.scrollLeft = 0;
        this.container.scrollTop = 0;
        if (changed || !this.canvas || this.detailCanvas || this.renderTask) this.scheduleRender();
    }

    setMarkers(markers) {
        this.markers = (markers || []).map((marker) => ({ ...marker }));
        this.renderMarkers();
    }

    renderMarkers() {
        this.layer.replaceChildren();
        this.buttons.clear();
        for (const [index, marker] of this.markers.entries()) {
            if (Number(marker.page) !== this.pageNumber) continue;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `pdf-plan-marker${marker.workbook ? ' has-workbook' : ''}`;
            button.dataset.pdfMarkerId = marker.id;
            button.textContent = String(index + 1);
            const label = marker.label || `Botón ${index + 1}`;
            button.title = `${label} · ${marker.workbook ? marker.workbook.name : 'Sin Excel asignado'}`;
            button.setAttribute('aria-label', button.title);
            this.positionButton(button, marker.x, marker.y);
            this.layer.append(button);
            this.buttons.set(Number(marker.id), button);
        }
        this.select(this.selectedId);
    }

    positionButton(button, x, y) {
        if (!button) return;
        button.style.left = `${Math.max(0, Math.min(1, Number(x))) * 100}%`;
        button.style.top = `${Math.max(0, Math.min(1, Number(y))) * 100}%`;
    }

    select(id) {
        this.selectedId = id === null || id === undefined ? null : Number(id);
        for (const [markerId, button] of this.buttons) {
            const selected = markerId === this.selectedId;
            button.classList.toggle('selected', selected);
            button.setAttribute('aria-pressed', String(selected));
        }
    }

    setPlacing(value) {
        this.placing = Boolean(value);
        if (this.placing) this.moving = false;
        this.updateMode();
    }

    setMoving(value) {
        this.moving = Boolean(value);
        if (this.moving) this.placing = false;
        this.updateMode();
    }

    updateMode() {
        this.container.classList.toggle('placing-marker', this.placing);
        this.container.classList.toggle('moving-markers', this.moving);
    }

    point(clientX, clientY, clamp = false) {
        if (!this.page) return null;
        const rectangle = this.stage.getBoundingClientRect();
        const x = (clientX - rectangle.left) / rectangle.width;
        const y = (clientY - rectangle.top) / rectangle.height;
        if (!clamp && (x < 0 || x > 1 || y < 0 || y > 1)) return null;
        return { page: this.pageNumber, x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y)) };
    }

    pointerDown(event) {
        if (!this.page || event.button !== 0) return;
        event.preventDefault();
        const button = event.target.closest('[data-pdf-marker-id]');
        if (button) button.focus({ preventScroll: true });
        else this.container.focus({ preventScroll: true });
        const marker = button && this.markers.find((item) => Number(item.id) === Number(button.dataset.pdfMarkerId));
        this.pointers.set(event.pointerId, {
            x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY,
            moved: false, markerId: marker ? Number(marker.id) : null,
            markerStart: marker ? { page: marker.page, x: marker.x, y: marker.y } : null,
            preview: null,
        });
        this.container.setPointerCapture(event.pointerId);
        if (this.pointers.size > 1) {
            this.gesture = true;
            for (const pointer of this.pointers.values()) {
                if (pointer.preview && pointer.markerStart) {
                    this.positionButton(this.buttons.get(pointer.markerId), pointer.markerStart.x, pointer.markerStart.y);
                    pointer.preview = null;
                }
            }
        }
        this.container.classList.add('dragging-pdf');
        if (!this.overviewPromise && this.renderTask) {
            this.renderVersion += 1;
            this.cancelRender();
        }
    }

    pointerMove(event) {
        const pointer = this.pointers.get(event.pointerId);
        if (!pointer || !this.page) return;
        event.preventDefault();
        const previous = [...this.pointers.values()].map((item) => ({ x: item.x, y: item.y }));
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        pointer.moved ||= Math.hypot(pointer.x - pointer.startX, pointer.y - pointer.startY) > 6;
        const current = [...this.pointers.values()];
        if (current.length === 1 && this.moving && pointer.markerId !== null && !this.gesture) {
            if (pointer.moved) {
                pointer.preview = this.point(event.clientX, event.clientY, true);
                this.positionButton(this.buttons.get(pointer.markerId), pointer.preview.x, pointer.preview.y);
            }
            return;
        }
        const center = (items) => ({
            x: items.reduce((sum, item) => sum + item.x, 0) / items.length,
            y: items.reduce((sum, item) => sum + item.y, 0) / items.length,
        });
        const before = center(previous);
        const after = center(current);
        this.container.scrollLeft -= after.x - before.x;
        this.container.scrollTop -= after.y - before.y;
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
        if (pointer.preview) {
            if (!cancelled && !this.gesture && this.moving) {
                this.commitMove(pointer.markerId, pointer.preview);
            } else {
                const button = this.buttons.get(pointer.markerId);
                if (button) this.positionButton(button, pointer.markerStart.x, pointer.markerStart.y);
            }
        } else if (!cancelled && !pointer.moved && !this.gesture) {
            if (pointer.markerId !== null) this.emit('onSelect', pointer.markerId);
            else if (this.placing) {
                const point = this.point(event.clientX, event.clientY);
                if (point) this.emit('onPlace', point);
            }
        }
        if (!this.pointers.size) {
            this.gesture = false;
            this.container.classList.remove('dragging-pdf');
            this.scheduleRender();
        }
    }

    keyDown(event) {
        if (!this.page) return;
        const button = event.target.closest('[data-pdf-marker-id]');
        const moves = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (button && this.moving && moves[event.key]) {
            event.preventDefault();
            const id = Number(button.dataset.pdfMarkerId);
            const marker = this.markers.find((item) => Number(item.id) === id);
            const step = event.shiftKey ? 0.02 : 0.005;
            const [dx, dy] = moves[event.key];
            this.commitMove(id, {
                page: marker.page,
                x: Math.max(0, Math.min(1, marker.x + dx * step)),
                y: Math.max(0, Math.min(1, marker.y + dy * step)),
            });
        } else if (event.key === '+' || event.key === '=') {
            event.preventDefault(); this.zoom(1.35);
        } else if (event.key === '-') {
            event.preventDefault(); this.zoom(1 / 1.35);
        } else if (event.key === '0') {
            event.preventDefault(); this.fit();
        } else if (event.key === 'Escape') {
            this.setPlacing(false); this.setMoving(false);
        }
    }

    showMessage(message) {
        this.message.textContent = message;
        this.message.hidden = false;
    }

    commitMove(id, point) {
        const marker = this.markers.find((item) => Number(item.id) === Number(id));
        if (!marker) return;
        const original = { page: marker.page, x: marker.x, y: marker.y };
        Object.assign(marker, point);
        this.positionButton(this.buttons.get(Number(id)), point.x, point.y);
        const restore = (error) => {
            if (this.markers.includes(marker)) {
                Object.assign(marker, original);
                this.positionButton(this.buttons.get(Number(id)), original.x, original.y);
            }
            this.emit('onError', error);
        };
        try {
            const result = this.options.onMove?.(Number(id), point);
            if (result?.catch) result.catch(restore);
        } catch (error) { restore(error); }
    }

    emit(name, ...arguments_) {
        try {
            const result = this.options[name]?.(...arguments_);
            if (result?.catch) result.catch((error) => {
                if (name !== 'onError') this.options.onError?.(error);
            });
        } catch (error) {
            if (name !== 'onError') this.options.onError?.(error);
        }
    }

    clear() {
        this.version += 1;
        this.pageVersion += 1;
        this.renderVersion += 1;
        this.cancelRender();
        this.overviewPromise = null;
        this.pointers.clear();
        this.gesture = false;
        this.container.classList.remove('dragging-pdf');
        this.releaseCanvases();
        this.operationBounds = null;
        this.page = null;
        this.baseViewport = null;
        this.pageNumber = 0;
        this.pageCount = 0;
        this.markers = [];
        this.buttons.clear();
        this.layer.replaceChildren();
        this.stage.hidden = true;
        this.selectedId = null;
        this.setPlacing(false);
        this.setMoving(false);
        const loadingTask = this.loadingTask;
        const document = this.document;
        this.loadingTask = null;
        this.document = null;
        if (loadingTask) loadingTask.destroy().catch(() => {});
        else if (document) document.destroy().catch(() => {});
        this.showMessage('Importe un plano PDF para comenzar.');
    }

    destroy() {
        this.clear();
        this.events.abort();
        this.resizeObserver.disconnect();
        this.container.replaceChildren();
    }
}
window.PdfPlanViewer = PdfPlanViewer;
