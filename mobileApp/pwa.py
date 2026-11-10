"""Public PWA resources. Private API responses never enter the worker cache."""
import hashlib
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def _calculate_asset_version():
    root = Path(__file__).resolve().parent
    files = list((root / 'static' / 'mobileApp').rglob('*'))
    files.extend((root / 'templates' / 'mobileApp').glob('*.html'))
    files.append(root / 'pwa.py')
    signature = '|'.join(f'{p.relative_to(root)}:{p.stat().st_mtime_ns}:{p.stat().st_size}'
                         for p in sorted(files) if p.is_file())
    return str(int(hashlib.sha256(signature.encode()).hexdigest()[:16], 16))


@lru_cache(maxsize=1)
def _production_asset_version():
    return _calculate_asset_version()


def asset_version():
    # Release files cannot change inside a running Render instance. Development
    # still detects edits immediately, including changes to PDF.js dependencies.
    return _calculate_asset_version() if settings.DEBUG else _production_asset_version()


@require_GET
def manifest(request):
    return JsonResponse({
        'id': reverse('index'), 'name': 'Reloncaví APP', 'short_name': 'Reloncaví',
        'description': 'Planos, sectores y fichas de mantenimiento de Reloncaví.',
        'lang': 'es-CL', 'start_url': reverse('index'), 'scope': reverse('index'),
        'display': 'standalone', 'background_color': '#ffffff', 'theme_color': '#eaf4ff',
        'icons': [
            {'src': f'/static/mobileApp/img/pwa/icon-{size}.png', 'sizes': f'{size}x{size}',
             'type': 'image/png', 'purpose': purpose}
            for size, purpose in [(192, 'any'), (512, 'any'), (512, 'maskable')]
        ],
    }, content_type='application/manifest+json')


@require_GET
@never_cache
def service_worker(request):
    response = render(request, 'mobileApp/service-worker.js', {
        'asset_version': asset_version(),
    }, content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class OfflineIdentityMiddleware:
    """Bind every private snapshot to the server-verified account."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        expected = request.headers.get('X-Reloncavi-Expected-User')
        if expected and (not request.user.is_authenticated or expected != str(request.user.pk)):
            response = JsonResponse({'error': 'La sesión cambió. Inicia sesión con la cuenta del borrador.'}, status=409)
        else:
            response = self.get_response(request)
        if request.path.startswith('/api/'):
            response['X-Reloncavi-User'] = str(request.user.pk) if request.user.is_authenticated else ''
            response['Cache-Control'] = 'private, no-store'
        return response
