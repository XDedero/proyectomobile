# Render gratuito con Supabase y Google Drive

El error `mkdir: cannot create directory /var/data: Permission denied` se produce
porque un servicio gratuito no tiene un disco persistente montado en esa ruta.
Cambiar solamente la ruta de SQLite deja los datos sujetos a reinicios. El modo
nube guarda las tablas principales de Django en PostgreSQL y los originales de
PDF, DXF y Excel en Drive. Los libros editables y los proyectos guardados que ya
son campos binarios se conservan en PostgreSQL. La publicacion PDF/XLSX existente
continua utilizando sus carpetas, asociaciones y metadatos actuales.

## Configuracion del servicio

| Campo | Valor |
| --- | --- |
| Repositorio | `https://github.com/XDedero/proyectomobile` |
| Branch | `main` |
| Runtime | Python 3 |
| Root Directory | Vacio |
| Build Command | `bash build.sh` |
| Start Command | `bash start.sh` |
| Health Check Path | `/healthz/` |
| Disco | Ninguno |

Retirar el Start Command anterior que ejecutaba `mkdir /var/data`, `cp` y `ln`.
Retirar tambien `PLANOS_DATA_DIR=/var/data/reloncavi` del entorno de Render.

Variables necesarias:

```dotenv
PLANOS_CLOUD_MODE=1
PLANOS_DATABASE_SCHEMA=app_runtime
SUPABASE_DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres?sslmode=require
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=CONSERVAR_LA_CLAVE_PRIVADA_DEL_SERVICIO
APP_SHARED_ACCOUNTS=1
APP_TERMINAL_DEBUG=1
WEB_CONCURRENCY=1
GUNICORN_THREADS=2
GUNICORN_TIMEOUT=120
PYTHON_VERSION=3.13.18
NODE_VERSION=24
```

Copiar la URI real desde **Supabase > Connect > Session pooler**. El puerto 5432
reserva una conexion de sesion y permite fijar el esquema privado por conexion;
no utilizar el transaction pooler del puerto 6543 para la base principal.
Las credenciales con caracteres reservados deben estar codificadas en la URI.
El esquema `app_runtime` no debe agregarse a los esquemas expuestos de la Data API.
Los catalogos `public` y `app_identity` existentes se conservan por separado.

En **Environment > Secret Files**, agregar:

- Filename: `gdrive-token-seed.json`.
- Contents: contenido completo del `gdrive-token.json` autorizado de esta PC.

El arranque crea una copia local escribible de la autorizacion si no existe y
renueva el access token cuando sea necesario. Su refresh token permanente procede
del archivo secreto. Ni el token ni la URI privada pertenecen al repositorio.

El hostname asignado por Render se incorpora automaticamente a hosts y CSRF.
Para un dominio propio, configurar `DJANGO_ALLOWED_HOSTS` y
`DJANGO_CSRF_TRUSTED_ORIGINS` con ese dominio.

## Transferencia de una instalacion existente

Hacer la transferencia antes de activar la app remota para escrituras. Conservar
la base y media originales. La transferencia obtiene una instantanea SQLite en
modo lectura, verifica los originales subidos y copia usuarios, hashes de
contrasena, permisos, fichas, sectores, botones y guardados. Rechaza un destino
que ya contenga cuentas o proyectos y revierte la transaccion ante un fallo.

Guardar la URI privada real en `conexion-base-datos.txt`, junto a `manage.py`, o
definir `SUPABASE_DATABASE_URL` en el entorno local. El archivo esta ignorado en
Git. Desde PowerShell, usando el Python instalado para esta copia:

```powershell
$env:PLANOS_CLOUD_MODE = '1'
$env:PLANOS_DATABASE_SCHEMA = 'app_runtime'
$env:APP_TERMINAL_DEBUG = '0'
.\.venv-local\Scripts\python.exe -B scripts/prepare_runtime.py
.\.venv-local\Scripts\python.exe -B manage.py migrate --noinput
.\.venv-local\Scripts\python.exe -B manage.py migrate_local_to_cloud --sqlite "$PWD\db.sqlite3" --media "$PWD\media"
.\.venv-local\Scripts\python.exe -B manage.py migrate_local_to_cloud --sqlite "$PWD\db.sqlite3" --media "$PWD\media" --execute
.\.venv-local\Scripts\python.exe -B manage.py setup_shared_accounts
```

Si la ultima comprobacion informa que faltan las funciones del registro privado,
ejecutar `manage.py setup_shared_accounts --initialize` y repetir la comprobacion.
No ejecutar `init_admin.py` antes de transferir: el destino debe estar vacio.
Si ya se inicializo otro destino con cuentas, utilizar un esquema privado nuevo
y configurar el mismo nombre tanto en la transferencia como en Render.

Al terminar, quitar las variables temporales de esta consola para volver a
utilizar SQLite local:

```powershell
Remove-Item Env:PLANOS_CLOUD_MODE
Remove-Item Env:PLANOS_DATABASE_SCHEMA
Remove-Item Env:APP_TERMINAL_DEBUG
```

Una instalacion realmente nueva puede definir `DJANGO_SUPERUSER_USERNAME=admin`
y `DJANGO_SUPERUSER_PASSWORD` en Render para crear su administrador. En cuentas
existentes el arranque conserva su contrasena. Un reinicio explicito requiere
`DJANGO_RESET_SUPERUSER_PASSWORD=1`; retirar esa variable despues de usarla.

## Verificacion

```text
python -B manage.py test --noinput
python -B scripts/render_deploy_smoke.py
python -B scripts/cloud_migration_smoke.py
node --test scripts/drive-storage.test.js scripts/cloud-workbook.test.js scripts/drive-recovery.test.js
```

Estas pruebas locales utilizan bases temporales y transporte simulado de Drive;
no certifican las credenciales remotas. Una vez desplegado, comprobar inicio de
sesion, apertura PDF/DXF, fichas, guardado, exportacion y reapertura despues de
un reinicio. La opcion de limpiar copias del servidor queda desactivada en modo
nube para no eliminar proyectos de la base compartida; las copias del navegador
mantienen su manejo independiente.

El plan gratuito puede suspender la ejecucion y hacer mas lento el siguiente
inicio; PostgreSQL y Drive conservan los datos principales durante ese periodo.
