const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const { GoogleDriveService, GOOGLE_DRIVE_CONFIG } = require('../google-drive.js');

function resolver(pdfs, books) {
    const service = {
        BASE_URL: 'https://drive.test',
        fileMetadata: async id => pdfs.find(file => file.id === id) || null,
        listFilesInFolder: async (folder, key) => {
            assert.equal(key, 'project_one');
            return folder === 'pdf' ? pdfs : books;
        },
        _fetchWithAuth: async () => new Response('%PDF-local-copy'),
    };
    const source = fs.readFileSync(path.join(__dirname, '../gdrive-sync-cli.js'), 'utf8').split('const AUTH_WRITE_URL')[0];
    const context = vm.createContext({
        require(name) {
            if (name === './google-drive.js') return { GOOGLE_DRIVE_CONFIG: { FOLDERS: { PDF: 'pdf', EXCEL_CSV: 'xlsx' } }, GoogleDriveService: service };
            if (name === './scripts/cloud-workbook.js') return {};
            return require(name);
        },
    });
    vm.runInContext(source + '\nthis.resolveTarget = cloudPublishTarget;', context);
    return context.resolveTarget;
}

const pdf = { id: 'pdf1', name: 'Plano.pdf', modifiedTime: 'v1', parents: ['pdf'], appProperties: { associationKey: 'project_one' } };

test('missing Excel still resolves the original PDF for local recovery', async () => {
    const result = await resolver([pdf], [])('pdf1');
    assert.equal(result.excelId, null);
    assert.equal(result.projectKey, 'project_one');
    assert.equal(result.pdfVersion, 'v1');
});

test('missing PDF is distinguished from a missing Excel', async () => {
    assert.equal(await resolver([], [])('pdf1'), null);
});

test('ambiguous Excel matches block recovery', async () => {
    const book = { id: 'excel1', name: 'Plano.xlsx', appProperties: { associationKey: 'project_one' } };
    await assert.rejects(resolver([pdf], [book, { ...book, id: 'excel2' }])('pdf1'), /asociación del Excel/);
});

test('recovery creates a new Excel with the same association', async () => {
    const uploads = [];
    const service = {
        ...GoogleDriveService,
        listFilesInFolder: async () => [],
        _fetchWithAuth: async (url, options) => {
            uploads.push({ url, ...options });
            return { ok: true, json: async () => ({ id: 'new-excel', name: 'Plano.xlsx', modifiedTime: 'v2' }) };
        },
    };
    const result = await service.upsertFileToProjectFolder({ fileName: 'Plano.xlsx', content: Buffer.from('local-xlsx'),
        associationKey: 'project_one', localRecordKey: 'project_one_xlsx', driveTarget: { missing: true } });
    assert.equal(result.action, 'created');
    assert.equal(uploads[0].method, 'POST');
    assert.match(uploads[0].body, /"associationKey":"project_one"/);
    assert.match(uploads[0].body, /bG9jYWwteGxzeA==/);
});

test('recovery refuses to overwrite an Excel recreated by another device', async () => {
    const service = {
        ...GoogleDriveService,
        listFilesInFolder: async () => [{ id: 'other-excel', appProperties: { associationKey: 'project_one' } }],
        _fetchWithAuth: async () => { throw new Error('Unexpected upload'); },
    };
    await assert.rejects(service.upsertFileToProjectFolder({ fileName: 'Plano.xlsx', content: Buffer.from('local'),
        associationKey: 'project_one', driveTarget: { missing: true } }), /recreado en Drive desde otro equipo/);
});

test('explicit forced save replaces a newer Excel at the same Drive ID', async () => {
    const uploads = [];
    const service = {
        ...GoogleDriveService,
        fileMetadata: async () => ({ id: 'original-excel', modifiedTime: 'newer',
            parents: [GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV], appProperties: { associationKey: 'project_one' } }),
        _fetchWithAuth: async (url, options) => {
            uploads.push({ url, ...options });
            return { ok: true, json: async () => ({ id: 'original-excel', name: 'Plano.xlsx', modifiedTime: 'saved' }) };
        },
    };
    const result = await service.upsertFileToProjectFolder({ fileName: 'Plano.xlsx', content: Buffer.from('local-correct'),
        associationKey: 'project_one', driveTarget: { id: 'original-excel', modifiedTime: 'older', force: true } });
    assert.equal(result.action, 'updated');
    assert.equal(uploads[0].method, 'PATCH');
    assert.match(uploads[0].url, /files\/original-excel\?/);
    assert.match(uploads[0].body, /bG9jYWwtY29ycmVjdA==/);
});

test('an identical published PDF reuses its ID and only patches its name', async () => {
    const content = Buffer.from('%PDF-original');
    const file = { ...pdf, parents: [GOOGLE_DRIVE_CONFIG.FOLDERS.PDF], version: '8',
        sha256Checksum: createHash('sha256').update(content).digest('hex') };
    const calls = [];
    const service = {
        ...GoogleDriveService,
        fileMetadata: async id => {
            assert.equal(id, file.id);
            return file;
        },
        listFilesInFolder: async () => assert.fail('Unexpected folder listing'),
        _fetchWithAuth: async (url, options) => {
            calls.push(url);
            assert.match(url, /\/drive\/v3\/files\/pdf1\?/);
            assert.equal(options.method, 'PATCH');
            assert.deepEqual(JSON.parse(options.body), {name: 'Renombrado.pdf'});
            return {ok: true, json: async () => ({...file, name: 'Renombrado.pdf', version: '9'})};
        },
    };
    const args = {fileName: file.name, content, associationKey: 'project_one',
        driveTarget: {id: file.id, modifiedTime: file.modifiedTime}};
    const reused = await service.upsertFileToProjectFolder(args);
    assert.equal(reused.action, 'reused');
    assert.equal(reused.drive_file_id, file.id);
    assert.equal(calls.length, 0);
    const renamed = await service.upsertFileToProjectFolder({...args, fileName: 'Renombrado.pdf'});
    assert.equal(renamed.action, 'reused');
    assert.equal(renamed.drive_version, '9');
    assert.equal(calls.length, 1);
});

test('a different PDF still uploads its bytes to the validated original ID', async () => {
    const file = { ...pdf, parents: [GOOGLE_DRIVE_CONFIG.FOLDERS.PDF], sha256Checksum: 'different' };
    const content = Buffer.from('%PDF-updated');
    const service = {
        ...GoogleDriveService,
        fileMetadata: async () => file,
        _fetchWithAuth: async (url, options) => {
            assert.match(url, /\/upload\/drive\/v3\/files\/pdf1\?/);
            assert.equal(options.method, 'PATCH');
            assert.ok(options.body.includes(content.toString('base64')));
            return {ok: true, json: async () => file};
        },
    };
    const result = await service.upsertFileToProjectFolder({fileName: file.name, content,
        associationKey: 'project_one', driveTarget: {id: file.id, modifiedTime: file.modifiedTime}});
    assert.equal(result.action, 'updated');
});

test('forced recovery updates an Excel recreated since the initial lookup', async () => {
    const service = {
        ...GoogleDriveService,
        listFilesInFolder: async () => [{ id: 'recreated-excel', appProperties: { associationKey: 'project_one' } }],
        _fetchWithAuth: async (url, options) => {
            assert.equal(options.method, 'PATCH');
            assert.match(url, /files\/recreated-excel\?/);
            return { ok: true, json: async () => ({ id: 'recreated-excel', name: 'Plano.xlsx' }) };
        },
    };
    const result = await service.upsertFileToProjectFolder({ fileName: 'Plano.xlsx', content: Buffer.from('local-correct'),
        associationKey: 'project_one', driveTarget: { missing: true, force: true } });
    assert.equal(result.action, 'updated');
});
