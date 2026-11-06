# Planos PDF y DXF con fichas de motores

Aplicación Django para importar planos, colocar botones, vincular fichas Excel y guardar o transportar proyectos completos.

La interfaz está adaptada a una futura WebView Android local, sin marco de
teléfono ni indicadores simulados. Los cambios preparados, el almacenamiento
`PLANOS_DATA_DIR`, las comprobaciones y lo pendiente del APK están en
[Preparación para Android local](docs/webview-local.md). No se configura nube
ni Android Studio; el futuro APK deberá incorporar también el código Python.

## Ejecutar

En Windows, abre **ejecutar_debug.bat**. Si falta el entorno de Python,
el lanzador prepara uno local e instala las dependencias. Luego aplica las
migraciones de SQLite y abre la app en **http://127.0.0.1:8000/** cuando esté lista.
El arranque local no depende de una sesión en Supabase. Para comprobar solo la
instalación, ejecuta `ejecutar_debug.bat --check-only`.

Para iniciar manualmente desde un entorno ya creado:

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

El visor PDF mantiene una vista general de **1280 píxeles en el lado mayor**, con las proporciones del plano. Al ampliar o desplazar, dibuja nuevamente solo la zona visible, también con un máximo de 1280 píxeles en su lado mayor. Durante el gesto reutiliza la imagen existente y recupera el detalle al detenerse, tras una pausa de 160 ms. No aumenta el tamaño del lienzo con el zoom ni con la densidad de píxeles del dispositivo. El zoom llega hasta 32 veces el ajuste inicial y los botones conservan sus coordenadas.

El recorte utiliza los límites de operaciones de PDF.js para omitir trazos fuera de la zona visible; al cambiar de página se liberan los lienzos anteriores. El PDF original se conserva sin reducir su resolución. La nitidez de un plano escaneado depende de su imagen original; el límite de los lienzos no limita la memoria que PDF.js necesita para decodificar imágenes del archivo. La implementación sigue el [renderizado de detalle de PDF.js](https://github.com/mozilla/pdf.js/blob/master/web/pdf_page_detail_view.js).

El visor utiliza **PDF.js 6.4.299 legacy**, con compatibilidad adicional para `Promise.withResolvers` en la página y en su worker. Ambos componentes tienen versiones de caché coordinadas, lo que evita mezclar archivos antiguos y nuevos. Esto corrige fallos reproducidos con funciones JavaScript ausentes en perfiles de Safari. La versión concreta de iOS sigue determinando las funciones disponibles. El origen de la dependencia y sus verificaciones están en [SOURCE.md](mobileApp/static/mobileApp/vendor/pdfjs/SOURCE.md).

Si un PDF guardado conserva su registro pero falta el archivo físico en `media/`, la aplicación lee el original incluido en su proyecto guardado. También utiliza esa copia al exportar o volver a guardar el proyecto. Se comprueban formato, referencia y tamaño del archivo. Al eliminar el plano se elimina también esa copia guardada. Si el original tampoco está en el respaldo, aparece un error que solicita volver a importarlo. Importar en `localhost` guarda archivos en esa instalación; transferir únicamente la base de datos no transfiere `media/`.

Los mensajes del visor distinguen un PDF inaccesible, una sesión sin acceso, un archivo inválido, una incompatibilidad del navegador y un fallo de dibujo. La consola conserva el error técnico para diagnosticarlo; un archivo físico ausente no se confunde con un plano eliminado.

## Guardar y abrir

**Guardar plano**, situado junto al visor PDF/DXF y en la barra de acciones de pantalla completa, guarda un proyecto con el archivo original, todos sus botones, las fichas asociadas y el listado del plano. Solo aparece para usuarios con permisos de agregar o modificar planos, proyectos, motores o fichas. El indicador muestra Sin guardar, Cambios pendientes o Guardado para el trabajo de la sesión actual.

**Ver planos guardados** en el menú principal, el menú de usuario y el módulo PDF abre una biblioteca común. Cada registro muestra **PDF** o **DXF** en pequeño. Volver a guardar el mismo plano actualiza su registro; no crea otra copia. Los planos existentes antes de esta actualización se conservan mediante la migración `0006`.

Las modificaciones y asignaciones del trabajo abierto se mantienen en el servidor. El botón Guardar plano registra o actualiza la copia completa de la biblioteca y su archivo de proyecto. Un plano nuevo no aparece en esa biblioteca hasta guardarlo.

**Eliminar plano** aparece junto a Guardar plano, en pantalla completa y como papelera en la biblioteca, exclusivamente para usuarios con permiso de eliminar el formato correspondiente. Tras confirmar, elimina el plano original, sus botones y su copia guardada; también cierra cualquier copia abierta de ese plano y descarta su restauración automática. No vuelve a la biblioteca hasta importarlo y guardarlo de nuevo. Los motores guardados y las fichas independientes se conservan.

Las sesiones conectadas a la misma instalación comprueban los planos abiertos y la biblioteca visible cada cinco segundos, al volver a la aplicación y al recuperar la conexión. Un plano eliminado se retira también de esos visores y se cierra su ficha abierta. Los fallos de conexión conservan el último estado confirmado; una respuesta antigua no puede reemplazar una consulta más reciente. Los planos recién importados siguen disponibles aunque aún no estén guardados.

Si un plano vuelve a aparecer **después de recargar**, hay que comprobar que ambos dispositivos usan la misma instalación y que su almacenamiento es persistente. Este proyecto utiliza SQLite en `PLANOS_DATA_DIR` (por defecto, la carpeta del proyecto) y actualmente `db.sqlite3` está incluido en Git. Un despliegue que recupera esa copia antigua puede recuperar también sus planos. Render utiliza por defecto un [sistema de archivos temporal](https://render.com/docs/disks): el código de borrado no puede conservar cambios de una base que la plataforma descarta. Esta corrección no cambia la configuración de Render ni modifica o elimina la base de datos local.

**Eliminar botón** está junto a la selección del plano y en la barra de selección de pantalla completa. En una ficha asociada a un botón también aparece una papelera en la cabecera del editor; las fichas de la biblioteca y los listados no muestran esa acción. La eliminación pide confirmación y conserva el dibujo y el resto de botones. La barra inferior del visor completo ofrece añadir, mover, guardar y eliminar plano según los permisos, sin abrir el menú.

## Excel del plano y hojas de motor

Cada plano tiene **un único libro Excel**. Los botones guardan el nombre de la hoja que utilizan. Dos botones vinculados a la misma hoja ven los mismos datos y cambios; fichas de motores diferentes pueden seguir en hojas distintas del mismo libro.

**Excel del plano** ofrece dos opciones:

- **Formato vacío:** utiliza las plantillas actuales, con logo, estilos, bordes, campos y gráficos, sin datos de ejemplo. Cada motor nuevo obtiene una hoja vacía independiente.
- **Importar Excel:** conserva las hojas, los datos y el formato del archivo. La primera hoja se asigna automáticamente al listado original. Los botones permiten elegir una hoja de motor del libro, crear una ficha vacía o incorporar un motor guardado.

Al importar otro libro se conservan en el libro resultante las fichas anteriores que contienen datos, incluidas las que aún no tienen botón, y los listados anteriores con información. Las fichas vacías iniciales se pueden reemplazar sin generar archivos separados. Si hay una variante anterior editada de un motor ya identificado, se conserva como otra hoja con el sufijo «anterior». Un botón de un libro importado permanece sin hoja hasta que el usuario elige una.

**Motor guardado** incorpora su ficha como una hoja del libro del plano. Si ese motor ya está incorporado, vuelve a vincular la misma hoja. También se reconocen fichas con los mismos datos, sin depender del nombre de su archivo. Las fichas vacías de motores todavía no identificados mantienen hojas independientes. La identidad de los motores registrados se conserva por hoja al exportar el XLSX completo y al transportar un proyecto.

**Guardar cambios** y Ctrl+S dentro del editor guardan únicamente los datos del Excel. Al cerrar una ficha también se guardan sus cambios pendientes. **Guardar plano**, fuera del editor, registra la copia completa en la biblioteca. Los conflictos de revisión mantienen abiertos los cambios pendientes para evitar sobrescrituras.

**Exportar .xlsx** descarga el libro completo del plano. **Exportar .csv** descarga únicamente la hoja seleccionada en UTF-8 con BOM. Las fichas que usan el formato estándar conservan Vista ficha, ajuste de ancho/página, zoom, logos y gráficos. Las fórmulas muestran el último resultado calculado por Excel; la aplicación no incluye un motor completo de recálculo.

## Listados de motores

**Gestionar listados** permite agregar un listado vacío, importar otro listado o convertir una hoja libre del mismo libro en un listado adicional. Se nombran **Listado motores 2**, **Listado motores 3**, etc. Una hoja asignada a un botón no puede convertirse en listado ni eliminarse como tal. También puede cambiar la hoja del listado original por otra hoja libre.

**Ver listados** abre los listados en el editor; su selector de hojas contiene todos los listados del plano. El botón **Listados** del editor permite administrarlos. **+ Fila** y **+ Columna** conservan la estructura y los estilos de las plantillas. Se eliminó la opción Reutilizar un Excel asignado: los listados pertenecen al propio libro del plano.

El listado original requiere permiso de eliminación del plano PDF/DXF. Los listados adicionales pueden ser eliminados por su creador o por una cuenta con ese permiso. La protección de un original se conserva si se importa otro libro y el listado anterior queda como hoja adicional.

## Motores guardados

**Guardar motor**, desde una ficha rellenada del plano, conserva únicamente esa hoja como original reutilizable en **Motores guardados**. Un formulario vacío no se registra. El original de la biblioteca se consulta sin modificarlo; la hoja incorporada a un plano sí puede editarse con los permisos correspondientes. Sus cambios se comparten entre los botones de ese plano que apuntan a la misma hoja.

La biblioteca permite buscar por nombre, consultar una ficha e incorporarla al botón seleccionado. Un motor ya registrado muestra su origen y desactiva Guardar motor, incluso después de editar su hoja. Los originales de esta biblioteca se conservan por separado para poder utilizarlos en otros planos; los botones no crean archivos adicionales por cada asignación.

## Transportar un proyecto

**Exportar proyecto** descarga un archivo **.planproj**. Si el usuario tiene permiso de guardar, actualiza primero su copia del proyecto; un lector puede exportar el trabajo abierto sin crear una copia en la biblioteca. Es un ZIP versionado que reúne:

- El PDF o DXF original.
- Las páginas y posiciones de los botones.
- Un XLSX que reúne las fichas y todos los listados, conservando su formato.
- Los vínculos de cada botón con su hoja y los orígenes de los motores guardados.

El formato actual es la **versión 3**: incluye el plano, un único XLSX, las hojas asignadas a cada botón, todos los listados y su protección. También se aceptan proyectos de las versiones 1 y 2 y se reúnen sus fichas en el libro del plano al abrirlos. Los documentos antiguos se conservan durante esa adaptación.

En otra instalación, use **Importar PDF configurable → Importar proyecto**. Se admiten PDF y DXF; el visor elige el formato automáticamente. **Guardar plano** añade la copia importada a la biblioteca. La importación valida el manifiesto, los nombres de hojas y los archivos y no extrae rutas arbitrarias del ZIP. Límites: proyecto de 100 MB, contenido de 250 MB y hasta 5.000 botones. La importación normal de XLSX admite 20 MB; el libro reunido puede alcanzar 100 MB.

## Usuarios y permisos

Todos los usuarios registrados y activos pueden importar, añadir botones, asignar fichas o listados, registrar motores con fichas ya rellenadas y exportar proyectos. Guardar un plano requiere permisos de agregar o modificar. Los permisos de modificación y eliminación se mantienen independientes:

| Acción | Permiso |
| --- | --- |
| Guardar un plano DXF | Cualquier permiso `add` o `change` de `plan`, `motor`, `workbookdocument` o `savedproject` |
| Guardar un plano PDF | Los anteriores, o `add`/`change` de `pdfplan` o `pdfmarker` |
| Editar datos y agregar filas/columnas de Excel | `mobileApp.change_workbookdocument` o `mobileApp.change_motor` |
| Cambiar nombre o mover botones de un DXF | `mobileApp.change_plan` |
| Eliminar un DXF | `mobileApp.delete_plan` |
| Cambiar nombre o mover/eliminar botones de un PDF | `mobileApp.change_pdfplan` o `mobileApp.change_plan` |
| Eliminar un PDF | `mobileApp.delete_pdfplan` o `mobileApp.delete_plan` |
| Eliminar un botón DXF | `change_plan`, `delete_plan` o `delete_motor` |
| Eliminar el listado original o un listado protegido | Permiso de eliminar el plano PDF/DXF correspondiente |
| Eliminar un listado adicional | Su creador o una cuenta con permiso de eliminar el plano |
| Eliminar un botón PDF | `change_pdfplan`, `change_plan`, `delete_pdfplan`, `delete_plan`, `delete_pdfmarker` o `delete_motor` |

Los lectores ven los mismos documentos y pueden exportarlos, pero el servidor rechaza cambios de contenido y movimientos sin permiso. Todas las modificaciones requieren CSRF. No se cambian cuentas, contraseñas o permisos existentes.

## Actualizar la instalación y verificar

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test mobileApp --noinput
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/shared_workbook_browser_smoke.py
.\.venv\Scripts\python.exe scripts/pdf_resolution_browser_smoke.py
.\.venv\Scripts\python.exe scripts/multi_device_deletion_browser_smoke.py
.\.venv\Scripts\python.exe -m playwright install webkit
.\.venv\Scripts\python.exe scripts/pdf_compatibility_browser_smoke.py
```

Las migraciones 0008–0011 incorporan las referencias a hojas y la protección de listados. Los planos de instalaciones anteriores reúnen sus documentos al abrirse; los originales anteriores permanecen disponibles en la base de datos como respaldo de la conversión.

Las pruebas usan bases de datos y archivos temporales. Cubren PDF/DXF, libros con formato, motores compartidos, originales de la biblioteca, varios listados, permisos, importación y exportación de proyectos y el flujo móvil. Las capturas quedan en `.test-artifacts/`. La prueba de navegador actual del sistema de hojas es `shared_workbook_browser_smoke.py`.

`pdf_resolution_browser_smoke.py` comprueba el límite de resolución, la calidad del recorte frente al renderizado completo, el agrupamiento de cambios de zoom, el arrastre, la alineación de botones, páginas giradas, escaneos, cambios de orientación y la liberación de lienzos.

`multi_device_deletion_browser_smoke.py` abre sesiones independientes, elimina PDF y DXF y comprueba su retirada de los visores, fichas y bibliotecas del otro dispositivo, incluida la recarga. También cubre consultas que llegan fuera de orden y fallos de conexión.

`pdf_compatibility_browser_smoke.py` comprueba Chromium y WebKit con sesiones independientes, funciones JavaScript ausentes en la página y el worker y un PDF cuyo archivo en `media/` se ha eliminado. Abre la copia guardada, páginas giradas y escaneadas y verifica el zoom a 1280 píxeles. También comprueba el mensaje de archivo inaccesible y la reapertura posterior.
