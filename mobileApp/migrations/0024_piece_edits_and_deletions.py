from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0023_piece_library_and_display')]
    operations = [
        migrations.AddField(model_name='pdfmarker', name='piece_source', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='motor', name='piece_source', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='workbookdocument', name='source_pieces', field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='savedpiece', name='cloud_version', field=models.CharField(max_length=64, blank=True)),
        migrations.CreateModel(name='PieceDeletion', fields=[
            ('id', models.UUIDField(primary_key=True, serialize=False, editable=False)),
            ('synced', models.BooleanField(default=False)),
            ('created_at', models.DateTimeField(auto_now_add=True)),
        ]),
    ]
