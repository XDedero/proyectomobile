# Conexión de cuentas y solicitudes a Supabase

Los planos, las sesiones y las cuentas anteriores se conservan en SQLite local.
Las solicitudes nuevas y sus cuentas aprobadas se comparten mediante el esquema
privado `app_identity` de PostgreSQL en Supabase. No utilizan `public.users` ni
Supabase Auth. La cuenta principal `admin` conserva su contraseña y acceso local.

Si este equipo ya usa la herramienta oficial de Supabase, la app también puede
utilizar su sesión guardada. Detecta el ejecutable instalado por npm aunque no esté
en `PATH`. Esa sesión debe pertenecer a una cuenta con acceso al proyecto enlazado
en `supabase/.temp/project-ref`; iniciar sesión con otra cuenta devuelve un error
403 de permisos. No hace falta añadir una URI PostgreSQL para utilizar una sesión
válida de la herramienta oficial.

## Configuración inicial en Windows

1. En el proyecto correcto de Supabase, abre **Connect → Session pooler** y copia
   la URI PostgreSQL con la contraseña real de la base de datos. El pooler de sesión
   permite conectar desde redes IPv4. Usa la dirección exacta del panel.
2. Guarda la URI completa como una única línea en `conexion-base-datos.txt`, en la
   carpeta donde está `manage.py`. También puedes configurar la variable secreta
   `SUPABASE_DATABASE_URL`, que tiene prioridad. Si la contraseña contiene símbolos
   reservados de una URL, debe estar codificada en la URI.
3. Ejecuta:

   ```powershell
   .\.venv-local\Scripts\python.exe manage.py setup_shared_accounts --initialize
   .\.venv-local\Scripts\python.exe manage.py setup_shared_accounts
   .\.venv-local\Scripts\python.exe manage.py sync_shared_accounts
   ```

La inicialización crea las tablas y funciones de identidad que falten y actualiza
sus funciones sin borrar cuentas ni solicitudes. Se ejecuta una vez por base de
datos, también después de actualizar las funciones SQL. No reemplaza la migración
local de Django: `ejecutar_debug.bat` sigue aplicando esa migración automáticamente.

El documento `conexion_supabase.txt` es informativo: una URI que conserva
`[TU_PASSWORD]` no permite conectarse. Una API key pública o una sesión del panel
tampoco sustituyen la contraseña PostgreSQL del servidor. No publiques la URI ni
la incorpores a JavaScript. El archivo `conexion-base-datos.txt` está excluido de Git.

## Verificar el flujo entre instalaciones

Todas las instalaciones deben ejecutar la misma versión y apuntar al mismo proyecto
con `APP_SHARED_ACCOUNTS=1`, que es el valor predeterminado. El administrador puede
revisar las solicitudes en **Administrar → Solicitudes**. Una cuenta aprobada accede
con su RUT y la contraseña que eligió al solicitar el registro.

Para comprobar el envío, aprobación, acceso, cambio de rol y rechazo desde dos bases
locales independientes:

```powershell
.\.venv-local\Scripts\python.exe scripts/shared_registration_smoke.py
```

La prueba usa registros con identificadores nuevos y elimina sus propios registros
de Supabase al terminar. Las bases de datos de prueba se crean aparte de `db.sqlite3`.

Si Supabase no responde, las solicitudes aún no enviadas se conservan localmente y
se informa el fallo. `sync_shared_accounts` permite reintentar el envío. Un fallo de
conexión no impide iniciar sesión con cuentas locales; las cuentas compartidas sí
necesitan consultar su contraseña y permisos vigentes en el registro remoto.

La conexión nativa utiliza transacciones de lectura, sin modificar opciones de
sesión del pooler, y desactiva sentencias preparadas. Consulta la
[documentación de conexión de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).
