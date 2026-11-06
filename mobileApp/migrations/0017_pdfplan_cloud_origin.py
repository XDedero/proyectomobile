from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0016_cloud_publication')]
    operations = [
        migrations.AddField(model_name='pdfplan', name='cloud_origin', field=models.CharField(blank=True, db_index=True, max_length=128)),
    ]
