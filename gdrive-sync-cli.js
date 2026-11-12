/**
 * ============================================================================
 * PUENTE CLI NODE.JS PARA SUBIDA Y SINCRONIZACIÓN AUTOMÁTICA EN GOOGLE DRIVE
 * ============================================================================
 * Usa exclusivamente google-drive.js (100% JavaScript, sin librerías Python).
 * Sube/actualiza automáticamente archivos .pdf a la carpeta "pdf" y archivos
 * .xlsx / .csv a la carpeta "excel(xlsx-csv)", devolviendo el Drive File ID real.
 */

const fs = require('fs');
const path = require('path');
const { Readable, Transform } = require('stream');
const { pipeline } = require('stream/promises');
const { GOOGLE_DRIVE_CONFIG, GoogleDriveAuth, GoogleDriveService } = require('./google-drive.js');
const { attachMetadata, readMetadata } = require('./scripts/cloud-workbook.js');

async function cloudProjects() {
    const [pdfs, books] = await Promise.all([
        GoogleDriveService.listFilesInFolder(GOOGLE_DRIVE_CONFIG.FOLDERS.PDF),
        GoogleDriveService.listFilesInFolder(GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV),
    ]);
    const association = file => file.appProperties?.associationKey;
    return pdfs.flatMap(pdf => {
        const key = association(pdf);
        if (!key || !/\.pdf$/i.test(pdf.name) || pdfs.filter(file => association(file) === key).length !== 1) return [];
        const matching = books.filter(file => association(file) === key);
        if (matching.length !== 1 || !/\.xlsx$/i.test(matching[0].name)) return [];
        const excel = matching[0];
        return [{ id: pdf.id, name: pdf.name, date: excel.modifiedTime || pdf.modifiedTime, projectKey: key,
            pdfVersion: pdf.modifiedTime, excelId: excel.id, excelName: excel.name }];
    });
}

async function cloudPublishTarget(pdfId) {
    if (typeof pdfId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(pdfId)) throw new Error('Identificador de PDF no válido.');
    const file = await GoogleDriveService.fileMetadata(pdfId);
    if (!file || file.trashed || !file.parents?.includes(GOOGLE_DRIVE_CONFIG.FOLDERS.PDF) || !/\.pdf$/i.test(file.name)) return null;
    const key = file.appProperties?.associationKey;
    if (!key) throw new Error('El PDF no tiene una asociación válida en Drive.');
    const [pdfs, books] = await Promise.all([
        GoogleDriveService.listFilesInFolder(GOOGLE_DRIVE_CONFIG.FOLDERS.PDF, key),
        GoogleDriveService.listFilesInFolder(GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV, key),
    ]);
    const pdf = pdfs.find(file => file.id === pdfId && /\.pdf$/i.test(file.name));
    if (!pdf) return null;
    if (pdfs.filter(file => file.appProperties?.associationKey === key).length !== 1) throw new Error('La asociación del PDF en Drive no es única.');
    const matching = books.filter(file => file.appProperties?.associationKey === key);
    if (matching.length > 1 || (matching.length && !/\.xlsx$/i.test(matching[0].name))) throw new Error('La asociación del Excel en Drive no es única o no es válida.');
    const excel = matching[0];
    const project = { id: pdf.id, name: pdf.name, projectKey: key, pdfVersion: pdf.modifiedTime,
        excelId: excel?.id || null, excelName: excel?.name || '', date: excel?.modifiedTime || pdf.modifiedTime,
        pdfRevision: pdf.version, excelRevision: excel?.version,
        pdfChecksum: pdf.sha256Checksum, excelChecksum: excel?.sha256Checksum };
    return project;
}

async function downloadProjectFile(id, destination, limit) {
    const response = await GoogleDriveService._fetchWithAuth(
        `${GoogleDriveService.BASE_URL}/files/${encodeURIComponent(id)}?alt=media`,
    );
    if (!response.ok || !response.body) throw new Error('No se pudo descargar un archivo del proyecto.');
    let size = 0;
    const enforceLimit = new Transform({
        transform(chunk, encoding, done) {
            size += chunk.length;
            if (size > limit) done(new Error('El archivo de Drive supera el tamaño permitido.'));
            else done(null, chunk);
        },
    });
    await pipeline(Readable.fromWeb(response.body), enforceLimit, fs.createWriteStream(destination, { flags: 'wx' }));
}

async function setProjectTrash(ids, trashed) {
    if (ids.some(id => typeof id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(id))) throw new Error('Identificador de Drive no válido.');
    const update = async (id, value) => {
        const response = await GoogleDriveService._fetchWithAuth(`${GoogleDriveService.BASE_URL}/files/${id}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ trashed: value }),
        });
        if (!response.ok) throw new Error('No se pudo cambiar la papelera de Drive.');
    };
    const completed = [];
    try {
        for (const id of ids) { await update(id, trashed); completed.push(id); }
    } catch (error) {
        for (const id of completed.reverse()) await update(id, !trashed);
        throw error;
    }
}

const AUTH_WRITE_URL =
    'https://accounts.google.com/o/oauth2/v2/auth?' +
    new URLSearchParams({
        redirect_uri: 'https://developers.google.com/oauthplayground',
        prompt: 'consent',
        response_type: 'code',
        client_id: GOOGLE_DRIVE_CONFIG.CLIENT_ID,
        scope: 'https://www.googleapis.com/auth/drive',
        access_type: 'offline'
    }).toString();

function parseArgs(argv) {
    const args = {};
    for (let i = 2; i < argv.length; i++) {
        const arg = argv[i];
        if (arg.startsWith('--')) {
            const key = arg.slice(2);
            const next = argv[i + 1];
            if (next && !next.startsWith('--')) {
                args[key] = next;
                i++;
            } else {
                args[key] = true;
            }
        }
    }
    return args;
}

async function exchangeAuthCode(rawInput) {
    let code = String(rawInput || '').trim();
    if (code.includes('code=')) {
        const match = code.match(/[?&]code=([^&\s]+)/);
        if (match) code = decodeURIComponent(match[1]);
    }
    if (!code) {
        throw new Error('No se encontró un código de autorización válido.');
    }

    const res = await fetch('https://oauth2.googleapis.com/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
            code: code,
            client_id: GOOGLE_DRIVE_CONFIG.CLIENT_ID,
            client_secret: GOOGLE_DRIVE_CONFIG.CLIENT_SECRET,
            redirect_uri: 'https://developers.google.com/oauthplayground',
            grant_type: 'authorization_code'
        })
    });

    const data = await res.json();
    if (!res.ok || !data.access_token) {
        throw new Error(data.error_description || data.error || 'Error al canjear el código OAuth.');
    }

    // Guardar en gdrive-token.json
    const tokenPath = path.join(__dirname, 'gdrive-token.json');
    let existing = {};
    if (fs.existsSync(tokenPath)) {
        try { existing = JSON.parse(fs.readFileSync(tokenPath, 'utf8')); } catch (_) {}
    }
    const merged = Object.assign({}, existing, data);
    fs.writeFileSync(tokenPath, JSON.stringify(merged, null, 2), 'utf8');

    // Actualizar google-drive.js y mobileApp/static/mobileApp/google-drive.js
    const jsFiles = [
        path.join(__dirname, 'google-drive.js'),
        path.join(__dirname, 'mobileApp', 'static', 'mobileApp', 'google-drive.js')
    ];
    for (const file of jsFiles) {
        if (fs.existsSync(file)) {
            let content = fs.readFileSync(file, 'utf8');
            content = content.replace(/ACCESS_TOKEN:\s*'[^']*'/, `ACCESS_TOKEN: '${merged.access_token}'`);
            if (merged.refresh_token) {
                content = content.replace(/REFRESH_TOKEN:\s*'[^']*'/, `REFRESH_TOKEN: '${merged.refresh_token}'`);
            }
            fs.writeFileSync(file, content, 'utf8');
        }
    }

    // Actualizar credenciales_google_drive.txt
    const credPath = path.join(__dirname, 'credenciales_google_drive.txt');
    if (fs.existsSync(credPath)) {
        let txt = fs.readFileSync(credPath, 'utf8');
        txt = txt.replace(/Alcances\/Scopes\s*:.*$/m, `Alcances/Scopes : ${merged.scope || 'https://www.googleapis.com/auth/drive'}`);
        txt = txt.replace(/Access Token \(Actual\):\r?\n[^\r\n]+/, `Access Token (Actual):\n${merged.access_token}`);
        if (merged.refresh_token) {
            txt = txt.replace(/Refresh Token \(Permanente[^\r\n]*\):\r?\n[^\r\n]+/, `Refresh Token (Permanente - usado para renovar automáticamente el Access Token):\n${merged.refresh_token}`);
        }
        fs.writeFileSync(credPath, txt, 'utf8');
    }

    return merged;
}

async function main() {
    const args = parseArgs(process.argv);

    if (args['print-auth-url']) {
        console.log(JSON.stringify({ ok: true, authUrl: AUTH_WRITE_URL }));
        return;
    }

    if (args['exchange-code']) {
        try {
            const tokenData = await exchangeAuthCode(args['exchange-code']);
            console.log(JSON.stringify({
                ok: true,
                scope: tokenData.scope,
                canWrite: String(tokenData.scope || '').includes('drive') && !String(tokenData.scope || '').endsWith('readonly')
            }));
        } catch (err) {
            console.log(JSON.stringify({ ok: false, error: err.message }));
            process.exit(1);
        }
        return;
    }

    // Validar token antes de operar
    const tokenInfo = await GoogleDriveAuth.validateToken();
    if (!tokenInfo.valid) {
        console.log(JSON.stringify({
            ok: false,
            error: `Token de Google Drive inválido: ${tokenInfo.error}`,
            authUrl: AUTH_WRITE_URL
        }));
        process.exit(1);
    }

    if (args['publish-target']) {
        console.log(JSON.stringify({ ok: true, project: await cloudPublishTarget(args['drive-id']) }));
        return;
    }

    if (args['read-pieces']) {
        const {findLibrary, readPieceIndex} = require('./scripts/piece-library.js');
        const library = await findLibrary(GoogleDriveService);
        let pieces = [];
        if (library) {
            const destination = path.join(args.output, 'pieces.xlsx');
            await downloadProjectFile(library.id, destination, 100*1024*1024);
            pieces = await readPieceIndex(fs.readFileSync(destination));
        }
        console.log(JSON.stringify({ok:true, library, pieces})); return;
    }

    if (args['list-projects']) {
        console.log(JSON.stringify({ ok: true, projects: await cloudProjects() }));
        return;
    }

    if (args['read-project']) {
        const project = await cloudPublishTarget(args['drive-id']);
        if (!project?.excelId) throw new Error('El par PDF y Excel ya no está disponible.');
        // Settle both transfers before the caller can remove the temporary directory.
        const results = await Promise.allSettled([
            downloadProjectFile(project.id, path.join(args.output, 'plan.pdf'), 20 * 1024 * 1024),
            downloadProjectFile(project.excelId, path.join(args.output, 'project.xlsx'), 100 * 1024 * 1024),
        ]);
        const failed = results.find(result => result.status === 'rejected');
        if (failed) throw failed.reason;
        const manifest = await readMetadata(fs.readFileSync(path.join(args.output, 'project.xlsx')),
            fs.readFileSync(path.join(args.output, 'plan.pdf')), project.projectKey);
        console.log(JSON.stringify({ ok: true, project, manifest }));
        return;
    }

    if (!tokenInfo.canWrite) {
        console.log(JSON.stringify({
            ok: false,
            readonlyScope: true,
            currentScope: tokenInfo.scope,
            error: `El token actual de Google Drive tiene alcance de SOLO LECTURA (${tokenInfo.scope}). Para subir archivos .pdf y .xlsx/.csv a tus carpetas se requiere el permiso 'https://www.googleapis.com/auth/drive'.`,
            authUrl: AUTH_WRITE_URL
        }));
        process.exit(2);
    }

    if (args['write-pieces']) {
        const {findLibrary, attachPieceIndex, writeLibrary} = require('./scripts/piece-library.js');
        const existing = await findLibrary(GoogleDriveService);
        if ((existing?.id || '') !== (args['drive-id'] || '') || (existing?.modifiedTime || '') !== (args['modified-time'] || '')) throw new Error('El catálogo cambió; se volverá a combinar antes de guardar.');
        const content = await attachPieceIndex(fs.readFileSync(args.file), JSON.parse(fs.readFileSync(args.index,'utf8')));
        const library = await writeLibrary(GoogleDriveService, existing, content);
        console.log(JSON.stringify({ok:true,library})); return;
    }
    if (args.upload) {
        const filePath = args.file;
        const fileName = args.name || path.basename(filePath);
        const associationKey = args.key || GoogleDriveService.generateAssociationKey(fileName);
        const localRecordKey = args['local-key'] || '';

        if (!filePath || !fs.existsSync(filePath)) {
            console.log(JSON.stringify({ ok: false, error: `Archivo local no encontrado: ${filePath}` }));
            process.exit(1);
        }

        let content = fs.readFileSync(filePath);
        if (args.manifest) {
            content = await attachMetadata(content, JSON.parse(fs.readFileSync(args.manifest, 'utf8')),
                fs.readFileSync(args['project-source'] || args['pdf-source']), associationKey);
        }
        const record = await GoogleDriveService.upsertFileToProjectFolder({
            fileName,
            content,
            associationKey,
            localRecordKey,
            companyId: 1,
            driveTarget: args['create-only'] ? { missing: true, force: Boolean(args['force-replace']) }
                : args['drive-id'] ? { id: args['drive-id'], modifiedTime: args['modified-time'], force: Boolean(args['force-replace']) } : undefined,
        });

        console.log(JSON.stringify({ ok: true, record }));
        return;
    }

    if (args['delete-project'] || args['restore-project']) {
        if (args['delete-project']) {
            const project = (await cloudProjects()).find(item => item.id === args['drive-id']);
            if (!project || project.excelId !== args['excel-id']) throw new Error('El proyecto cambió o ya no está en Drive.');
            await setProjectTrash([project.id, project.excelId], true);
        } else {
            await setProjectTrash([args['drive-id'], args['excel-id']], false);
        }
        console.log(JSON.stringify({ ok: true }));
        return;
    }

    if (args.delete) {
        const folderType = args.folder === 'excel' ? GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV : GOOGLE_DRIVE_CONFIG.FOLDERS.PDF;
        const result = await GoogleDriveService.deleteFileFromFolder({
            folderId: folderType,
            driveFileId: args['drive-id'] || '',
            localRecordKey: args['local-key'] || '',
            projectKey: args['project-key'] || '',
            fileName: args.name || ''
        });
        console.log(JSON.stringify({ ok: true, result }));
        return;
    }

    console.log(JSON.stringify({ ok: false, error: 'Acción no especificada (--upload, --delete, --exchange-code, --print-auth-url).' }));
    process.exit(1);
}

main().catch(err => {
    console.log(JSON.stringify({ ok: false, error: err.message }));
    process.exit(1);
});

