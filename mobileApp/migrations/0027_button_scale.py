from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0026_transient_plan_display')]
    operations = [
        migrations.AddField(model_name=model, name='button_scale', field=models.FloatField(default=1))
        for model in ('pdfmarker', 'motor')
    ]
