"""Read legacy view settings without persisting transient viewer preferences."""
import math
from django.core.exceptions import ValidationError


def clean_display(value):
    if not isinstance(value, dict):
        raise ValidationError('La apariencia del plano no es válida.')
    opacity = value.get('opacity', 1)
    if type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValidationError('La opacidad debe estar entre 0 y 100 %.')
    for key, default in [('legend', False), ('hover', True)]:
        flag = value.get(key, default)
        if type(flag) is not bool:
            raise ValidationError('La opción de apariencia no es válida.')
    # Opacity, the retired legend and hover controls are no longer plan data.
    return {}


def clean_shape(value):
    if value not in ('ellipse', 'rectangle'):
        raise ValidationError('El área debe ser circular o rectangular.')
    return value


def clean_button_scale(value):
    if type(value) not in (float, int) or not .25 <= value <= 1.5:
        raise ValidationError('El tamaño del botón debe estar entre 25 % y 150 %.')
    return value
