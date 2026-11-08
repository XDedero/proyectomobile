"""Validated project display settings shared by both viewers and portable saves."""
import math
from django.core.exceptions import ValidationError


def clean_display(value):
    if not isinstance(value, dict):
        raise ValidationError('La apariencia del plano no es válida.')
    opacity = value.get('opacity', 1)
    if type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValidationError('La opacidad debe estar entre 0 y 100 %.')
    result = {'opacity': opacity}
    for key, default in [('legend', False), ('hover', True)]:
        flag = value.get(key, default)
        if type(flag) is not bool:
            raise ValidationError('La opción de apariencia no es válida.')
        result[key] = flag
    return result


def clean_shape(value):
    if value not in ('ellipse', 'rectangle'):
        raise ValidationError('El área debe ser circular o rectangular.')
    return value
