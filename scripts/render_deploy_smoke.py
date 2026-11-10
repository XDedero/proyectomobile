"""Exercise production middleware and collected assets without touching real data."""
import gzip
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    artifacts = ROOT / '.test-artifacts'
    artifacts.mkdir(exist_ok=True)
    with ExitStack() as cleanup:
        temporary = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='render-deploy-', dir=artifacts))
        directory = Path(temporary)
        assert directory.resolve().is_relative_to(artifacts.resolve())
        os.environ.update({
            'DJANGO_SETTINGS_MODULE': 'proyectomobile.settings', 'DJANGO_DEBUG': 'false',
            'DJANGO_SECRET_KEY': secrets.token_urlsafe(64), 'RENDER': 'true',
            'RENDER_EXTERNAL_HOSTNAME': 'reloncavi-test.onrender.com',
            'PLANOS_DATA_DIR': str(directory),
        })
        # Disable external cloud work, just as in the isolated Django test suite.
        sys.argv.append('test')
        import django
        django.setup()
        from django.conf import settings
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        from django.db import connections
        from django.test import Client
        from mobileApp.test_pdf import pdf_upload
        cleanup.callback(connections.close_all)

        settings.STATIC_ROOT = directory / 'static'
        call_command('migrate', verbosity=0)
        call_command('collectstatic', interactive=False, verbosity=0)
        client = Client(HTTP_HOST='reloncavi-test.onrender.com')
        headers = {'HTTP_X_FORWARDED_PROTO': 'https', 'HTTP_ACCEPT_ENCODING': 'gzip'}
        assert client.get('/healthz/').status_code == 200
        assert client.get('/').status_code == 301
        page = client.get('/', **headers)
        assert page.status_code == 200 and page['Content-Encoding'] == 'gzip'
        assert b'apple-touch-icon' in gzip.decompress(page.content)
        assert page.cookies['csrftoken']['secure']
        worker = client.get('/service-worker.js', **headers)
        assert worker.status_code == 200 and 'no-store' in worker['Cache-Control']
        assert worker['Service-Worker-Allowed'] == '/'
        manifest = client.get('/manifest.webmanifest', **headers)
        manifest_bytes = gzip.decompress(manifest.content) if manifest.has_header('Content-Encoding') else manifest.content
        assert json.loads(manifest_bytes)['display'] == 'standalone'
        print('PASS: production HTTPS, automatic Render hostname, health, secure cookies and PWA routes', flush=True)

        static_path = 'mobileApp/vendor/pdfjs/pdf.worker.mjs'
        response = client.get('/static/' + static_path + '?v=smoke', **headers)
        assert response.status_code == 200 and response['Content-Encoding'] == 'gzip'
        transferred = b''.join(response.streaming_content) if response.streaming else response.content
        response.close()
        original = (ROOT / 'mobileApp/static' / static_path).read_bytes()
        assert hashlib.sha256(gzip.decompress(transferred)).digest() == hashlib.sha256(original).digest()
        print(f'PASS: WhiteNoise serves identical PDF worker, {len(original)} -> {len(transferred)} transfer bytes', flush=True)

        user = get_user_model().objects.create_user('render-smoke')
        client.force_login(user)
        assert 'Content-Encoding' not in client.get('/api/auth/session/', **headers)
        upload = pdf_upload()
        source = upload.read()
        upload.seek(0)
        imported = client.post('/api/pdf/plans/import/', {'file': upload}, **headers)
        assert imported.status_code == 201, imported.content[:200]
        data = json.loads(gzip.decompress(imported.content) if imported.has_header('Content-Encoding') else imported.content)
        pdf = client.get(data['source_url'], **headers)
        assert pdf.status_code == 200 and 'Content-Encoding' not in pdf
        assert b''.join(pdf.streaming_content) == source
        pdf.close()
        print('PASS: authenticated JSON and original PDF download retain their behavior in production', flush=True)


if __name__ == '__main__':
    main()
