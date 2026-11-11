from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('mobileApp', '0031_shared_registration'),
    ]

    operations = [
        migrations.DeleteModel(name='CategoryWorkbook'),
    ]
