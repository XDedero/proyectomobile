from django.db import migrations, models

from mobileApp.file_content import FileContentField, read_content


def verify_copies(apps, schema_editor):
    for name in ('WorkbookDocument', 'SavedProject'):
        model = apps.get_model('mobileApp', name)
        for row in model.objects.using(schema_editor.connection.alias).iterator(chunk_size=1):
            if read_content(row.content_file) != bytes(row.content or b''):
                raise OSError('El contenido cambió durante la migración. Se conservan los binarios; detenga las escrituras antes de reintentar.')


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0034_copy_binary_files')]
    operations = [
        migrations.RunPython(verify_copies, migrations.RunPython.noop),
        # On rollback recreate an empty column before 0034 restores each file.
        migrations.AlterField(model_name='workbookdocument', name='content', field=models.BinaryField(default=bytes)),
        migrations.RemoveField(model_name='workbookdocument', name='content'),
        migrations.RemoveField(model_name='savedproject', name='content'),
        migrations.RenameField(model_name='workbookdocument', old_name='content_file', new_name='content'),
        migrations.RenameField(model_name='savedproject', old_name='content_file', new_name='content'),
        migrations.AlterField(model_name='workbookdocument', name='content', field=FileContentField(extension='xlsx')),
        migrations.AlterField(model_name='savedproject', name='content', field=FileContentField(extension='planproj')),
    ]
