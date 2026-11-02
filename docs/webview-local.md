# Preparación para Android local

El plan externo se tomó como referencia, no como instrucciones ejecutables.
Se retiraron el marco de teléfono, el reloj, la cámara, la batería y el
indicador de gestos ficticios. La aplicación ocupa la ventana real en todos
los tamaños. Se mantienen usuarios, permisos, planos DXF/PDF, motores,
fichas, listados y proyectos.

No se implementaron las fases de alojamiento, túneles, nube, Android Studio,
permisos Android ni compilación APK. No se cambió la configuración de red
del proyecto.

## Arquitectura pendiente del APK

La WebView muestra HTML/JavaScript; no ejecuta Python ni interpreta plantillas
Django. Copiar `index.html` a los assets de Android no conserva la aplicación:
importar DXF, editar XLSX, autenticar usuarios y guardar proyectos requieren
las funciones Python existentes.

Para conservarlas sin nube, la integración futura debe incluir un runtime
Python compatible con Android y las dependencias de `requirements.txt`,
ejecutar Django dentro del teléfono y comunicarlo con la WebView por un origen
local estable. El mecanismo para despachar las peticiones locales queda
pendiente de esa integración; aquí no se instala ni configura un servidor.
El teléfono debe funcionar sin el PC, sin LAN y sin conexión a Internet.

Hay que validar en Android la disponibilidad de las dependencias con código
nativo, especialmente NumPy/ezdxf y Pillow. El entorno Windows `.venv` no se
copia al APK. Si no se incorpora Python, se necesitará reescribir esas funciones
en otra tecnología; el HTML por sí solo no las reemplaza.

Android recomienda un origen HTTP(S) para contenido local en lugar de `file://`
para evitar las limitaciones de origen de `fetch()` y módulos. La documentación
de [contenido local en WebView](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content)
explica esa distinción. `WebViewAssetLoader` sirve archivos; por sí solo no
ejecuta las vistas Django ni las rutas `/api/`.

## Datos persistentes

`PLANOS_DATA_DIR` permite separar los datos del código empaquetado:

- Debe ser una ruta absoluta, privada, persistente y escribible, proporcionada
  y creada por el futuro contenedor Android antes de cargar Django.
- SQLite se guarda en `<PLANOS_DATA_DIR>/db.sqlite3`.
- Archivos importados, fichas y proyectos se guardan en `<PLANOS_DATA_DIR>/media/`.
- Si la variable no está definida, se conservan `db.sqlite3` y `media/` actuales.
  Esta adaptación no mueve ni borra datos existentes.

En una instalación nueva el contenedor deberá ejecutar las migraciones antes
de abrir la interfaz y establecer el procedimiento de creación de cuentas.
No se incluyen usuarios ni contraseñas de prueba y no se elimina la
autenticación. Para trasladar una instalación existente se deben copiar su
base de datos y su carpeta de archivos conjuntamente, con la app cerrada.
La base y los archivos de esta instalación no deben distribuirse como datos
iniciales del APK. Las plantillas vacías de `mobileApp/data/` sí son recursos
necesarios y deben incluirse junto con templates, static y migraciones.

El almacenamiento principal sigue siendo SQLite y los archivos locales;
`localStorage` solo recuerda el plano abierto. Habilitar DOM storage no
reemplaza las cookies de sesión de Django. No hay caché de API ni service worker
que pueda devolver datos de otro usuario o una revisión antigua.

## Contrato web disponible para el futuro contenedor

Los recursos visuales, iconos, Bootstrap y PDF.js están incluidos en
`mobileApp/static/mobileApp/`. No se agregaron CDN, fuentes remotas ni servicios
externos. Mantener página, API, workers, WASM, fuentes y descargas bajo el mismo
origen local evita introducir CORS o excepciones de CSRF.

La futura WebView deberá tener JavaScript, DOM storage, cookies y un motor
actualizado compatible con los módulos y PDF.js incluidos. La compatibilidad
no depende únicamente de la versión de Android; debe comprobarse con la
versión concreta de Android System WebView del dispositivo.

### Pantalla y teclado

`mobile-layout.css` contempla pantalla vertical y horizontal, botones táctiles,
áreas seguras y formularios desplazables. El editor conserva su borrador al
cambiar de tamaño. En pantallas pequeñas sus herramientas se desplazan
horizontalmente para dejar espacio a la ficha. El zoom de accesibilidad del
navegador no está bloqueado; PDF, DXF y fichas mantienen su propio zoom.

`mobile-runtime.js` usa `visualViewport` para seguir el espacio disponible
cuando aparece el teclado. El contenedor debe entregar correctamente los
cambios de tamaño o los insets del sistema. Si CSS `env(safe-area-inset-*)`
no refleja los insets de la WebView, puede comunicar los valores **en píxeles
CSS**, sin duplicar un margen que ya aplica Android:

```js
window.mobileApp.setInsets({ top: 24, right: 0, bottom: 20, left: 0 });
window.mobileApp.setInsets(); // Restablecer los valores CSS env().
```

### Botón Atrás

```js
await window.mobileApp.requestBack();
```

Devuelve `true` si la app atendió la acción: cierra un diálogo o menú, guarda
y cierra la ficha, sale del visor completo o vuelve al menú principal. Si el
guardado falla, conserva la ficha abierta y sus cambios y también devuelve
`true` para evitar que Android cierre la app. Devuelve `false` en el inicio
o login, donde el contenedor puede aplicar su navegación normal. El contenedor
debe esperar la resolución de la promesa y recibir su resultado mediante su
puente; `evaluateJavascript()` no espera promesas automáticamente.

### Importación y exportación

Se mantienen los inputs de archivo originales y las validaciones de contenido
y tamaño. En Android habrá que implementar el selector de documentos de la
WebView, incluida la cancelación, para DXF, PDF, XLSX y `.planproj`.

Las exportaciones usan URLs del mismo origen, con autenticación, y no requieren
convertir archivos grandes a base64. Opcionalmente, el contenedor puede exponer:

```text
PlanosAndroid.download(urlAbsoluta, nombreSugerido)
```

La interfaz invoca ese método para los enlaces `download` si existe; en un
navegador mantiene la descarga normal. El nombre puede estar vacío: el nombre
definitivo y el tipo MIME están en la respuesta HTTP (`Content-Disposition`
y `Content-Type`). CSV propone extensión `.csv`.

El contenedor deberá conservar las cookies de sesión al leer el archivo y
permitir guardar o compartir mediante el selector del sistema. Debe validar
el origen local en el lado nativo y exponer el puente solo a contenido propio.
Este proyecto no implementa la descarga nativa: hasta entonces se valida la
descarga del navegador y el contrato del puente con una implementación simulada.
Los diálogos JavaScript de confirmar/eliminar y renombrar también deben
funcionar en la futura WebView.

## Verificación en VS Code

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test mobileApp --noinput
.\.venv\Scripts\python.exe scripts/mobile_browser_smoke.py
.\.venv\Scripts\python.exe scripts/project_browser_smoke.py
```

Las pruebas de navegador usan Django y archivos temporales aislados únicamente
para comprobar el proyecto en el PC. La prueba móvil bloquea las solicitudes
externas y comprueba tamaños, insets, rotación, reducción del área visible,
edición persistente, fallo de guardado, descargas y el botón Atrás. Reducir el
viewport simula el espacio del teclado; no sustituye una prueba con IME real.
Las pruebas Android físicas, el empaquetado Python, el selector de documentos
y las descargas nativas siguen pendientes de la etapa Android Studio.

`index.html` es solo una vista previa generada de la plantilla real. Para
actualizarla, sin tocar la base de datos:

```powershell
.\.venv\Scripts\python.exe scripts/export_preview.py
```
