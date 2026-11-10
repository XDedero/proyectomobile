from django.db import migrations


def remove_resolved_requests(apps, schema_editor):
    Request = apps.get_model('mobileApp', 'RegistrationRequest')
    Request.objects.using(schema_editor.connection.alias).exclude(status='pending').delete()


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0028_registration_accounts')]

    operations = [migrations.RunPython(remove_resolved_requests, migrations.RunPython.noop)]
