const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {ROLE,metadata,save,open,trash} = require('./drive-storage');
const original=Buffer.from('%PDF-exact-private-original\0\xff');
const file={id:'private-source',size:String(original.length),md5Checksum:createHash('md5').update(original).digest('hex'),appProperties:{storageRole:ROLE}};
const response=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});

test('resumable uploads verify the original bytes and keep sources separate from publications',async()=>{
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'drive-source-'));
    try {
        const source=path.join(directory,'source'); fs.writeFileSync(source,original);
        let calls=0;
        const drive={_fetchWithAuth:async(url,options)=>{
            calls++;
            if(calls===1){
                const data=JSON.parse(options.body);
                assert.equal(data.appProperties.storageRole,ROLE);
                assert.equal(data.appProperties.associationKey,undefined);
                assert.deepEqual(data.parents,['pdf-folder']);
                return new Response(null,{headers:{location:'https://www.googleapis.com/upload/session'}});
            }
            assert.deepEqual(options.body,original);
            return response(file);
        }};
        assert.equal((await save(drive,{FOLDERS:{PDF:'pdf-folder',EXCEL_CSV:'excel-folder'}},source,'pdf_plans/original.pdf')).id,file.id);
    } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test('downloads verify checksum before returning bytes',async()=>{
    const directory=fs.mkdtempSync(path.join(os.tmpdir(),'drive-source-'));
    try {
        const drive={BASE_URL:'https://drive.test',_fetchWithAuth:async url=>url.includes('alt=media')?new Response(original):response(file)};
        const destination=path.join(directory,'source');
        await open(drive,file.id,destination);
        assert.deepEqual(fs.readFileSync(destination),original);
        const bad={...drive,_fetchWithAuth:async url=>url.includes('alt=media')?new Response('damaged'):response(file)};
        await assert.rejects(open(bad,file.id,path.join(directory,'damaged')),/verificacion/);
    } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test('the storage cannot trash a published or unrelated Drive file',async()=>{
    let writes=0;
    const drive={BASE_URL:'https://drive.test',_fetchWithAuth:async(url,options)=>{
        if(options?.method==='PATCH') writes++;
        return response({...file,appProperties:{associationKey:'published-project'}});
    }};
    await assert.rejects(trash(drive,file.id),/no pertenece/);
    assert.equal(writes,0);
});

test('missing and already trashed originals are handled without deleting other files',async()=>{
    assert.equal(await metadata({BASE_URL:'https://drive.test',_fetchWithAuth:async()=>new Response(null,{status:404})},file.id),null);
    assert.equal(await metadata({BASE_URL:'https://drive.test',_fetchWithAuth:async()=>response({...file,trashed:true})},file.id),null);
});
