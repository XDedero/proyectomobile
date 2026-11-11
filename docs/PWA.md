# Reloncaví como PWA en iPhone

La app incorpora manifiesto, iconos PNG opacos, modo `standalone`, guía de instalación,
service worker en la raíz, caché de la interfaz, aviso de conexión, actualización manual
y almacenamiento IndexedDB separado por cuenta. No se necesita publicar en App Store.

## Publicación

1. Configurar el servidor con las variables de [.env.production.example](../.env.production.example).
   El archivo es una referencia: establecer las variables en el panel de hosting o proceso.
   Generar una clave secreta nueva y privada. `DJANGO_DEBUG=false` exige esa clave y prohíbe
   el comodín `*` en los dominios. Configurar `DJANGO_TRUST_PROXY` solamente si el proxy
   elimina cabeceras de origen no confiable y fija `X-Forwarded-Proto`.
2. Usar un dominio HTTPS con certificado válido y un volumen persistente para
   `PLANOS_DATA_DIR`. Antes de cambiar esa ruta, respaldar y copiar allí la base existente
   y su carpeta `media`; una ruta vacía corresponde a una instalación nueva.
3. Ejecutar `python manage.py migrate`, `python manage.py collectstatic --noinput`
   y `python manage.py check --deploy` en el entorno de producción. Iniciar el servidor
   WSGI detrás del proxy HTTPS. Mantener respaldos de la base y archivos.
4. Abrir la URL en Safari de un iPhone, tocar el control «Instalar app» y seguir la guía:
   Compartir → Agregar a pantalla de inicio → Abrir como app, si aparece → Agregar.
5. En el iPhone instalado comprobar inicio de sesión, teclado, notch, cambio de orientación,
   planos grandes, importación desde Archivos, descargas y Compartir. Probar también una
   actualización con otra pestaña abierta y recuperación tras cerrar completamente la app.

En desarrollo `http://localhost` permite service workers. Una IP de la red local por HTTP
no permite validar la instalación offline desde otro dispositivo: usar HTTPS también para
esa prueba. Las pruebas automatizadas usan Chromium con pantalla y controles táctiles
móviles; no sustituyen una validación en Safari/iPhone físico.

## Uso sin conexión y límites

- Iniciar sesión con conexión y abrir el plano guardado. La app conserva automáticamente
  el PDF o la vista SVG del DXF, sus botones y sectores en IndexedDB. Si el rol permite
  exportar y el XLSX está disponible para la misma versión, también lo conserva. Para usar
  el editor de Excel sin conexión, abrir previamente las hojas y zonas que se van a consultar.
- En «Planos guardados», filtrar por «Guardados en este dispositivo» para encontrar las copias.
  Al reabrir la misma versión, el visor reutiliza el archivo local. «Quitar copia de este
  dispositivo» elimina la copia del navegador y conserva el plano compartido
  y los borradores. La limpieza de una copia de la instalación local y el borrado completo
  siguen siendo acciones distintas.
- La consulta offline usa la última copia descargada. El visor avisa que no hay conexión.
  Los recursos auxiliares de PDFs (por ejemplo fuentes especiales) se conservan al usarlos;
  comprobar el plano que se va a llevar a terreno antes de desconectarse.
- Guardar sectores y cambios de celdas sin señal conserva un borrador local en IndexedDB.
  Se pueden reabrir tras recargar. Las operaciones de estructura de Excel, importaciones,
  mover/crear botones y guardado en la nube requieren conexión y no se simulan como éxitos.
- Al reconectar, reabrir o volver a la app, se intenta enviar los borradores con la app
  abierta. También existe «Sincronizar pendientes». iOS no garantiza ejecución en segundo
  plano: no se depende de Background Sync ni de procesos cuando la app está cerrada.
- Sectores conservan su valor anterior y Excel conserva su revisión original. El servidor
  comprueba ambos y los permisos actuales. Un conflicto, eliminación o permiso revocado
  conserva el borrador y lo muestra para revisión; nunca fuerza una sobrescritura.
- «Exportar borradores» produce JSON con todos los valores pendientes; puede guardarse en
  Archivos mediante Compartir. Es una copia para recuperación manual, no un importador
  automático. Después de sincronizar o descartar, reabrir el plano para renovar sus datos.
- El cierre de sesión exige resolver, o exportar y descartar, los pendientes. Cerrar sesión
  elimina la identidad offline y las copias consultables del dispositivo. Las copias no se
  comparten entre cuentas; cada respuesta se asocia con la cuenta comprobada por el servidor.
- iOS puede eliminar almacenamiento local. La app solicita persistencia al guardar la copia.
  La caché automática tiene un presupuesto de 80 MiB y expulsa lecturas antiguas. Las
  copias de planos tienen un presupuesto aparte de 240 MiB y un límite de 40 MiB por
  archivo. La app conserva esas copias y los borradores cuando vence la caché de lecturas.
  Si falta espacio, avisa y conserva la copia anterior. El navegador separa los datos por
  dirección: al pasar de la app local a la URL de Render se descargan de nuevo los planos.
- Las actualizaciones se aceptan manualmente y se bloquean mientras hay sectores en edición,
  celdas sin guardar o borradores pendientes. La interfaz pública se almacena aparte; el
  service worker no guarda APIs privadas, archivos de usuario ni administración.

## Icono

El logo original permanece en `mobileApp/static/mobileApp/img/reloncavi-logo.png`.
La figura sin letras se preparó con la herramienta integrada `image_gen` a partir de ese
archivo, conservando el cubo negro y su cara roja sobre fondo blanco. Los resultados están
en `mobileApp/static/mobileApp/img/pwa/`: `icon-source.png`, `apple-touch-icon.png` (180 px),
`icon-192.png` e `icon-512.png`. Los tamaños se exportaron con remuestreo Lanczos.

Prompt utilizado: «Remove ALL lettering, including Reloncaví and trademark, retain only
the exact geometric cube/building figure at the top (dark near-black three-dimensional
outlined hexagonal cube with a small red solid face on lower left). Preserve its exact
geometry, proportions, line widths relative to the figure, black and red colors. Enlarge
and center this isolated figure, occupying approximately 65 percent of the square canvas,
with equal whitespace. Solid pure white opaque background, no rounded corners (iOS applies
those), no text, no shadows, no gradients, no added shapes.»

## Validación

Ejecutar con el Python del entorno del proyecto:

```text
python manage.py test mobileApp.test_pwa --noinput
python scripts/pwa_browser_smoke.py
python scripts/pwa_browser_smoke.py --webkit
```

La prueba de navegador usa base, usuarios y documentos temporales. Comprueba shell offline,
PDF, leyenda condicional, borradores de sectores/Excel tras recargar, sincronización real,
conflictos, exportación recuperable y limpieza al cerrar sesión.

En esta implementación pasaron los 12 tests de `mobileApp.test_pwa` y
`mobileApp.test_sectors`, además del flujo PWA en Chromium y WebKit. El script general
preexistente `scripts/browser_smoke.py` se detiene en el selector antiguo
`#btnAssignExcelCorner`, ausente en la interfaz actual; no se considera una validación
completada de ese flujo general.

Las notificaciones push son opcionales y no se activan: requieren definir qué eventos
notificar y una infraestructura de envío. La instalación PWA y el trabajo offline no
dependen de ellas.

Referencias de plataforma: [instalación en Safari 26](https://webkit.org/blog/17333/webkit-features-in-safari-26-0/),
[persistencia y cuotas de WebKit](https://webkit.org/blog/14403/updates-to-storage-policy/)
y [configuración HTTPS de Django](https://docs.djangoproject.com/en/5.2/ref/settings/#secure-proxy-ssl-header).
El chequeo de despliegue puede avisar de HSTS en subdominios y de preload: ambas opciones
quedan desactivadas por defecto porque dependen del resto de los dominios del operador.
