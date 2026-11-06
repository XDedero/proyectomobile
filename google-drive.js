/**
 * ============================================================================
 * GESTOR DE PROYECTOS Y ARCHIVOS EN GOOGLE DRIVE (JAVASCRIPT PURO - SIN PYTHON)
 * ============================================================================
 * Cuenta vinculada: Vicente Sepúlveda (vicente.shent@gmail.com)
 * Carpetas en Google Drive:
 *   - Carpeta "pdf"             : 1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX
 *   - Carpeta "excel(xlsx-csv)" : 13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s
 *
 * Funcionalidades principales:
 *   1. Enrutamiento automático por carpeta:
 *      - Planos (.pdf)        --> van a la carpeta "pdf"
 *      - Tablas (.xlsx, .csv) --> van a la carpeta "excel(xlsx-csv)"
 *   2. Asociación automática (associationKey):
 *      - Vincula el PDF con su XLSX/CSV mediante metadatos appProperties de
 *        Google Drive y por clave de proyecto para la Base de Datos SQL.
 *   3. Visualización en vivo SIN descargar:
 *      - Carga el flujo de bytes en memoria RAM (Uint8Array / ArrayBuffer /
 *        Texto CSV) directamente hacia el visor sin descargar nada al equipo.
 *   4. Descarga explícita opcional:
 *      - Solo descarga el archivo al dispositivo cuando el usuario lo pide.
 * ============================================================================
 */

const GOOGLE_DRIVE_CONFIG = {
    CLIENT_ID: (typeof process !== 'undefined' && process.env && process.env.GOOGLE_DRIVE_CLIENT_ID) || '',
    CLIENT_SECRET: (typeof process !== 'undefined' && process.env && process.env.GOOGLE_DRIVE_CLIENT_SECRET) || '',
    PROJECT_ID: (typeof process !== 'undefined' && process.env && process.env.GOOGLE_DRIVE_PROJECT_ID) || 'proyecto-app-mobile-513818',

    // Tokens cargados dinamicamente desde gdrive-token.json o localStorage
    ACCESS_TOKEN: '',
    REFRESH_TOKEN: '',

    // IDs de las dos carpetas creadas en tu Google Drive
    FOLDERS: {
        PDF: '1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX',       // Carpeta: "pdf"
        EXCEL_CSV: '13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s'  // Carpeta: "excel(xlsx-csv)"
    },

    SCOPES: [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/drive.file',
        'https://www.googleapis.com/auth/drive.readonly'
    ].join(' ')
};


// ============================================================================
// 1. AUTENTICACIÓN Y RENOVACIÓN AUTOMÁTICA DE TOKEN
// ============================================================================
const GoogleDriveAuth = {
    _loadNodeTokenFile: function () {
        if (typeof window === 'undefined' && typeof require === 'function') {
            try {
                const fs = require('fs');
                const path = require('path');
                const tokenPath = path.join(__dirname, 'gdrive-token.json');
                if (fs.existsSync(tokenPath)) {
                    const raw = JSON.parse(fs.readFileSync(tokenPath, 'utf8'));
                    if (raw.client_id) GOOGLE_DRIVE_CONFIG.CLIENT_ID = raw.client_id.trim();
                    if (raw.client_secret) GOOGLE_DRIVE_CONFIG.CLIENT_SECRET = raw.client_secret.trim();
                    if (raw.project_id) GOOGLE_DRIVE_CONFIG.PROJECT_ID = raw.project_id.trim();
                    if (raw.access_token) GOOGLE_DRIVE_CONFIG.ACCESS_TOKEN = raw.access_token.trim();
                    if (raw.refresh_token) GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN = raw.refresh_token.trim();
                }
            } catch (_) {}
        }
    },

    _saveNodeTokenFile: function (tokenData) {
        if (typeof window === 'undefined' && typeof require === 'function') {
            try {
                const fs = require('fs');
                const path = require('path');
                const tokenPath = path.join(__dirname, 'gdrive-token.json');
                let existing = {};
                if (fs.existsSync(tokenPath)) {
                    existing = JSON.parse(fs.readFileSync(tokenPath, 'utf8'));
                }
                const updated = Object.assign({}, existing, tokenData);
                fs.writeFileSync(tokenPath, JSON.stringify(updated, null, 2), 'utf8');
            } catch (_) {}
        }
    },

    getToken: function () {
        this._loadNodeTokenFile();
        if (typeof window !== 'undefined' && window.localStorage) {
            const saved = window.localStorage.getItem('google_drive_token');
            if (saved && saved.trim() !== '') return saved.trim();
        }
        if (typeof document !== 'undefined') {
            const input = document.getElementById('gdrive-token-input');
            if (input && input.value && input.value.trim() !== '') return input.value.trim();
        }
        if (GOOGLE_DRIVE_CONFIG.ACCESS_TOKEN && GOOGLE_DRIVE_CONFIG.ACCESS_TOKEN.trim() !== '') {
            return GOOGLE_DRIVE_CONFIG.ACCESS_TOKEN.trim();
        }
        return null;
    },

    setToken: function (token, extraData = {}) {
        if (!token) return;
        GOOGLE_DRIVE_CONFIG.ACCESS_TOKEN = token.trim();
        if (extraData.refresh_token) {
            GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN = extraData.refresh_token.trim();
        }
        if (typeof window !== 'undefined' && window.localStorage) {
            window.localStorage.setItem('google_drive_token', token.trim());
        }
        this._saveNodeTokenFile(Object.assign({ access_token: token.trim() }, extraData));
    },

    /**
     * Renueva automáticamente el Access Token usando el Refresh Token guardado
     */
    refreshAccessToken: async function () {
        this._loadNodeTokenFile();
        if (!GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN) {
            throw new Error('No hay Refresh Token configurado para renovar la sesión.');
        }

        const res = await fetch('https://oauth2.googleapis.com/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams({
                client_id: GOOGLE_DRIVE_CONFIG.CLIENT_ID,
                client_secret: GOOGLE_DRIVE_CONFIG.CLIENT_SECRET,
                refresh_token: GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN,
                grant_type: 'refresh_token'
            })
        });

        const data = await res.json();
        if (!res.ok || !data.access_token) {
            throw new Error(data.error_description || 'Error al renovar el Access Token.');
        }

        this.setToken(data.access_token, data);
        return data.access_token;
    },

    /**
     * Obtiene un token válido, renovándolo automáticamente si expiró
     */
    getValidToken: async function () {
        let token = this.getToken();
        if (!token) {
            return await this.refreshAccessToken();
        }
        return token;
    },

    validateToken: async function () {
        try {
            let token = await this.getValidToken();
            let res = await fetch(`https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=${encodeURIComponent(token)}`);
            if (!res.ok && GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN) {
                token = await this.refreshAccessToken();
                res = await fetch(`https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=${encodeURIComponent(token)}`);
            }
            let data = await res.json();
            if (!res.ok) return { valid: false, error: data.error_description || 'Token inválido' };

            // Si quedan menos de 5 minutos, renovarlo automáticamente con el Refresh Token
            if (Number(data.expires_in) < 300 && GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN) {
                token = await this.refreshAccessToken();
                res = await fetch(`https://www.googleapis.com/oauth2/v3/tokeninfo?access_token=${encodeURIComponent(token)}`);
                data = await res.json();
            }

            return {
                valid: true,
                expiresInSeconds: Number(data.expires_in),
                scope: data.scope,
                canWrite: data.scope.includes('drive.file') || data.scope.endsWith('/drive') || data.scope.includes('/drive ')
            };
        } catch (err) {
            return { valid: false, error: err.message };
        }
    }
};


// ============================================================================
// 2. SERVICIO CENTRAL DE CARPETAS, ASOCIACIÓN Y STREAMING SIN DESCARGA
// ============================================================================
const PROJECT_KEY_PATTERN = /^(?:local_(pdf|dxf)_\d+|project_[a-f0-9]{32})$/;

const GoogleDriveService = {
    BASE_URL: 'https://www.googleapis.com/drive/v3',
    UPLOAD_URL: 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,mimeType,size,parents,appProperties,webViewLink,webContentLink',

    /**
     * Realiza peticiones autenticadas con reintento automático si el token expiró
     */
    _fetchWithAuth: async function (url, options = {}) {
        let token = await GoogleDriveAuth.getValidToken();
        const makeHeaders = (tk) => Object.assign({}, options.headers || {}, {
            'Authorization': `Bearer ${tk}`
        });

        let res = await fetch(url, Object.assign({}, options, { headers: makeHeaders(token) }));
        if (res.status === 401 && GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN) {
            token = await GoogleDriveAuth.refreshAccessToken();
            res = await fetch(url, Object.assign({}, options, { headers: makeHeaders(token) }));
        }
        return res;
    },

    /**
     * Extrae la clave base de un nombre de archivo para asociar automáticamente
     * Ejemplo: "Plano_Planta_Norte.pdf" -> "plano_planta_norte"
     *          "Plano_Planta_Norte.xlsx" -> "plano_planta_norte"
     */
    generateAssociationKey: function (projectNameOrFilename) {
        const clean = String(projectNameOrFilename || '')
            .replace(/\.(pdf|xlsx|csv|dxf)$/i, '')
            .trim()
            .toLowerCase()
            .replace(/[^a-z0-9_-]+/g, '_');
        return clean || `proj_${Date.now()}`;
    },

    /**
     * Determina automáticamente a qué carpeta de Drive va el archivo según su tipo
     */
    resolveTargetFolder: function (fileName, mimeType = '') {
        const lowerName = String(fileName || '').toLowerCase();
        if (lowerName.endsWith('.pdf') || mimeType === 'application/pdf') {
            return {
                folderId: GOOGLE_DRIVE_CONFIG.FOLDERS.PDF,
                folderName: 'pdf',
                category: 'pdf',
                mimeType: 'application/pdf'
            };
        }
        if (lowerName.endsWith('.xlsx') || mimeType.includes('spreadsheetml')) {
            return {
                folderId: GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV,
                folderName: 'excel(xlsx-csv)',
                category: 'excel',
                fileFormat: 'xlsx',
                mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            };
        }
        if (lowerName.endsWith('.csv') || mimeType === 'text/csv') {
            return {
                folderId: GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV,
                folderName: 'excel(xlsx-csv)',
                category: 'excel',
                fileFormat: 'csv',
                mimeType: 'text/csv'
            };
        }
        throw new Error(`Formato de archivo no soportado (${fileName}). Solo se admiten .pdf, .xlsx y .csv.`);
    },

    /**
     * Convierte un Blob, File, Uint8Array o Buffer a Base64 (compatible con Navegador y Node.js)
     */
    _toBase64: async function (content) {
        if (typeof Buffer !== 'undefined') {
            if (Buffer.isBuffer(content)) return content.toString('base64');
            if (content instanceof ArrayBuffer) return Buffer.from(content).toString('base64');
            if (ArrayBuffer.isView(content)) return Buffer.from(content.buffer).toString('base64');
            if (typeof content === 'string') return Buffer.from(content, 'utf8').toString('base64');
        }
        if (typeof FileReader !== 'undefined' && typeof Blob !== 'undefined' && content instanceof Blob) {
            return new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result.split(',')[1]);
                reader.onerror = reject;
                reader.readAsDataURL(content);
            });
        }
        throw new Error('Tipo de contenido binario no reconocido para subir a Drive.');
    },

    /**
     * SUBIDA INTELIGENTE A LA CARPETA CORRESPONDIENTE ("pdf" o "excel(xlsx-csv)")
     * Sube un archivo y le inyecta la clave de asociación (associationKey) para
     * que quede vinculado automáticamente con su par y con la base de datos SQL.
     */
    uploadFileToProjectFolder: async function ({ fileName, content, associationKey, companyId = 1 }) {
        const target = this.resolveTargetFolder(fileName);
        const assocKey = associationKey || this.generateAssociationKey(fileName);

        const metadata = {
            name: fileName,
            mimeType: target.mimeType,
            parents: [target.folderId],
            appProperties: {
                associationKey: String(assocKey),
                companyId: String(companyId),
                fileCategory: target.category
            }
        };

        const base64Data = await this._toBase64(content);
        const boundary = '-------DriveProjectBoundary' + Date.now();
        const delimiter = `\r\n--${boundary}\r\n`;
        const closeDelim = `\r\n--${boundary}--`;

        const body =
            delimiter +
            'Content-Type: application/json; charset=UTF-8\r\n\r\n' +
            JSON.stringify(metadata) +
            delimiter +
            `Content-Type: ${target.mimeType}\r\n` +
            'Content-Transfer-Encoding: base64\r\n\r\n' +
            base64Data +
            closeDelim;

        const res = await this._fetchWithAuth(this.UPLOAD_URL, {
            method: 'POST',
            headers: {
                'Content-Type': `multipart/related; boundary=${boundary}`
            },
            body: body
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error?.message || `Error al subir ${fileName} a la carpeta "${target.folderName}" (HTTP ${res.status})`);
        }

        const driveFile = await res.json();
        return this._formatDriveRecordForDB(driveFile, target, assocKey, companyId);
    },

    /**
     * CREA O ACTUALIZA UN ARCHIVO EN LA CARPETA DE GOOGLE DRIVE ("pdf" o "excel(xlsx-csv)")
     * Un localRecordKey identifica el archivo del proyecto. Sin él, se busca por asociación o nombre.
     * actualiza su contenido binario y nombre sin duplicarlo.
     */
    upsertFileToProjectFolder: async function ({ fileName, content, associationKey, localRecordKey = '', companyId = 1, driveTarget }) {
        const target = this.resolveTargetFolder(fileName);
        const assocKey = associationKey || this.generateAssociationKey(fileName);

        const existingFiles = await this.listFilesInFolder(target.folderId);
        let existing = null;
        if (driveTarget) {
            existing = existingFiles.find(file => file.id === driveTarget.id && file.appProperties?.associationKey === assocKey);
            if (!existing || existing.modifiedTime !== driveTarget.modifiedTime) {
                throw new Error('El archivo original cambió en Drive. Abre la versión de la nube antes de publicar.');
            }
        } else if (localRecordKey) {
            const projectKey = PROJECT_KEY_PATTERN.test(assocKey) ? assocKey : '';
            if (projectKey) {
                const projectFiles = existingFiles.filter(f => f.appProperties?.associationKey === projectKey);
                existing = projectFiles.find(f => f.appProperties?.localRecordKey === String(localRecordKey)) || projectFiles[0];
            }
            if (!existing) {
                existing = existingFiles.find(f => f.appProperties?.localRecordKey === String(localRecordKey) &&
                    (!projectKey || !PROJECT_KEY_PATTERN.test(f.appProperties?.associationKey || '')));
            }
        }
        if (!driveTarget && !localRecordKey) {
            existing = existingFiles.find(f =>
                (f.appProperties && f.appProperties.associationKey === String(assocKey)) ||
                f.name.toLowerCase() === fileName.toLowerCase()
            );
        }

        const metadata = {
            name: fileName,
            mimeType: target.mimeType,
            appProperties: {
                associationKey: String(assocKey),
                companyId: String(companyId),
                fileCategory: target.category,
                localRecordKey: String((driveTarget && existing.appProperties.localRecordKey) || localRecordKey || assocKey)
            }
        };

        if (!existing) {
            metadata.parents = [target.folderId];
        }

        const base64Data = await this._toBase64(content);
        const boundary = '-------DriveProjectBoundary' + Date.now();
        const delimiter = `\r\n--${boundary}\r\n`;
        const closeDelim = `\r\n--${boundary}--`;

        const body =
            delimiter +
            'Content-Type: application/json; charset=UTF-8\r\n\r\n' +
            JSON.stringify(metadata) +
            delimiter +
            `Content-Type: ${target.mimeType}\r\n` +
            'Content-Transfer-Encoding: base64\r\n\r\n' +
            base64Data +
            closeDelim;

        const url = existing
            ? `https://www.googleapis.com/upload/drive/v3/files/${existing.id}?uploadType=multipart&fields=id,name,mimeType,size,parents,appProperties,webViewLink,webContentLink,modifiedTime`
            : this.UPLOAD_URL;
        const method = existing ? 'PATCH' : 'POST';

        const res = await this._fetchWithAuth(url, {
            method: method,
            headers: {
                'Content-Type': `multipart/related; boundary=${boundary}`
            },
            body: body
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error?.message || `Error al subir ${fileName} a la carpeta "${target.folderName}" (HTTP ${res.status})`);
        }

        const driveFile = await res.json();
        return Object.assign(
            this._formatDriveRecordForDB(driveFile, target, assocKey, companyId),
            { action: existing ? 'updated' : 'created', modified_time: driveFile.modifiedTime }
        );
    },

    /**
     * ELIMINA UN ARCHIVO DE GOOGLE DRIVE POR SU ID O LOCAL KEY
     */
    deleteFileFromFolder: async function ({ folderId, driveFileId, localRecordKey, fileName, projectKey }) {
        const targetId = driveFileId && !String(driveFileId).startsWith('local_') ? driveFileId : null;
        let targets = targetId ? [targetId] : [];
        if (!targets.length && folderId) {
            const files = await this.listFilesInFolder(folderId);
            if (projectKey) {
                if (!PROJECT_KEY_PATTERN.test(projectKey)) throw new Error('Clave de proyecto no válida.');
                targets = files.filter(file => {
                    const properties = file.appProperties || {};
                    return properties.associationKey === projectKey || (
                        localRecordKey && properties.localRecordKey === String(localRecordKey) &&
                        !PROJECT_KEY_PATTERN.test(properties.associationKey || '')
                    );
                }).map(file => file.id);
            } else {
                const found = localRecordKey
                    ? files.find(f => f.appProperties?.localRecordKey === String(localRecordKey))
                    : files.find(f => fileName && f.name.toLowerCase() === String(fileName).toLowerCase());
                if (found) targets = [found.id];
            }
        }
        if (!targets.length) return { deleted: false, reason: 'not_found' };

        const results = await Promise.allSettled(targets.map(async id => {
            const res = await this._fetchWithAuth(`${this.BASE_URL}/files/${id}`, { method: 'DELETE' });
            if (!res.ok && res.status !== 404) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error?.message || `Error al eliminar archivo ${id} de Drive (HTTP ${res.status})`);
            }
            return id;
        }));
        const driveFileIds = results.filter(result => result.status === 'fulfilled').map(result => result.value);
        const errors = results.filter(result => result.status === 'rejected').map(result => result.reason.message);
        if (errors.length && !projectKey) {
            throw new Error(errors[0]);
        }
        return { deleted: driveFileIds.length > 0, driveFileId: driveFileIds[0], driveFileIds, errors };
    },

    /**
     * SUBIDA COMPLETA DE UN PROYECTO DE PLANO (PDF + XLSX/CSV JUNTOS)
     * Sube el PDF a la carpeta "pdf", el XLSX/CSV a la carpeta "excel(xlsx-csv)"
     * y los asocia automáticamente bajo la misma clave de proyecto.
     */
    uploadLinkedProject: async function ({ projectName, pdfFile, excelOrCsvFile, companyId = 1 }) {
        const associationKey = this.generateAssociationKey(projectName || (pdfFile && pdfFile.name));
        const results = {
            associationKey,
            companyId,
            pdfRecord: null,
            excelRecord: null,
            sqlInsertStatements: []
        };

        if (pdfFile) {
            results.pdfRecord = await this.uploadFileToProjectFolder({
                fileName: pdfFile.name,
                content: pdfFile.content || pdfFile,
                associationKey,
                companyId
            });
        }

        if (excelOrCsvFile) {
            results.excelRecord = await this.uploadFileToProjectFolder({
                fileName: excelOrCsvFile.name,
                content: excelOrCsvFile.content || excelOrCsvFile,
                associationKey,
                companyId
            });
        }

        return results;
    },

    /**
     * Formatea la respuesta de Google Drive con las columnas exactas de schema.sql
     */
    _formatDriveRecordForDB: function (driveFile, target, associationKey, companyId) {
        const fileId = driveFile.id;
        return {
            company_id: companyId,
            association_key: associationKey,
            name: driveFile.name,
            category: target.category,
            file_format: target.fileFormat || 'pdf',
            drive_file_id: fileId,
            drive_folder_id: target.folderId,
            folder_name: target.folderName,
            size_bytes: Number(driveFile.size || 0),
            // URL para cargar en vivo en memoria SIN descargar al equipo
            drive_stream_url: `${this.BASE_URL}/files/${fileId}?alt=media`,
            // URL para previsualizar en iframe SIN descargar al equipo
            drive_preview_url: `https://drive.google.com/file/d/${fileId}/preview`,
            // URL para descargar SOLO si el usuario pulsa "Descargar"
            drive_download_url: `https://drive.google.com/uc?export=download&id=${fileId}`
        };
    },

    /**
     * LISTA TODOS LOS ARCHIVOS DENTRO DE UNA DE LAS DOS CARPETAS ("pdf" o "excel(xlsx-csv)")
     */
    listFilesInFolder: async function (folderId) {
        const q = `'${folderId}' in parents and trashed = false`;
        const params = new URLSearchParams({
            q: q,
            pageSize: '100',
            fields: 'nextPageToken,files(id, name, mimeType, size, modifiedTime, parents, appProperties, webViewLink, webContentLink)'
        });

        const files = [];
        do {
            const res = await this._fetchWithAuth(`${this.BASE_URL}/files?${params.toString()}`);
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error?.message || `Error al leer carpeta ${folderId} (HTTP ${res.status})`);
            }
            const data = await res.json();
            files.push(...(data.files || []));
            if (!data.nextPageToken) return files;
            params.set('pageToken', data.nextPageToken);
        } while (true);
    },

    /**
     * DESCUBRIMIENTO Y ASOCIACIÓN AUTOMÁTICA DESDE LAS CARPETAS DE DRIVE
     * Revisa la carpeta "pdf" y la carpeta "excel(xlsx-csv)" y empareja
     * automáticamente cada plano PDF con su tabla XLSX o CSV correspondiente.
     */
    getLinkedProjectsFromFolders: async function () {
        const [pdfFiles, excelFiles] = await Promise.all([
            this.listFilesInFolder(GOOGLE_DRIVE_CONFIG.FOLDERS.PDF),
            this.listFilesInFolder(GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV)
        ]);

        const projectsMap = new Map();

        // 1. Indexar todos los planos de la carpeta "pdf"
        for (const pdf of pdfFiles) {
            const key = (pdf.appProperties && pdf.appProperties.associationKey)
                ? pdf.appProperties.associationKey
                : this.generateAssociationKey(pdf.name);

            if (!projectsMap.has(key)) {
                projectsMap.set(key, {
                    associationKey: key,
                    projectName: pdf.name.replace(/\.pdf$/i, ''),
                    pdfFile: null,
                    excelOrCsvFiles: []
                });
            }
            projectsMap.get(key).pdfFile = {
                id: pdf.id,
                name: pdf.name,
                size: Number(pdf.size || 0),
                folderId: GOOGLE_DRIVE_CONFIG.FOLDERS.PDF,
                folderName: 'pdf',
                streamUrl: `${this.BASE_URL}/files/${pdf.id}?alt=media`,
                previewUrl: `https://drive.google.com/file/d/${pdf.id}/preview`,
                downloadUrl: `https://drive.google.com/uc?export=download&id=${pdf.id}`
            };
        }

        // 2. Asociar automáticamente cada tabla de la carpeta "excel(xlsx-csv)"
        for (const excel of excelFiles) {
            const key = (excel.appProperties && excel.appProperties.associationKey)
                ? excel.appProperties.associationKey
                : this.generateAssociationKey(excel.name);

            if (!projectsMap.has(key)) {
                projectsMap.set(key, {
                    associationKey: key,
                    projectName: excel.name.replace(/\.(xlsx|csv)$/i, ''),
                    pdfFile: null,
                    excelOrCsvFiles: []
                });
            }
            projectsMap.get(key).excelOrCsvFiles.push({
                id: excel.id,
                name: excel.name,
                format: excel.name.toLowerCase().endsWith('.csv') ? 'csv' : 'xlsx',
                mimeType: excel.mimeType,
                size: Number(excel.size || 0),
                folderId: GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV,
                folderName: 'excel(xlsx-csv)',
                streamUrl: `${this.BASE_URL}/files/${excel.id}?alt=media`,
                previewUrl: `https://drive.google.com/file/d/${excel.id}/preview`,
                downloadUrl: `https://drive.google.com/uc?export=download&id=${excel.id}`
            });
        }

        return {
            foldersChecked: {
                pdfFolderId: GOOGLE_DRIVE_CONFIG.FOLDERS.PDF,
                excelFolderId: GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV
            },
            totalPdfFiles: pdfFiles.length,
            totalExcelCsvFiles: excelFiles.length,
            projects: Array.from(projectsMap.values())
        };
    },

    // ========================================================================
    // 3. VISUALIZACIÓN EN VIVO DESDE GOOGLE DRIVE (SIN DESCARGAR AL EQUIPO)
    // ========================================================================

    /**
     * Lee el contenido de un archivo de Google Drive directamente en MEMORIA RAM
     * (Uint8Array / ArrayBuffer) para pasarlo a PDF.js o al lector de tablas,
     * SIN disparar ninguna descarga en el navegador ni guardar en disco.
     */
    streamFileInMemory: async function (fileId) {
        const res = await this._fetchWithAuth(`${this.BASE_URL}/files/${fileId}?alt=media`);
        if (!res.ok) {
            throw new Error(`No se pudo cargar en memoria el archivo ${fileId} (HTTP ${res.status})`);
        }
        const arrayBuffer = await res.arrayBuffer();
        return {
            arrayBuffer,
            uint8Array: new Uint8Array(arrayBuffer),
            byteLength: arrayBuffer.byteLength
        };
    },

    /**
     * Lee un archivo CSV de la carpeta "excel(xlsx-csv)" en memoria y devuelve
     * las filas y columnas listas para mostrar en tabla HTML sin descargarlo.
     */
    streamCsvTableInMemory: async function (fileId) {
        const res = await this._fetchWithAuth(`${this.BASE_URL}/files/${fileId}?alt=media`);
        if (!res.ok) {
            throw new Error(`No se pudo leer el CSV ${fileId} desde Drive (HTTP ${res.status})`);
        }
        const text = await res.text();
        const rows = text
            .replace(/^\uFEFF/, '')
            .split(/\r?\n/)
            .filter(line => line.trim() !== '')
            .map(line => line.split(/[,;]/).map(cell => cell.trim()));

        return {
            rawText: text,
            headers: rows[0] || [],
            rows: rows.slice(1)
        };
    },

    /**
     * CARGA UN PROYECTO COMPLETO EN MEMORIA PARA VISUALIZARLO AUTOMÁTICAMENTE
     * Carga el PDF desde la carpeta "pdf" y su tabla desde "excel(xlsx-csv)"
     * sin descargar ningún archivo al dispositivo del usuario.
     */
    loadProjectForViewingWithoutDownload: async function (associationKey) {
        const discovery = await this.getLinkedProjectsFromFolders();
        const project = discovery.projects.find(p => p.associationKey === associationKey);

        if (!project) {
            throw new Error(`No se encontró ningún proyecto con la clave "${associationKey}" en las carpetas de Google Drive.`);
        }

        const viewSession = {
            associationKey: project.associationKey,
            projectName: project.projectName,
            pdfViewerData: null,
            tableViewerData: null
        };

        // Cargar el PDF en memoria (sin descargar)
        if (project.pdfFile) {
            const memoryPdf = await this.streamFileInMemory(project.pdfFile.id);
            viewSession.pdfViewerData = {
                fileId: project.pdfFile.id,
                fileName: project.pdfFile.name,
                embedPreviewUrl: project.pdfFile.previewUrl,
                uint8ArrayForPdfJs: memoryPdf.uint8Array, // Se pasa directo a: pdfjsLib.getDocument({ data: uint8ArrayForPdfJs })
                byteLength: memoryPdf.byteLength
            };
        }

        // Cargar la tabla XLSX/CSV asociada en memoria (sin descargar)
        if (project.excelOrCsvFiles.length > 0) {
            const primaryTable = project.excelOrCsvFiles[0];
            if (primaryTable.format === 'csv') {
                const csvData = await this.streamCsvTableInMemory(primaryTable.id);
                viewSession.tableViewerData = {
                    fileId: primaryTable.id,
                    fileName: primaryTable.name,
                    format: 'csv',
                    headers: csvData.headers,
                    rows: csvData.rows
                };
            } else {
                const memoryXlsx = await this.streamFileInMemory(primaryTable.id);
                viewSession.tableViewerData = {
                    fileId: primaryTable.id,
                    fileName: primaryTable.name,
                    format: 'xlsx',
                    embedPreviewUrl: primaryTable.previewUrl,
                    uint8ArrayForWorkbook: memoryXlsx.uint8Array,
                    byteLength: memoryXlsx.byteLength
                };
            }
        }

        return viewSession;
    },

    // ========================================================================
    // 4. DESCARGA EXPLÍCITA (SOLO CUANDO EL USUARIO QUIERA DESCARGAR ALGO)
    // ========================================================================

    /**
     * Descarga un archivo al dispositivo ÚNICAMENTE cuando el usuario presiona
     * explícitamente el botón "Descargar".
     */
    downloadFileExplicitly: async function (fileId, fileName = 'archivo_descargado') {
        const res = await this._fetchWithAuth(`${this.BASE_URL}/files/${fileId}?alt=media`);
        if (!res.ok) {
            throw new Error(`Error al descargar el archivo solicitado (HTTP ${res.status})`);
        }

        const blob = await res.blob();

        // Si estamos en navegador, disparar el guardado del archivo en el equipo
        if (typeof window !== 'undefined' && typeof document !== 'undefined') {
            const objectUrl = window.URL.createObjectURL(blob);
            const anchor = document.createElement('a');
            anchor.href = objectUrl;
            anchor.download = fileName;
            document.body.appendChild(anchor);
            anchor.click();
            document.body.removeChild(anchor);
            window.URL.revokeObjectURL(objectUrl);
        }

        return blob;
    }
};

// Exportación universal (Navegador y Node.js)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { GOOGLE_DRIVE_CONFIG, GoogleDriveAuth, GoogleDriveService };
} else if (typeof window !== 'undefined') {
    window.GOOGLE_DRIVE_CONFIG = GOOGLE_DRIVE_CONFIG;
    window.GoogleDriveAuth = GoogleDriveAuth;
    window.GoogleDriveService = GoogleDriveService;
}
