/* The catalog lives in one root workbook; its index is not a visible worksheet. */
const JSZip = require('../mobileApp/static/mobileApp/vendor/jszip.min.js');
const PART = 'customXml/reloncavi-pieces.xml';
async function readPieceIndex(content) {
    const zip = await JSZip.loadAsync(content);
    const xml = await zip.file(PART)?.async('string');
    if (!xml) return [];
    const encoded = xml.match(/<pieces\b[^>]*>([A-Za-z0-9+/=\s]+)<\/pieces>/)?.[1];
    if (!encoded) throw new Error('El índice de piezas.xlsx no es válido.');
    const data = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
    if (!Array.isArray(data) || data.length > 5000) throw new Error('El catálogo de piezas no es válido.');
    return data;
}
async function attachPieceIndex(content, index) {
    const zip = await JSZip.loadAsync(content);
    zip.file(PART, '<?xml version="1.0" encoding="UTF-8"?><pieces xmlns="urn:reloncavi:pieces:1">'+Buffer.from(JSON.stringify(index)).toString('base64')+'</pieces>');
    return zip.generateAsync({type:'nodebuffer', compression:'DEFLATE'});
}
async function findLibrary(drive) {
    const params = new URLSearchParams({q:"trashed = false and 'root' in parents and name = 'piezas.xlsx'", fields:'files(id,name,modifiedTime,appProperties)', pageSize:'100'});
    const response = await drive._fetchWithAuth(`${drive.BASE_URL}/files?${params}`);
    if (!response.ok) throw new Error('No se pudo consultar piezas.xlsx.');
    const {files} = await response.json();
    if (files.length > 1) throw new Error('Hay más de un piezas.xlsx en la raíz de Drive. No se reemplazará ninguno.');
    if (files[0] && files[0].appProperties?.role !== 'reloncavi-piece-library') throw new Error('Ya existe un piezas.xlsx ajeno al catálogo. Cambie su nombre antes de crear el catálogo.');
    return files[0] || null;
}
async function writeLibrary(drive, existing, content) {
    const boundary = 'pieceLibrary'+Date.now();
    const metadata = {name:'piezas.xlsx', appProperties:{role:'reloncavi-piece-library'}};
    if (!existing) metadata.parents = ['root'];
    const body = Buffer.concat([
        Buffer.from(`--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify(metadata)}\r\n--${boundary}\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n`),
        content, Buffer.from(`\r\n--${boundary}--\r\n`),
    ]);
    const url = `https://www.googleapis.com/upload/drive/v3/files${existing ? '/'+encodeURIComponent(existing.id) : ''}?uploadType=multipart&fields=id,name,modifiedTime`;
    const response = await drive._fetchWithAuth(url, {method:existing?'PATCH':'POST',headers:{'Content-Type':`multipart/related; boundary=${boundary}`},body});
    if (!response.ok) throw new Error('No se pudo guardar piezas.xlsx en Drive.');
    return response.json();
}
module.exports = {readPieceIndex, attachPieceIndex, findLibrary, writeLibrary};
