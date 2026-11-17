import uuid

from django.db import migrations, models
import django.db.models.deletion


RELONCAVI_ID = uuid.UUID('25c4e566-ed01-47dd-8107-a715fcd125d2')


def seed_reloncavi(apps, schema_editor):
    Company = apps.get_model('mobileApp', 'Company')
    Company.objects.using(schema_editor.connection.alias).get_or_create(
        pk=RELONCAVI_ID,
        defaults={'name': 'Reloncaví', 'slug': 'reloncavi', 'status': 'active', 'drive_ready': True},
    )


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0037_independent_edit_sessions')]

    operations = [
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('tax_id', models.CharField(blank=True, max_length=10, null=True, unique=True)),
                ('status', models.CharField(choices=[
                    ('pending_storage', 'Pendiente de almacenamiento'),
                    ('active', 'Activa'), ('suspended', 'Suspendida'), ('archived', 'Archivada'),
                ], default='pending_storage', max_length=20)),
                ('drive_ready', models.BooleanField(default=False)),
            ],
            options={
                'ordering': ['name', 'id'],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(status__in=['pending_storage', 'active', 'suspended', 'archived']),
                        name='company_valid_status',
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(status='active') | models.Q(id=RELONCAVI_ID),
                        name='company_only_legacy_active',
                    ),
                ],
            },
        ),
        migrations.RunPython(seed_reloncavi, migrations.RunPython.noop),
        migrations.AddField(
            model_name='registrationrequest',
            name='company',
            # Older Django processes may still insert requests during a rolling deploy.
            field=models.ForeignKey(default=RELONCAVI_ID, db_default=RELONCAVI_ID,
                                    on_delete=django.db.models.deletion.PROTECT,
                                    related_name='registration_requests', to='mobileApp.company'),
        ),
    ]
