from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0017_pdfplan_cloud_origin')]
    operations = [
        migrations.AddField(model_name='pdfplan', name='cloud_workbook_version', field=models.CharField(blank=True, max_length=192)),
    ]
