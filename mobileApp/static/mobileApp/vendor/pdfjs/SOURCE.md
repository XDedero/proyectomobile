# PDF.js incluido en la aplicación

Versión: **6.4.299**, edición oficial **legacy**, compilación `d0991a0d5`.
Se conserva la licencia Apache-2.0 en `LICENSE` y en los archivos originales.

Origen: [paquete oficial pdfjs-dist](https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-6.4.299.tgz).
Se verificó SHA-512 contra `dist.integrity` del registro antes de leer el paquete.
Los archivos originales son `package/legacy/build/pdf.mjs` y
`package/legacy/build/pdf.worker.mjs`, con estos SHA-256:

| Archivo | SHA-256 |
| --- | --- |
| pdf.mjs | bc51f4874a66e3853189cb5d50aba4b3096afa83ed433b885d293a3d94583cdc |
| pdf.worker.mjs | bb422f60804477de4a0b09091d8347a916a387755ed1cbad24ccaec1d09c78bf |

`pdf-entry.mjs`, `pdf.worker-entry.mjs` y `compat.mjs` son adaptaciones de esta
aplicación. Instalan `Promise.withResolvers` cuando falta, tanto en la página
como en el worker, y fijan la misma revisión de ambos componentes. La edición
legacy aporta las demás adaptaciones, incluida `URL.parse`.

Los módulos de entrada reciben además la versión de recursos de la plantilla
para renovar la caché al actualizar la aplicación. Al cambiar los archivos
originales o `compat.mjs`, se deben actualizar también las revisiones de sus
importaciones en los módulos de entrada.

WASM, fuentes y CMaps se sirven desde esta misma carpeta. No se descargan
dependencias de Internet al abrir un plano. El soporte de navegadores depende
también de la versión del motor y de las funciones del propio PDF; ver la
[compatibilidad oficial](https://github.com/mozilla/pdf.js/wiki/Frequently-Asked-Questions#which-browsersenvironments-are-supported).
