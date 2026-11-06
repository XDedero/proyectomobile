from django.db import migrations, models
import mobileApp.models


def preserve_existing_keys(apps, schema_editor):
    for model, kind in [('PdfPlan', 'pdf'), ('Plan', 'dxf')]:
        table = apps.get_model('mobileApp', model)
        for plan in table.objects.using(schema_editor.connection.alias).all().iterator():
            table.objects.using(schema_editor.connection.alias).filter(pk=plan.pk).update(cloud_key=f'local_{kind}_{plan.pk}')


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0015_cloud_snapshot')]
    operations = [
        migrations.AddField(model_name=model, name='cloud_key', field=models.CharField(max_length=80, null=True))
        for model in ('plan', 'pdfplan')
    ] + [
        migrations.RunPython(preserve_existing_keys, migrations.RunPython.noop),
    ] + [
        migrations.AlterField(model_name=model, name='cloud_key', field=models.CharField(default=mobileApp.models.new_cloud_key, editable=False, max_length=80, unique=True))
        for model in ('plan', 'pdfplan')
    ] + [
        migrations.AddField(model_name='savedproject', name='cloud_status', field=models.CharField(default='local', max_length=16)),
        migrations.AddField(model_name='savedproject', name='cloud_error', field=models.CharField(blank=True, max_length=500)),
    ]
