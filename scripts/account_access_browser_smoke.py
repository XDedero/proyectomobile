"""Exercise direct account creation, RUT login, personal passwords and PDF editing.

The server, database and media are temporary. No production records are changed.
"""
import os
from datetime import datetime
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PASSWORD = 'PlanosSeguro!2026'
PRIMARY_RUT = '22222222-2'
SECONDARY_RUT = '33333333-3'
NEW_RUT = '12345678-5'


def serve():
    os.environ['APP_SHARED_ACCOUNTS'] = '0'
    os.environ['PLANOS_CLOUD_MODE'] = '0'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
    from django.conf import settings
    directory = Path(os.environ['ACCOUNT_ACCESS_TEST_DIR'])
    settings.DATABASES['default']['NAME'] = directory / 'test.sqlite3'
    settings.MEDIA_ROOT = directory / 'media'
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from mobileApp.models import UserProfile
    call_command('migrate', verbosity=0)
    admin = get_user_model().objects.create_superuser('admin', password=PASSWORD)
    UserProfile.objects.create(user=admin, rut=PRIMARY_RUT, role='administrator', is_primary_administrator=True)
    secondary = get_user_model().objects.create_superuser('supervisor', password=PASSWORD)
    UserProfile.objects.create(user=secondary, rut=SECONDARY_RUT, role='administrator')
    call_command('runserver', f"127.0.0.1:{os.environ['ACCOUNT_ACCESS_TEST_PORT']}", use_reloader=False)


def login(page, rut, password=PASSWORD):
    from playwright.sync_api import expect
    page.wait_for_function('appState.authReady')
    page.locator('#usernameInput').fill(rut)
    page.locator('#passwordInput').fill(password)
    page.locator('#btnLogin').click()
    expect(page.locator('#viewMainMenu')).to_be_visible()


def check_company_color(context, url):
    from playwright.sync_api import expect

    app = context.new_page()
    settings = context.new_page()
    settings.set_viewport_size({'width': 390, 'height': 844})
    try:
        app.goto(url)
        expect(app.locator('#viewMainMenu')).to_be_visible()
        settings.goto(url + 'administracion/')
        company_link = settings.get_by_role('link', name='Configuración de la empresa')
        company_link.click()

        color = settings.locator('#companyColor')
        toggle = settings.locator('#companyPaletteButton')
        palette = settings.locator('#companyPalette')
        expect(palette).to_be_hidden()
        toggle.click()
        expect(toggle).to_have_attribute('aria-expanded', 'true')
        expect(palette).to_be_visible()
        assert settings.evaluate('document.documentElement.scrollWidth <= innerWidth')
        settings.locator('#companyCustomColor').fill('#2c6e49')
        expect(color).to_have_value('#2c6e49')
        purple = palette.get_by_role('button', name='Morado')
        purple.click()
        expect(purple).to_have_attribute('aria-pressed', 'true')
        expect(color).to_have_value('#7e22ce')
        settings.get_by_role('button', name='Guardar cambios').click()
        expect(settings).to_have_url(url + 'administracion/')
        expect(settings.locator('.management-tabs button.active')).to_have_css('background-color', 'rgb(126, 34, 206)')
        app.evaluate('appAuth.refresh()')
        expect(app.locator('#appScreen')).to_have_css('background-color', 'rgb(240, 228, 249)')

        company_link.click()
        expect(color).to_have_value('#7e22ce')
        toggle.click()
        settings.get_by_role('button', name='Usar color predeterminado').click()
        settings.get_by_role('button', name='Guardar cambios').click()
        expect(settings).to_have_url(url + 'administracion/')
        app.evaluate('appAuth.refresh()')
        expect(app.locator('#appScreen')).to_have_css('background-color', 'rgb(237, 245, 255)')
        print('PASS: mobile company color palette, custom color selection, saved theme and reset', flush=True)
    finally:
        settings.close()
        app.close()


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
    from scripts.generate_pdf_demo import generate

    generate(directory)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            admin_context = browser.new_context(viewport={'width': 1150, 'height': 850})
            admin = admin_context.new_page()
            admin.goto(url)
            expect(admin.get_by_role('button', name='Solicitar registro')).to_have_count(0)
            login(admin, PRIMARY_RUT)
            expect(admin.locator('#btnGoToImportSection')).to_have_count(0)
            admin.locator('#btnAdminister').click()
            expect(admin.get_by_role('button', name='Solicitudes')).to_have_count(0)
            admin.locator('#newUser').click()
            admin.locator('#userFirstName').fill('María')
            admin.locator('#userLastName').fill('Pérez')
            admin.locator('#userRut').fill(NEW_RUT)
            admin.locator('#userRole').select_option('worker')
            admin.locator('#userPassword').fill(PASSWORD)
            with admin.expect_response('**/api/administration/users/') as created:
                admin.locator('#saveUser').click()
            assert created.value.status == 201, created.value.text()
            expect(admin.locator('#userRows')).to_contain_text('María Pérez')
            check_company_color(admin.context, url)

            secondary = browser.new_page()
            secondary.goto(url)
            login(secondary, SECONDARY_RUT)
            secondary.locator('#btnAdminister').click()
            expect(secondary.locator('#newUser')).to_have_count(0)

            worker = browser.new_page(viewport={'width': 412, 'height': 915}, is_mobile=True, has_touch=True)
            worker.goto(url)
            worker.wait_for_function('appState.authReady')
            worker.locator('#usernameInput').fill('María')
            worker.locator('#passwordInput').fill(PASSWORD)
            with worker.expect_response('**/api/auth/login/') as denied:
                worker.locator('#btnLogin').click()
            assert denied.value.status == 401
            login(worker, '12.345.678-5')
            worker.locator('#userMenuBtn').click()
            worker.get_by_role('link', name='Cambiar contraseña').click()
            changed = 'Cambio!Costa2026'
            worker.locator('#currentPassword').fill(PASSWORD)
            worker.locator('#newPassword').fill(changed)
            worker.locator('#confirmPassword').fill(changed)
            worker.get_by_role('button', name='Guardar contraseña').click()
            expect(worker.get_by_role('status')).to_have_text('Contraseña actualizada.')
            worker.get_by_role('link', name='Volver a los planos').click()
            expect(worker.locator('#viewMainMenu')).to_be_visible()

            worker.locator('#btnGoToPdfSection').click()
            worker.locator('#pdfFileInput').set_input_files(directory / 'plano_pdf_configurable.pdf')
            worker.wait_for_function("projectWorkspace.active?.kind === 'pdf' && projectWorkspace.active.plan.is_draft && !projectWorkspace.active.isBusy()")
            worker.locator('#btnPdfFullscreen').click()
            worker.locator('#btnPlanFullscreenMenu').click()
            worker.locator('[data-project-action="add"]').click()
            worker.locator('#newButtonCategory').select_option(label='Motores')
            worker.locator('#newButtonName').fill('Motor de prueba')
            worker.locator('#newButtonMaintenanceDate').fill(datetime.now(ZoneInfo('America/Santiago')).date().isoformat())
            worker.locator('#newButtonMaintenanceInterval').fill('2')
            worker.locator('#btnConfirmNewButton').click()
            worker.wait_for_function('projectWorkspace.active.viewer.placing')
            bounds = worker.locator('#pdfViewport .pdf-plan-page').bounding_box()
            with worker.expect_response(lambda response: response.url.endswith('/markers/') and response.request.method == 'POST') as marker:
                worker.touchscreen.tap(bounds['x'] + bounds['width'] * .4, bounds['y'] + bounds['height'] * .4)
            assert marker.value.status == 201, marker.value.text()
            worker.wait_for_function('projectWorkspace.active.plan._projectDirty && !projectWorkspace.active.isBusy()')
            worker.locator('#fullscreenSavePlan').click()
            worker.wait_for_function('projectWorkspace.active.plan.saved_at && !projectWorkspace.active.plan._projectDirty')

            fresh = browser.new_page()
            fresh.goto(url)
            login(fresh, NEW_RUT, changed)
            expect(fresh.locator('#welcomeUserText')).to_contain_text('María Pérez')
            print('PASS: direct user creation, primary administrator restriction, RUT-only login, password change and mobile PDF editing', flush=True)
        finally:
            browser.close()


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts, prefix='account-access-') as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment.update(ACCOUNT_ACCESS_TEST_DIR=temporary, ACCOUNT_ACCESS_TEST_PORT=str(port), PYTHONUNBUFFERED='1', APP_TERMINAL_DEBUG='0')
        with (artifacts / 'account-access-server.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen([sys.executable, __file__, '--serve'], cwd=ROOT, env=environment,
                                      stdout=output, stderr=subprocess.STDOUT,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                url = f'http://127.0.0.1:{port}/'
                for _ in range(150):
                    if server.poll() is not None:
                        raise RuntimeError('Test server failed; see .test-artifacts/account-access-server.log')
                    try:
                        with urlopen(url, timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Test server did not start.')
                check_browser(url, Path(temporary))
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    serve() if '--serve' in sys.argv else main()
