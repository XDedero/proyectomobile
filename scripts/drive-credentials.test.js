const {test} = require('node:test');
const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const path = require('node:path');

function config(payload) {
    const script = `const {GOOGLE_DRIVE_CONFIG:c,GoogleDriveAuth:a}=require('./google-drive');
        require('./scripts/piece-library');
        a._loadNodeTokenFile();
        console.log(JSON.stringify({client:c.CLIENT_ID,refresh:c.REFRESH_TOKEN,folders:c.FOLDERS}));`;
    return spawnSync(process.execPath, ['-e', script], {
        cwd:path.resolve(__dirname, '..'), encoding:'utf8',
        env:{...process.env, PLANOS_COMPANY_DRIVE:JSON.stringify(payload)},
    });
}

test('Vault credentials for the legacy company retain its existing folders and piece library', () => {
    const result = config({legacy:true, credentials:{client_id:'vault-client',client_secret:'test-secret',refresh_token:'vault-token'}});
    assert.equal(result.status,0,result.stderr);
    const actual = JSON.parse(result.stdout);
    assert.equal(actual.client,'vault-client');
    assert.equal(actual.refresh,'vault-token');
    assert.equal(actual.folders.PDF,'1d7sf6i8zPsi52JVQJuaPu-Vv25GHHXoX');
});

test('company connections never use legacy folders or a token file', () => {
    const folders={pdf:'other-pdf-folder',excel:'other-excel-folder',originals:'other-originals',root:'other-root-folder'};
    const result = config({credentials:{client_id:'other-client',client_secret:'test-secret',refresh_token:'other-token'},folders});
    assert.equal(result.status,0,result.stderr);
    const actual = JSON.parse(result.stdout);
    assert.equal(actual.client,'other-client');
    assert.equal(actual.refresh,'other-token');
    assert.equal(actual.folders.ROOT,folders.root);
    assert.equal(actual.folders.PDF,folders.pdf);
});

test('an incomplete company authorization fails instead of using the legacy token file', () => {
    const result=config({credentials:{client_id:'client',client_secret:'secret',refresh_token:'token'},folders:{}});
    assert.notEqual(result.status,0);
});
