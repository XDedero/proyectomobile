/* Carry project configuration inside the one XLSX published to Drive.
 * A custom XML part preserves worksheets, formulas and styling byte for byte.
 * No extra worksheet or separate Drive file is created.
 */
const JSZip = require('../mobileApp/static/mobileApp/vendor/jszip.min.js');
const { createHash } = require('node:crypto');
const PART = 'customXml/reloncavi-project.xml';
const RELS = 'xl/_rels/workbook.xml.rels';
const MAX_METADATA = 8 * 1024 * 1024;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

function appendElement(xml, root, name, attributes) {
    const prefix = xml.match(new RegExp(`<((?:[\\w.-]+:)?)${root}\\b`))?.[1];
    if (prefix === undefined) throw new Error('El Excel no contiene un paquete válido.');
    const element = `<${prefix}${name} ${attributes}/>`;
    const closing = `</${prefix}${root}>`;
    if (xml.includes(closing)) return xml.replace(closing, element + closing);
    const empty = new RegExp(`(<${prefix}${root}\\b[^<>]*?)/>`);
    if (!empty.test(xml)) throw new Error('Las relaciones del Excel no son válidas.');
    return xml.replace(empty, `$1>${element}${closing}`);
}

async function workbookDigest(zip) {
    const expandedSize = Object.values(zip.files).reduce((total, part) => total + (part._data?.uncompressedSize || 0), 0);
    if (expandedSize > 250 * 1024 * 1024) throw new Error('El Excel descomprimido supera el tamaño permitido.');
    const hash = createHash('sha256');
    for (const name of Object.keys(zip.files).sort()) {
        // Package relationships change when adding our part. Sheet relationships
        // and all actual content remain covered, including images and formulas.
        if (zip.files[name].dir || [PART, RELS, '[Content_Types].xml'].includes(name)) continue;
        const bytes = await zip.files[name].async('nodebuffer');
        hash.update(JSON.stringify([name, bytes.length]));
        hash.update(bytes);
    }
    return hash.digest('hex');
}

async function attachMetadata(content, manifest, pdf, projectKey) {
    const zip = await JSZip.loadAsync(content);
    const metadata = { version: 1, projectKey, pdfSha256: digest(pdf), workbookSha256: await workbookDigest(zip), manifest };
    const json = Buffer.from(JSON.stringify(metadata));
    if (json.length > MAX_METADATA) throw new Error('La configuración del proyecto supera el tamaño permitido.');
    zip.file(PART, '<?xml version="1.0" encoding="UTF-8"?><project xmlns="urn:reloncavi:project:1">' + json.toString('base64') + '</project>');
    let relations = await zip.file(RELS)?.async('string');
    if (!relations) relations = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>';
    if (!relations.includes('../' + PART)) {
        let id = 'reloncaviProject';
        while (relations.includes('"' + id + '"')) id += '_';
        relations = appendElement(relations, 'Relationships', 'Relationship', `Id="${id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../${PART}"`);
        zip.file(RELS, relations);
    }
    let types = await zip.file('[Content_Types].xml').async('string');
    if (!types.includes('/' + PART)) {
        types = appendElement(types, 'Types', 'Override', `PartName="/${PART}" ContentType="application/xml"`);
        zip.file('[Content_Types].xml', types);
    }
    return zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' });
}

async function readMetadata(content, pdf, projectKey) {
    const zip = await JSZip.loadAsync(content);
    const part = zip.file(PART);
    if (!part) return null; // Older projects use their Supabase record.
    if (part._data?.uncompressedSize > MAX_METADATA * 2) throw new Error('La configuración del proyecto supera el tamaño permitido.');
    const xml = await part.async('string');
    const encoded = xml.match(/<project xmlns="urn:reloncavi:project:1">([A-Za-z0-9+/=]+)<\/project>/)?.[1];
    if (!encoded || encoded.length > MAX_METADATA * 2) throw new Error('La configuración del proyecto no es válida.');
    const metadata = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
    if (metadata.version !== 1 || metadata.projectKey !== projectKey || metadata.pdfSha256 !== digest(pdf) || metadata.workbookSha256 !== await workbookDigest(zip)) {
        throw new Error('El PDF, el Excel y los botones no pertenecen al mismo guardado. Vuelva a guardar el proyecto desde el equipo de origen.');
    }
    return metadata.manifest;
}

module.exports = { attachMetadata, readMetadata };
