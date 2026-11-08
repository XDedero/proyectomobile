/* Complete worksheet access with a virtual grid. Only visible cells enter the DOM. */
(() => {
    const ROW_HEIGHT = 20;
    const COLUMN_WIDTH = 64;
    const HEADER_HEIGHT = 30;
    const ROW_HEADER_WIDTH = 48;
    const TILE_ROWS = 100;
    const TILE_COLUMNS = 20;
    const MAX_ROWS = 1048576;
    const MAX_COLUMNS = 16384;

    // Sparse dimension changes preserve the original layout without allocating
    // a DOM node (or a large array) for each row in a million-row worksheet.
    class DimensionAxis {
        constructor(defaultSize, sizes = {}, hidden = []) {
            this.defaultSize = Math.max(1, Number(defaultSize) || 20);
            const changes = new Map(Object.entries(sizes).map(([index, size]) => [Number(index), Math.max(0, Number(size) || 0)]));
            for (const index of hidden) changes.set(Number(index), 0);
            this.changes = changes;
            this.indices = [...changes.keys()].filter((index) => index > 0).sort((a, b) => a - b);
            this.adjustments = [0];
            for (const index of this.indices) this.adjustments.push(this.adjustments.at(-1) + changes.get(index) - this.defaultSize);
        }

        size(index) { return this.changes.has(index) ? this.changes.get(index) : this.defaultSize; }

        offset(index) {
            let low = 0, high = this.indices.length;
            while (low < high) {
                const middle = (low + high) >>> 1;
                if (this.indices[middle] < index) low = middle + 1;
                else high = middle;
            }
            return (index - 1) * this.defaultSize + this.adjustments[low];
        }

        at(position, count) {
            let low = 1, high = count;
            while (low < high) {
                const middle = Math.ceil((low + high) / 2);
                if (this.offset(middle) <= Math.max(0, position)) low = middle;
                else high = middle - 1;
            }
            return this.visible(low, count);
        }

        visible(index, count, direction = 1) {
            while (index >= 1 && index <= count && this.size(index) === 0) index += direction;
            return Math.max(1, Math.min(count, index));
        }
    }

    const CELL_STYLE_PROPERTIES = ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'textDecoration', 'color', 'backgroundColor', 'borderTop', 'borderRight', 'borderBottom', 'borderLeft', 'textAlign', 'verticalAlign', 'whiteSpace', 'overflowWrap', 'paddingLeft', 'textIndent', 'lineHeight'];

    function columnName(number) {
        let result = '';
        while (number > 0) {
            number -= 1;
            result = String.fromCharCode(65 + number % 26) + result;
            number = Math.floor(number / 26);
        }
        return result;
    }

    function parseAddress(address) {
        const match = /^\$?([A-Z]{1,3})\$?([1-9]\d*)$/i.exec(address.trim());
        if (!match) return null;
        let col = 0;
        for (const char of match[1].toUpperCase()) col = col * 26 + char.charCodeAt(0) - 64;
        const row = Number(match[2]);
        return row <= MAX_ROWS && col <= MAX_COLUMNS ? { row, col } : null;
    }

    function rawText(value) {
        if (value === null || value === undefined) return '';
        if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
        return String(value);
    }

    function parsedValue(text) {
        if (text === '') return null;
        if (text.startsWith("'")) return text.slice(1);
        if (text.startsWith('=')) return text;
        if (/^(TRUE|VERDADERO)$/i.test(text)) return true;
        if (/^(FALSE|FALSO)$/i.test(text)) return false;
        const normalized = /^[+-]?\d+,\d+$/.test(text) ? text.replace(',', '.') : text;
        if (/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(normalized)) {
            const number = Number(normalized);
            if (Number.isFinite(number)) return number;
        }
        return text;
    }

    class WorkbookEditor {
        constructor({ request, onSaved } = {}) {
            this.request = request || this.defaultRequest.bind(this);
            this.onSaved = onSaved;
            this.pending = new Map();
            this.tiles = new Map();
            this.inflight = new Map();
            this.sheetPositions = new Map();
            this.epoch = 0;
            this.selectionRevision = 0;
            this.zoom = 1;
            this.formMode = false;
            this.busy = false;
            this.visible = false;
            this.createDialog();
            this.resizeObserver = new ResizeObserver(() => {
                if (this.visible && this.sheet && this.fitMode) this.fitSheet(this.fitMode);
                else this.scheduleRender();
            });
            this.resizeObserver.observe(this.viewport);
            this.documentKeydown = (event) => this.dialogKeydown(event);
            document.addEventListener('keydown', this.documentKeydown);
            this.beforeUnload = (event) => {
                if (!this.hasUnsavedChanges) return;
                event.preventDefault();
                event.returnValue = '';
            };
            window.addEventListener('beforeunload', this.beforeUnload);
        }

        async defaultRequest(url, options = {}) {
            const settings = { credentials: 'same-origin', cache: 'no-store', ...options };
            if (settings.method && settings.method !== 'GET') {
                const token = typeof csrfToken === 'function' ? csrfToken() : decodeURIComponent(document.cookie.split('; ').find((item) => item.startsWith('csrftoken='))?.slice(10) || '');
                settings.headers = { 'Content-Type': 'application/json', 'X-CSRFToken': token, ...settings.headers };
            }
            const response = await fetch(url, settings);
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = new Error(data.error || 'No se pudo completar la operación con el Excel.');
                error.status = response.status;
                throw error;
            }
            return data;
        }

        node(tag, className = '', text) {
            const node = document.createElement(tag);
            if (className) node.className = className;
            if (text !== undefined) node.textContent = text;
            return node;
        }

        button(text, id, callback, className = '') {
            const button = this.node('button', className, text);
            button.type = 'button';
            button.id = id;
            button.addEventListener('click', callback);
            return button;
        }

        createDialog() {
            this.overlay = this.node('div', 'workbook-editor-overlay');
            this.overlay.hidden = true;
            this.panel = this.node('section', 'workbook-editor-panel');
            this.panel.setAttribute('role', 'dialog');
            this.panel.setAttribute('aria-modal', 'true');
            this.panel.setAttribute('aria-labelledby', 'workbookEditorTitle');
            const titlebar = this.node('header', 'wbe-titlebar');
            const titleCopy = this.node('div', 'wbe-title-copy');
            const title = this.node('h2', '', 'Excel del plano');
            title.id = 'workbookEditorTitle';
            this.filename = this.node('div', 'wbe-filename');
            titleCopy.append(title, this.filename);
            this.closeButton = this.button('×', 'workbookEditorClose', () => this.requestClose(), 'wbe-close');
            this.closeButton.setAttribute('aria-label', 'Cerrar ventana de Excel');
            this.removeMarkerButton = this.button('', 'workbookEditorDeleteMarker', () => this.removeMarker?.(), 'wbe-delete-marker');
            this.removeMarkerButton.innerHTML = '<i class="bi bi-trash" aria-hidden="true"></i><span>Eliminar botón</span>';
            this.removeMarkerButton.title = 'Eliminar botón del plano';
            this.removeMarkerButton.setAttribute('aria-label', 'Eliminar botón del plano');
            this.removeMarkerButton.hidden = true;
            titlebar.append(titleCopy, this.removeMarkerButton, this.closeButton);

            const toolbar = this.node('div', 'wbe-toolbar');
            this.sheetSelect = this.node('select', 'wbe-sheet-select');
            this.sheetSelect.id = 'workbookEditorSheet';
            this.sheetSelect.setAttribute('aria-label', 'Hoja del Excel');
            this.sheetSelect.addEventListener('change', () => this.selectWorkbookSheet(this.sheetSelect.value));
            this.saveButton = this.button('Guardar cambios', 'workbookEditorSave', () => this.save(), 'wbe-save');
            this.rowButton = this.button('+ Fila', 'workbookEditorAddRow', () => this.appendDimension('insert_row'));
            this.colButton = this.button('+ Columna', 'workbookEditorAddColumn', () => this.appendDimension('insert_column'));
            this.downloadButton = this.button('Exportar .xlsx', 'workbookEditorDownload', () => this.download());
            this.csvButton = this.button('Exportar .csv', 'workbookEditorCsv', () => this.download('csv'));
            this.saveMotorButton = this.button('Guardar pieza', 'workbookEditorSaveMotor', () => window.pieceLibrary?.registerCurrent(this));
            this.replacePieceButton = this.button('Importar otro Excel o CSV', 'workbookEditorReplacePiece', () => window.pieceLibrary?.replaceCurrent(this));
            this.reloadButton = this.button('Recargar', 'workbookEditorReload', () => this.reload());
            this.reloadButton.title = 'Volver a cargar la versión guardada';
            this.listingsButton = this.button('Listados', 'workbookEditorListings', async () => {
                if (await this.requestClose()) window.planExcel?.listings();
            });
            toolbar.append(this.sheetSelect, this.saveButton, this.saveMotorButton, this.replacePieceButton, this.listingsButton, this.rowButton, this.colButton, this.downloadButton, this.csvButton, this.reloadButton);
            this.motorRegistrationNotice = this.node('p', 'wbe-motor-registration');
            this.motorRegistrationNotice.id = 'workbookEditorMotorRegistration';
            this.motorRegistrationNotice.hidden = true;
            this.presentationButton = this.button('Cuadrícula', 'workbookEditorPresentation', () => this.togglePresentation());
            this.fitWidthButton = this.button('Ajustar ancho', 'workbookEditorFitWidth', () => this.fitSheet('width'));
            this.fitPageButton = this.button('Ficha completa', 'workbookEditorFitPage', () => this.fitSheet('page'));
            this.zoomSelect = this.node('select', 'wbe-zoom-select');
            this.zoomSelect.id = 'workbookEditorZoom';
            this.zoomSelect.setAttribute('aria-label', 'Ampliación del Excel');
            for (const zoom of [.25, .5, .75, 1, 1.25, 1.5, 2]) this.zoomSelect.append(new Option(`${zoom * 100}%`, String(zoom)));
            this.zoomSelect.value = '1';
            this.zoomSelect.addEventListener('change', () => this.setZoom(Number(this.zoomSelect.value)));
            toolbar.append(this.presentationButton, this.fitWidthButton, this.fitPageButton, this.zoomSelect);

            const formulaRow = this.node('div', 'wbe-formula-row');
            const addressForm = this.node('form', 'wbe-address-form');
            this.address = this.node('input', 'wbe-address-input');
            this.address.id = 'workbookEditorAddress';
            this.address.type = 'text';
            this.address.value = 'A1';
            this.address.setAttribute('aria-label', 'Ir a celda, por ejemplo A155');
            this.address.autocomplete = 'off';
            this.address.addEventListener('input', () => { this.addressDirty = true; });
            const go = this.button('Ir', 'workbookEditorGo', () => this.goToAddress());
            addressForm.addEventListener('submit', (event) => { event.preventDefault(); this.goToAddress(); });
            addressForm.append(this.address, go);
            this.formula = this.node('input', 'wbe-formula-input');
            this.formula.id = 'workbookEditorFormula';
            this.formula.type = 'text';
            this.formula.placeholder = 'Seleccione una celda para editar su valor';
            this.formula.setAttribute('aria-label', 'Valor o fórmula de la celda seleccionada');
            this.formula.autocomplete = 'off';
            this.formula.spellcheck = false;
            this.formula.addEventListener('input', () => { this.formulaDirty = true; this.updatePending(); });
            this.formula.addEventListener('blur', () => this.commitFormula());
            this.formula.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    this.commitFormula();
                    this.viewport.focus();
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    this.formulaDirty = false;
                    this.updateSelectionInputs();
                    this.viewport.focus();
                }
            });
            formulaRow.append(addressForm, this.node('span', 'wbe-formula-label', 'fx'), this.formula);

            this.status = this.node('div', 'wbe-status', 'Toque una celda para editarla. Deslice en ambas direcciones para recorrer la hoja.');
            this.status.id = 'workbookEditorStatus';
            this.status.setAttribute('role', 'status');
            this.status.setAttribute('aria-live', 'polite');
            this.viewport = this.node('div', 'wbe-grid-viewport');
            this.viewport.id = 'workbookEditorViewport';
            this.viewport.tabIndex = 0;
            this.viewport.setAttribute('role', 'grid');
            this.viewport.setAttribute('aria-label', 'Hoja de cálculo editable. Deslice para recorrer todas las filas y columnas.');
            this.canvas = this.node('div', 'wbe-grid-canvas');
            this.dataLayer = this.node('div', 'wbe-data-layer');
            this.borderLayer = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            this.borderLayer.classList.add('wbe-borders-layer');
            this.borderLayer.setAttribute('aria-hidden', 'true');
            this.imageLayer = this.node('div', 'wbe-images-layer');
            this.headersLayer = this.node('div', 'wbe-headers-layer');
            this.canvas.append(this.dataLayer, this.borderLayer, this.imageLayer, this.headersLayer);
            this.viewport.append(this.canvas);
            this.viewport.addEventListener('scroll', () => this.scheduleRender(), { passive: true });
            this.viewport.addEventListener('keydown', (event) => this.gridKeydown(event));
            this.dataLayer.addEventListener('click', (event) => {
                const cell = event.target.closest('[data-wbe-row]');
                if (!cell || this.busy) return;
                this.selectCell(Number(cell.dataset.wbeRow), Number(cell.dataset.wbeCol), false);
                this.beginEdit();
            });
            const footer = this.node('footer', 'wbe-footer');
            this.footerCopy = this.node('span', 'wbe-footer-copy');
            this.pendingLabel = this.node('span', 'wbe-pending', 'Guardado');
            this.calculationNotice = this.node('p', 'wbe-calculation-notice');
            this.calculationNotice.id = 'workbookEditorCalculationNotice';
            this.calculationNotice.hidden = true;
            this.calculationNotice.setAttribute('role', 'note');
            footer.append(this.footerCopy, this.pendingLabel, this.calculationNotice);
            this.panel.append(titlebar, toolbar, this.motorRegistrationNotice, formulaRow, this.status, this.viewport, footer);
            this.overlay.append(this.panel);
            document.body.append(this.overlay);
            this.installDragging(titlebar);
        }

        get hasUnsavedChanges() {
            return this.pending.size > 0 || Boolean(this.formulaDirty) || Boolean(this.activeEdit && this.activeEdit.input.value !== this.activeEdit.originalText);
        }

        get canEdit() { return this.metadata?.can_edit === true; }
        get pieceRegistration() { return this.metadata?.piece_registrations ? this.metadata.piece_registrations[this.sheet] || {registered:false,is_motor:true} : this.metadata?.motor_registration; }

        applyPermissions() {
            this.listingsButton.hidden = this.context !== 'listing' || !this.metadata?.plan_mode;
            this.removeMarkerButton.hidden = !this.removeMarker;
            for (const button of [this.saveButton, this.rowButton, this.colButton]) button.hidden = !this.canEdit;
            if (this.context === 'saved-piece' || this.pieceEdit) this.saveButton.hidden = true;
            this.replacePieceButton.hidden = !this.pieceEdit;
            const registration = this.pieceRegistration;
            this.saveMotorButton.hidden = !(this.metadata?.is_piece || registration?.is_motor) || this.context === 'listing';
            this.saveMotorButton.disabled = (!this.pieceEdit && Boolean(registration?.registered)) || this.busy || this.staleRevision;
            this.motorRegistrationNotice.hidden = !registration?.is_motor || this.context === 'listing';
            this.motorRegistrationNotice.textContent = registration?.registered
                ? (this.context === 'saved-piece' ? `Pieza guardada: ${registration.name}. Puede incorporarlo al Excel de un plano.` : `Pieza guardada: ${registration.name}. Los botones vinculados a esta hoja comparten sus datos.`)
                : 'Ficha independiente. Use Guardar pieza para registrarla con un nombre.';
            if (this.pieceEdit) {
                this.motorRegistrationNotice.hidden = false;
                this.motorRegistrationNotice.textContent = `Modificando «${this.pieceDraftName}». Guardar pieza reemplaza únicamente la hoja seleccionada en el catálogo.`;
            }
            this.formula.readOnly = !this.canEdit || Boolean(this.staleRevision);
            this.formula.placeholder = this.canEdit ? 'Seleccione una celda para editar su valor' : 'Valor de la celda seleccionada';
            this.viewport.setAttribute('aria-readonly', String(!this.canEdit));
            this.viewport.setAttribute('aria-label', this.canEdit ? 'Hoja de cálculo editable. Deslice para recorrer todas las filas y columnas.' : 'Hoja de cálculo de solo lectura. Deslice para recorrer todas las filas y columnas.');
            this.panel.classList.toggle('is-readonly', !this.canEdit);
        }

        async open(metadata) {
            if (this.visible && !(await this.requestClose())) return false;
            const epoch = ++this.epoch;
            this.returnFocus = document.activeElement;
            this.metadata = metadata;
            this.context = metadata.context || 'motor';
            this.pieceEdit = metadata.piece_edit || null;
            this.pieceUnpublished = metadata.piece_unpublished === true;
            this.pieceCategoryId = metadata.piece_category_id || (this.context === 'piece-import' ? null : window.projectWorkspace?.selected?.target?.category_id);
            this.pieceDraftName = metadata.piece_name || '';
            this.selectSourceUrl = metadata.select_source_url || '';
            this.sheetSelect.setAttribute('aria-label', this.selectSourceUrl ? 'Hoja que se incorporará al guardar el plano' : 'Hoja del Excel');
            this.removeMarker = metadata.removeMarker || null;
            this.visible = true;
            this.overlay.hidden = false;
            this.panel.style.left = '';
            this.panel.style.top = '';
            this.panel.style.transform = '';
            this.filename.textContent = metadata.name || 'Excel';
            this.pending.clear();
            this.tiles.clear();
            this.inflight.clear();
            this.sheetPositions.clear();
            this.formulaDirty = false;
            this.addressDirty = false;
            this.staleRevision = false;
            this.formulaTarget = null;
            this.sheet = null;
            this.fitMode = null;
            this.zoom = 1;
            this.previousBodyOverflow = document.body.style.overflow;
            document.body.style.overflow = 'hidden';
            this.applyPermissions();
            this.setBusy(true);
            this.setStatus('Abriendo libro Excel…');
            this.closeButton.focus();
            try {
                const fresh = await this.request(metadata.metadata_url || `/api/pdf/workbooks/${metadata.id}/metadata/`);
                if (epoch !== this.epoch || !this.visible) return false;
                this.metadata = fresh;
                this.applyPermissions();
                this.filename.textContent = fresh.name;
                this.populateSheets();
                this.changeSheet(this.visibleSheets().find((sheet) => sheet.name === metadata.preferred_sheet)?.name || this.visibleSheets()[0]?.name);
                if (this.selectSourceUrl) this.setStatus(`Se incorporará «${this.sheet}» al guardar el plano. Puede elegir otra hoja en el menú.`);
                else this.setStatus(this.canEdit ? (this.context === 'listing' ? 'Listado del plano. Use + Fila para más motores y + Columna para ampliar sus datos. Se incluye al guardar el plano.' : 'Toque una celda para editarla. Los datos se incluyen al guardar el plano.') : 'Solo lectura. Deslice en ambas direcciones para recorrer toda la hoja y seleccione una celda para consultar su valor.');
                this.viewport.focus();
                return true;
            } catch (error) {
                if (epoch === this.epoch) this.setStatus(error.message, 'error');
                return false;
            } finally {
                if (epoch === this.epoch) this.setBusy(false);
            }
        }

        async requestClose() {
            if (!this.visible) return true;
            this.commitAll();
            if (this.busy) return false;
            if (this.pieceEdit) return this.close();
            if (this.hasUnsavedChanges && !(await this.save())) return false;
            return this.close(true);
        }

        close(force = false) {
            if (!this.visible) return true;
            this.commitAll();
            if (!force && this.busy) { this.setStatus('Espere a que termine la operación antes de cerrar.'); return false; }
            if (!force && (this.hasUnsavedChanges || this.pieceUnpublished) && !window.confirm('Hay cambios sin guardar. ¿Desea cerrar y descartarlos?')) return false;
            this.visible = false;
            this.epoch += 1;
            this.selectionRevision += 1;
            this.cancelCellEditor();
            this.overlay.hidden = true;
            this.metadata = null;
            this.pieceEdit = null;
            this.pieceUnpublished = false;
            this.selectSourceUrl = '';
            this.removeMarker = null;
            this.sheet = null;
            this.pending.clear();
            this.tiles.clear();
            this.inflight.clear();
            this.sheetPositions.clear();
            this.formulaDirty = false;
            this.addressDirty = false;
            this.staleRevision = false;
            this.formulaTarget = null;
            this.formula.value = '';
            this.address.value = '';
            this.filename.textContent = '';
            this.sheetSelect.replaceChildren();
            this.dataLayer.replaceChildren();
            this.borderLayer.replaceChildren();
            this.imageLayer.replaceChildren();
            this.headersLayer.replaceChildren();
            this.calculationNotice.hidden = true;
            this.calculationNotice.textContent = '';
            this.canvas.style.width = this.canvas.style.height = '';
            this.setStatus('');
            this.motorRegistrationNotice.hidden = true;
            document.body.style.overflow = this.previousBodyOverflow || '';
            if (this.returnFocus?.isConnected) this.returnFocus.focus();
            return true;
        }

        destroy() {
            this.close(true);
            this.resizeObserver.disconnect();
            document.removeEventListener('keydown', this.documentKeydown);
            window.removeEventListener('beforeunload', this.beforeUnload);
            this.overlay.remove();
        }

        populateSheets() {
            this.sheetSelect.replaceChildren();
            for (const sheet of this.visibleSheets()) {
                const option = this.node('option', '', sheet.name);
                option.value = sheet.name;
                this.sheetSelect.append(option);
            }
        }

        visibleSheets() { return this.metadata?.sheets.filter((sheet) => !['hidden', 'veryHidden'].includes(sheet.state)) || []; }

        currentSheet() {
            return this.metadata?.sheets.find((sheet) => sheet.name === this.sheet);
        }

        get headerWidth() { return this.formMode ? 0 : ROW_HEADER_WIDTH; }
        get headerHeight() { return this.formMode ? 0 : HEADER_HEIGHT; }

        setZoom(zoom, fitMode = null) {
            if (!this.sheet || !Number.isFinite(zoom)) return;
            this.commitAll();
            const top = this.viewport.scrollTop / this.zoom;
            const left = this.viewport.scrollLeft / this.zoom;
            this.zoom = Math.max(.15, Math.min(3, zoom));
            this.fitMode = fitMode;
            this.canvas.style.zoom = this.zoom;
            this.updateChartStrokes();
            this.zoomSelect.querySelector('[data-custom-zoom]')?.remove();
            if (![...this.zoomSelect.options].some((option) => Math.abs(Number(option.value) - this.zoom) < .001)) {
                const option = new Option(`${Math.round(this.zoom * 100)}%`, String(this.zoom));
                option.dataset.customZoom = 'true';
                this.zoomSelect.append(option);
            }
            this.zoomSelect.value = String(this.zoom);
            this.updateCanvasSize();
            this.viewport.scrollTop = fitMode ? 0 : top * this.zoom;
            this.viewport.scrollLeft = fitMode ? 0 : left * this.zoom;
            this.render();
        }

        fitSheet(mode = 'page') {
            if (!this.sheet || !this.colAxis) return;
            const bounds = this.layout.content_bounds;
            const width = this.formMode ? this.sheetWidth : this.headerWidth + this.colAxis.offset((bounds?.max_col || this.currentSheet().columns) + 1);
            const height = this.formMode ? this.sheetHeight : this.headerHeight + this.rowAxis.offset((bounds?.max_row || this.currentSheet().rows) + 1);
            const zoom = (this.viewport.clientWidth - 4) / Math.max(1, width);
            this.setZoom(mode === 'page' ? Math.min(zoom, (this.viewport.clientHeight - 4) / Math.max(1, height)) : zoom, mode);
        }

        togglePresentation() {
            this.commitAll();
            this.formMode = !this.formMode;
            this.prepareLayout();
            this.updateCanvasSize();
            if (this.formMode) this.fitSheet('page');
            else this.setZoom(1);
            this.viewport.scrollTop = this.viewport.scrollLeft = 0;
            this.render();
        }

        async selectWorkbookSheet(name) {
            if (!this.selectSourceUrl) { this.changeSheet(name); return; }
            if (!name || this.busy || name === this.sheet) return;
            const epoch = this.epoch;
            this.setBusy(true);
            try {
                const result = await this.request(this.selectSourceUrl, {
                    method: 'POST',
                    body: JSON.stringify({ action: 'select_source', source_sheet: name, source_workbook_id: this.metadata.id }),
                    headers: { 'Content-Type': 'application/json' },
                });
                if (epoch !== this.epoch) return;
                this.changeSheet(name);
                window.planExcel?.sourceSelected(this.metadata.id, name, result);
                this.setStatus(`Se incorporará «${name}» al guardar el plano.`, 'success');
            } catch (error) {
                if (epoch !== this.epoch) return;
                this.sheetSelect.value = this.sheet;
                this.setStatus(error.message, 'error');
            } finally {
                if (epoch === this.epoch) this.setBusy(false);
            }
        }

        changeSheet(name) {
            if (!name || !this.visibleSheets().some((sheet) => sheet.name === name)) return;
            this.commitAll();
            if (this.sheet) this.sheetPositions.set(this.sheet, { top: this.viewport.scrollTop, left: this.viewport.scrollLeft, row: this.selectedRow, col: this.selectedCol, zoom: this.zoom, fitMode: this.fitMode, formMode: this.formMode });
            this.sheet = name;
            this.applyPermissions();
            this.addressDirty = false;
            this.sheetSelect.value = name;
            const saved = this.sheetPositions.get(name);
            this.selectedRow = saved?.row || 1;
            this.selectedCol = saved?.col || 1;
            const sheet = this.currentSheet();
            this.formMode = saved?.formMode ?? (sheet.layout?.presentation === 'form');
            this.zoom = saved?.zoom || 1;
            this.fitMode = saved?.fitMode || null;
            this.canvas.style.zoom = this.zoom;
            this.showSelection = !this.formMode;
            this.rowCount = Math.min(MAX_ROWS, Math.max(100, sheet.rows + 30, this.selectedRow + 10));
            this.colCount = Math.min(MAX_COLUMNS, Math.max(26, sheet.columns + 5, this.selectedCol + 5));
            this.prepareLayout();
            if (this.formMode && !saved) {
                this.selectedRow = this.layout.content_bounds.min_row;
                this.selectedCol = this.layout.content_bounds.min_col;
            }
            this.selectedRow = this.rowAxis.visible(this.selectedRow, MAX_ROWS);
            this.selectedCol = this.colAxis.visible(this.selectedCol, MAX_COLUMNS);
            this.updateCanvasSize();
            this.viewport.scrollTop = saved?.top || 0;
            this.viewport.scrollLeft = saved?.left || 0;
            this.updateSelectionInputs();
            if (this.formMode && !saved) this.fitSheet('page');
            else this.setZoom(this.zoom, this.fitMode);
            this.render();
        }

        updateCanvasSize() {
            const bounds = this.layout.content_bounds;
            this.sheetWidth = this.headerWidth + this.colAxis.offset((this.formMode ? bounds.max_col : this.colCount) + 1) - this.originX;
            this.sheetHeight = this.headerHeight + this.rowAxis.offset((this.formMode ? bounds.max_row : this.rowCount) + 1) - this.originY;
            this.canvas.style.width = `${this.sheetWidth}px`;
            this.canvas.style.height = `${this.sheetHeight}px`;
            this.borderLayer.setAttribute('width', this.sheetWidth);
            this.borderLayer.setAttribute('height', this.sheetHeight);
            this.borderLayer.setAttribute('viewBox', `0 0 ${this.sheetWidth} ${this.sheetHeight}`);
            this.viewport.setAttribute('aria-rowcount', this.rowCount);
            this.viewport.setAttribute('aria-colcount', this.colCount);
            const sheet = this.currentSheet();
            this.footerCopy.textContent = `${sheet?.rows || 0} filas · ${sheet?.columns || 0} columnas · ${this.sheet || ''}`;
        }

        prepareLayout() {
            const layout = this.currentSheet()?.layout || {};
            this.layout = layout;
            this.calculationNotice.hidden = !layout.has_formulas;
            this.calculationNotice.textContent = layout.has_formulas ? (layout.calculation_notice || 'Las fórmulas muestran el resultado guardado en Excel y no se recalculan aquí. Después de editar, descargue y recalcule el libro en Excel.') : '';
            this.rowAxis = new DimensionAxis(layout.default_row_height || ROW_HEIGHT, layout.row_heights, layout.hidden_rows);
            this.colAxis = new DimensionAxis(layout.default_column_width || COLUMN_WIDTH, layout.column_widths, layout.hidden_columns);
            this.originX = this.formMode && layout.content_bounds ? this.colAxis.offset(layout.content_bounds.min_col) : 0;
            this.originY = this.formMode && layout.content_bounds ? this.rowAxis.offset(layout.content_bounds.min_row) : 0;
            this.panel.classList.toggle('is-form-view', this.formMode);
            this.presentationButton.hidden = layout.presentation !== 'form';
            this.presentationButton.textContent = this.formMode ? 'Ver cuadrícula' : 'Vista ficha';
            this.fitPageButton.hidden = layout.presentation !== 'form';
            this.canvas.classList.toggle('without-gridlines', this.formMode || layout.show_gridlines === false);
            this.imageLayer.replaceChildren();
            for (const image of [...layout.images || [], ...layout.charts || []]) {
                // Images originate in the validated XLSX package, never remote URLs.
                if (typeof image.src !== 'string' || !/^data:image\/(?:png|jpeg|gif|webp|bmp|svg\+xml);base64,/i.test(image.src)) continue;
                const node = this.node('img', `wbe-sheet-image${image.src.startsWith('data:image/svg+xml;') ? ' wbe-sheet-chart' : ''}`);
                node.src = image.src;
                if (image.src.startsWith('data:image/svg+xml;')) node.dataset.originalSrc = image.src;
                node.alt = image.description || 'Imagen del Excel';
                node.draggable = false;
                Object.assign(node.style, { left: `${this.headerWidth + Number(image.x || 0) - this.originX}px`, top: `${this.headerHeight + Number(image.y || 0) - this.originY}px`, width: `${Number(image.width) || 0}px`, height: `${Number(image.height) || 0}px` });
                this.imageLayer.append(node);
            }
            this.updateChartStrokes();
        }

        updateChartStrokes() {
            for (const image of this.imageLayer.querySelectorAll('[data-original-src]')) {
                const xml = atob(image.dataset.originalSrc.split(',')[1]);
                const adjusted = xml.replace(/stroke-width="([\d.e+-]+)"/gi, (_, value) => `stroke-width="${Math.max(Number(value), 1 / this.zoom)}"`);
                image.src = 'data:image/svg+xml;base64,' + btoa(adjusted);
            }
        }

        cellBorders(cell, value, row, col, merge, borders) {
            const rect = this.cellRect(row, col, merge);
            for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
                const border = value.style?.[`border${side}`];
                const match = /^(\d+(?:\.\d+)?)px (\w+) (#[\da-f]+)$/i.exec(border || '');
                if (!match) continue;
                // Paint each boundary once, centered on the shared edge. Two
                // neighboring CSS boxes otherwise double thin table borders.
                cell.style[`border${side}Color`] = 'transparent';
                const horizontal = side === 'Top' || side === 'Bottom';
                const position = horizontal ? rect.top + (side === 'Bottom' ? rect.height : 0) : rect.left + (side === 'Right' ? rect.width : 0);
                const start = horizontal ? rect.left : rect.top;
                const end = start + (horizontal ? rect.width : rect.height);
                const key = `${horizontal}:${position}:${start}:${end}`;
                const candidate = { horizontal, position, start, end, width: Number(match[1]), style: match[2], color: match[3] };
                if (!borders.has(key) || borders.get(key).width < candidate.width) borders.set(key, candidate);
            }
        }

        drawBorders(borders) {
            const fragment = document.createDocumentFragment();
            const groups = new Map();
            for (const border of borders.values()) {
                const key = `${border.horizontal}:${border.position}:${border.width}:${border.style}:${border.color}`;
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(border);
            }
            const combined = [];
            for (const group of groups.values()) {
                let current;
                for (const border of group.sort((a, b) => a.start - b.start)) {
                    if (current && border.start <= current.end + .001) current.end = Math.max(current.end, border.end);
                    else { current = { ...border }; combined.push(current); }
                }
            }
            for (const border of combined.sort((a, b) => a.width - b.width)) {
                const width = Math.max(border.style === 'double' ? border.width / 3 : border.width, 1 / this.zoom);
                for (const offset of border.style === 'double' ? [-border.width / 3, border.width / 3] : [0]) {
                    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                    const limit = border.horizontal ? this.sheetHeight : this.sheetWidth;
                    const position = Math.max(width / 2, Math.min(limit - width / 2, border.position + offset));
                    const attributes = border.horizontal ? { x1: border.start, x2: border.end, y1: position, y2: position } : { y1: border.start, y2: border.end, x1: position, x2: position };
                    for (const [name, value] of Object.entries(attributes)) line.setAttribute(name, value);
                    line.setAttribute('stroke', border.color);
                    line.setAttribute('stroke-width', width);
                    if (border.style === 'dotted') line.setAttribute('stroke-dasharray', `${1 / this.zoom} ${2 / this.zoom}`);
                    else if (border.style.includes('dash')) line.setAttribute('stroke-dasharray', `${4 / this.zoom} ${2 / this.zoom}`);
                    fragment.append(line);
                }
            }
            this.borderLayer.replaceChildren(fragment);
        }

        mergeAt(row, col) {
            return (this.layout?.merges || []).find((merge) => row >= merge.min_row && row <= merge.max_row && col >= merge.min_col && col <= merge.max_col);
        }

        cellRect(row, col, merge = this.mergeAt(row, col)) {
            const startRow = merge?.min_row || row;
            const startCol = merge?.min_col || col;
            return {
                left: this.headerWidth + this.colAxis.offset(startCol) - this.originX,
                top: this.headerHeight + this.rowAxis.offset(startRow) - this.originY,
                width: this.colAxis.offset((merge?.max_col || col) + 1) - this.colAxis.offset(startCol),
                height: this.rowAxis.offset((merge?.max_row || row) + 1) - this.rowAxis.offset(startRow),
            };
        }

        positionNode(node, rect) {
            for (const key of ['left', 'top', 'width', 'height']) node.style[key] = `${rect[key]}px`;
        }

        applyCellStyle(cell, style = {}) {
            const combined = { ...this.layout?.default_style, ...style };
            for (const property of CELL_STYLE_PROPERTIES) {
                if (combined[property] !== undefined && combined[property] !== null) cell.style[property] = combined[property];
            }
            const vertical = combined.verticalAlign || 'bottom';
            cell.style.justifyContent = vertical === 'center' || vertical === 'middle' ? 'center' : vertical === 'top' ? 'flex-start' : 'flex-end';
        }

        fitCellText(cell, content, value) {
            if (!value.style?.shrinkToFit || !content.textContent) return;
            const style = { ...this.layout?.default_style, ...value.style };
            const size = parseFloat(style.fontSize || '14.667');
            this.textMeasure ||= document.createElement('canvas').getContext('2d');
            this.textMeasure.font = `${style.fontStyle || 'normal'} ${style.fontWeight || 'normal'} ${size}px ${style.fontFamily || 'Arial'}`;
            const measured = Math.max(...content.textContent.split('\n').map((line) => this.textMeasure.measureText(line).width));
            const available = parseFloat(cell.style.width) - 4;
            if (measured > available && available > 0) content.style.fontSize = `${Math.max(1, size * available / measured)}px`;
        }

        allowTextOverflow(cell, content, value, row, col, lastColumn) {
            const style = { ...this.layout?.default_style, ...value.style };
            if (typeof value.value !== 'string' || !value.value || value.value.startsWith('=') || this.mergeAt(row, col) || (style.whiteSpace && !['nowrap', 'pre'].includes(style.whiteSpace)) || style.shrinkToFit || (style.textAlign && !['left', 'general', 'start'].includes(style.textAlign))) return;
            let end = col;
            for (let next = col + 1; next <= Math.min(this.colCount, lastColumn); next += 1) {
                if (!this.colAxis.size(next)) continue;
                if (this.mergeAt(row, next) || !this.tiles.has(this.tileInfo(row, next).key)) break;
                const neighbor = this.cellValue(row, next).value;
                if (neighbor !== null && neighbor !== undefined && neighbor !== '') break;
                end = next;
            }
            if (end === col) return;
            cell.classList.add('has-overflow');
            content.style.maxWidth = 'none';
            content.style.width = `${Math.max(0, this.colAxis.offset(end + 1) - this.colAxis.offset(col) - 4)}px`;
        }

        cellKey(sheet, row, col) { return JSON.stringify([sheet, row, col]); }
        tileInfo(row, col, sheet = this.sheet) {
            const startRow = Math.floor((row - 1) / TILE_ROWS) * TILE_ROWS + 1;
            const startCol = Math.floor((col - 1) / TILE_COLUMNS) * TILE_COLUMNS + 1;
            return { sheet, startRow, startCol, key: this.cellKey(sheet, startRow, startCol) };
        }

        cellValue(row, col, sheet = this.sheet) {
            const pending = this.pending.get(this.cellKey(sheet, row, col));
            const original = this.tiles.get(this.tileInfo(row, col, sheet).key)?.get(`${row}:${col}`) || { value: null };
            if (pending) return { ...original, value: pending.value, display: rawText(pending.value) };
            return original;
        }

        async loadTile(info) {
            if (this.staleRevision) return;
            if (this.tiles.has(info.key)) return;
            if (this.inflight.has(info.key)) return this.inflight.get(info.key);
            const epoch = this.epoch;
            const workbookRevision = this.metadata.revision;
            const url = new URL(this.metadata.cells_url || `/api/pdf/workbooks/${this.metadata.id}/cells/`, location.href);
            url.search = new URLSearchParams({ sheet: info.sheet, row: info.startRow, col: info.startCol, rows: Math.min(TILE_ROWS, MAX_ROWS - info.startRow + 1), cols: Math.min(TILE_COLUMNS, MAX_COLUMNS - info.startCol + 1) });
            const promise = this.request(url.pathname + url.search).then((data) => {
                if (epoch !== this.epoch || !this.visible || workbookRevision !== this.metadata.revision) return;
                if (data.revision !== undefined && data.revision !== this.metadata.revision) {
                    this.markStale();
                    return;
                }
                const cells = new Map();
                for (const cell of data.cells) cells.set(`${cell.row}:${cell.col}`, cell);
                this.tiles.set(info.key, cells);
                if (this.tiles.size > 64) this.tiles.delete(this.tiles.keys().next().value);
                this.scheduleRender();
                if (!this.activeEdit && !this.formulaDirty) this.updateSelectionInputs();
            }).finally(() => { if (epoch === this.epoch && this.inflight.get(info.key) === promise) this.inflight.delete(info.key); });
            this.inflight.set(info.key, promise);
            return promise;
        }

        scheduleRender() {
            if (!this.visible || this.renderScheduled) return;
            this.renderScheduled = true;
            requestAnimationFrame(() => { this.renderScheduled = false; if (this.visible) this.render(); });
        }

        render() {
            if (!this.visible || !this.sheet) return;
            const top = this.viewport.scrollTop / this.zoom;
            const left = this.viewport.scrollLeft / this.zoom;
            const bounds = this.formMode ? this.layout.content_bounds : { min_row: 1, min_col: 1, max_row: this.rowCount, max_col: this.colCount };
            const rowStart = Math.max(bounds.min_row, this.rowAxis.at(Math.max(0, top + this.originY - ROW_HEIGHT), this.rowCount));
            const colStart = Math.max(bounds.min_col, this.colAxis.at(Math.max(0, left + this.originX - COLUMN_WIDTH), this.colCount));
            const rowEnd = Math.min(bounds.max_row, this.rowCount, this.rowAxis.at(top + this.originY + this.viewport.clientHeight / this.zoom + ROW_HEIGHT * 2, this.rowCount));
            const colEnd = Math.min(bounds.max_col, this.colCount, this.colAxis.at(left + this.originX + this.viewport.clientWidth / this.zoom + COLUMN_WIDTH * 2, this.colCount));
            const cells = document.createDocumentFragment();
            const headers = document.createDocumentFragment();
            const required = new Map();
            const drawn = new Set();
            const borders = new Map();
            for (let row = rowStart; row <= rowEnd; row += 1) {
                if (!this.rowAxis.size(row)) continue;
                const rowHeader = this.node('div', `wbe-row-header${row === this.selectedRow ? ' is-selected' : ''}`, row);
                this.positionNode(rowHeader, { left, top: this.headerHeight + this.rowAxis.offset(row) - this.originY, width: this.headerWidth, height: this.rowAxis.size(row) });
                rowHeader.setAttribute('role', 'rowheader');
                headers.append(rowHeader);
                for (let col = colStart; col <= colEnd; col += 1) {
                    if (!this.colAxis.size(col)) continue;
                    const merge = this.mergeAt(row, col);
                    const cellRow = merge?.min_row || row;
                    const cellCol = merge?.min_col || col;
                    const key = this.cellKey(this.sheet, cellRow, cellCol);
                    if (drawn.has(key)) continue;
                    drawn.add(key);
                    const selected = this.showSelection && cellRow === this.selectedRow && cellCol === this.selectedCol;
                    const value = this.cellValue(cellRow, cellCol);
                    const cell = this.node('div', `wbe-cell${merge ? ' is-merged' : ''}${selected ? ' is-selected' : ''}${this.pending.has(key) ? ' is-edited' : ''}${typeof value.value === 'number' ? ' is-number' : ''}`);
                    const content = this.node('span', 'wbe-cell-text', rawText(value.display ?? value.value));
                    cell.append(content);
                    cell.dataset.wbeRow = cellRow;
                    cell.dataset.wbeCol = cellCol;
                    cell.id = `wbe-cell-${cellRow}-${cellCol}`;
                    cell.setAttribute('role', 'gridcell');
                    cell.setAttribute('aria-rowindex', cellRow);
                    cell.setAttribute('aria-colindex', cellCol);
                    cell.setAttribute('aria-selected', selected);
                    cell.setAttribute('aria-readonly', String(!this.canEdit));
                    if (merge) {
                        cell.setAttribute('aria-rowspan', merge.max_row - merge.min_row + 1);
                        cell.setAttribute('aria-colspan', merge.max_col - merge.min_col + 1);
                    }
                    cell.setAttribute('aria-label', `${columnName(cellCol)}${cellRow}: ${rawText(value.value)}`);
                    if (value.formula_cached === false) cell.title = 'Esta fórmula no tiene un resultado guardado. Descargue y recalcule el libro en Excel.';
                    this.positionNode(cell, this.cellRect(cellRow, cellCol, merge));
                    this.applyCellStyle(cell, value.style);
                    this.cellBorders(cell, value, cellRow, cellCol, merge, borders);
                    this.fitCellText(cell, content, value);
                    this.allowTextOverflow(cell, content, value, cellRow, cellCol, colEnd);
                    cells.append(cell);
                    const tile = this.tileInfo(cellRow, cellCol);
                    required.set(tile.key, tile);
                }
            }
            for (let col = colStart; col <= colEnd; col += 1) {
                if (!this.colAxis.size(col)) continue;
                const header = this.node('div', `wbe-column-header${col === this.selectedCol ? ' is-selected' : ''}`, columnName(col));
                this.positionNode(header, { left: this.headerWidth + this.colAxis.offset(col) - this.originX, top, width: this.colAxis.size(col), height: this.headerHeight });
                header.setAttribute('role', 'columnheader');
                headers.append(header);
            }
            const corner = this.node('div', 'wbe-corner');
            Object.assign(corner.style, { left: `${left}px`, top: `${top}px`, width: `${this.headerWidth}px`, height: `${this.headerHeight}px` });
            headers.append(corner);
            this.dataLayer.replaceChildren(cells);
            this.drawBorders(borders);
            this.headersLayer.replaceChildren(headers);
            this.viewport.setAttribute('aria-activedescendant', `wbe-cell-${this.selectedRow}-${this.selectedCol}`);
            for (const info of required.values()) {
                this.loadTile(info).catch((error) => { if (this.visible) this.setStatus(error.message, 'error'); });
            }
            this.updatePending();
        }

        updateSelectionInputs() {
            if (!this.sheet) return;
            const value = this.cellValue(this.selectedRow, this.selectedCol);
            const known = this.pending.has(this.cellKey(this.sheet, this.selectedRow, this.selectedCol)) || this.tiles.has(this.tileInfo(this.selectedRow, this.selectedCol).key);
            this.formula.disabled = this.busy || !known;
            this.formula.readOnly = !this.canEdit || Boolean(this.staleRevision);
            // Preserve a typed address through blur onto the Go button, too.
            if (!this.addressDirty) this.address.value = `${columnName(this.selectedCol)}${this.selectedRow}`;
            if (!this.formulaDirty) {
                this.formula.value = rawText(value.value);
                this.formulaTarget = { sheet: this.sheet, row: this.selectedRow, col: this.selectedCol };
            }
        }

        selectCell(row, col, ensureVisible = true) {
            if (!this.sheet) return;
            this.commitAll();
            if (this.formMode && this.layout.content_bounds && (row < this.layout.content_bounds.min_row || row > this.layout.content_bounds.max_row || col < this.layout.content_bounds.min_col || col > this.layout.content_bounds.max_col)) {
                this.formMode = false;
                this.fitMode = null;
                this.prepareLayout();
            }
            this.showSelection = true;
            this.addressDirty = false;
            this.selectionRevision += 1;
            this.selectedRow = this.rowAxis.visible(Math.max(1, Math.min(MAX_ROWS, row)), MAX_ROWS, row < this.selectedRow ? -1 : 1);
            this.selectedCol = this.colAxis.visible(Math.max(1, Math.min(MAX_COLUMNS, col)), MAX_COLUMNS, col < this.selectedCol ? -1 : 1);
            const merge = this.mergeAt(this.selectedRow, this.selectedCol);
            if (merge) { this.selectedRow = merge.min_row; this.selectedCol = merge.min_col; }
            this.rowCount = Math.min(MAX_ROWS, Math.max(this.rowCount, this.selectedRow + 10));
            this.colCount = Math.min(MAX_COLUMNS, Math.max(this.colCount, this.selectedCol + 5));
            this.updateCanvasSize();
            if (ensureVisible) this.ensureSelectedVisible();
            this.updateSelectionInputs();
            this.render();
        }

        ensureSelectedVisible() {
            const rect = this.cellRect(this.selectedRow, this.selectedCol);
            const x = rect.left * this.zoom, y = rect.top * this.zoom;
            const width = rect.width * this.zoom, height = rect.height * this.zoom;
            const headerWidth = this.headerWidth * this.zoom, headerHeight = this.headerHeight * this.zoom;
            if (x < this.viewport.scrollLeft + headerWidth) this.viewport.scrollLeft = x - headerWidth;
            else if (x + width > this.viewport.scrollLeft + this.viewport.clientWidth) this.viewport.scrollLeft = Math.min(x - headerWidth, x + width - this.viewport.clientWidth);
            if (y < this.viewport.scrollTop + headerHeight) this.viewport.scrollTop = y - headerHeight;
            else if (y + height > this.viewport.scrollTop + this.viewport.clientHeight) this.viewport.scrollTop = Math.min(y - headerHeight, y + height - this.viewport.clientHeight);
        }

        goToAddress() {
            const address = parseAddress(this.address.value);
            if (!address) { this.setStatus('Escriba una celda válida, por ejemplo A1, Z155 o AA500.', 'error'); return; }
            this.selectCell(address.row, address.col);
            this.viewport.focus();
            const selected = `${columnName(this.selectedCol)}${this.selectedRow}`;
            this.address.value = selected;
            if (this.staleRevision) this.markStale();
            else this.setStatus(`Celda ${selected}.${this.canEdit ? ' Pulse Enter o toque la celda para editar.' : ' Solo lectura.'}`);
        }

        async beginEdit(initialText) {
            if (!this.canEdit || this.busy || this.staleRevision || !this.visible || !this.sheet) return;
            this.commitAll();
            const revision = ++this.selectionRevision;
            const row = this.selectedRow;
            const col = this.selectedCol;
            const sheet = this.sheet;
            try { await this.loadTile(this.tileInfo(row, col)); }
            catch (error) { this.setStatus(error.message, 'error'); return; }
            if (revision !== this.selectionRevision || !this.canEdit || !this.visible || this.busy || this.sheet !== sheet) return;
            this.updateSelectionInputs();
            const originalText = rawText(this.cellValue(row, col).value);
            const merge = this.mergeAt(row, col);
            const multiline = window.workbookFields.isNotesField(merge);
            const input = this.node(multiline ? 'textarea' : 'input', 'wbe-cell-editor');
            input.id = 'workbookEditorCellInput';
            if (!multiline) input.type = 'text';
            input.autocomplete = 'off';
            input.spellcheck = false;
            input.setAttribute('aria-label', `Editar celda ${columnName(col)}${row}`);
            input.value = initialText === undefined ? originalText : initialText;
            this.positionNode(input, this.cellRect(row, col));
            this.applyCellStyle(input, this.cellValue(row, col).style);
            this.activeEdit = { input, sheet, row, col, originalText };
            this.canvas.append(input);
            input.addEventListener('input', () => this.updatePending());
            input.addEventListener('blur', () => this.commitCellEditor());
            input.addEventListener('keydown', (event) => {
                event.stopPropagation();
                if (event.key === 'Escape') {
                    event.preventDefault();
                    this.cancelCellEditor();
                    this.viewport.focus();
                } else if (event.key === 'Tab' || (event.key === 'Enter' && (!multiline || event.ctrlKey || event.metaKey))) {
                    event.preventDefault();
                    this.commitCellEditor();
                    if (event.key === 'Tab') this.selectCell(row, event.shiftKey ? col - 1 : (merge?.max_col ?? col) + 1);
                    else this.selectCell(event.shiftKey ? row - 1 : (merge?.max_row ?? row) + 1, col);
                    this.viewport.focus();
                }
            });
            input.focus();
            if (initialText === undefined) input.select();
            else input.setSelectionRange(input.value.length, input.value.length);
            this.updatePending();
        }

        commitCellEditor() {
            const edit = this.activeEdit;
            if (!edit) return;
            this.activeEdit = null;
            if (edit.input.value !== edit.originalText) this.setPending(edit.sheet, edit.row, edit.col, parsedValue(edit.input.value));
            edit.input.remove();
            this.updateSelectionInputs();
            this.scheduleRender();
        }

        cancelCellEditor() {
            const edit = this.activeEdit;
            this.activeEdit = null;
            edit?.input.remove();
            this.updatePending();
        }

        commitFormula() {
            if (!this.canEdit || !this.formulaDirty || !this.formulaTarget || !this.visible || this.staleRevision) return;
            const target = this.formulaTarget;
            this.formulaDirty = false;
            this.setPending(target.sheet, target.row, target.col, parsedValue(this.formula.value));
            this.scheduleRender();
        }

        commitAll() {
            this.commitCellEditor();
            this.commitFormula();
        }

        setPending(sheet, row, col, value) {
            if (!this.canEdit || this.staleRevision) return;
            const key = this.cellKey(sheet, row, col);
            const original = this.tiles.get(this.tileInfo(row, col, sheet).key)?.get(`${row}:${col}`)?.value ?? null;
            if (Object.is(original, value)) this.pending.delete(key);
            else this.pending.set(key, { sheet, row, col, value });
            if (this.pieceEdit) this.pieceUnpublished = true;
            this.updatePending();
        }

        updatePending() {
            const count = this.pending.size;
            const draft = this.formulaDirty || (this.activeEdit && this.activeEdit.input.value !== this.activeEdit.originalText);
            this.pendingLabel.textContent = !this.canEdit ? 'Solo lectura' : count || draft ? `${count + (draft ? 1 : 0)} cambio${count + (draft ? 1 : 0) === 1 ? '' : 's'} sin guardar` : this.pieceUnpublished ? 'Cambios pendientes de Guardar pieza' : 'Sin cambios pendientes';
            this.saveButton.disabled = !this.canEdit || this.busy || this.staleRevision;
            this.saveMotorButton.disabled = (!this.pieceEdit && Boolean(this.pieceRegistration?.registered)) || this.busy || this.staleRevision;
            this.replacePieceButton.disabled = this.busy || this.staleRevision;
            this.removeMarkerButton.disabled = this.busy;
        }

        setStatus(text, kind = '') {
            this.status.textContent = text;
            this.status.className = `wbe-status${kind ? ` is-${kind}` : ''}`;
        }

        setBusy(value) {
            this.busy = value;
            for (const element of [this.sheetSelect, this.rowButton, this.colButton, this.downloadButton, this.csvButton, this.reloadButton, this.formula]) element.disabled = value;
            if (!this.canEdit || this.staleRevision) {
                this.rowButton.disabled = true;
                this.colButton.disabled = true;
            }
            this.updateSelectionInputs();
            this.updatePending();
        }

        async save() {
            if (!this.canEdit) return false;
            this.commitAll();
            if (this.staleRevision) { this.markStale(); return false; }
            if (this.busy || !this.metadata) return false;
            if (!this.pending.size) { this.setStatus('Sin cambios pendientes.', 'success'); return true; }
            const epoch = this.epoch;
            const edits = [...this.pending.values()];
            this.setBusy(true);
            this.setStatus('Guardando cambios en el archivo Excel…');
            try {
                const result = await this.request(this.metadata.save_url || `/api/pdf/workbooks/${this.metadata.id}/save/`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ revision: this.metadata.revision, edits, sheet: this.sheet, context: this.context }),
                });
                if (epoch !== this.epoch || !this.visible) return false;
                this.metadata = result;
                this.applyPermissions();
                this.prepareLayout();
                this.pending.clear();
                this.tiles.clear();
                this.inflight.clear();
                this.populateSheets();
                this.sheetSelect.value = this.sheet;
                const plan = window.projectWorkspace?.active?.plan;
                const savedForPlan = this.metadata.id === plan?.workbook?.id || this.context === 'button-source';
                this.setStatus(savedForPlan ? 'Cambios de Excel guardados. Usa «Guardar plano» para publicarlos en Drive.' : 'Cambios de Excel guardados.', 'success');
                this.updateCanvasSize();
                this.updateSelectionInputs();
                this.render();
                this.notifySaved(result);
                return true;
            } catch (error) {
                if (epoch === this.epoch) {
                    if (error.status === 409) this.markStale();
                    else this.setStatus(error.message, 'error');
                }
                return false;
            } finally {
                if (epoch === this.epoch) this.setBusy(false);
            }
        }

        async appendDimension(type) {
            if (!this.canEdit || this.busy || this.staleRevision || !this.metadata) return;
            this.commitAll();
            if (this.pending.size && !(await this.save())) return;
            const sheet = this.currentSheet();
            const isRow = type === 'insert_row';
            const motorListing = this.context === 'listing';
            const index = (isRow ? sheet.rows : this.context === 'listing' ? sheet.layout?.content_bounds?.max_col || sheet.columns : sheet.columns) + 1;
            if (index > (isRow ? MAX_ROWS : MAX_COLUMNS)) { this.setStatus('La hoja ya tiene el máximo de filas o columnas de Excel.', 'error'); return; }
            const epoch = this.epoch;
            this.setBusy(true);
            this.setStatus(isRow ? 'Agregando fila…' : 'Agregando columna…');
            try {
                const operation = { type, sheet: this.sheet, index };
                const edits = [];
                if (motorListing) {
                    operation.copy_from = index - 1;
                    if (isRow) edits.push({ sheet: this.sheet, row: index, col: 2, value: index - 7 });
                    else {
                        operation.extend_header_through_row = 6;
                        edits.push({ sheet: this.sheet, row: 7, col: index, value: 'Nueva columna' });
                    }
                }
                const result = await this.request(this.metadata.save_url || `/api/pdf/workbooks/${this.metadata.id}/save/`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ revision: this.metadata.revision, edits, operations: [operation], sheet: this.sheet, context: this.context }),
                });
                if (epoch !== this.epoch || !this.visible) return;
                this.metadata = result;
                this.applyPermissions();
                this.prepareLayout();
                this.tiles.clear();
                this.inflight.clear();
                this.rowCount = Math.min(MAX_ROWS, Math.max(this.rowCount, this.currentSheet().rows + 30));
                this.colCount = Math.min(MAX_COLUMNS, Math.max(this.colCount, this.currentSheet().columns + 5));
                this.selectCell(isRow ? index : motorListing ? 7 : this.selectedRow, isRow ? motorListing ? 3 : this.selectedCol : index);
                this.setStatus(isRow ? 'Fila agregada. Toque una celda de la nueva fila para escribir.' : 'Columna agregada. Toque una celda de la nueva columna para escribir.', 'success');
                this.notifySaved(result);
            } catch (error) { if (epoch === this.epoch) this.setStatus(error.message, 'error'); }
            finally { if (epoch === this.epoch) this.setBusy(false); }
        }

        notifySaved(metadata) {
            if (this.pieceEdit) { this.pieceUnpublished = true; this.updatePending(); return; }
            try { this.onSaved?.(metadata); } catch (_) { /* A host callback must not hide a successful save. */ }
            document.dispatchEvent(new CustomEvent('workbook:saved', { detail: metadata }));
        }

        markStale() {
            this.staleRevision = true;
            this.setStatus('Otro usuario guardó cambios en este Excel. Sus cambios siguen aquí. Copie los valores que necesite y use Recargar para abrir la versión actual.', 'error');
            this.rowButton.disabled = true;
            this.colButton.disabled = true;
            this.formula.readOnly = true;
            if (this.activeEdit) this.activeEdit.input.readOnly = true;
            this.updatePending();
        }

        async reload() {
            if (this.busy || !this.metadata) return;
            this.commitAll();
            if (this.hasUnsavedChanges && !window.confirm('Recargar descartará sus cambios sin guardar. ¿Desea continuar?')) return;
            const metadata = { ...this.metadata, context: this.context, preferred_sheet: this.sheet,
                piece_edit:this.pieceEdit, piece_unpublished:this.pieceUnpublished, piece_name:this.pieceDraftName, piece_category_id:this.pieceCategoryId,
                removeMarker: this.removeMarker, select_source_url: this.selectSourceUrl };
            this.close(true);
            await this.open(metadata);
        }

        async download(format = 'xlsx') {
            if (this.busy || !this.metadata) return;
            this.commitAll();
            if (this.pending.size && !(await this.save())) return;
            const link = this.node('a');
            link.href = format === 'csv' ? `${this.metadata.csv_url || `/api/pdf/workbooks/${this.metadata.id}/export.csv`}?sheet=${encodeURIComponent(this.sheet)}` : this.metadata.download_url || `/api/pdf/workbooks/${this.metadata.id}/download/`;
            link.download = format === 'csv' ? this.metadata.name.replace(/\.xlsx$/i, '') + '.csv' : this.metadata.name;
            const merges = this.currentSheet()?.layout?.merges || [];
            let blobUrl;
            if (format === 'csv' && window.workbookFields.hasCsvFields(merges) && typeof window.PlanosAndroid?.download !== 'function') {
                const epoch = this.epoch;
                this.setBusy(true);
                try {
                    const response = await fetch(link.href, { credentials: 'same-origin', cache: 'no-store' });
                    if (response.status === 401) window.appAuth?.expire();
                    if (!response.ok) throw new Error('No se pudo exportar la hoja a CSV.');
                    const csv = window.workbookFields.compactCsv(await response.text(), merges);
                    if (this.epoch !== epoch || !this.visible) return;
                    blobUrl = URL.createObjectURL(new Blob([csv.startsWith('\uFEFF') ? csv : '\uFEFF' + csv], { type: 'text/csv;charset=utf-8' }));
                    link.href = blobUrl;
                    link.download = `${this.metadata.name.replace(/\.xlsx$/i, '')}-${this.sheet}.csv`;
                } catch (error) {
                    if (this.epoch === epoch) this.setStatus(error.message, 'error');
                    return;
                } finally {
                    if (this.epoch === epoch) this.setBusy(false);
                }
            }
            document.body.append(link);
            link.click();
            link.remove();
            if (blobUrl) setTimeout(() => URL.revokeObjectURL(blobUrl), 30_000);
        }

        gridKeydown(event) {
            if (event.target !== this.viewport || this.busy || !this.sheet) return;
            const delta = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[event.key];
            if (delta) {
                event.preventDefault();
                const merge = this.mergeAt(this.selectedRow, this.selectedCol);
                this.selectCell((delta[0] > 0 ? merge?.max_row || this.selectedRow : this.selectedRow) + delta[0], (delta[1] > 0 ? merge?.max_col || this.selectedCol : this.selectedCol) + delta[1]);
            }
            else if (event.key === 'Enter' || event.key === 'F2') { event.preventDefault(); this.beginEdit(); }
            else if ((event.key === 'Delete' || event.key === 'Backspace') && this.canEdit && !this.staleRevision) { event.preventDefault(); this.setPending(this.sheet, this.selectedRow, this.selectedCol, null); this.render(); this.updateSelectionInputs(); }
            else if (event.key === 'Tab') { event.preventDefault(); const merge = this.mergeAt(this.selectedRow, this.selectedCol); this.selectCell(this.selectedRow, (event.shiftKey ? this.selectedCol : merge?.max_col || this.selectedCol) + (event.shiftKey ? -1 : 1)); }
            else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); this.beginEdit(event.key); }
        }

        dialogKeydown(event) {
            if (!this.visible) return;
            if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (this.pieceEdit) window.pieceLibrary?.registerCurrent(this); else this.save(); return; }
            if (event.key === 'Escape' && !this.activeEdit && event.target !== this.formula) { event.preventDefault(); this.requestClose(); return; }
            if (event.key !== 'Tab' || event.target === this.viewport || event.target === this.activeEdit?.input) return;
            const elements = [...this.panel.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]')].filter((element) => element.getClientRects().length);
            const first = elements[0];
            const last = elements[elements.length - 1];
            if (event.shiftKey && event.target === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && event.target === last) { event.preventDefault(); first.focus(); }
        }

        installDragging(titlebar) {
            let drag = null;
            titlebar.addEventListener('pointerdown', (event) => {
                if (window.innerWidth <= 600 || event.button !== 0 || event.target.closest('button')) return;
                const bounds = this.panel.getBoundingClientRect();
                drag = { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
                this.panel.style.left = `${bounds.left}px`;
                this.panel.style.top = `${bounds.top}px`;
                this.panel.style.transform = 'none';
                titlebar.setPointerCapture(event.pointerId);
            });
            titlebar.addEventListener('pointermove', (event) => {
                if (!drag) return;
                this.panel.style.left = `${Math.max(0, Math.min(window.innerWidth - this.panel.offsetWidth, event.clientX - drag.x))}px`;
                this.panel.style.top = `${Math.max(0, Math.min(window.innerHeight - 50, event.clientY - drag.y))}px`;
            });
            const end = () => { drag = null; };
            titlebar.addEventListener('pointerup', end);
            titlebar.addEventListener('pointercancel', end);
        }
    }

    window.WorkbookEditor = WorkbookEditor;
})();
