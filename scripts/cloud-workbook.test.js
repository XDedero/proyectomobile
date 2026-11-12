const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const JSZip = require('../mobileApp/static/mobileApp/vendor/jszip.min.js');
const { attachMetadata, readMetadata } = require('./cloud-workbook');
const source = fs.readFileSync(path.join(__dirname, '../mobileApp/data/motor-template.xlsx'));
const pdf = Buffer.from('%PDF-test');
const manifest = { name: 'Motores', markers: [{ page: 1, x: .4, y: .5, sheet: 'Motor', category_name: 'Motores' }] };
const key = 'project_0123456789abcdef0123456789abcdef';

test('carries buttons without changing any original sheet, formula or style', async () => {
    const result = await attachMetadata(source, manifest, pdf, key);
    assert.deepEqual(await readMetadata(result, pdf, key), manifest);
    const before = await JSZip.loadAsync(source), after = await JSZip.loadAsync(result);
    for (const name of Object.keys(before.files)) {
        if (before.files[name].dir || ['[Content_Types].xml', 'xl/_rels/workbook.xml.rels'].includes(name)) continue;
        assert.deepEqual(await after.file(name).async('nodebuffer'), await before.file(name).async('nodebuffer'), name);
    }
    const twice = await attachMetadata(result, manifest, pdf, key);
    assert.deepEqual(await readMetadata(twice, pdf, key), manifest);
    const rels = await (await JSZip.loadAsync(twice)).file('xl/_rels/workbook.xml.rels').async('string');
    assert.equal(rels.match(/Target="..\/customXml\/reloncavi-project.xml"/g).length, 1);
});

test('legacy XLSX still opens and mismatched PDF or project is rejected', async () => {
    assert.equal(await readMetadata(source, pdf, key), null);
    const result = await attachMetadata(source, manifest, pdf, key);
    await assert.rejects(readMetadata(result, Buffer.from('another PDF'), key));
    await assert.rejects(readMetadata(result, pdf, 'another-project'));
    const zip = await JSZip.loadAsync(result);
    zip.file('xl/worksheets/sheet1.xml', '<changed/>');
    await assert.rejects(readMetadata(await zip.generateAsync({ type: 'nodebuffer' }), pdf, key));
});

test('two PCs with the same local ID cannot overwrite each other under new project keys', async () => {
    const { GoogleDriveService: drive } = require('../google-drive');
    const oldList = drive.listFilesInFolder, oldFetch = drive._fetchWithAuth;
    try {
        drive.listFilesInFolder = async () => [{ id: 'other-pc-file', name: 'Motor.xlsx', appProperties: {
            associationKey: 'project_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', localRecordKey: 'local_xlsx_1',
        } }];
        let method;
        drive._fetchWithAuth = async (url, options) => {
            method = options.method;
            return { ok: true, json: async () => ({ id: 'new-file', name: 'Motor.xlsx' }) };
        };
        await drive.upsertFileToProjectFolder({ fileName: 'Motor.xlsx', content: source, associationKey: key, localRecordKey: 'local_xlsx_1' });
        assert.equal(method, 'POST');
    } finally {
        drive.listFilesInFolder = oldList;
        drive._fetchWithAuth = oldFetch;
    }
});

test('a shared workbook updates its original Drive ID only while the expected version exists', async () => {
    const { GoogleDriveService: drive, GOOGLE_DRIVE_CONFIG } = require('../google-drive');
    const oldMetadata = drive.fileMetadata, oldFetch = drive._fetchWithAuth;
    const file = { id: 'shared-excel', name: 'Motor.xlsx', modifiedTime: 'original-date',
        parents: [GOOGLE_DRIVE_CONFIG.FOLDERS.EXCEL_CSV], appProperties: { associationKey: key } };
    const args = { fileName: 'Nuevo nombre.xlsx', content: source, associationKey: key,
        driveTarget: { id: file.id, modifiedTime: file.modifiedTime } };
    let patches = 0;
    try {
        drive.fileMetadata = async () => file;
        drive._fetchWithAuth = async (url, options) => {
            assert.match(url, /\/files\/shared-excel\?/);
            assert.equal(options.method, 'PATCH');
            patches++;
            return { ok: true, json: async () => ({ ...file, modifiedTime: 'new-date' }) };
        };
        assert.equal((await drive.upsertFileToProjectFolder(args)).modified_time, 'new-date');
        file.modifiedTime = 'changed-by-another-pc';
        await assert.rejects(drive.upsertFileToProjectFolder(args));
        drive.fileMetadata = async () => null;
        await assert.rejects(drive.upsertFileToProjectFolder(args));
        assert.equal(patches, 1);
    } finally {
        drive.fileMetadata = oldMetadata;
        drive._fetchWithAuth = oldFetch;
    }
});
