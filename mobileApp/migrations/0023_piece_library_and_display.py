from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0022_savedproject_cloud_progress')]
    operations = [
        migrations.AlterModelTable(name='savedmotor', table='mobileApp_savedmotor'),
        migrations.RenameModel(old_name='SavedMotor', new_name='SavedPiece'),
        migrations.AddField(model_name='savedpiece', name='category', field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, to='mobileApp.buttoncategory')),
        migrations.AddField(model_name='savedpiece', name='cloud_sheet', field=models.CharField(max_length=31, blank=True)),
        migrations.AddField(model_name='savedpiece', name='cloud_status', field=models.CharField(max_length=16, default='local')),
        migrations.AddField(model_name='savedproject', name='cloud_receipt', field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='plan', name='display_settings', field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='pdfplan', name='display_settings', field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='pdfmarker', name='area_shape', field=models.CharField(max_length=12, default='ellipse')),
        migrations.AddField(model_name='motor', name='area_shape', field=models.CharField(max_length=12, default='ellipse')),
    ]
