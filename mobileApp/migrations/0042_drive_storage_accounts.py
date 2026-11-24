import django.db.models.deletion
import uuid
from django.db import migrations, models


def protect_inventory(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    from psycopg import sql
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT current_schema()')
        schema = cursor.fetchone()[0]
        table = sql.Identifier(schema, 'mobileApp_drivestorageaccount')
        cursor.execute(sql.SQL('REVOKE ALL ON TABLE {} FROM PUBLIC').format(table))
        for role in ('anon', 'authenticated'):
            cursor.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', [role])
            if cursor.fetchone():
                cursor.execute(sql.SQL('REVOKE ALL ON TABLE {} FROM {}').format(table, sql.Identifier(role)))
        cursor.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(table))


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0041_company_drive_vault')]
    operations = [
        migrations.CreateModel(
            name='DriveStorageAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('number', models.PositiveIntegerField(unique=True)),
                ('account_id', models.CharField(max_length=128, unique=True)),
                ('email', models.EmailField(max_length=254)),
                ('secret_id', models.UUIDField(editable=False, unique=True)),
                ('verified_at', models.DateTimeField()),
                ('company', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='mobileApp.company')),
            ],
            options={
                'ordering': ['number'],
                'constraints': [models.CheckConstraint(condition=models.Q(number__gte=1), name='drive_account_positive_number')],
            },
        ),
        migrations.RunPython(protect_inventory, migrations.RunPython.noop),
    ]
