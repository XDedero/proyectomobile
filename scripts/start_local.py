"""Start this copy of the Django app without a cloud account or CLI."""

import argparse
from http.client import HTTPConnection, HTTPException
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser


ROOT = Path(__file__).resolve().parent.parent
HOST = '127.0.0.1'
PORT = 8000
URL = f'http://{HOST}:{PORT}/'
CHECK_IMPORTS = 'import django, ezdxf, openpyxl, defusedxml, PIL, psycopg, pypdf, whitenoise'


def has_dependencies(python):
    return subprocess.run(
        [str(python), '-c', CHECK_IMPORTS], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def can_run(python):
    try:
        return subprocess.run(
            [str(python), '-V'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except OSError:
        return False


def app_python():
    current = Path(sys.executable)
    if has_dependencies(current):
        return current

    directory = ROOT / '.venv-local'
    python = directory / 'Scripts' / 'python.exe'
    if not can_run(python):
        print('Preparando el entorno Python de esta instalación…', flush=True)
        subprocess.run([str(current), '-m', 'venv', str(directory)], cwd=ROOT, check=True)
    if not has_dependencies(python):
        print('Instalando las dependencias de la aplicación…', flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt'], cwd=ROOT, check=True)
        if not has_dependencies(python):
            raise RuntimeError('El entorno se instaló, pero alguna dependencia de la aplicación no se puede cargar.')
    return python


def wait_until_ready(server):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        code = server.poll()
        if code is not None:
            raise RuntimeError(f'Django se cerró durante el arranque (código {code}). Revisa el error anterior.')
        connection = HTTPConnection(HOST, PORT, timeout=1)
        try:
            connection.request('GET', '/api/auth/session/')
            if connection.getresponse().status == 200:
                return
        except (OSError, HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.25)
    raise RuntimeError(f'Django no respondió en {URL} a tiempo. Revisa los mensajes de esta ventana.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    options = parser.parse_args()
    try:
        if not options.check_only:
            with socket.socket() as port_check:
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    port_check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    port_check.bind((HOST, PORT))
                except OSError as error:
                    raise RuntimeError(f'No se puede usar {HOST}:{PORT}. Si la app ya está abierta, cierra el servidor anterior.') from error
        python = app_python()
        print('Preparando la base de datos local de la aplicación…', flush=True)
        subprocess.run([str(python), 'manage.py', 'migrate', '--noinput'], cwd=ROOT, check=True)
        if options.check_only:
            subprocess.run([str(python), 'manage.py', 'check'], cwd=ROOT, check=True)
            return 0
        server = subprocess.Popen(
            [str(python), '-u', 'manage.py', 'runserver', f'{HOST}:{PORT}', '--noreload'], cwd=ROOT,
        )
        try:
            wait_until_ready(server)
            print(f'Aplicación lista en {URL}', flush=True)
            if not options.no_browser:
                try:
                    webbrowser.open(URL)
                except webbrowser.Error:
                    pass  # The server remains available at the address printed above.
            return server.wait()
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait()
    except KeyboardInterrupt:
        return 0
    except subprocess.CalledProcessError as error:
        print(f'Falló el paso «{Path(error.cmd[0]).name} {" ".join(error.cmd[1:3])}». Revisa el error anterior.', file=sys.stderr)
        return error.returncode or 1
    except (OSError, RuntimeError) as error:
        print(f'No se pudo iniciar la aplicación: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
