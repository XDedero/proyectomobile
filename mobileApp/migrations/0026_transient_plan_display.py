from django.db import migrations


def discard_saved_view_settings(apps, schema_editor):
    for model in ('Plan', 'PdfPlan'):
        apps.get_model('mobileApp', model).objects.using(schema_editor.connection.alias).update(display_settings={})


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0025_plan_sectors')]
    operations = [migrations.RunPython(discard_saved_view_settings, migrations.RunPython.noop)]
