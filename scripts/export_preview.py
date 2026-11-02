"""Refresh the local HTML preview from the actual Django template (no database)."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')


def main():
    import django
    django.setup()
    from django.conf import settings
    from django.test import RequestFactory
    from mobileApp.views import index

    response = index(RequestFactory().get('/'))
    html = response.content.decode('utf-8')
    html = html.replace(settings.STATIC_URL, 'mobileApp/static/')
    (ROOT / 'index.html').write_text(html, encoding='utf-8')
    print('index.html actualizado; es una vista previa, no una aplicación autónoma.')


if __name__ == '__main__':
    main()
