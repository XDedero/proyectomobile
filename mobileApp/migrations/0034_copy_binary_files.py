"""Resumable copy: retain the BLOB until every durable file is verified."""
from django.db import migrations

from mobileApp.file_content import read_content, write_content


def copy_files(apps, schema_editor):
    alias = schema_editor.connection.alias
    for model_name, extension in (('WorkbookDocument', 'xlsx'), ('SavedProject', 'planproj')):
        model = apps.get_model('mobileApp', model_name)
        # One file at a time: do not collect all BLOBs in the migration process.
        last_pk = 0
        while True:
            row = model.objects.using(alias).filter(pk__gt=last_pk).order_by('pk').first()
            if row is None:
                break
            original = bytes(row.content or b'')
            name = row.content_file
            if name:
                if read_content(name) != original:
                    raise OSError(f'La copia de {model_name} #{row.pk} no coincide. Se conserva el binario.')
            elif original:
                name = write_content(original, extension)
                if read_content(name) != original:
                    raise OSError(f'No se pudo verificar {model_name} #{row.pk}. Se conserva el binario.')
                model.objects.using(alias).filter(pk=row.pk).update(content_file=name)
            last_pk = row.pk


def restore_blobs(apps, schema_editor):
    alias = schema_editor.connection.alias
    for model_name in ('WorkbookDocument', 'SavedProject'):
        model = apps.get_model('mobileApp', model_name)
        for row in model.objects.using(alias).only('pk', 'content_file').iterator(chunk_size=1):
            model.objects.using(alias).filter(pk=row.pk).update(content=read_content(row.content_file))


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('mobileApp', '0033_content_file_references')]
    operations = [migrations.RunPython(copy_files, restore_blobs)]
