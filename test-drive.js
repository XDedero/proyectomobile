/**
 * ============================================================================
 * VERIFICACIÓN DE CARPETAS Y PROYECTOS VINCULADOS EN GOOGLE DRIVE (NODE.JS)
 * ============================================================================
 * Carpetas configuradas:
 *   - pdf             : 1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX
 *   - excel(xlsx-csv) : 13sc_1Nt_LBWvKNlGPV42LLb6XP6Qd0-s
 * ============================================================================
 */

const fs = require('fs');
const path = require('path');
const { GOOGLE_DRIVE_CONFIG, GoogleDriveAuth, GoogleDriveService } = require('./google-drive.js');

const TOKEN_FILE = path.join(__dirname, 'gdrive-token.json');

async function main() {
    const args = process.argv.slice(2);

    // Si pasan un token nuevo por consola: node test-drive.js "ya29..."
    if (args.length > 0 && !args[0].startsWith('--')) {
        const newToken = args[0].trim();
        let saved = {};
        if (fs.existsSync(TOKEN_FILE)) {
            saved = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
        }
        saved.access_token = newToken;
        fs.writeFileSync(TOKEN_FILE, JSON.stringify(saved, null, 2));
        GoogleDriveAuth.setToken(newToken);
    } else if (fs.existsSync(TOKEN_FILE)) {
        const saved = JSON.parse(fs.readFileSync(TOKEN_FILE, 'utf8'));
        if (saved.access_token) GoogleDriveAuth.setToken(saved.access_token);
        if (saved.refresh_token) GOOGLE_DRIVE_CONFIG.REFRESH_TOKEN = saved.refresh_token;
    }

    console.log('\n============================================================');
    console.log('🔍 VERIFICACIÓN DE CARPETAS Y ASOCIACIÓN EN GOOGLE DRIVE');
    console.log('============================================================');

    const status = await GoogleDriveAuth.validateToken();
    if (!status.valid) {
        console.error('❌ Token inválido:', status.error);
        process.exit(1);
    }

    // Obtener datos de la cuenta
    const aboutRes = await GoogleDriveService._fetchWithAuth('https://www.googleapis.com/drive/v3/about?fields=user');
    const aboutData = await aboutRes.json();

    console.log('✓ Conexión activa con Google Drive');
    if (aboutData.user) {
        console.log(`  👤 Cuenta vinculada : ${aboutData.user.displayName} (${aboutData.user.emailAddress})`);
    }
    console.log(`  🔑 Permisos actuales: ${status.scope}`);
    console.log(`  📁 Carpeta PDF      : pdf (${GOOGLE_DRIVE_CONFIG.FOLDERS.PDF})`);
    console.log(`  📊 Carpeta Tablas   : excel(xlsx-csv) (${GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV})`);

    // Consultar contenido de ambas carpetas y asociar proyectos
    const report = await GoogleDriveService.getLinkedProjectsFromFolders();

    console.log('\n------------------------------------------------------------');
    console.log(`📄 Archivos en carpeta "pdf"             : ${report.totalPdfFiles}`);
    console.log(`📊 Archivos en carpeta "excel(xlsx-csv)" : ${report.totalExcelCsvFiles}`);
    console.log(`🔗 Proyectos asociados detectados        : ${report.projects.length}`);
    console.log('------------------------------------------------------------');

    if (report.projects.length === 0) {
        console.log('ℹ Ambas carpetas existen y están listas para recibir planos (.pdf) y tablas (.xlsx / .csv).');
    } else {
        report.projects.forEach((proj, idx) => {
            console.log(`\n[${idx + 1}] Proyecto: "${proj.projectName}" (Clave: ${proj.associationKey})`);
            if (proj.pdfFile) {
                console.log(`    ├─ Plano PDF   : ${proj.pdfFile.name} (ID: ${proj.pdfFile.id})`);
                console.log(`    │  Vista viva  : ${proj.pdfFile.previewUrl}`);
            } else {
                console.log(`    ├─ Plano PDF   : (Sin PDF asociado aún)`);
            }

            if (proj.excelOrCsvFiles.length > 0) {
                proj.excelOrCsvFiles.forEach(tbl => {
                    console.log(`    └─ Tabla (${tbl.format.toUpperCase()}) : ${tbl.name} (ID: ${tbl.id})`);
                });
            } else {
                console.log(`    └─ Tabla       : (Sin XLSX/CSV asociado aún)`);
            }
        });
    }

    console.log('\n============================================================');
    console.log('✅ LÓGICA DE CARPETAS Y ASOCIACIÓN VERIFICADA CORRECTAMENTE');
    console.log('============================================================\n');
}

main().catch(err => {
    console.error('❌ Error:', err.message);
    process.exit(1);
});
