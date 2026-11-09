"""Optional regions stored as part of the drawing, including portable projects."""
import json
import math
from uuid import UUID

from django.core.exceptions import ValidationError

from .button_descriptions import clean_color

MAX_SECTORS = 200
MAX_POINTS = 5000


def clean_sectors(value, *, pages=None):
    if not isinstance(value, list) or len(value) > MAX_SECTORS:
        raise ValidationError('Un plano admite hasta 200 sectores.')
    result, identifiers, total_points = [], set(), 0
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError('El sector no es válido.')
        try:
            identifier = str(UUID(item.get('id', '')))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError('El identificador del sector no es válido.') from exc
        if identifier in identifiers:
            raise ValidationError('No se puede repetir el identificador de un sector.')
        identifiers.add(identifier)
        name = item.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValidationError('Escriba un nombre de sector de hasta 120 caracteres.')
        shape = item.get('shape')
        if shape not in ('circle', 'square', 'rectangle', 'triangle', 'brush'):
            raise ValidationError('Elija una forma válida para el sector.')
        color = clean_color(item.get('color'))
        if not color:
            raise ValidationError('Elija un color para el sector.')
        page = item.get('page', 1)
        if type(page) is not int or page < 1 or (pages is not None and page > pages):
            raise ValidationError('La página del sector no existe en este plano.')
        row = dict(id=identifier, name=name.strip(), shape=shape, color=color, page=page)
        for key, lower, upper in [('x', 0, 1000), ('y', 0, 1000), ('width', 1, 2000),
                                  ('height', 1, 2000), ('rotation', -360, 360)]:
            number = item.get(key)
            if type(number) not in (int, float) or not math.isfinite(number) or not lower <= number <= upper:
                raise ValidationError('La posición, rotación o tamaño del sector no es válido.')
            row[key] = number
        if shape == 'brush':
            size, strokes = item.get('brush_size'), item.get('strokes')
            if type(size) not in (int, float) or not math.isfinite(size) or not .001 <= size <= 2:
                raise ValidationError('El tamaño del pincel no es válido.')
            if not isinstance(strokes, list) or not strokes or len(strokes) > 500:
                raise ValidationError('Dibuje al menos un trazo para el sector.')
            for stroke in strokes:
                if not isinstance(stroke, list) or not stroke:
                    raise ValidationError('El trazo del sector no es válido.')
                total_points += len(stroke)
                if total_points > MAX_POINTS:
                    raise ValidationError('El plano supera los 5.000 puntos de pincel. Simplifique los trazos.')
                for point in stroke:
                    if not isinstance(point, list) or len(point) != 2 or any(
                        type(n) not in (int, float) or not math.isfinite(n) or not -.5 <= n <= .5 for n in point
                    ):
                        raise ValidationError('El trazo del sector contiene coordenadas no válidas.')
            row.update(brush_size=size, strokes=strokes)
        result.append(row)
    if len(json.dumps(result)) > 1024 * 1024:
        raise ValidationError('Los sectores superan el tamaño permitido.')
    return result
