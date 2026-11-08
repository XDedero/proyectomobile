from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0021_pdfplan_cloud_base_state')]
    operations = [
        migrations.AddField(model_name='savedproject', name='cloud_stage', field=models.CharField(default='local', max_length=24)),
        migrations.AddField(model_name='savedproject', name='cloud_progress', field=models.PositiveSmallIntegerField(default=0)),
    ]
