# Despliegue y rendimiento en Render

Las optimizaciones conservan las APIs, los datos y los permisos. No cambian el formato
de los planos, las fichas de Excel, las exportaciones ni los procesos de nube.

## Cambios

- La firma de archivos estáticos se calcula una vez por proceso cuando `DEBUG=false`.
  En desarrollo sigue detectando cada edición. El HTML y el service worker mantienen
  la misma versión y sus respuestas no se almacenan como páginas privadas.
- Los listados, detalles de metadatos y controles de permisos de DXF omiten el campo
  SVG en las consultas. El dibujo se sigue entregando íntegro desde `drawing.svg`.
  Los controles de permisos de un Excel omiten su binario hasta la vista que lo necesita.
- Los listados de proyectos incluyen el propietario en la consulta inicial, evitando
  cargarlo con consultas adicionales por cada registro.
- Se comprimen HTML, JSON, SVG y otros textos mediante gzip cuando el navegador lo
  admite. La descompresión es transparente. Se conservan las cabeceras de privacidad;
  las respuestas de autenticación y las descargas PDF/XLSX/ZIP no se recomprimen.
- WhiteNoise conserva su backend actual y la compresión de estáticos de `collectstatic`.
  No se cambia a nombres con hash para evitar romper imports relativos de PDF.js.
- Gunicorn tiene valores conservadores: un proceso por defecto, dos hilos y 120 s de
  timeout. `WEB_CONCURRENCY`, `GUNICORN_THREADS` y `GUNICORN_TIMEOUT` permiten ajustarlos.
  No se reciclan procesos por número de solicitudes, para no interrumpir las tareas
  actuales de publicación en nube.

En una medición local con cuatro DXF de 2 MiB, el listado pasó de 136,78 ms a 51,94 ms.
Las consultas que incluían el SVG pasaron de 13 a 0 y el JSON de 8283 bytes conservó
exactamente el mismo SHA-256. No es una garantía de latencia del hosting: depende de
los documentos, CPU, disco, carga y red. La firma de archivos pasó de unos 40 ms por
consulta a menos de 0,001 ms en solicitudes posteriores del mismo proceso.

## Servicio existente

1. Antes de cambiar rutas de almacenamiento, respaldar la base y `media/` del servicio
   actual. El repositorio aún contiene una base SQLite histórica: no usarla para
   reemplazar los datos vigentes del hosting. El push de esta mejora no incluye los
   cambios de la base local ni los archivos de usuarios.
2. Configurar **Build Command** como `bash build.sh` y **Start Command** como
   `bash start.sh`. Las migraciones y la inicialización actual del administrador se
   ejecutan durante el arranque, cuando el disco ya está montado. Por compatibilidad,
   el build también conserva esos pasos si `PLANOS_DATA_DIR` no está definido.
3. Configurar **Health Check Path** como `/healthz/`. Responde sin sesión, sin leer la
   base y sin redirigir el chequeo interno HTTP hacia HTTPS.
4. Definir `DJANGO_DEBUG=false` y una clave privada y estable en `DJANGO_SECRET_KEY`.
   También se admite `SECRET_KEY`, común en servicios Render existentes. La clave no se
   genera ni se publica desde el repositorio. Cambiarla invalida sesiones anteriores.
5. Render informa su hostname en `RENDER_EXTERNAL_HOSTNAME`; se incorpora automáticamente
   a los hosts y orígenes CSRF aceptados. Para un dominio propio, agregarlo mediante
   `DJANGO_ALLOWED_HOSTS` y su URL HTTPS en `DJANGO_CSRF_TRUSTED_ORIGINS`. Se conservan
   las direcciones definidas explícitamente.
6. Para conservar SQLite y archivos entre despliegues, adjuntar un disco persistente
   y apuntar `PLANOS_DATA_DIR` a una carpeta dentro de ese disco, por ejemplo
   `/var/data/reloncavi`. Copiar allí primero la base vigente y `media/`, con el servicio
   detenido y un respaldo verificado. `start.sh` crea carpetas faltantes, pero nunca
   copia encima de una base ni elimina documentos. Una carpeta vacía inicia una base
   nueva mediante las migraciones; no representa una migración de los datos existentes.
7. Conservar las variables actuales de Drive/Supabase y del administrador. Después del
   despliegue, comprobar inicio de sesión, apertura de un PDF y un DXF, edición y guardado
   de Excel, guardado en nube, exportación y reapertura. Para instalar la PWA, seguir
   [PWA.md](PWA.md).

`PLANOS_DATA_DIR` tiene que estar en un disco real: una variable por sí sola no hace
persistente una carpeta. Render monta los discos durante la ejecución, no durante
builds o comandos de pre-deploy. El disco se configura en el panel del servicio;
estos archivos no crean servicios ni contratan recursos.

## Validación y recomendaciones de capacidad

```text
python manage.py check
python manage.py test --noinput
python scripts/render_deploy_smoke.py
python scripts/pwa_browser_smoke.py
python scripts/pwa_browser_smoke.py --webkit
python scripts/plan_visuals_browser_smoke.py
```

En esta entrega pasaron las 256 pruebas Django, la simulación de producción y los
flujos PWA en Chromium y WebKit. También pasó el recorrido de PDF/DXF con sectores,
transformaciones, opacidad, exportación y controles táctiles en pantalla estrecha.

El chequeo `python manage.py check --deploy` debe ejecutarse con las variables reales
de producción. Las opciones HSTS para subdominios y preload son decisiones del dominio
y quedan desactivadas por defecto.

La suite general de navegador anterior todavía utiliza un selector inexistente
`#btnAssignExcelCorner`. Las verificaciones PWA actuales usan su propio flujo completo
y los tests Django cubren las funciones existentes; no se modifica ese recorrido antiguo
para atribuirle una validación que no se ejecutó.

Si el servicio se queda sin memoria al abrir/importar documentos grandes, ampliar su
memoria antes de aumentar procesos o hilos. SQLite se conserva para evitar cambiar el
comportamiento de la app. Para varios servidores o muchas escrituras simultáneas,
planificar por separado una migración a PostgreSQL y almacenamiento externo de archivos,
con pruebas y migración de datos. En planes que se suspenden por inactividad, el primer
acceso puede ser más lento y no se corrige mediante cambios al visor.

## Migración al catálogo central

La primera etapa separa las descargas del dispositivo en IndexedDB. El visor consulta
solo el estado de los planos abiertos, cada dos segundos durante una publicación y
cada quince segundos en reposo. La consulta al catálogo completo se limita a la biblioteca.
El sondeo no vuelve a listar las carpetas de la nube. Si el servicio devuelve un error
temporal de infraestructura, la PWA instalada puede abrir su interfaz y copias descargadas.

Antes de mover los datos de producción:

1. Respaldar SQLite, media, el catálogo actual de Supabase y el inventario de Drive.
   Reconciliar propietarios, botones, hojas e IDs de archivos por clave de proyecto.
2. Ensayar las migraciones de Django y el traspaso de cuentas y metadatos en PostgreSQL.
   Mover los binarios a Drive y cambiar la lectura del catálogo a la base principal.
3. Configurar la autorización de Drive en el servidor y migrar la publicación a una cola
   persistente. Las copias offline mantienen su flujo del navegador. Retirar la opción
   de limpiar copias de la instalación cuando se desactive el almacenamiento local del servidor.
4. Validar en un entorno aislado con 20 usuarios activos, archivos representativos y
   guardados concurrentes. Probar un reinicio durante la publicación y una reapertura
   desde otro equipo. Medir memoria, CPU y transferencia antes de elegir el plan de Render.
5. Después de verificar el traspaso, hacer el corte con la instalación anterior en solo
   lectura. Las descargas de localhost deben volver a descargarse desde el dominio de Render.

La configuración de arranque actual sigue usando SQLite. El cambio al catálogo central
requiere completar el traslado de archivos y la publicación antes de habilitar escrituras.

Referencias oficiales: [Django en Render](https://render.com/docs/deploy-django),
[discos persistentes](https://render.com/docs/disks),
[variables de Render](https://render.com/docs/environment-variables) y
[WhiteNoise para Django](https://whitenoise.readthedocs.io/en/stable/django.html).
