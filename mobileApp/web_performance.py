"""Reduce transfer size without buffering downloads or caching private APIs."""
from django.http import JsonResponse
from django.middleware.gzip import GZipMiddleware
from django.views.decorators.http import require_GET


class CompressTextMiddleware(GZipMiddleware):
    def process_response(self, request, response):
        content_type = response.get('Content-Type', '').split(';', 1)[0].strip()
        if request.path.startswith('/api/auth/') or content_type not in {
            'text/html', 'text/css', 'text/javascript', 'application/javascript',
            'application/json', 'application/manifest+json', 'image/svg+xml',
        }:
            return response
        return super().process_response(request, response)


@require_GET
def health(request):
    return JsonResponse({'status': 'ok'})
