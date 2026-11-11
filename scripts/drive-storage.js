/* Private original documents for Django FileFields. Published projects retain
 * their existing folders, association keys and explicit publication workflow. */
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { Readable, Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const { GOOGLE_DRIVE_CONFIG, GoogleDriveService } = require('../google-drive.js');
const ROLE = 'reloncavi-source-v1';
const LIMIT = 100 * 1024 * 1024;

function identifier(value) {
    if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value)) throw new Error('Identificador de archivo no valido.');
    return value;
}

async function metadata(drive, id) {
    const response = await drive._fetchWithAuth(`${drive.BASE_URL}/files/${identifier(id)}?fields=id,size,md5Checksum,appProperties,trashed`);
    if (response.status === 404) return null;
    if (!response.ok) throw new Error('No se pudo consultar el original en Drive.');
    const file = await response.json();
    if (file.trashed) return null;
    if (file.appProperties?.storageRole !== ROLE) throw new Error('Este archivo no pertenece al almacenamiento de originales.');
    return file;
}

async function save(drive, config, filename, name) {
    const size = fs.statSync(filename).size;
    if (size > LIMIT) throw new Error('El original supera el limite de almacenamiento.');
    const hash = createHash('md5');
    for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
    const checksum = hash.digest('hex');
    const extension = path.extname(name).toLowerCase();
    const contentType = ({'.pdf':'application/pdf','.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','.dxf':'application/dxf'})[extension] || 'application/octet-stream';
    const folder = extension === '.xlsx' ? config.FOLDERS.EXCEL_CSV : config.FOLDERS.PDF;
    const begin = await drive._fetchWithAuth('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,size,md5Checksum,appProperties', {
        method:'POST', headers:{'Content-Type':'application/json','X-Upload-Content-Type':contentType,'X-Upload-Content-Length':String(size)},
        body:JSON.stringify({name:path.basename(name),parents:[folder],appProperties:{storageRole:ROLE}}),
    });
    if (!begin.ok) throw new Error('No se pudo iniciar la subida del original.');
    const location = begin.headers.get('location');
    if (!location || new URL(location).origin !== 'https://www.googleapis.com') throw new Error('Destino de subida no valido.');
    // A Buffer can be replayed when the existing connector refreshes a 401 token.
    const response = await drive._fetchWithAuth(location, {method:'PUT',headers:{'Content-Type':contentType,'Content-Length':String(size)},body:fs.readFileSync(filename)});
    if (!response.ok) throw new Error('No se pudo guardar el original en Drive.');
    const file = await response.json();
    if (Number(file.size) !== size || file.md5Checksum !== checksum) throw new Error('La verificacion del archivo subido no coincide.');
    return file;
}

async function open(drive, id, destination) {
    const file = await metadata(drive, id);
    if (!file) return null;
    if (Number(file.size) > LIMIT) throw new Error('El original supera el limite de almacenamiento.');
    const response = await drive._fetchWithAuth(`${drive.BASE_URL}/files/${identifier(id)}?alt=media`);
    if (!response.ok || !response.body) throw new Error('No se pudo descargar el original.');
    let size = 0;
    const hash = createHash('md5');
    const verify = new Transform({transform(chunk, encoding, done) {
        size += chunk.length;
        if (size > LIMIT) return done(new Error('El original supera el limite de almacenamiento.'));
        hash.update(chunk); done(null,chunk);
    }});
    await pipeline(Readable.fromWeb(response.body),verify,fs.createWriteStream(destination,{flags:'wx'}));
    if (size !== Number(file.size) || hash.digest('hex') !== file.md5Checksum) throw new Error('La verificacion del original descargado no coincide.');
    return file;
}

async function trash(drive, id) {
    if (!await metadata(drive,id)) return;
    const response = await drive._fetchWithAuth(`${drive.BASE_URL}/files/${identifier(id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({trashed:true})});
    if (!response.ok) throw new Error('No se pudo mover el original a la papelera.');
}

async function main() {
    const args = {};
    for (let i=2;i<process.argv.length;i++) {
        const key = process.argv[i].replace(/^--/,'');
        args[key] = process.argv[i+1] && !process.argv[i+1].startsWith('--') ? process.argv[++i] : true;
    }
    let file;
    if (args.save) file=await save(GoogleDriveService,GOOGLE_DRIVE_CONFIG,args.file,args.name);
    else if (args.open) file=await open(GoogleDriveService,args.id,args.output);
    else if (args.stat) file=await metadata(GoogleDriveService,args.id);
    else if (args.delete) {await trash(GoogleDriveService,args.id); file={};}
    else throw new Error('Operacion no especificada.');
    console.log(JSON.stringify(file ? {ok:true,file} : {ok:false,missing:true}));
}
if (require.main === module) main().catch(() => {console.log(JSON.stringify({ok:false,error:'No se pudo completar el almacenamiento privado en Drive.'}));process.exitCode=1;});
module.exports = {ROLE,metadata,save,open,trash};
