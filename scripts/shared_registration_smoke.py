"""Verify two independent SQLite installations against the configured shared registry.

Creates only uniquely identified test records and removes them in finally.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def step(operation, directory, state_path):
    os.environ.update(PLANOS_DATA_DIR=str(directory), APP_SHARED_ACCOUNTS='1', APP_TERMINAL_DEBUG='0',
                      DJANGO_SETTINGS_MODULE='proyectomobile.settings')
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db.models import Max
    from django.test import Client
    from mobileApp.models import RegistrationRequest, UserProfile

    state = json.loads(state_path.read_text(encoding='utf-8'))
    if not (directory / 'db.sqlite3').exists():
        call_command('migrate', verbosity=0)
        for index in range(3 if directory.name == 'second' else 0):
            get_user_model().objects.create_user(f'offset-{index}')
        get_user_model().objects.create_superuser('test-shared-admin', password=state['password'])
    client = Client()
    if operation != 'login':
        client.force_login(get_user_model().objects.get(username='test-shared-admin'))
    if operation == 'submit':
        try:
            response = client.post('/api/auth/register/', {
                'first_name': 'Prueba', 'last_name': 'Sincronización', 'rut': state['rut'],
                'password': state['password'], 'confirm_password': state['password'],
            }, content_type='application/json')
        finally:
            # Capture the UUID even if the response failed after committing remotely.
            # Cleanup is restricted to these test identities, never another RUT's records.
            identifiers = state.setdefault('cloud_ids', [])
            for identifier in RegistrationRequest.objects.filter(rut=state['rut']).values_list('cloud_id', flat=True):
                if str(identifier) not in identifiers:
                    identifiers.append(str(identifier))
            state_path.write_text(json.dumps(state), encoding='utf-8')
        assert response.status_code == 201, (response.status_code, response.content)
        item = RegistrationRequest.objects.get(pk=response.json()['id'])
        assert str(item.cloud_id) != state.get('cloud_id'), 'A resubmission must use a new UUID'
        state.update(cloud_id=str(item.cloud_id), first_id=item.pk)
        state_path.write_text(json.dumps(state), encoding='utf-8')
    elif operation in ('approve', 'reject'):
        response = client.get('/api/administration/requests/pending/')
        assert response.status_code == 200, (response.status_code, response.content)
        item = RegistrationRequest.objects.get(cloud_id=state['cloud_id'])
        # Deliberately make local request IDs differ between the installations.
        next_id = RegistrationRequest.objects.aggregate(maximum=Max('pk'))['maximum'] + 100
        RegistrationRequest.objects.filter(pk=item.pk).update(id=next_id)
        item = RegistrationRequest.objects.get(cloud_id=state['cloud_id'])
        assert item.pk != state['first_id']
        assert 'password' not in response.content.decode()
        response = client.post('/api/administration/requests/', {
            'id': item.pk, 'action': operation, 'role': 'worker',
        }, content_type='application/json')
        assert response.status_code == 200, (response.status_code, response.content)
        if operation == 'approve':
            assert UserProfile.objects.get(cloud_id=state['cloud_id']).role == 'worker'
        else:
            assert not UserProfile.objects.filter(cloud_id=state['cloud_id']).exists()
        assert not RegistrationRequest.objects.filter(cloud_id=state['cloud_id']).exists()
    elif operation == 'login':
        response = client.post('/api/auth/login/', {
            'username': state['rut'], 'password': state['password'],
        }, content_type='application/json')
        assert response.status_code == 200, (response.status_code, response.content)
        assert response.json()['user']['role'] == state.get('expected_role', 'worker')
    elif operation == 'role':
        user_id = UserProfile.objects.get(cloud_id=state['cloud_id']).user_id
        response = client.post('/api/administration/users/', {
            'id': user_id, 'role': 'viewer',
        }, content_type='application/json')
        assert response.status_code == 200, (response.status_code, response.content)
        state['expected_role'] = 'viewer'
        state_path.write_text(json.dumps(state), encoding='utf-8')
    print(f'PASS {operation}', flush=True)


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shared-registration-', dir=artifacts) as temporary:
        directory = Path(temporary)
        first, second = directory / 'first', directory / 'second'
        first.mkdir()
        second.mkdir()
        body = str(80000000 + uuid4().int % 9999999)
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
        import django
        django.setup()
        from mobileApp.accounts import rut_check_digit
        state_path = directory / 'fixture.json'
        state_path.write_text(json.dumps({
            'rut': body + '-' + rut_check_digit(body), 'password': 'Registro!' + uuid4().hex,
        }), encoding='utf-8')
        try:
            for operation, installation in [('submit', first), ('reject', second), ('submit', first),
                                             ('approve', second), ('login', first),
                                             ('role', second), ('login', first)]:
                subprocess.run([sys.executable, __file__, '--step', operation, str(installation), str(state_path)],
                               cwd=ROOT, check=True)
        finally:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if state.get('cloud_ids'):
                os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
                import django
                django.setup()
                from mobileApp.cloud_projects import supabase_query
                from uuid import UUID
                identifiers = ','.join("'" + str(UUID(value)) + "'" for value in state['cloud_ids'])
                supabase_query(
                    f"BEGIN; DELETE FROM app_identity.registration_requests WHERE id IN ({identifiers}); "
                    f"DELETE FROM app_identity.accounts WHERE id IN ({identifiers}); COMMIT;", read_only=False)
                print('PASS removed test registry records', flush=True)


if __name__ == '__main__':
    if '--step' in sys.argv:
        step(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        main()
