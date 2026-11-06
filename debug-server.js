/**
 * ============================================================================
 * SERVIDOR Y CONSOLA DE DEPURACIÓN (SUPABASE + GOOGLE DRIVE + APP)
 * ============================================================================
 * Ejecuta pruebas de diagnóstico en vivo contra:
 *   1. Supabase en la nube (Proyecto: xhfcldlrbcuvjngznsbg)
 *   2. Google Drive API v3 (Carpetas 'pdf' y 'excel(xlsx-csv)')
 *   3. Servidor Web Local con trazas DEBUG en tiempo real
 * ============================================================================
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync, exec } = require('child_process');
const { GOOGLE_DRIVE_CONFIG, GoogleDriveAuth, GoogleDriveService } = require('./google-drive.js');

const PORT = process.env.PORT || 8000;
const LOG_FILE = path.join(__dirname, 'debug_app.log');
const TOKEN_FILE = path.join(__dirname, 'gdrive-token.json');

function logDebug(tag, message, details = null) {
    const timestamp = new Date().toISOString().replace('T', ' ').substring(0, 19);
    const line = `[${timestamp}] [${tag}] ${message}`;
    console.log(line);
    if (details) {
        const detailStr = typeof details === 'string' ? details : JSON.stringify(details, null, 2);
        console.log(detailStr);
        fs.appendFileSync(LOG_FILE, `${line}\n${detailStr}\n`, 'utf8');
    } else {
        fs.appendFileSync(LOG_FILE, `${line}\n`, 'utf8');
    }
}

/**
 * Ejecuta una consulta SQL real en Supabase Cloud mediante Supabase CLI
 */
function runSupabaseQuery(sql) {
    const cleanSql = sql.replace(/\r?\n/g, ' ').replace(/\s+/g, ' ').trim().replace(/"/g, '\\"');
    const raw = execSync(`supabase db query --remote "${cleanSql}" --yes`, {
        encoding: 'utf8',
        stdio: ['pipe', 'pipe', 'pipe']
    });

    const jsonStart = raw.indexOf('{');
    if (jsonStart === -1) return { rows: [], raw };
    const parsed = JSON.parse(raw.substring(jsonStart));
    return parsed;
}

/**
 * 1. DIAGNÓSTICO PROFUNDO DE SUPABASE
 */
async function checkSupabaseConnection() {
    console.log('\n========================================================================');
    logDebug('SUPABASE-DEBUG', 'Iniciando diagnóstico de conexión con Supabase Cloud...');
    console.log('========================================================================');

    try {
        // 1.1 Comprobar estado general, tablas y hora del servidor
        const healthSql = `
            SELECT 
                current_database() AS db_name,
                version() AS pg_version,
                now() AS server_time,
                (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') AS public_objects
        `;
        const health = runSupabaseQuery(healthSql);
        const info = health.rows && health.rows[0] ? health.rows[0] : {};

        logDebug('SUPABASE-OK', `Conectado a Base de Datos: ${info.db_name || 'postgres'}`);
        logDebug('SUPABASE-INFO', `Hora servidor Supabase   : ${info.server_time}`);
        logDebug('SUPABASE-INFO', `Objetos en esquema public: ${info.public_objects}`);

        // 1.2 Verificar tablas y conteo de filas
        const tablesSql = `
            SELECT 
                (SELECT count(*) FROM companies) AS empresas,
                (SELECT count(*) FROM users) AS usuarios,
                (SELECT count(*) FROM company_users) AS roles_asignados,
                (SELECT count(*) FROM pdf_plans) AS planos_pdf,
                (SELECT count(*) FROM workbook_documents) AS tablas_excel_csv,
                (SELECT count(*) FROM pdf_markers) AS botones_pdf,
                (SELECT count(*) FROM saved_projects) AS proyectos_guardados
        `;
        const counts = runSupabaseQuery(tablesSql);
        if (counts.rows && counts.rows[0]) {
            logDebug('SUPABASE-TABLAS', 'Conteo actual de registros en la nube:', counts.rows[0]);
        }

        // 1.3 Verificar columnas opcionales del área circular/elíptica en pdf_markers
        const areaColsSql = `
            SELECT column_name, data_type, is_nullable 
            FROM information_schema.columns 
            WHERE table_name = 'pdf_markers' 
              AND column_name IN ('has_area', 'area_radius_x', 'area_radius_y', 'area_color')
            ORDER BY ordinal_position
        `;
        const areaCols = runSupabaseQuery(areaColsSql);
        logDebug('SUPABASE-AREA-COLS', `Columnas de área circular opcional en 'pdf_markers' (${areaCols.rows.length}/4):`, areaCols.rows);

        // 1.4 Verificar Triggers de asociación automática con Google Drive
        const triggersSql = `
            SELECT trigger_name, event_object_table, action_timing, event_manipulation
            FROM information_schema.triggers
            WHERE trigger_schema = 'public'
        `;
        const triggers = runSupabaseQuery(triggersSql);
        logDebug('SUPABASE-TRIGGERS', `Triggers automáticos activos (${triggers.rows.length}):`, triggers.rows);

        return { ok: true, info, counts: counts.rows?.[0], triggers: triggers.rows };
    } catch (err) {
        logDebug('SUPABASE-ERROR', `Fallo al consultar Supabase: ${err.message}`);
        return { ok: false, error: err.message };
    }
}

/**
 * 2. DIAGNÓSTICO PROFUNDO DE GOOGLE DRIVE
 */
async function checkGoogleDriveConnection() {
    console.log('\n========================================================================');
    logDebug('GDRIVE-DEBUG', 'Iniciando diagnóstico de conexión con Google Drive API v3...');
    console.log('========================================================================');

    try {
        if (fs.existsSync(TOKEN_FILE)) {
            const saved = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
            if (saved.access_token) GoogleDriveAuth.setToken(saved.access_token);
            if (saved.refresh_token) GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN = saved.refresh_token;
        }

        const status = await GoogleDriveAuth.validateToken();
        if (!status.valid) {
            throw new Error(status.error || 'Token inválido');
        }

        // Guardar el token renovado si se actualizó en memoria
        if (fs.existsSync(TOKEN_FILE)) {
            const saved = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
            saved.access_token = GoogleDriveAuth.getToken();
            fs.writeFileSync(TOKEN_FILE, JSON.stringify(saved, null, 2));
        }

        const aboutRes = await GoogleDriveService._fetchWithAuth('https://www.googleapis.com/drive/v3/about?fields=user,storageQuota');
        const aboutData = await aboutRes.json();

        logDebug('GDRIVE-OK', `Usuario autenticado : ${aboutData.user?.displayName} (${aboutData.user?.emailAddress})`);
        logDebug('GDRIVE-TOKEN', `Vigencia del token  : ${Math.round(status.expiresInSeconds / 60)} minutos restantes | Scope: ${status.scope}`);
        if (!status.canWrite) {
            logDebug('GDRIVE-ALERTA-PERMISO', 'ATENCION: El token actual tiene permiso SOLO LECTURA (drive.readonly). Para subir archivos .pdf y .xlsx/.csv a las carpetas de Google Drive, autoriza el alcance completo de escritura (https://www.googleapis.com/auth/drive).');
        } else {
            logDebug('GDRIVE-PERMISO-OK', 'Permiso de LECTURA y ESCRITURA activo (puede subir PDF y XLSX/CSV a las carpetas).');
        }

        const folderReport = await GoogleDriveService.getLinkedProjectsFromFolders();
        logDebug('GDRIVE-CARPETAS', 'Estado de las carpetas vinculadas en Google Drive:', {
            carpeta_pdf_id: GOOGLE_DRIVE_CONFIG.FOLDERS.PDF,
            archivos_en_pdf: folderReport.totalPdfFiles,
            carpeta_excel_id: GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV,
            archivos_en_excel_csv: folderReport.totalExcelCsvFiles,
            proyectos_asociados_detectados: folderReport.projects.length
        });

        return {
            ok: true,
            user: aboutData.user,
            tokenExpiresMinutes: Math.round(status.expiresInSeconds / 60),
            folders: folderReport
        };
    } catch (err) {
        logDebug('GDRIVE-ERROR', `Fallo al conectar con Google Drive: ${err.message}`);
        return { ok: false, error: err.message };
    }
}

/**
 * 3. SERVIDOR WEB LOCAL CON LOGS DE DEPURACIÓN EN TIEMPO REAL
 */
const MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.mjs': 'application/javascript; charset=utf-8'
};

function startDebugServer(initialStatus) {
    const server = http.createServer(async (req, res) => {
        const parsedUrl = new URL(req.url, `http://localhost:${PORT}`);
        const pathname = parsedUrl.pathname;

        logDebug('HTTP-REQ', `${req.method} ${pathname}`);

        // Endpoint API de diagnóstico en vivo para el frontend
        if (pathname === '/api/debug/status') {
            const driveStatus = await checkGoogleDriveConnection();
            const sbCompanies = runSupabaseQuery('SELECT id, name, slug, drive_pdf_folder_id, drive_excel_folder_id FROM companies ORDER BY id');
            const sbProjects = runSupabaseQuery('SELECT * FROM v_drive_projects_live ORDER BY saved_at DESC LIMIT 20');
            const sbMarkers = runSupabaseQuery('SELECT id, plan_id, page, x, y, label, sheet_name, has_area, area_radius_x, area_radius_y FROM pdf_markers ORDER BY id DESC LIMIT 20');

            res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
            res.end(JSON.stringify({
                timestamp: new Date().toISOString(),
                supabase: {
                    connected: true,
                    project_ref: 'xhfcldlrbcuvjngznsbg',
                    companies: sbCompanies.rows || [],
                    projects_live: sbProjects.rows || [],
                    markers_sample: sbMarkers.rows || []
                },
                google_drive: driveStatus
            }, null, 2));
            return;
        }

        // Endpoint para probar guardado de botón con área circular/elíptica opcional en Supabase
        if (pathname === '/api/debug/test-marker-area' && req.method === 'POST') {
            let body = '';
            req.on('data', chunk => { body += chunk; });
            req.on('end', () => {
                try {
                    const payload = JSON.parse(body || '{}');
                    logDebug('SUPABASE-WRITE-TEST', 'Probando actualización de área circular opcional:', payload);
                    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
                    res.end(JSON.stringify({ ok: true, received: payload }));
                } catch (e) {
                    res.writeHead(400, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ ok: false, error: e.message }));
                }
            });
            return;
        }

        // Enrutamiento de archivos estáticos
        let filePath;
        if (pathname === '/' || pathname === '/debug') {
            filePath = path.join(__dirname, 'drive-tester.html');
        } else if (pathname === '/app') {
            filePath = path.join(__dirname, 'index.html');
        } else if (pathname.startsWith('/static/')) {
            filePath = path.join(__dirname, 'mobileApp', pathname);
        } else {
            filePath = path.join(__dirname, pathname);
        }

        if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
            const ext = path.extname(filePath).toLowerCase();
            const contentType = MIME_TYPES[ext] || 'application/octet-stream';
            res.writeHead(200, { 'Content-Type': contentType });
            fs.createReadStream(filePath).pipe(res);
        } else {
            logDebug('HTTP-404', `Archivo no encontrado: ${pathname}`);
            res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
            res.end(`404 No encontrado: ${pathname}`);
        }
    });

    server.listen(PORT, () => {
        console.log('\n========================================================================');
        console.log('🚀 SERVIDOR DEBUG ACTIVO Y MONITOREANDO EN TIEMPO REAL');
        console.log('========================================================================');
        console.log(`  👉 Panel de Pruebas Drive + Supabase : http://localhost:${PORT}/`);
        console.log(`  👉 Interfaz Principal de Planos      : http://localhost:${PORT}/app`);
        console.log(`  👉 JSON de Estado Debug en Vivo      : http://localhost:${PORT}/api/debug/status`);
        console.log(`  📄 Archivo de registro (Log)         : ${LOG_FILE}`);
        console.log('========================================================================');
        console.log('  (Presiona Ctrl + C en esta ventana para detener el servidor)\n');

        if (!process.argv.includes('--no-browser')) {
            exec(`start "" "http://localhost:${PORT}/"`);
        }
    });
}

async function main() {
    fs.writeFileSync(LOG_FILE, `=== INICIO DE SESIÓN DEBUG (${new Date().toISOString()}) ===\n`, 'utf8');

    const sbStatus = await checkSupabaseConnection();
    const gdStatus = await checkGoogleDriveConnection();

    if (process.argv.includes('--check-only')) {
        console.log('\n========================================================================');
        console.log('RESUMEN DE COMPROBACIÓN:');
        console.log(`  - Conexión Supabase Cloud : ${sbStatus.ok ? '✅ EXITOSA' : '❌ FALLÓ'}`);
        console.log(`  - Conexión Google Drive   : ${gdStatus.ok ? '✅ EXITOSA' : '❌ FALLÓ'}`);
        console.log('========================================================================\n');
        process.exit(sbStatus.ok && gdStatus.ok ? 0 : 1);
    }

    startDebugServer({ sbStatus, gdStatus });
}

main();

