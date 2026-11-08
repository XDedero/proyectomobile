from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0020_pdfplan_owner_username_plan_owner_username_and_more')]
    operations = [migrations.AddField(model_name='pdfplan', name='cloud_base_state', field=models.JSONField(blank=True, null=True))]
