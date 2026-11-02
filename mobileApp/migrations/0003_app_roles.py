from django.db import migrations


def create_roles(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    permissions = {}
    for model, actions in [('plan', ['view']), ('motor', ['view', 'change'])]:
        content_type, _ = ContentType.objects.get_or_create(app_label='mobileApp', model=model)
        for action in actions:
            permission, _ = Permission.objects.get_or_create(
                content_type=content_type, codename=f'{action}_{model}',
                defaults={'name': f'Can {action} {model}'},
            )
            permissions[f'{action}_{model}'] = permission
    readers, _ = Group.objects.get_or_create(name='Lectores de planos')
    editors, _ = Group.objects.get_or_create(name='Editores de motores')
    readers.permissions.add(permissions['view_plan'], permissions['view_motor'])
    editors.permissions.add(*permissions.values())


class Migration(migrations.Migration):
    dependencies = [
        ('mobileApp', '0002_remove_plan_owner_session_plan_imported_by'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(create_roles, migrations.RunPython.noop)]
