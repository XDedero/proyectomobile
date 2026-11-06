/* Normalize the editable fields of the standard motor layouts in XLSX and CSV. */
(() => {
    const specificationFields = Array.from({ length: 12 }, (_, index) => `C${18 + index}:G${18 + index}`);
    const notes = 'B65:K72';
    const motorFields = ['D10:E10', 'G10:H10', 'D11:H11', ...specificationFields, 'D30:G30', notes];
    const motorLabels = {
        B6: 'ficha de identificacion de equipos', B18: 'marca', B19: 'modelo',
        B20: 'no de serie', B33: 'mediciones', B64: 'observaciones',
    };
    const listingLabels = { B7: 'no motor', C7: 'especificaciones del motor', F7: 'sector' };
    const listingFields = row => [`C${row}:E${row}`, `F${row}:H${row}`];
    const maxUploadSize = 20 * 1024 * 1024;
    const maxXmlSize = 32 * 1024 * 1024;
    const afterMerges = new Set([
        'phoneticPr', 'conditionalFormatting', 'dataValidations', 'hyperlinks', 'printOptions', 'pageMargins',
        'pageSetup', 'headerFooter', 'rowBreaks', 'colBreaks', 'customProperties', 'cellWatches', 'ignoredErrors',
        'smartTags', 'drawing', 'legacyDrawing', 'legacyDrawingHF', 'picture', 'oleObjects', 'controls',
        'webPublishItems', 'tableParts', 'extLst',
    ]);

    function label(value) {
        return String(value ?? '').normalize('NFKD').replace(/\p{M}/gu, '')
            .toLowerCase().replace(/n°/g, 'no').replace(/[^a-z0-9\s]/g, '').trim().replace(/\s+/g, ' ');
    }

    function matches(read, labels) {
        return Object.entries(labels).every(([address, expected]) => label(read(address)) === expected);
    }

    function coordinates(address) {
        const [, letters, row] = /^\$?([A-Z]+)\$?(\d+)$/.exec(address);
        const col = [...letters].reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
        return [Number(row), col];
    }

    function bounds(ref) {
        const [first, last = first] = ref.split(':');
        const [min_row, min_col] = coordinates(first);
        const [max_row, max_col] = coordinates(last);
        return { min_row, min_col, max_row, max_col };
    }

    function intersects(a, b) {
        return a.min_row <= b.max_row && a.max_row >= b.min_row && a.min_col <= b.max_col && a.max_col >= b.min_col;
    }

    async function readXml(entry) {
        if (!entry) return null;
        const data = await new Promise((resolve, reject) => {
            let size = 0;
            const chunks = [];
            const stream = entry.internalStream('uint8array');
            stream.on('data', chunk => {
                size += chunk.byteLength;
                if (size > maxXmlSize) {
                    stream.pause();
                    reject(new Error('Una hoja del Excel es demasiado grande para ajustar su formato.'));
                    return;
                }
                chunks.push(chunk);
            }).on('error', reject).on('end', () => resolve(new Blob(chunks))).resume();
        });
        const xml = new DOMParser().parseFromString(await data.text(), 'application/xml');
        if (xml.getElementsByTagName('parsererror').length) throw new Error('No se pudo leer el formato del Excel.');
        return xml;
    }

    const elements = (node, tag) => [...node.getElementsByTagNameNS(node.namespaceURI, tag)];
    const child = (node, tag) => [...node.children].find(element => element.localName === tag);
    const textOf = node => elements(node, 't').filter(t => t.parentElement.localName !== 'rPh').map(t => t.textContent).join('');

    function newElement(parent, tag) {
        return parent.ownerDocument.createElementNS(parent.namespaceURI, parent.prefix ? `${parent.prefix}:${tag}` : tag);
    }

    function mergeSheet(sheet, sharedStrings, styles) {
        const grid = child(sheet, 'sheetData');
        if (!grid) return false;
        const cells = new Map(elements(grid, 'c').map(cell => [cell.getAttribute('r'), cell]));
        const read = address => {
            const cell = cells.get(address);
            if (!cell) return '';
            const value = child(cell, 'v')?.textContent ?? '';
            if (cell.getAttribute('t') === 's' && value !== '') return sharedStrings[Number(value)];
            if (cell.getAttribute('t') === 'inlineStr') return textOf(cell);
            return value;
        };
        const isMotor = matches(read, motorLabels);
        const isListing = matches(read, listingLabels);
        if (!isMotor && !isListing) return false;
        const ranges = isMotor ? motorFields : [
            ...listingFields(7),
            ...elements(grid, 'row').map(row => Number(row.getAttribute('r')))
                .filter(row => row > 7 && /^\d+$/.test(read(`B${row}`))).flatMap(listingFields),
        ];
        let mergeGroup = child(sheet, 'mergeCells');
        const existing = new Set(mergeGroup ? [...mergeGroup.children].map(merge => merge.getAttribute('ref')) : []);
        const occupied = [...existing].map(bounds);
        for (const link of elements(sheet, 'hyperlink')) occupied.push(bounds(link.getAttribute('ref')));
        // Legacy drawings can hold notes. Excel tables do not support merged cells.
        if (child(sheet, 'legacyDrawing') || child(sheet, 'tableParts')) return false;
        let changed = false;
        for (const ref of ranges) {
            if (existing.has(ref)) continue;
            const area = bounds(ref);
            if (occupied.some(other => intersects(area, other))) continue;
            const anchor = ref.split(':')[0];
            let canMerge = true;
            for (let row = area.min_row; row <= area.max_row; row++) {
                for (let col = area.min_col; col <= area.max_col; col++) {
                    const address = `${String.fromCharCode(64 + col)}${row}`;
                    const cell = cells.get(address);
                    if (address !== anchor && cell && (read(address) || child(cell, 'f') || cell.hasAttribute('cm') || cell.hasAttribute('vm'))) canMerge = false;
                }
            }
            if (!canMerge) continue;
            if (!mergeGroup) {
                mergeGroup = newElement(sheet, 'mergeCells');
                sheet.insertBefore(mergeGroup, [...sheet.children].find(element => afterMerges.has(element.localName)) || null);
            }
            const merge = newElement(mergeGroup, 'mergeCell');
            merge.setAttribute('ref', ref);
            mergeGroup.append(merge);
            if (ref === notes && cells.has(anchor)) styles.wrapNotes(cells.get(anchor));
            changed = true;
        }
        if (changed) mergeGroup.setAttribute('count', mergeGroup.children.length);
        return changed;
    }

    async function normalizeUpload(file) {
        if (!/\.xlsx$/i.test(file.name) || !file.size || file.size > maxUploadSize) return file;
        try {
            const zip = await JSZip.loadAsync(await file.arrayBuffer());
            if (!zip.file('xl/workbook.xml') || zip.file(/^_xmlsignatures\//).length) return file;
            const data = await normalizeWorkbook(zip);
            if (!data || data.byteLength > maxUploadSize) return file;
            return new File([data], file.name, { type: file.type, lastModified: file.lastModified });
        } catch (_) {
            // A workbook outside the supported layout or size can still use the normal import validation.
            return file;
        }
    }

    async function normalizeWorkbook(zip) {
        const [stringsXml, stylesXml] = await Promise.all([
            readXml(zip.file('xl/sharedStrings.xml')), readXml(zip.file('xl/styles.xml')),
        ]);
        const sharedStrings = stringsXml ? elements(stringsXml.documentElement, 'si').map(textOf) : [];
        const formats = stylesXml && child(stylesXml.documentElement, 'cellXfs');
        const wrapped = new Map();
        const styles = {
            wrapNotes(cell) {
                const style = Number(cell.getAttribute('s') || 0);
                const format = formats?.children[style];
                if (!format) return;
                if (!wrapped.has(style)) {
                    const copy = format.cloneNode(true);
                    let alignment = child(copy, 'alignment');
                    if (!alignment) {
                        alignment = newElement(copy, 'alignment');
                        copy.insertBefore(alignment, copy.firstElementChild);
                    }
                    alignment.setAttribute('wrapText', '1');
                    alignment.setAttribute('vertical', 'top');
                    copy.setAttribute('applyAlignment', '1');
                    wrapped.set(style, formats.children.length);
                    formats.append(copy);
                    formats.setAttribute('count', formats.children.length);
                }
                cell.setAttribute('s', wrapped.get(style));
            },
        };
        const serializer = new XMLSerializer();
        let changed = false;
        for (const entry of zip.file(/^xl\/worksheets\/[^/]+\.xml$/)) {
            const xml = await readXml(entry);
            if (!mergeSheet(xml.documentElement, sharedStrings, styles)) continue;
            zip.file(entry.name, serializer.serializeToString(xml));
            changed = true;
        }
        if (!changed) return null;
        if (wrapped.size) zip.file('xl/styles.xml', serializer.serializeToString(stylesXml));
        return zip.generateAsync({ type: 'uint8array', compression: 'DEFLATE' });
    }

    function parseCsv(text) {
        const field = /("(?:[^"]|"")*"|[^",\r\n]*)(,|\r\n|\n|\r|$)/gy;
        const rows = [];
        let row = [];
        let position = 0;
        while (position < text.length) {
            const match = field.exec(text);
            if (!match || field.lastIndex === position) return null;
            const [, value, delimiter] = match;
            row.push(value.startsWith('"') ? value.slice(1, -1).replaceAll('""', '"') : value);
            position = field.lastIndex;
            if (delimiter === ',') continue;
            rows.push(row);
            row = [];
        }
        if (row.length) rows.push([...row, '']);
        return rows;
    }

    const mergeKey = area => `${area.min_row}:${area.min_col}:${area.max_row}:${area.max_col}`;
    const notesKey = mergeKey(bounds(notes));
    const csvMergeKeys = new Set([...motorFields, ...listingFields(7)].map(ref => mergeKey(bounds(ref))));

    function compactCsv(csv, merges) {
        const rows = parseCsv(csv.replace(/^\uFEFF/, ''));
        if (!rows?.length) return csv;
        const read = address => {
            const [row, col] = coordinates(address);
            return rows[row - 1]?.[col - 1];
        };
        const isMotor = matches(read, motorLabels);
        const isListing = matches(read, listingLabels);
        if (!isMotor && !isListing) return csv;
        const merged = new Set(merges.map(mergeKey));
        const remove = new Map();
        if (isListing) {
            const data = rows.slice(6);
            const omit = new Set();
            for (const range of listingFields(7).map(bounds)) {
                if (!merged.has(mergeKey(range))) continue;
                const columns = Array.from({ length: range.max_col - range.min_col }, (_, index) => range.min_col + index + 1);
                if (data.every(row => columns.every(col => !row[col - 1]))) {
                    for (const col of columns) omit.add(col);
                }
            }
            if (omit.size) data.forEach((_, index) => remove.set(index + 7, omit));
        } else {
            for (const range of motorFields.map(bounds)) {
                if (!merged.has(mergeKey(range))) continue;
                for (let row = range.min_row; row <= Math.min(range.max_row, rows.length); row++) {
                    if (!remove.has(row)) remove.set(row, new Set());
                    for (let col = range.min_col + (row === range.min_row ? 1 : 0); col <= range.max_col; col++) {
                        if (!rows[row - 1][col - 1]) remove.get(row).add(col);
                    }
                }
            }
        }
        if (!remove.size) return csv;
        const escape = value => /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
        return '\uFEFF' + rows.map((row, index) => {
            const omitted = remove.get(index + 1);
            return row.filter((_, col) => !omitted?.has(col + 1)).map(escape).join(',');
        }).join('\r\n') + '\r\n';
    }

    window.workbookFields = {
        normalizeUpload, compactCsv,
        isNotesField: merge => Boolean(merge && mergeKey(merge) === notesKey),
        hasCsvFields: merges => merges.some(merge => csvMergeKeys.has(mergeKey(merge))),
    };
})();
