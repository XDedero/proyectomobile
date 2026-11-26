# Registro privado de cuentas en Supabase

Las cuentas compartidas se almacenan en el esquema privado `app_identity` de PostgreSQL. El administrador principal las crea desde **Administración → Usuarios**. El personal accede con RUT y contraseña. Las cuentas locales conservan su acceso independiente y sus contraseñas.

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

La inicialización prepara la tabla privada de cuentas y retira las funciones antiguas de solicitudes. Conserva las cuentas y los registros históricos. Ejecuta el comando en cada base de datos compartida que use esta versión. Las migraciones locales de Django siguen aplicándose con `manage.py migrate`.

El documento `conexion_supabase.txt` es informativo: una URI que conserva
`[TU_PASSWORD]` no permite conectarse. Una API key pública o una sesión del panel
tampoco sustituyen la contraseña PostgreSQL del servidor. No publiques la URI ni
la incorpores a JavaScript. El archivo `conexion-base-datos.txt` está excluido de Git.

## Compartir cuentas entre instalaciones

Todas las instalaciones deben ejecutar esta versión y apuntar al mismo registro con `APP_SHARED_ACCOUNTS=1`. Las cuentas creadas por el administrador principal están disponibles en las instalaciones enlazadas. `manage.py sync_shared_accounts` actualiza la copia local para la administración. Los cambios de contraseña se guardan en el registro compartido y requieren volver a iniciar sesión en los demás equipos.

Si Supabase no responde, el panel informa el fallo de las operaciones del registro compartido. Las cuentas locales existentes pueden seguir iniciando sesión por RUT.

La conexión nativa utiliza transacciones de lectura, sin modificar opciones de
sesión del pooler, y desactiva sentencias preparadas. Consulta la
[documentación de conexión de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).
