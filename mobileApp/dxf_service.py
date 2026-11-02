"""Read DXF modelspace, locate motor names and render their world positions in SVG."""

import math
import re
import tempfile
from pathlib import Path
from xml.etree import ElementTree

import ezdxf
from django.conf import settings
from django.core.exceptions import ValidationError
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration, ImagePolicy
from ezdxf.math import BoundingBox2d, Matrix44, Vec3

MAX_ENTITIES = 100_000
MAX_MOTORS = 5_000
MAX_DEPTH = 48


def find_motors(doc):
    """Find motor blocks, attributes and visible TEXT/MTEXT labels in world space.

    Unused definitions are not equipment instances. Each path identifies one
    occurrence even when several INSERTs share the same block definition. A
    motor block's own labels identify that same motor rather than extra motors.
    """
    motors = []
    visited = 0
    keyword = settings.DXF_MOTOR_KEYWORD.casefold()
    if not keyword:
        raise ValidationError('La palabra clave de motores no puede estar vacía.')
    # Underscores and numeric suffixes are common in CAD equipment identifiers.
    # A whole label word avoids interpreting e.g. "automotor" as "motor".
    label_pattern = re.compile(rf'(?<![^\W_]){re.escape(keyword)}(?![^\W_\d])', re.IGNORECASE)
    cache = bbox.Cache()
    bounds_pending = []

    def add_motor(key, name, position, entity, parent):
        if not all(math.isfinite(value) for value in position):
            raise ValidationError('El plano contiene coordenadas no válidas.')
        if len(motors) >= MAX_MOTORS:
            raise ValidationError('El plano supera el límite de 5.000 motores.')
        motor = {
            'entity_key': key, 'name': ' '.join(name.split())[:255],
            'x': position.x, 'y': position.y, 'z': position.z,
        }
        motors.append(motor)
        bounds_pending.append((motor, entity, parent, position))

    def walk(entities, parent, path, ancestors, motor_owner=False):
        nonlocal visited
        if len(ancestors) > MAX_DEPTH:
            raise ValidationError('El plano contiene demasiados niveles de bloques anidados.')
        for entity in entities:
            visited += 1
            if visited > MAX_ENTITIES:
                raise ValidationError('El plano supera el límite de 100.000 entidades expandidas.')
            kind = entity.dxftype()
            if kind in ('TEXT', 'MTEXT', 'ATTDEF'):
                text = entity.plain_text()
                if not motor_owner and label_pattern.search(text):
                    if kind == 'MTEXT':
                        # MTEXT insertion is already WCS; TEXT alignment is OCS.
                        position = Vec3(entity.dxf.insert)
                    else:
                        _, position, _ = entity.get_placement()
                        position = entity.ocs().to_wcs(position)
                    key = f'{path}/{entity.dxf.handle or kind}:text'
                    add_motor(key, text, parent.transform(position), entity, parent)
                continue
            if kind != 'INSERT':
                continue
            name = entity.dxf.name
            if name.casefold() in ancestors:
                raise ValidationError('El plano contiene referencias circulares entre bloques.')
            if entity.mcount > MAX_ENTITIES:
                raise ValidationError('El plano contiene una matriz de bloques demasiado grande.')
            instances = entity.multi_insert() if entity.mcount > 1 else [entity]
            for index, instance in enumerate(instances):
                visited += 1
                if visited > MAX_ENTITIES:
                    raise ValidationError('El plano supera el límite de 100.000 entidades expandidas.')
                key = f'{path}/{entity.dxf.handle or name}:{index}'
                position = parent.transform(instance.ocs().to_wcs(instance.dxf.insert))
                if not all(math.isfinite(value) for value in position):
                    raise ValidationError('El plano contiene coordenadas no válidas.')
                attribute_label = None
                for attribute in instance.attribs:
                    visited += 1
                    if visited > MAX_ENTITIES:
                        raise ValidationError('El plano supera el límite de 100.000 entidades expandidas.')
                    value = attribute.plain_mtext() if attribute.has_embedded_mtext_entity else attribute.plain_text()
                    if attribute_label is None and (label_pattern.search(value) or label_pattern.search(attribute.dxf.tag)):
                        attribute_label = value or name
                is_motor = keyword in name.casefold() or attribute_label is not None
                if is_motor:
                    add_motor(key, name if keyword in name.casefold() else attribute_label,
                              position, instance, parent)
                block = instance.block()
                if block is not None:
                    walk(block, instance.matrix44() @ parent, key,
                         ancestors | {name.casefold()}, motor_owner or is_motor)

    walk(doc.modelspace(), Matrix44(), '', set())
    # Validate recursion/expansion first, before asking the renderer for extents.
    for motor, instance, parent, position in bounds_pending:
        local_box = bbox.extents([instance], cache=cache)
        motor['_bounds'] = BoundingBox2d(
            parent.transform(point) for point in local_box.cube_vertices()
        ) if local_box.has_data else BoundingBox2d([position])
    return motors


def import_dxf(upload):
    suffix = Path(upload.name).suffix.lower()
    if suffix != '.dxf':
        raise ValidationError('Solo se admiten archivos .dxf.')
    if not upload.size or upload.size > settings.PLAN_UPLOAD_MAX_BYTES:
        raise ValidationError('El plano debe tener contenido y pesar como máximo 20 MB.')
    with tempfile.TemporaryDirectory(prefix='mobile-plan-') as directory:
        filename = Path(directory) / f'drawing{suffix}'
        with filename.open('wb') as target:
            for chunk in upload.chunks():
                target.write(chunk)

        try:
            doc = ezdxf.readfile(filename)
            motors = find_motors(doc)
            backend = svg.SVGBackend()
            config = Configuration(
                background_policy=BackgroundPolicy.CUSTOM, custom_bg_color='#0f172a',
                image_policy=ImagePolicy.IGNORE,
            )
            Frontend(RenderContext(doc), backend, config=config).draw_layout(doc.modelspace())
            render_box = backend.player().bbox()
            render_box.extend((motor['x'], motor['y']) for motor in motors)
            if not render_box.has_data:
                raise ValidationError('El plano no contiene geometría visible en el espacio modelo.')
            padding = max(render_box.size.x, render_box.size.y, 1e-6) * 0.05
            render_box = BoundingBox2d([
                (render_box.extmin.x - padding, render_box.extmin.y - padding),
                (render_box.extmax.x + padding, render_box.extmax.y + padding),
            ])
            root = backend.get_xml_root_element(
                layout.Page(300, 220, layout.Units.mm), render_box=render_box
            )
            transform = backend.transformation_matrix
            if transform is None:
                raise ValidationError('No se pudo generar la vista del plano.')
            origin = transform.transform(Vec3())
            axis_x = transform.transform(Vec3(1, 0, 0)) - origin
            axis_y = transform.transform(Vec3(0, 1, 0)) - origin
            root.set('data-wcs-transform', ' '.join(str(value) for value in (
                axis_x.x, axis_x.y, axis_y.x, axis_y.y, origin.x, origin.y,
            )))
            for motor in motors:
                point = transform.transform(Vec3(motor['x'], motor['y'], motor['z']))
                motor['svg_x'], motor['svg_y'] = point.x, point.y
                bounds = motor.pop('_bounds')
                screen_box = BoundingBox2d(transform.transform_vertices([
                    (bounds.extmin.x, bounds.extmin.y, 0),
                    (bounds.extmax.x, bounds.extmax.y, 0),
                ]))
                motor['svg_bounds'] = [
                    screen_box.extmin.x, screen_box.extmin.y,
                    screen_box.size.x, screen_box.size.y,
                ]
            return ElementTree.tostring(root, encoding='unicode'), motors
        except ValidationError:
            raise
        except (ezdxf.DXFError, OSError, UnicodeError, ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            raise ValidationError('El archivo no es un plano DXF válido o contiene geometría dañada.') from exc
        finally:
            upload.seek(0)
