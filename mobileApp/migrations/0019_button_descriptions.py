from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0018_pdfplan_cloud_workbook_version')]
    operations = [
        operation for name in ('pdfmarker', 'motor') for operation in (
            migrations.AddField(model_name=name, name='descriptions', field=models.JSONField(blank=True, null=True)),
            migrations.AddField(model_name=name, name='button_color', field=models.CharField(blank=True, max_length=7)),
            migrations.AddField(model_name=name, name='show_description', field=models.BooleanField(default=False)),
        )
    ]
