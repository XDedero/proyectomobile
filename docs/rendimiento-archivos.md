# Archivos y rendimiento del guardado

## Qué se cambia

| Estructura | Decisión y motivo |
| --- | --- |
| `WorkbookDocument.content`, `SavedProject.content` | Sustituir `BinaryField` por referencias privadas de hasta 255 caracteres. Los XLSX y `.planproj` se escriben en el almacenamiento configurado: Drive en modo nube y `media/documents` en modo local. |
| Lectura de documentos | Carga diferida: consultar el modelo no descarga el archivo. El contenido se verifica con SHA-256 y se reutiliza dentro de la instancia. |
| Cola de publicación | Conservar la cola ordenada entre guardados. Las tareas retienen la referencia de su archivo inmutable; ya no mantienen todos los ZIP pendientes ni sus modelos cargados en RAM. |
| PDF y Excel publicados | Subir ambos en paralelo, con un máximo de dos transferencias dentro de cada tarea. Registrar el proyecto únicamente cuando ambas terminan correctamente. |
| Descargas repetidas de Drive | Caché privada y descartable en disco, limitada a 128 MiB por instalación/proceso de despliegue que comparte ese directorio. `PLANOS_FILE_CACHE_BYTES=0` la desactiva. No sustituye a Drive ni permite confirmar una subida sin respuesta del servidor. |
| Descargas del Excel y del proyecto guardado | Verificar el archivo por bloques y transmitirlo con `FileResponse`, evitando cargar otra copia completa en un búfer de Python. |

La aplicación mantiene `document.content` como bytes para conservar el código de
edición, exportación y validación. El campo personalizado guarda exclusivamente
una referencia en SQL. `values('content')` y `values_list('content')` entregan esa
referencia; para leer bytes se utiliza una instancia del modelo. La serialización
Django también transporta referencias, igual que los campos de archivo. Véase
la [documentación de campos personalizados de Django](https://docs.djangoproject.com/en/5.2/howto/custom-model-fields/).

## Qué se conserva

- El formato `.planproj` versión 3 y la importación de versiones 1 y 2.
- PDF/DXF originales, fórmulas, estilos, hojas, listados, sectores y botones.
- Permisos, revisiones y respuesta de conflicto al intentar sobrescribir un Excel modificado por otro usuario.
- Las asociaciones e identificadores de publicación en Drive, comprobaciones de versiones, recuperación de pares PDF/Excel y fusión de adiciones.
- El estado pendiente hasta confirmar el registro completo. La constancia de archivos subidos permite reintentar SQL sin volver a subirlos.

El código ya publicaba en segundo plano y agrupaba el registro SQL del proyecto
en una transacción. También utiliza `psycopg` cuando hay una URL PostgreSQL
configurada. Se conservan estas optimizaciones. El conector JavaScript de Drive
y la alternativa CLI de Supabase siguen disponibles: su sustitución completa
requiere validar autenticación, conflictos y recuperación por separado. Se hace
atómica la escritura del token para que las subidas paralelas no lean JSON parcial.

## Aplicar a una instalación existente

1. Detener los procesos de la aplicación que puedan escribir y respaldar la base
   de datos. En una instalación local, respaldar también `media/`; después de esta
   actualización la base de datos por sí sola ya no contiene los documentos.
2. Mantener la misma configuración de almacenamiento. En modo nube se requiere
   autorización de Drive con permisos de escritura y espacio para las copias.
3. Ejecutar `python manage.py migrate` con el entorno de esa instalación.
4. Iniciar la aplicación y comprobar un proyecto anterior: abrir, descargar su
   Excel, guardar y exportar/importar su `.planproj`.

Las migraciones están separadas deliberadamente:

- `0033` añade las referencias, conservando los binarios.
- `0034` copia un archivo a la vez, verifica el contenido y registra la referencia.
  Si falla, los BLOB siguen en la base; al reintentar reutiliza las copias ya verificadas.
- `0035` vuelve a verificar las copias antes de retirar las columnas binarias y
  activar el nuevo campo. Si detecta una modificación o un archivo inaccesible,
  se detiene. La reversión restaura los bytes desde los archivos.

Durante la migración deben permanecer detenidas las escrituras. No se aplica esta
migración automáticamente a la base real durante el desarrollo de estos cambios.
No debe borrarse `media/` en modo local. En nube, la caché de `media/.drive-cache`
sí puede perderse sin perder los archivos duraderos de Drive.

Retirar las columnas no garantiza que el fichero físico de una base SQLite se
reduzca inmediatamente: recuperar su espacio libre es una operación de
mantenimiento posterior, distinta de esta migración.

Las versiones reemplazadas de archivos son inmutables y se conservan para que
un guardado pendiente o un lector de una revisión anterior siga siendo válido.
No se añade un barrido automático de versiones: eliminarlas requiere una política
de retención que contemple tareas pendientes y respaldos. Al borrar un registro,
su archivo actual se retira después del commit si ningún otro registro lo utiliza.

## Alcance de la validación

Las pruebas verifican los bytes exactos y las referencias SQL, lectura diferida,
conflictos de revisión, fallo de escritura, caché, eliminación después del commit,
subidas simultáneas y confirmación posterior de SQL. También prueban migración
con datos antiguos, reversión y reintento después de una copia fallida.

No se ha medido la latencia real de Render, Drive y Supabase. Quitar los BLOB
reduce el tráfico y la memoria asociados a consultas SQL; cada versión nueva
todavía requiere una escritura duradera de su archivo. La primera lectura sin
caché necesita acceder al almacenamiento remoto. El editor aún debe cargar los
bytes del libro que transforma y el guardado aún construye un proyecto en memoria.
