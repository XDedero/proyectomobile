# Planos PDF y DXF con fichas de motores

Aplicación Django para importar planos, colocar botones, vincular fichas Excel y guardar o transportar proyectos completos.

La interfaz está adaptada a una futura WebView Android local, sin marco de
teléfono ni indicadores simulados. Los cambios preparados, el almacenamiento
`PLANOS_DATA_DIR`, las comprobaciones y lo pendiente del APK están en
[Preparación para Android local](docs/webview-local.md). No se configura nube
ni Android Studio; el futuro APK deberá incorporar también el código Python.

## Ejecutar

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Abra http://127.0.0.1:8000/ e inicie sesión con una cuenta activa de Django. Para crear un administrador use `manage.py createsuperuser`. La importación y el guardado requieren el servidor; `index.html` solo sirve como copia de la interfaz.

## Importar y usar planos

**Importar plano AutoCAD** acepta exclusivamente DXF de texto o binarios, hasta 20 MB. DWG se rechaza por extensión y contenido. Los motores se detectan por `MOTOR` en nombres de bloques, textos TEXT/MTEXT y atributos, respetando sus coordenadas y transformaciones. Cada motor recibe automáticamente un botón.

**Importar PDF configurable** admite PDF sin contraseña de hasta 20 MB y 1.000 páginas. Ambos formatos permiten añadir botones, importar fichas del dispositivo o usar motores de la sección **Motores guardados**.

**Pantalla completa** amplía el visor al dispositivo. El menú **⋮** contiene Explorar, Agregar botón, Mover botón, Importar motor guardado, Asignar listado, Ver listado, Guardar plano, Exportar proyecto y zoom. Los PDF tienen también un selector de página. Las fichas y los diálogos de asignación funcionan sobre el visor completo; **Volver** recupera la vista normal. Los navegadores sin la API nativa usan la misma vista que ocupa toda la ventana.

Mover botones requiere permiso para modificar el formato correspondiente. Las coordenadas se conservan al cambiar el zoom, la orientación o el tamaño del visor.

## Guardar y abrir

**Guardar plano**, disponible en PDF, DXF, pantalla completa y el editor de fichas, guarda un proyecto con el archivo original, todos sus botones, las fichas asociadas y el listado del plano.

**Ver planos guardados** en el menú principal, el menú de usuario y el módulo PDF abre una biblioteca común. Cada registro muestra **PDF** o **DXF** en pequeño. Volver a guardar el mismo plano actualiza su registro; no crea otra copia. Los planos existentes antes de esta actualización se conservan mediante la migración `0006`.

Las modificaciones y asignaciones del trabajo abierto se mantienen en el servidor. El botón Guardar plano registra o actualiza la copia completa de la biblioteca y su archivo de proyecto. Un plano nuevo no aparece en esa biblioteca hasta guardarlo.

## Listado general de motores

El listado se vincula directamente al plano, de forma independiente de sus botones. **Asignar listado → Crear listado de motores** usa la plantilla de la captura: logo, título, número de motor, especificaciones y sector. Se ofrece inicialmente espacio para nueve motores y se amplía al crearla si ya hay más botones.

**+ Fila** agrega motores, conservando los campos combinados y la numeración. **+ Columna** agrega campos junto a la tabla, conservando estilos y ampliando el encabezado. También puede importar su propio XLSX o asignar un libro existente. **Ver listado** abre el archivo ligado al plano completo.

La plantilla se guarda en `mobileApp/data/motor-list-template.xlsx`, obtenida de la hoja Listado del libro de referencia.

## Fichas Excel y exportación

Cada botón nuevo, detectado automáticamente o colocado a mano en PDF/DXF, recibe una ficha vacía de `mobileApp/data/motor-template.xlsx`. Esta plantilla reproduce la ficha de referencia: logo, encabezado, datos eléctricos y mecánicos, accionamientos, mediciones, observaciones y los dos gráficos. Los datos de ejemplo se han eliminado. La migración `0007` completa los botones existentes sin ficha y separa los documentos compartidos sin perder sus datos.

Todos los botones parten del mismo formato, pero cada uno tiene su propio archivo. Rellenar, guardar o exportar una ficha no modifica las de otros motores ni el listado del plano. Importar un motor guardado también crea una copia independiente.

Los Excel se conservan con sus tamaños, estilos, bordes, combinaciones e imágenes. Las fichas numeradas se abren en Vista ficha, con ajuste de página, de ancho y zoom. Los gráficos de líneas de consumo y aislación se dibujan desde el XML original. Listado mantiene su vista de tabla.

El guardado es el del plano: no existe un apartado independiente de Excel guardados ni un botón Guardar Excel. Al cerrar una ficha, sus cambios se incorporan al trabajo del plano; **Guardar plano** conserva la copia completa con todas las fichas y el listado. Los conflictos de revisión impiden sobrescribir cambios ajenos; si falló el guardado, la ficha permanece abierta con los cambios.

**Exportar .xlsx** descarga el libro completo. **Exportar .csv** descarga la hoja seleccionada en UTF-8 con BOM y separador coma. Estas opciones están disponibles tanto en fichas de motor como en el listado. CSV contiene valores, sin los estilos y gráficos propios de XLSX.

Los cambios de celdas preservan las partes originales del paquete XLSX. Las fórmulas usan su último resultado calculado por Excel; la aplicación no incluye recálculo completo. Otros tipos de gráficos, formas especiales y formatos condicionales no tienen reproducción completa, pero se conservan en el XLSX.

## Motores guardados

**Guardar motor** en una ficha rellenada pide un nombre y conserva una copia original de sus datos en **Motores guardados**, accesible desde el menú principal. Una ficha vacía no se puede registrar. La sección permite buscar por nombre, consultar la ficha original, importarla en el botón seleccionado o abrir una ficha XLSX del dispositivo para registrarla. El registro original se consulta sin modificarlo; las copias asignadas a botones sí se pueden editar con los permisos correspondientes.

Una ficha proveniente de un motor ya guardado muestra su nombre de origen y desactiva Guardar motor, incluso si se modifican los datos de su copia. Al exportar XLSX se conserva ese identificador, por lo que se reconoce también al reimportarlo desde el dispositivo. Las fichas anteriores sin identificador se comparan por sus datos. Un archivo externo que no exista en esta biblioteca puede registrarse con un nombre único. CSV conserva los valores y no incluye el identificador de XLSX.

Guardar motor conserva un original para reutilizarlo; **Guardar plano** sigue incluyendo automáticamente todas sus fichas y el listado. No hay que registrar cada ficha para poder guardar un proyecto.

## Transportar un proyecto

**Exportar proyecto** guarda primero el trabajo y descarga un archivo **.planproj**. Es un ZIP versionado que reúne:

- El PDF o DXF original.
- Las páginas y posiciones de los botones.
- Las fichas XLSX asociadas, con sus formatos originales.
- El listado general de motores.

Los libros con contenido idéntico se incluyen una sola vez en el archivo; al importarlo se crea una ficha independiente para cada botón. El formato actual es la versión 2 y también se aceptan los proyectos de la versión 1. En otro dispositivo o instalación, use **Importar PDF configurable → Importar proyecto**, seleccione el archivo y abra el proyecto recuperado. Se admiten proyectos PDF y DXF desde ese mismo botón; el visor elige el formato automáticamente. **Guardar plano** añade el proyecto importado a la biblioteca del dispositivo o servidor actual.

La importación valida el manifiesto, los planos y los Excel; no extrae rutas arbitrarias del ZIP ni reemplaza proyectos existentes. Límites: archivo de proyecto de 100 MB, contenido de 250 MB, hasta 5.000 botones y 5.001 libros.

## Usuarios y permisos

Todos los usuarios registrados y activos pueden importar, añadir botones, asignar fichas o listados, registrar motores con fichas ya rellenadas, guardar proyectos y exportarlos. Los permisos de modificación y eliminación se mantienen independientes:

| Acción | Permiso |
| --- | --- |
| Editar datos y agregar filas/columnas de Excel | `mobileApp.change_workbookdocument` o `mobileApp.change_motor` |
| Cambiar nombre o mover botones de un DXF | `mobileApp.change_plan` |
| Eliminar un DXF | `mobileApp.delete_plan` |
| Cambiar nombre o mover/eliminar botones de un PDF | `mobileApp.change_pdfplan` o `mobileApp.change_plan` |
| Eliminar un PDF | `mobileApp.delete_pdfplan` o `mobileApp.delete_plan` |

Los lectores ven los mismos documentos y pueden exportarlos, pero el servidor rechaza cambios de contenido y movimientos sin permiso. Todas las modificaciones requieren CSRF. No se cambian cuentas, contraseñas o permisos existentes.

## Verificación

```powershell
.\.venv\Scripts\python.exe manage.py test mobileApp --noinput
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/browser_smoke.py
.\.venv\Scripts\python.exe scripts/pdf_browser_smoke.py
.\.venv\Scripts\python.exe scripts/project_browser_smoke.py
.\.venv\Scripts\python.exe scripts/motor_workbook_smoke.py
.\.venv\Scripts\python.exe scripts/motor_library_browser_smoke.py
.\.venv\Scripts\python.exe scripts/mobile_browser_smoke.py
```

Las pruebas Chrome usan bases de datos y archivos temporales independientes. Para comparar un Excel real: `scripts/motor_workbook_smoke.py --workbook "ruta\Ficha Motores.xlsx"`. Las capturas se guardan en `.test-artifacts/`. El archivo original y la base de datos de la aplicación no se modifican durante esas pruebas.
