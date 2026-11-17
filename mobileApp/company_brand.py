"""Branding for the currently supported company."""
import io
import re
from functools import lru_cache
from django.core.exceptions import ValidationError
from django.db.models import BooleanField, ExpressionWrapper, Q

from django.http import Http404, HttpResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe
from PIL import Image

from .models import CompanyBrand, RELONCAVI_COMPANY_ID


@lru_cache(maxsize=64)
def palette(color):
    channels = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    def mixed(target, amount):
        return tuple(round(value * (1 - amount) + target * amount) for value in channels)

    def hex_color(values):
        return '#' + ''.join(f'{value:02x}' for value in values)

    def linear(value):
        value /= 255
        return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4

    for step in range(101):
        primary = mixed(0, step / 100)
        luminance = sum(weight * linear(value) for weight, value in zip((.2126, .7152, .0722), primary))
        if 1.05 / (luminance + .05) >= 4.5:
            break
    return {
        'primary': hex_color(primary),
        'primary_rgb': ', '.join(map(str, primary)),
        'hover': hex_color(tuple(round(value * .8) for value in primary)),
        'background': hex_color(mixed(255, .88)),
        'header': hex_color(mixed(255, .79)),
    }


def brand_context():
    brand = (CompanyBrand.objects.filter(company_id=RELONCAVI_COMPANY_ID)
             .select_related('company').defer('logo_png')
             .annotate(has_logo=ExpressionWrapper(~Q(logo_png=b''), output_field=BooleanField())).first())
    if not brand:
        return {}
    valid_color = re.fullmatch(r'#[0-9a-fA-F]{6}', brand.base_color)
    return {
        'company_name': brand.company.name,
        'company_logo': f"{reverse('company_logo')}?v={brand.revision}" if brand.has_logo else '',
        'company_logo_revision': brand.revision if brand.has_logo else None,
        'company_palette': palette(brand.base_color) if valid_color else None,
    }


def normalize_logo(upload):
    if upload.size > 5 * 1024 * 1024:
        raise ValidationError('El logo debe pesar hasta 5 MB.')
    try:
        with Image.open(upload) as image:
            if image.format not in ('PNG', 'JPEG', 'WEBP') or max(image.size) > 4096:
                raise ValueError
            image.thumbnail((512, 512))
            output = io.BytesIO()
            image.convert('RGBA').save(output, format='PNG', optimize=True)
            return output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError('Seleccione un PNG, JPEG o WebP de hasta 4096 píxeles por lado.') from exc


@require_safe
@never_cache
def company_logo(request, size=None):
    data = CompanyBrand.objects.filter(company_id=RELONCAVI_COMPANY_ID).values_list('logo_png', flat=True).first()
    if not data or not bytes(data).startswith(b'\x89PNG\r\n\x1a\n'):
        raise Http404
    if size is not None:
        if size not in (180, 192, 512):
            raise Http404
        try:
            with Image.open(io.BytesIO(data)) as image:
                image = image.convert('RGBA')
                bound = round(size * .65)
                image.thumbnail((bound, bound))
                icon = Image.new('RGB', (size, size), 'white')
                icon.paste(image, ((size - image.width) // 2, (size - image.height) // 2), image)
                buffer = io.BytesIO()
                icon.save(buffer, format='PNG')
                data = buffer.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise Http404 from exc
    return HttpResponse(bytes(data), content_type='image/png', headers={'X-Content-Type-Options': 'nosniff'})
