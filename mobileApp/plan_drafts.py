"""Private working copies; publishing remains an explicit Guardar plano action."""
from copy import deepcopy

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Motor, PdfMarker, Plan, PlanWorkbookSheet, SavedProject, WorkbookDocument


def saved_version(plan):
    target = {'cad_plan_id' if isinstance(plan, Plan) else 'pdf_plan_id': plan.pk}
    stamp = SavedProject.objects.filter(**target).values_list('saved_at', flat=True).first()
    return stamp.isoformat() if stamp else ''


def copy_document(document, memo):
    if not document:
        return None
    if document.pk in memo:
        return memo[document.pk]
    values = {}
    for field in WorkbookDocument._meta.concrete_fields:
        if field.primary_key or field.name == 'updated_at':
            continue
        if field.name == 'content':
            values[field.attname] = (document.__dict__['content'] if 'content' in document.__dict__ else
                                    WorkbookDocument.objects.filter(pk=document.pk).values_list('content', flat=True).get())
        else:
            values[field.attname] = deepcopy(getattr(document, field.attname))
    clone = WorkbookDocument.objects.create(**values)
    memo[document.pk] = clone
    sheets = []
    for sheet in document.plan_sheets.all():
        data = {f.attname: deepcopy(getattr(sheet, f.attname)) for f in sheet._meta.concrete_fields
                if not f.primary_key and f.name != 'workbook'}
        sheets.append(PlanWorkbookSheet(workbook=clone, **data))
    PlanWorkbookSheet.objects.bulk_create(sheets)
    return clone


def copy_markers(source, destination, documents, previous=None):
    model, related = (Motor, 'motors') if isinstance(source, Plan) else (PdfMarker, 'markers')
    result, retained, created, updated, pairs = {}, [], [], [], []
    existing = {marker.pk: marker for marker in getattr(destination, related).all()}
    for marker in getattr(source, related).select_related('workbook', 'source_workbook'):
        values = {f.attname: deepcopy(getattr(marker, f.attname)) for f in model._meta.concrete_fields
                  if not f.primary_key and f.name not in ('plan', 'workbook', 'source_workbook')}
        values['workbook'] = copy_document(marker.workbook, documents)
        values['source_workbook'] = copy_document(marker.source_workbook, documents)
        original_id = (previous or {}).get(str(marker.pk))
        target = existing.get(original_id)
        if target:
            for key, value in values.items():
                setattr(target, key, value)
            updated.append(target)
        else:
            target = model(plan=destination, **values)
            created.append(target)
        pairs.append((marker.pk, target))
    model.objects.bulk_create(created, batch_size=200)
    if updated:
        model.objects.bulk_update(updated, [f.name for f in model._meta.concrete_fields if not f.primary_key and f.name != 'plan'], batch_size=100)
    for source_id, target in pairs:
        result[str(source_id)] = target.pk
        retained.append(target.pk)
    getattr(destination, related).exclude(pk__in=retained).delete()
    return result


@transaction.atomic
def working_copy(original, user):
    if original.draft_of_id:
        return original
    # Lock the parent to serialize first-open requests for the same user/plan.
    original = type(original).objects.select_for_update().get(pk=original.pk)
    existing = original.drafts.filter(draft_user=user).first()
    if existing:
        return existing
    excluded = {'id', 'cloud_key', 'cloud_snapshot', 'imported_at', 'workbook', 'listing',
                'draft_of', 'draft_user', 'draft_version', 'draft_markers', 'draft_dirty'}
    values = {f.attname: deepcopy(getattr(original, f.attname)) for f in original._meta.concrete_fields
              if f.name not in excluded}
    documents = {}
    draft = type(original).objects.create(**values, draft_of=original, draft_user=user,
        draft_version=saved_version(original), workbook=copy_document(original.workbook, documents),
        listing=copy_document(original.listing, documents))
    mapping = copy_markers(original, draft, documents)
    draft.draft_markers = {str(new): int(old) for old, new in mapping.items()}
    draft.save(update_fields=['draft_markers'])
    return draft


def publish_draft(draft, user):
    """Called inside the save transaction. Recheck access and prevent lost saves."""
    from .authorization import can_edit_plan
    original = type(draft).objects.select_for_update().get(pk=draft.draft_of_id)
    if draft.draft_user_id != user.pk or not can_edit_plan(user, original):
        raise ValidationError('El permiso de edición de este plano ya no está disponible.')
    if saved_version(original) != draft.draft_version:
        raise ValidationError('Otro usuario guardó una versión más reciente. Reabra el plano antes de guardar; su borrador se conserva.', code='draft_conflict')
    documents = {}
    for field in ('name', 'sectors', 'display_settings'):
        setattr(original, field, deepcopy(getattr(draft, field)))
    original.workbook = copy_document(draft.workbook, documents)
    original.listing = copy_document(draft.listing, documents)
    original.save(update_fields=['name', 'sectors', 'display_settings', 'workbook', 'listing'])
    draft.draft_markers = copy_markers(draft, original, documents, draft.draft_markers)
    return original
