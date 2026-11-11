from uuid import uuid4

from django.db import migrations, models


def request_ids(apps, schema_editor):
    Request = apps.get_model('mobileApp', 'RegistrationRequest')
    for item in Request.objects.using(schema_editor.connection.alias).all().iterator():
        item.cloud_id = uuid4()
        item.save(update_fields=['cloud_id'])


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0030_primary_administrator')]
    operations = [
        migrations.AddField('userprofile', 'cloud_id',
                            models.UUIDField(null=True, blank=True, unique=True, editable=False)),
        migrations.AddField('userprofile', 'cloud_revision',
                            models.PositiveBigIntegerField(default=0, editable=False)),
        migrations.AddField('registrationrequest', 'cloud_id',
                            models.UUIDField(null=True, editable=False)),
        migrations.RunPython(request_ids, migrations.RunPython.noop),
        migrations.AlterField('registrationrequest', 'cloud_id',
                              models.UUIDField(default=uuid4, unique=True, editable=False)),
        migrations.AddField('registrationrequest', 'cloud_synced',
                            models.BooleanField(default=False, editable=False)),
    ]
