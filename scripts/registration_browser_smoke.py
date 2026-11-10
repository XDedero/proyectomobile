"""Exercise registration review, account roles and plan history in an isolated app.

The server, database and media are temporary. No production records are changed.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PASSWORD = 'PlanosSeguro!2026'


def serve():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
    from django.conf import settings
    directory = Path(os.environ['REGISTRATION_TEST_DIR'])
    settings.DATABASES['default']['NAME'] = directory / 'test.sqlite3'
    settings.MEDIA_ROOT = directory / 'media'
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from mobileApp.accounts import next_test_rut
    from mobileApp.models import UserProfile
    call_command('migrate', verbosity=0)
    admin = get_user_model().objects.create_superuser('admin', password=PASSWORD)
    UserProfile.objects.create(user=admin, rut=next_test_rut(), rut_is_test=True,
                               role='administrator', is_primary_administrator=True)
    for username in ('camilo', 'vicente', 'inactivo'):
        get_user_model().objects.create_user(username, password=PASSWORD, is_active=username != 'inactivo')
    call_command('runserver', f"127.0.0.1:{os.environ['REGISTRATION_TEST_PORT']}", use_reloader=False)


def login(page, username):
    from playwright.sync_api import expect
    page.wait_for_function('appState.authReady')
    page.locator('#usernameInput').fill(username)
    page.locator('#passwordInput').fill(PASSWORD)
    page.locator('#btnLogin').click()
    expect(page.locator('#viewMainMenu')).to_be_visible()


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright
    from scripts.generate_demo import generate
    generate(directory)
    errors = []

    def post(page, path, data):
        token = next(cookie['value'] for cookie in page.context.cookies() if cookie['name'] == 'csrftoken')
        return page.request.post(url.rstrip('/') + path, data=data, headers={'X-CSRFToken': token})

    def management(page):
        return page.request.get(url + 'api/administration/').json()

    def register(page, first, last, rut):
        page.goto(url)
        page.wait_for_function('appState.authReady')
        page.locator('#btnRequestRegistration').click()
        expect(page.locator('#registrationPanel')).to_be_visible()
        for identifier, value in [('FirstName', first), ('LastName', last), ('Rut', rut),
                                  ('Password', PASSWORD), ('ConfirmPassword', PASSWORD)]:
            page.locator('#registration' + identifier).fill(value)
        if first == 'Camila':
            rut_input = page.locator('#registrationRut')
            expect(rut_input).to_have_attribute('placeholder', '12345678-5')
            for invalid in ('12.345.678-5', '123456785'):
                rut_input.fill(invalid)
                assert not rut_input.evaluate('(input) => input.checkValidity()')
            rut_input.fill(rut)
            page.screenshot(path=str(ROOT / '.test-artifacts/registration-form-mobile.png'), full_page=True)
        with page.expect_response('**/api/auth/register/') as response:
            page.locator('#btnSubmitRegistration').click()
        assert response.value.status == 201, response.value.text()
        expect(page.locator('#registrationStatus')).to_contain_text('Solicitud')
        page.locator('#btnBackToLogin').click()
        return response.value.json()['id']

    def select_user(page, name):
        page.locator('[data-tab="users"]').click()
        page.locator('#userSearch').fill(name)
        rows = page.locator('#userRows .management-row')
        expect(rows).to_have_count(1)
        rows.get_by_role('button', name='Editar', exact=True).click()

    def submit_role(page, role, confirm=False):
        page.locator('#userRole').select_option(role)
        if confirm:
            page.locator('#userForm button[type="submit"]').click()
            expect(page.locator('#managementConfirm')).to_be_visible()
            with page.expect_response('**/api/administration/users/') as response:
                page.locator('#confirmManagementAction').click()
        else:
            with page.expect_response('**/api/administration/users/') as response:
                page.locator('#userForm button[type="submit"]').click()
        assert response.value.status == 200, response.value.text()
        return response.value.json()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        try:
            visitor_context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            visitor = visitor_context.new_page()
            visitor.on('pageerror', lambda error: errors.append(str(error)))
            visitor.route('**/api/projects/cloud/', lambda route: route.fulfill(json={'plans': []}))
            request_id = register(visitor, 'Camila', 'Fuentes', '12345678-5')
            with visitor.expect_response('**/api/auth/login/') as denied:
                visitor.locator('#usernameInput').fill('12345678-5')
                visitor.locator('#passwordInput').fill(PASSWORD)
                visitor.locator('#btnLogin').click()
            assert denied.value.status == 401

            admin_context = browser.new_context(viewport={'width': 1365, 'height': 900})
            admin = admin_context.new_page()
            admin.on('pageerror', lambda error: errors.append(str(error)))
            admin.route('**/api/projects/cloud/', lambda route: route.fulfill(json={'plans': []}))
            admin.goto(url)
            login(admin, 'admin')
            admin.locator('#btnAdminister').click()
            expect(admin).to_have_url(url + 'administracion/')
            admin.locator('[data-tab="users"]').click()
            expect(admin.locator('#userRows .management-row')).to_have_count(4)
            for username in ('admin', 'camilo', 'vicente', 'inactivo'):
                expect(admin.locator('#userRows')).to_contain_text(username)
            admin.locator('#userSearch').fill('usuario inexistente')
            expect(admin.locator('#userRows .management-row')).to_have_count(0)
            admin.locator('[data-tab="requests"]').click()
            admin.locator('[data-tab="users"]').click()
            expect(admin.locator('#userSearch')).to_have_value('')
            expect(admin.locator('#userRows .management-row')).to_have_count(4)
            admin.locator('[data-tab="requests"]').click()
            row = admin.locator('#requestRows .request-row').filter(has_text='Camila Fuentes')
            with admin.expect_response('**/api/administration/requests/') as approved:
                row.locator('[data-action="approve"]').click()
            assert approved.value.status == 200, approved.value.text()
            expect(row).to_have_count(0)
            expect(admin.locator('#requestRows')).to_contain_text('No hay solicitudes pendientes.')
            assert not any(item['id'] == request_id for item in management(admin)['requests'])
            login(visitor, '12.345.678-5')
            permissions = visitor.evaluate('appState.user.permissions')
            assert permissions['can_import'] and not permissions['can_create'] and not permissions['can_history']
            expect(visitor.locator('#btnAdminister')).to_be_hidden()
            expect(visitor.locator('#btnMyPlans')).to_be_hidden()
            assert visitor.request.get(url + 'api/projects/?mine=1').status == 403
            visitor.locator('#btnGoToImportSection').click()
            with visitor.expect_response('**/api/plans/import/') as imported:
                visitor.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            assert imported.value.status == 201, imported.value.text()
            plan = imported.value.json()
            expect(visitor.locator('.motor-target')).to_have_count(3)
            expect(visitor.locator('#btnCadSavePlan')).to_be_hidden()
            assert post(visitor, plan['markers_url'], {'svg_x': 20, 'svg_y': 20}).status == 403
            assert post(visitor, plan['save_project_url'], {}).status == 403
            print('PASS: registration needs approval; accepted account can view/import without creating or history', flush=True)

            # A second request exercises both choices of the exact deletion modal.
            rejected_context = browser.new_context()
            rejected = rejected_context.new_page()
            rejected.on('pageerror', lambda error: errors.append(str(error)))
            rejected_id = register(rejected, 'Diego', 'Soto', '11111111-1')
            admin.reload()
            rejected_row = admin.locator('#requestRows .request-row').filter(has_text='Diego Soto')
            rejected_row.locator('[data-action="reject"]').click()
            expect(admin.locator('#managementConfirm')).to_be_visible()
            expect(admin.locator('#managementConfirmTitle')).to_have_text('¿Seguro que desea eliminar esta solicitud?')
            admin.locator('#managementConfirm [value="cancel"]').click()
            assert next(item for item in management(admin)['requests'] if item['id'] == rejected_id)['status'] == 'pending'
            rejected_row.locator('[data-action="reject"]').click()
            with admin.expect_response('**/api/administration/requests/') as rejected_response:
                admin.locator('#confirmManagementAction').click()
            assert rejected_response.value.status == 200
            expect(rejected_row).to_have_count(0)
            assert not any(item['id'] == rejected_id for item in management(admin)['requests'])
            assert post(admin, '/api/administration/requests/', {'id': rejected_id, 'action': 'reject'}).status == 404
            admin.reload()
            expect(admin.locator('#requestRows')).to_contain_text('No hay solicitudes pendientes.')
            rejected_context.close()
            print('PASS: users tab shows every account; approved and deleted requests disappear without history', flush=True)

            # The user list searches names and changes an existing account's role.
            select_user(admin, 'Camila')
            changed = submit_role(admin, 'worker')
            assert changed['requires_login']
            visitor.reload()
            expect(visitor.locator('#loginForm')).to_be_visible()
            login(visitor, '12345678-5')
            assert visitor.evaluate('appState.user.role') == 'worker'
            expect(visitor.locator('#btnMyPlans')).to_be_visible()
            assert post(visitor, '/api/administration/users/', {'id': changed['user']['id'], 'role': 'administrator'}).status == 403
            category = post(visitor, '/api/administration/categories/', {'name': 'Sensores prueba'})
            assert category.status == 200, category.text()
            category_id = category.json().get('id') or category.json().get('category', {}).get('id')
            assert post(visitor, '/api/administration/categories/', {'id': category_id, 'action': 'delete'}).status == 403
            visitor.locator('#btnGoToImportSection').click()
            with visitor.expect_response('**/api/plans/import/') as worker_import:
                visitor.locator('#autocadFileInput').set_input_files(directory / 'motores_demo.dxf')
            own = worker_import.value.json()
            assert own['permissions']['can_assign'] and own['permissions']['can_delete_plan']
            visitor.locator('#btnAddCadMarker').click()
            expect(visitor.locator('#newPlanButton')).to_be_visible()
            category_box = visitor.locator('#newPlanButton details')
            category_box.locator('summary').click()
            visitor.locator('#newButtonCategoryNewName').fill('Categoría desde el plano')
            with visitor.expect_response('**/api/administration/categories/') as inline_category:
                category_box.get_by_role('button', name='Crear categoría', exact=True).click()
            assert inline_category.value.status == 200, inline_category.value.text()
            expect(visitor.locator('#newButtonCategory')).to_have_value(inline_category.value.json()['id'])
            expect(category_box).to_contain_text('Categoría creada.')
            visitor.locator('#newPlanButton .btn-close').click()
            with visitor.expect_response('**/save/') as saved:
                visitor.locator('#btnCadSavePlan').click()
            assert saved.value.status == 200, saved.value.text()
            visitor.locator('#btnBackToMainMenu').click()
            visitor.locator('#btnMyPlans').click()
            expect(visitor.locator('#modalSavedPlans .modal-title')).to_have_text('Historial de mis planos')
            expect(visitor.locator('#savedPlansContainer .saved-plan-row')).to_have_count(1)
            own_rows = visitor.request.get(url + 'api/projects/?mine=1').json()['plans']
            assert [row['plan_id'] for row in own_rows] == [own['id']]
            # A separate owner gives the worker editing rights without deletion.
            token = next(cookie['value'] for cookie in admin.context.cookies() if cookie['name'] == 'csrftoken')
            foreign_result = admin.request.post(url + 'api/plans/import/', multipart={
                'file': {'name': 'plano-ajeno.dxf', 'mimeType': 'application/dxf', 'buffer': (directory / 'motores_demo.dxf').read_bytes()}},
                headers={'X-CSRFToken': token})
            assert foreign_result.status == 201, foreign_result.text()
            foreign = foreign_result.json()
            foreign_detail = visitor.request.get(url.rstrip('/') + foreign['detail_url']).json()
            assert foreign_detail['permissions']['can_assign'] and not foreign_detail['permissions']['can_delete_plan']
            assert post(visitor, foreign['delete_url'], {}).status == 403
            assert post(admin, foreign['save_project_url'], {}).status == 200
            assert [row['plan_id'] for row in visitor.request.get(url + 'api/projects/?mine=1').json()['plans']] == [own['id']]
            visitor.locator('#modalSavedPlans [data-bs-dismiss="modal"]').first.click()
            visitor.locator('#userMenuBtn').click()
            visitor.locator('#menuItemPlanos').click()
            expect(visitor.locator('#modalSavedPlans .modal-title')).to_have_text('Planos guardados')
            expect(visitor.locator('#savedDeviceFilter')).to_be_visible()
            expect(visitor.locator('#savedPlansContainer .saved-plan-row')).to_have_count(2)
            visitor.locator('#modalSavedPlans [data-bs-dismiss="modal"]').first.click()
            print('PASS: worker can create categories/plans and view own history, without deleting categories or other owners plans', flush=True)

            # Granting administration requires a deliberate confirmation and a new login.
            select_user(admin, 'Camila')
            admin.locator('#userRole').select_option('administrator')
            admin.locator('#userForm button[type="submit"]').click()
            expect(admin.locator('#managementConfirm')).to_be_visible()
            admin.locator('#managementConfirm [value="cancel"]').click()
            assert next(user for user in management(admin)['users'] if user['id'] == changed['user']['id'])['role'] == 'worker'
            changed = submit_role(admin, 'administrator', confirm=True)
            assert changed['requires_login']
            visitor.reload()
            expect(visitor.locator('#loginForm')).to_be_visible()
            login(visitor, '12345678-5')
            expect(visitor.locator('#btnAdminister')).to_be_visible()
            assert visitor.evaluate('appState.user.permissions.can_administer')

            admin.set_viewport_size({'width': 390, 'height': 844})
            for tab in ['requests', 'users', 'categories']:
                admin.locator(f'[data-tab="{tab}"]').click()
                assert admin.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), tab
            admin.evaluate('window.scrollTo(0,0)')
            admin.screenshot(path=str(ROOT / '.test-artifacts/registration-admin-mobile.png'), full_page=True)
            admin.locator('[data-tab="users"]').click()
            admin.locator('#userSearch').fill('Camila')
            protected = admin.locator('#userRows .management-row')
            expect(protected).to_have_count(1)
            expect(protected.get_by_role('button', name='Editar', exact=True)).to_have_count(1)
            visitor.locator('#btnAdminister').click()
            expect(visitor).to_have_url(url + 'administracion/')
            visitor.locator('[data-tab="users"]').click()
            visitor.locator('#userSearch').fill('admin')
            expect(visitor.locator('#userRows .management-row')).to_have_count(1)
            expect(visitor.locator('#userRows .management-row').get_by_role('button', name='Editar', exact=True)).to_have_count(0)
            primary_id = next(user['id'] for user in management(visitor)['users'] if user['primary_administrator'])
            assert post(visitor, '/api/administration/users/', {'id': primary_id, 'first_name': 'Cambio'}).status == 403
            assert post(visitor, '/api/administration/users/', {'id': primary_id, 'action': 'delete'}).status == 403
            worker_id = next(user['id'] for user in management(visitor)['users'] if user['username'] == 'camilo')
            select_user(visitor, 'camilo')
            expect(visitor.locator('#userRole option[value="administrator"]')).to_be_disabled()
            assert post(visitor, '/api/administration/users/', {'id': worker_id, 'role': 'administrator'}).status == 403
            select_user(visitor, 'Camila')
            expect(visitor.locator('#deleteUser')).to_be_hidden()
            visitor.locator('#userLastName').fill('Fuentes Soto')
            with visitor.expect_response('**/api/administration/users/') as own_change:
                visitor.locator('#userForm button[type="submit"]').click()
            assert own_change.value.status == 200, own_change.value.text()
            expect(visitor.locator('#userRows')).to_contain_text('Camila Fuentes Soto')

            # Only the primary account can modify and delete another administrator.
            select_user(admin, 'Camila')
            admin.locator('#userLastName').fill('Fuentes Ochoa')
            with admin.expect_response('**/api/administration/users/') as admin_change:
                admin.locator('#userForm button[type="submit"]').click()
            assert admin_change.value.status == 200, admin_change.value.text()
            admin.locator('#deleteUser').click()
            expect(admin.locator('#managementConfirm')).to_be_visible()
            admin.locator('#managementConfirm [value="cancel"]').click()
            assert any(user['id'] == changed['user']['id'] for user in management(admin)['users'])
            admin.locator('#deleteUser').click()
            with admin.expect_response('**/api/administration/users/') as admin_deleted:
                admin.locator('#confirmManagementAction').click()
            assert admin_deleted.value.status == 200
            assert not any(user['id'] == changed['user']['id'] for user in management(admin)['users'])

            # Accounts without administrator roles still support confirmed deletion.
            target_id = next(user['id'] for user in management(admin)['users'] if user['username'] == 'camilo')
            select_user(admin, 'camilo')
            admin.locator('#deleteUser').click()
            expect(admin.locator('#managementConfirm')).to_be_visible()
            admin.locator('#managementConfirm [value="cancel"]').click()
            assert any(user['id'] == target_id for user in management(admin)['users'])
            admin.locator('#deleteUser').click()
            with admin.expect_response('**/api/administration/users/') as deleted:
                admin.locator('#confirmManagementAction').click()
            assert deleted.value.status == 200
            assert not any(user['id'] == target_id for user in management(admin)['users'])
            assert not errors, errors
            print('PASS: only admin can grant administrator roles and edit/delete other administrators; regular administrators retain self editing', flush=True)
        finally:
            browser.close()


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts, prefix='registration-') as temporary:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        environment = os.environ.copy()
        environment.update(REGISTRATION_TEST_DIR=temporary, REGISTRATION_TEST_PORT=str(port), PYTHONUNBUFFERED='1', APP_TERMINAL_DEBUG='0')
        with (artifacts / 'registration-server.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen([sys.executable, __file__, '--serve'], cwd=ROOT, env=environment,
                                      stdout=output, stderr=subprocess.STDOUT,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                url = f'http://127.0.0.1:{port}/'
                for _ in range(150):
                    if server.poll() is not None:
                        raise RuntimeError('Test server failed; see .test-artifacts/registration-server.log')
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
