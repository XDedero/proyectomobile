# Planos PDF con fichas de motores

Aplicación Django para importar planos, colocar botones, vincular fichas Excel y guardar o transportar proyectos completos.

La interfaz está adaptada a una futura WebView Android local, sin marco de
teléfono ni indicadores simulados. Los cambios preparados, el almacenamiento
`PLANOS_DATA_DIR`, las comprobaciones y lo pendiente del APK están en
[Preparación para Android local](docs/webview-local.md). No se configura nube
ni Android Studio; el futuro APK deberá incorporar también el código Python.

## Ejecutar

Para instalarla como PWA en iPhone, publicar con HTTPS y usar los borradores sin conexión,
consulta [PWA de Reloncaví](docs/PWA.md).
La configuración de build, arranque, persistencia y rendimiento para el hosting está en
[Despliegue en Render](docs/RENDER.md) y [Render gratuito con Supabase y Drive](docs/RENDER_FREE.md).

En Windows, abre **ejecutar_debug.bat**. Si falta el entorno de Python,
el lanzador prepara uno local e instala las dependencias. Luego aplica las
migraciones de SQLite y abre la app en **http://127.0.0.1:8000/** cuando esté lista.
El arranque local no depende de una sesión en Supabase. Para comprobar solo la
instalación, ejecuta `ejecutar_debug.bat --check-only`.
El lanzador también repara paquetes binarios incompatibles con la versión de Python instalada.
Las cuentas locales, incluido `admin`, pueden iniciar sesión aunque Supabase no esté disponible.

Para iniciar manualmente desde un entorno ya creado:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Abre http://127.0.0.1:8000/ e inicia sesión con el RUT y la contraseña de una cuenta activa. Para preparar el administrador principal de una instalación nueva, configura `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_PASSWORD` y `DJANGO_SUPERUSER_RUT` y ejecuta `python init_admin.py` después de las migraciones. El archivo `index.html` es una vista previa de la interfaz.

## Importar y usar planos

**Importar PDF configurable** admite PDF sin contraseña de hasta 20 MB y 1.000 páginas. Permite añadir botones, importar fichas o usar motores guardados. Los planos DXF archivados se conservan en la biblioteca.

**Pantalla completa** amplía el visor al dispositivo. El menú **⋮** contiene Explorar, Agregar botón, Mover botón, Importar motor guardado, Asignar listado, Ver listado, Guardar plano, Exportar proyecto y zoom. Los PDF tienen también un selector de página. Las fichas y los diálogos de asignación funcionan sobre el visor completo; **Volver** recupera la vista normal. Los navegadores sin la API nativa usan la misma vista que ocupa toda la ventana.

Mover botones requiere ser el creador del plano o administrador. Las coordenadas se conservan al cambiar el zoom, la orientación o el tamaño del visor.

El visor PDF mantiene una vista general de **1280 píxeles en el lado mayor**, con las proporciones del plano. Al ampliar o desplazar, dibuja nuevamente solo la zona visible, también con un máximo de 1280 píxeles en su lado mayor. Durante el gesto reutiliza la imagen existente y recupera el detalle al detenerse, tras una pausa de 160 ms. No aumenta el tamaño del lienzo con el zoom ni con la densidad de píxeles del dispositivo. El zoom llega hasta 32 veces el ajuste inicial y los botones conservan sus coordenadas.

El recorte utiliza los límites de operaciones de PDF.js para omitir trazos fuera de la zona visible; al cambiar de página se liberan los lienzos anteriores. El PDF original se conserva sin reducir su resolución. La nitidez de un plano escaneado depende de su imagen original; el límite de los lienzos no limita la memoria que PDF.js necesita para decodificar imágenes del archivo. La implementación sigue el [renderizado de detalle de PDF.js](https://github.com/mozilla/pdf.js/blob/master/web/pdf_page_detail_view.js).

El visor utiliza **PDF.js 6.4.299 legacy**, con compatibilidad adicional para `Promise.withResolvers` en la página y en su worker. Ambos componentes tienen versiones de caché coordinadas, lo que evita mezclar archivos antiguos y nuevos. Esto corrige fallos reproducidos con funciones JavaScript ausentes en perfiles de Safari. La versión concreta de iOS sigue determinando las funciones disponibles. El origen de la dependencia y sus verificaciones están en [SOURCE.md](mobileApp/static/mobileApp/vendor/pdfjs/SOURCE.md).

Si un PDF guardado conserva su registro pero falta el archivo físico en `media/`, la aplicación lee el original incluido en su proyecto guardado. También utiliza esa copia al exportar o volver a guardar el proyecto. Se comprueban formato, referencia y tamaño del archivo. Al eliminar el plano se elimina también esa copia guardada. Si el original tampoco está en el respaldo, aparece un error que solicita volver a importarlo. Importar en `localhost` guarda archivos en esa instalación; transferir únicamente la base de datos no transfiere `media/`.

Los mensajes del visor distinguen un PDF inaccesible, una sesión sin acceso, un archivo inválido, una incompatibilidad del navegador y un fallo de dibujo. La consola conserva el error técnico para diagnosticarlo; un archivo físico ausente no se confunde con un plano eliminado.

## Guardar y abrir

Los Excel y los proyectos guardados se almacenan como archivos privados; la base
de datos conserva sus referencias. En modo nube se utiliza Drive y en modo local
se utiliza `media/`. Las instalaciones existentes necesitan aplicar las
migraciones `0033`–`0035` con las escrituras detenidas. Consulte el
[análisis de rendimiento y procedimiento de migración](docs/rendimiento-archivos.md)
para respaldos, compatibilidad y límites de las optimizaciones.

**Añadir botón** reúne la categoría, el nombre opcional, las descripciones, el color y el origen de la ficha Excel en un único modal sobre el plano. Permite usar un formato vacío, importar Excel/CSV, vincular una hoja del plano o elegir una pieza guardada. Después de confirmar, se toca la ubicación del botón.

**Opacidad** es el único control de visualización junto al plano: la barra y su porcentaje permiten atenuar los botones y sectores, hasta ocultarlos por completo al 0 %. Está disponible también para lectores. Su valor solo afecta al visor abierto; se reinicia al 100 % al volver a abrirlo y no se guarda en el plano, los proyectos exportados ni la nube. Las preferencias de visualización de versiones anteriores se descartan mediante la migración `0026`.

**Crear sector** permite pintar una zona con varios trazos de pincel o elegir un círculo, cuadrado, rectángulo o triángulo. El sector tiene nombre y color, con paleta y selector personalizado. Las figuras existentes se seleccionan directamente con un clic o una pulsación sobre su imagen y se pueden arrastrar desde ese primer gesto. Sus bordes y esquinas permiten estirar cada dimensión, el control superior permite girarlas y el selector **Forma** permite cambiarlas. **Mantener proporciones**, o Mayús al arrastrar, conserva su relación de aspecto; también hay controles numéricos. Las zonas de pincel no se seleccionan directamente desde el dibujo: se editan desde **Sectores**, donde se pueden añadir y deshacer trazos o transformar la zona. **Guardar sector** confirma la edición; **Cancelar** la descarta.

**Sectores** abre los sectores del plano y, al elegir uno, muestra sus botones. **Copiar sector** crea otra figura con el mismo nombre, color, forma, tamaño, rotación y trazos; se puede arrastrar a otra posición y confirmar con **Guardar sector**, o descartar con **Cancelar**. El original conserva sus datos y los sectores del mismo nombre y color comparten una única entrada en la leyenda. La copia se incorpora al trabajo abierto y se conserva al pulsar **Guardar plano**, igual que los demás cambios. En la vista simple, **Sectores**, **Editar sectores** y **Crear sector** se encuentran ordenados en una fila encima del visor PDF/DXF.

La pertenencia se calcula por la superficie del botón que queda dentro de la figura o la zona pintada: debe ser estrictamente mayor al 50 %. El contacto exterior o exactamente la mitad no cuentan. Se vuelve a calcular al consultar el sector, incluidos los cambios de posición y tamaño. Las zonas superpuestas pueden compartir botones y los sectores PDF pertenecen a su página.

Los sectores son opcionales y se guardan dentro del propio plano, con sus mismos permisos de edición. Se incluyen al guardar, exportar e importar proyectos y al transportar su configuración en el Excel de nube. Al eliminar un plano desaparecen sus sectores; eliminar solo un sector conserva los botones. No existe una biblioteca independiente de sectores. La migración `0025` añade estos datos a los planos existentes sin exigir sectores.

**Guardar plano**, situado junto al visor PDF/DXF y en la barra de acciones de pantalla completa, guarda un proyecto con el archivo original, todos sus botones, las fichas asociadas y el listado del plano. Está disponible para trabajadores y administradores. El indicador muestra Sin guardar, Cambios pendientes o Guardado para el trabajo de la sesión actual.

**Ver planos guardados** en el menú principal, el menú de usuario y el módulo PDF abre una biblioteca común. Cada registro muestra **PDF** o **DXF** en pequeño. Volver a guardar el mismo plano actualiza su registro; no crea otra copia. Los planos existentes antes de esta actualización se conservan mediante la migración `0006`.

Las modificaciones y asignaciones del trabajo abierto pertenecen únicamente a esa sesión de edición. Agregar o mover botones, cambiar sectores y editar el Excel no guarda el plano ni actualiza una copia local. Solo **Guardar plano** publica el conjunto y conserva una copia confirmada en el dispositivo. Si se recarga o se vuelve a abrir desde la biblioteca sin guardar, se carga la última versión confirmada, sin recuperar los cambios pendientes. Cada apertura tiene su propia sesión, incluso para la misma cuenta en varias pestañas. Un plano nuevo no aparece en la biblioteca hasta guardarlo. **Exportar proyecto** descarga el trabajo abierto sin guardarlo automáticamente. Si otro usuario ya guardó una versión posterior, se bloquea la sobrescritura; es necesario volver a abrir la versión más reciente. La migración `0036` añade los permisos y la `0037` permite sesiones independientes, sin cambiar los planos confirmados.

Los controles de **Guardar plano** utilizan los permisos de ese plano, no el rol general de trabajador. Al otorgar edición se habilita una sesión editable y aparece el botón; al revocarla se oculta y el servidor rechaza el guardado. Las respuestas de sesiones temporales no entran en la caché persistente del navegador. Al actualizar se retiran las copias privadas que versiones anteriores habían tratado como descargas. Las copias confirmadas se conservan y se consultan sin conexión en modo de lectura.

Dentro del menú de pantalla completa, **Plano absoluto** utiliza toda la superficie disponible para el plano y sus botones, sin listado lateral, herramientas ni indicadores. En PC, la rueda del mouse acerca y aleja sin mantener Ctrl o ⌘; en celular, se separan o juntan dos dedos para hacer zoom. Arrastrar con el mouse o un dedo permite recorrer el plano ampliado. Estos gestos funcionan tanto en PDF como en DXF y no modifican ni guardan el plano. Un toque o clic directo en un botón abre su Excel. Solo queda un control pequeño **Volver**, que regresa a la pantalla completa anterior: se oculta tras cinco segundos sin actividad y reaparece al tocar la pantalla o mover el mouse. Funciona también cuando el dispositivo no ofrece la API nativa de pantalla completa.

**Permisos de edición** aparece únicamente al creador del plano, tanto junto al visor como en el menú de pantalla completa. Permite seleccionar o retirar trabajadores y administradores activos para ese plano. Los lectores no pueden recibir edición. Un usuario autorizado trabaja sobre su propio borrador; el acceso se comprueba nuevamente en el servidor en cada operación. Delegar edición no concede eliminación del plano ni gestión de permisos. Los administradores conservan su acceso administrativo existente.

**Eliminar plano** aparece junto a Guardar plano, en pantalla completa y como papelera en la biblioteca, exclusivamente para el creador del plano y los administradores. Tras confirmar, elimina el plano original, sus botones y su copia guardada; también cierra cualquier copia abierta de ese plano y descarta su restauración automática. No vuelve a la biblioteca hasta importarlo y guardarlo de nuevo. Los motores guardados y las fichas independientes se conservan.

Las sesiones conectadas a la misma instalación comprueban los planos abiertos y la biblioteca visible cada cinco segundos, al volver a la aplicación y al recuperar la conexión. Un plano eliminado se retira también de esos visores y se cierra su ficha abierta. Los fallos de conexión conservan el último estado confirmado; una respuesta antigua no puede reemplazar una consulta más reciente. Los planos recién importados siguen disponibles aunque aún no estén guardados.

Si un plano vuelve a aparecer **después de recargar**, hay que comprobar que ambos dispositivos usan la misma instalación y que su almacenamiento es persistente. Este proyecto utiliza SQLite en `PLANOS_DATA_DIR` (por defecto, la carpeta del proyecto) y actualmente `db.sqlite3` está incluido en Git. Un despliegue que recupera esa copia antigua puede recuperar también sus planos. Render utiliza por defecto un [sistema de archivos temporal](https://render.com/docs/disks): el código de borrado no puede conservar cambios de una base que la plataforma descarta. Esta corrección no cambia la configuración de Render ni modifica o elimina la base de datos local.

**Eliminar botón** está junto a la selección del plano y en la barra de selección de pantalla completa. En una ficha asociada a un botón también aparece una papelera en la cabecera del editor; las fichas de la biblioteca y los listados no muestran esa acción. La eliminación pide confirmación y conserva el dibujo y el resto de botones. La barra inferior del visor completo ofrece añadir, mover, guardar y eliminar plano según los permisos, sin abrir el menú.

## Excel del plano y hojas de motor

Cada plano tiene **un único libro Excel**. Los botones guardan el nombre de la hoja que utilizan. Dos botones vinculados a la misma hoja ven los mismos datos y cambios; fichas de motores diferentes pueden seguir en hojas distintas del mismo libro.

El libro del plano se prepara automáticamente. **Añadir botón** permite crear una ficha vacía, importar una hoja de Excel/CSV, vincular una hoja del libro o incorporar una pieza guardada. **Añadir listado** permite usar una plantilla vacía o importar un listado. **Ver hojas de Excel** abre el libro completo. El botón **Excel del plano** se retiró de la vista normal y del menú de pantalla completa.

Al importar otro libro se conservan en el libro resultante las fichas anteriores que contienen datos, incluidas las que aún no tienen botón, y los listados anteriores con información. Las fichas vacías iniciales se pueden reemplazar sin generar archivos separados. Si hay una variante anterior editada de un motor ya identificado, se conserva como otra hoja con el sufijo «anterior». Un botón de un libro importado permanece sin hoja hasta que el usuario elige una.

**Motor guardado** incorpora su ficha como una hoja del libro del plano. Si ese motor ya está incorporado, vuelve a vincular la misma hoja. También se reconocen fichas con los mismos datos, sin depender del nombre de su archivo. Las fichas vacías de motores todavía no identificados mantienen hojas independientes. La identidad de los motores registrados se conserva por hoja al exportar el XLSX completo y al transportar un proyecto.

**Guardar cambios** y Ctrl+S dentro del editor guardan únicamente los datos del Excel. Al cerrar una ficha también se guardan sus cambios pendientes. **Guardar plano**, fuera del editor, registra la copia completa en la biblioteca. Los conflictos de revisión mantienen abiertos los cambios pendientes para evitar sobrescrituras.

**Exportar .xlsx** descarga el libro completo del plano. **Exportar .csv** descarga únicamente la hoja seleccionada en UTF-8 con BOM. Las fichas que usan el formato estándar conservan Vista ficha, ajuste de ancho/página, zoom, logos y gráficos. Las fórmulas muestran el último resultado calculado por Excel; la aplicación no incluye un motor completo de recálculo.

## Listados del plano

**Gestionar listados** permite agregar un listado vacío, importar otro listado o convertir una hoja libre del mismo libro en un listado adicional. Se nombran **Listado motores 2**, **Listado motores 3**, etc. Una hoja asignada a un botón no puede convertirse en listado ni eliminarse como tal. También puede cambiar la hoja del listado original por otra hoja libre.

Cada listado, incluido el original, tiene una opción **Cambiar nombre**. El nombre elegido se muestra en el administrador, el selector del editor y la hoja de Excel; se conserva al pulsar **Guardar plano** y al exportar o importar proyectos. Se admiten nombres de hasta 31 caracteres, según las reglas de Excel, sin repetir el nombre de otra hoja del mismo libro. Cambiar el nombre conserva los datos, formatos y referencias de las fórmulas. La opción requiere permiso de edición del plano.

**Ver listados** abre los listados en el editor; su selector de hojas contiene todos los listados del plano. El botón **Listados** del editor permite administrarlos. **+ Fila** y **+ Columna** conservan la estructura y los estilos de las plantillas. Se eliminó la opción Reutilizar un Excel asignado: los listados pertenecen al propio libro del plano.

Los listados del plano pueden ser modificados y eliminados por trabajadores y administradores. La protección de un original se conserva si se importa otro libro y el listado anterior queda como hoja adicional.

## Motores guardados

**Guardar motor**, desde una ficha rellenada del plano, conserva únicamente esa hoja como original reutilizable en **Motores guardados**. Un formulario vacío no se registra. El original de la biblioteca se consulta sin modificarlo; la hoja incorporada a un plano sí puede editarse con los permisos correspondientes. Sus cambios se comparten entre los botones de ese plano que apuntan a la misma hoja.

La biblioteca permite buscar por nombre, consultar una ficha e incorporarla al botón seleccionado. Un motor ya registrado muestra su origen y desactiva Guardar motor, incluso después de editar su hoja. Los originales de esta biblioteca se conservan por separado para poder utilizarlos en otros planos; los botones no crean archivos adicionales por cada asignación.

## Transportar un proyecto

**Exportar proyecto** permite a trabajadores y administradores descargar un archivo **.planproj** con el trabajo abierto, sin actualizar su copia guardada. Para actualizar la biblioteca y la nube debe pulsarse **Guardar plano**. Es un ZIP versionado que reúne:

- El PDF o DXF original.
- Las páginas y posiciones de los botones.
- Un XLSX que reúne las fichas y todos los listados, conservando su formato.
- Los vínculos de cada botón con su hoja y los orígenes de los motores guardados.

El formato actual es la **versión 3**: incluye el plano, un único XLSX, las hojas asignadas a cada botón, todos los listados y su protección. También se aceptan proyectos de las versiones 1 y 2 y se reúnen sus fichas en el libro del plano al abrirlos. Los documentos antiguos se conservan durante esa adaptación.

En otra instalación, usa **Importar PDF configurable → Importar proyecto** para abrir un archivo `.planproj` con un plano PDF. **Guardar plano** añade la copia importada a la biblioteca. La importación valida el manifiesto, los nombres de hojas y los archivos y no extrae rutas arbitrarias del ZIP. Límites: proyecto de 100 MB, contenido de 250 MB y hasta 5.000 botones. La importación normal de XLSX admite 20 MB; el libro reunido puede alcanzar 100 MB.

## Usuarios, roles y categorías

El **administrador principal** de cada empresa crea las cuentas en **Administración → Usuarios → Crear usuario**. Allí elige nombre, apellido, RUT, rol y contraseña inicial. La cuenta queda habilitada en cuanto se guarda. El servidor valida el dígito verificador, reserva un solo acceso por RUT y aplica las reglas de contraseña de Django.

El acceso usa **RUT y contraseña**. Desde el menú de usuario, **Cambiar contraseña** pide la contraseña actual y confirma la nueva. La sesión actual sigue abierta y las demás sesiones deben volver a iniciar sesión.

- **Espectador** consulta planos guardados e importa PDF para verlos.
- **Trabajador** crea y modifica sus planos, guarda proyectos y gestiona fichas y categorías.
- **Administrador** gestiona usuarios y categorías y puede editar o eliminar planos. La creación de cuentas y la administración de otros administradores se reservan al administrador principal.

Las cuentas existentes conservan sus contraseñas y autoría. Las cuentas sin RUT asignado requieren que un administrador les asigne uno. En instalaciones con `APP_SHARED_ACCOUNTS=1`, las cuentas compartidas usan el [registro privado de Supabase](docs/SUPABASE.md).

**Historial de mis planos**, en el menú principal y el menú de usuario, reúne los planos que esa persona creó y guardó.

En PDF y DXF, la opacidad general se multiplica por la opacidad base del radio (25 %). Al seleccionar el botón o pasar el puntero sobre él, el botón recupera el 100 % y el radio su 25 %.

## Actualizaciones pendientes de planos

Antes de abrir un plano, la aplicación compara la versión conocida por ese dispositivo con el último guardado. En las copias descargadas de Drive también consulta la versión publicada del Excel y la configuración del plano.

Si está desactualizado, aparece una advertencia roja con **Actualizar** y **Cancelar**. Actualizar obtiene el último plano, con sus botones y Excel. Una descarga de nube se valida antes de retirar el registro, el archivo y el libro antiguos; conserva las asignaciones de roles. Si falla la descarga o la validación, se mantiene la copia anterior. Cancelar vuelve a la biblioteca. Una actualización conocida pendiente continúa bloqueando la apertura si no se puede verificar la nube.

La detección entre navegadores de la misma instalación funciona tanto con PDF como con DXF. Las instalaciones separadas utilizan el flujo de nube PDF/Excel existente y requieren la conexión de Drive para comprobar y descargar las versiones publicadas. El autor se conserva en el manifiesto del proyecto publicado: descargar un plano ajeno no convierte al lector en su creador. Las publicaciones antiguas sin autor identificable se abren para consulta y su gestión queda a cargo de un administrador.

## Actualizar la instalación y verificar

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test mobileApp --noinput
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/account_access_browser_smoke.py
.\.venv\Scripts\python.exe scripts/annotation_opacity_browser_smoke.py
node --test scripts/sector-geometry.test.js
.\.venv\Scripts\python.exe scripts/pdf_resolution_browser_smoke.py
.\.venv\Scripts\python.exe -m playwright install webkit
.\.venv\Scripts\python.exe scripts/pdf_compatibility_browser_smoke.py
```

Las migraciones 0008–0011 incorporan las referencias a hojas y la protección de listados. Los planos de instalaciones anteriores reúnen sus documentos al abrirse; los originales anteriores permanecen disponibles en la base de datos como respaldo de la conversión.

Las pruebas usan bases de datos y archivos temporales. Cubren PDF, planos DXF guardados en versiones anteriores, libros con formato, motores compartidos, originales de la biblioteca, varios listados, permisos, importación y exportación de proyectos PDF y el flujo móvil. Las capturas quedan en `.test-artifacts/`.

`pdf_resolution_browser_smoke.py` comprueba el límite de resolución, la calidad del recorte frente al renderizado completo, el agrupamiento de cambios de zoom, el arrastre, la alineación de botones, páginas giradas, escaneos, cambios de orientación y la liberación de lienzos.


`pdf_compatibility_browser_smoke.py` comprueba Chromium y WebKit con sesiones independientes, funciones JavaScript ausentes en la página y el worker y un PDF cuyo archivo en `media/` se ha eliminado. Abre la copia guardada, páginas giradas y escaneadas y verifica el zoom a 1280 píxeles. También comprueba el mensaje de archivo inaccesible y la reapertura posterior.

La migración `0032` retira las antiguas plantillas de categoría del almacenamiento local. Las fichas ya copiadas a los botones se conservan. `account_access_browser_smoke.py` comprueba la administración de cuentas y la edición de un PDF desde el navegador móvil.
