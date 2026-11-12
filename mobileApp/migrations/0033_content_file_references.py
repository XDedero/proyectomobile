from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0032_remove_category_workbook')]
    operations = [
        migrations.AddField(model_name=name, name='content_file',
                            field=models.CharField(max_length=255, default='', editable=False))
        for name in ('workbookdocument', 'savedproject')
    ]
