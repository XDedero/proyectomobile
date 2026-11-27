"""Validate and transport the maintenance schedule stored on a plan button."""
from calendar import monthrange
from datetime import date, timedelta
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import MaintenanceUnit


def next_date(last, interval, unit):
    if unit == MaintenanceUnit.DAYS:
        return last + timedelta(days=interval)
    if unit == MaintenanceUnit.WEEKS:
        return last + timedelta(weeks=interval)
    months = interval * 12 if unit == MaintenanceUnit.YEARS else interval
    year, month = divmod(last.year * 12 + last.month - 1 + months, 12)
    month += 1
    return date(year, month, min(last.day, monthrange(year, month)[1]))


def clean_schedule(value):
    if value is None:
        return dict(last_maintenance=None, maintenance_interval=None, maintenance_unit='', next_maintenance=None)
    if not isinstance(value, dict):
        raise ValidationError('La configuración de mantención no es válida.')
    raw_date = value.get('last_date')
    try:
        last = date.fromisoformat(raw_date)
        if last.isoformat() != raw_date or last > timezone.localdate():
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValidationError('Ingresa una fecha válida de última mantención que no sea futura.') from exc
    interval, unit = value.get('interval'), value.get('unit')
    if type(interval) is not int or not 1 <= interval <= 999 or unit not in MaintenanceUnit.values:
        raise ValidationError('Ingresa una frecuencia entre 1 y 999 días, semanas, meses o años.')
    try:
        due = next_date(last, interval, unit)
    except (OverflowError, ValueError) as exc:
        raise ValidationError('La fecha de próxima mantención está fuera del rango válido.') from exc
    return dict(last_maintenance=last, maintenance_interval=interval, maintenance_unit=unit, next_maintenance=due)


def require_schedule(category, due):
    if category and category.maintenance_required and due is None:
        raise ValidationError('Esta categoría requiere la fecha de última mantención y la frecuencia.')


def new_schedule(data, user, category):
    from .maintenance_assignments import assignment_changes

    values = clean_schedule(data.get('maintenance'))
    require_schedule(category, values['next_maintenance'])
    assignment = assignment_changes(data, user, creating=True)
    return {**values, 'button_creator': user.get_username(),
            'maintenance_key': uuid4() if values['next_maintenance'] else None, **assignment}


def update_schedule(button, value, user, *, category):
    values = clean_schedule(value)
    require_schedule(category, values['next_maintenance'])
    changes = {key: field_value for key, field_value in values.items()
               if getattr(button, key) != field_value}
    if not changes:
        return changes
    if value is not None:
        if button.maintenance_key is None:
            changes['maintenance_key'] = uuid4()
        if not button.button_creator:
            plan = button.plan.draft_of if button.plan.draft_of_id else button.plan
            owner = plan.owner_username or (plan.imported_by.get_username() if plan.imported_by_id else '')
            changes['button_creator'] = owner or user.get_username()
    return changes


def schedule_data(button):
    if button.last_maintenance is None:
        return None
    overdue = button.next_maintenance < timezone.localdate()
    return {
        'last_date': button.last_maintenance.isoformat(),
        'interval': button.maintenance_interval,
        'unit': button.maintenance_unit,
        'next_date': None if overdue else button.next_maintenance.isoformat(),
        'overdue': overdue,
    }


def keep_approved_date(values, performed_on):
    """Keep an approved cycle when restoring an older snapshot or draft."""
    last = values['last_maintenance']
    if last and performed_on and last < performed_on:
        values['last_maintenance'] = performed_on
        values['next_maintenance'] = next_date(performed_on, values['maintenance_interval'], values['maintenance_unit'])


def export_schedule(button):
    result = {}
    if button.button_creator:
        result['button_creator'] = button.button_creator
    if button.maintenance_key:
        result['maintenance_key'] = str(button.maintenance_key)
    for field in ('maintenance_responsibles', 'maintenance_supervisors'):
        if getattr(button, field) is not None:
            result[field] = getattr(button, field)
    schedule = schedule_data(button)
    if schedule:
        result['maintenance'] = {name: schedule[name] for name in ('last_date', 'interval', 'unit')}
    return result


def import_schedule(item, user, *, restore):
    values = clean_schedule(item.get('maintenance'))
    if restore:
        creator = item.get('button_creator', '')
        if not isinstance(creator, str) or len(creator) > 150:
            raise ValidationError('El creador del botón no es válido.')
        key = item.get('maintenance_key')
        try:
            key = UUID(key) if key is not None else None
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError('La referencia de mantención del botón no es válida.') from exc
    else:
        creator = user.get_username()
        key = None
    if key is None and values['next_maintenance']:
        key = uuid4()
    assignments = {}
    if restore:
        for field in ('maintenance_responsibles', 'maintenance_supervisors'):
            names = item.get(field)
            if names is None:
                continue
            if (not isinstance(names, list) or (field == 'maintenance_supervisors' and len(names) > 2)
                    or any(not isinstance(name, str) or not 1 <= len(name) <= 150 for name in names)
                    or len(set(names)) != len(names)):
                raise ValidationError('La asignación de mantención del botón no es válida.')
            assignments[field] = names
    return {**values, **assignments, 'maintenance_key': key, 'button_creator': creator}
