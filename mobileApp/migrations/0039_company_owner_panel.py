import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0038_company_registration')]

    operations = [
        migrations.CreateModel(
            name='CompanyAdminSetup',
            fields=[
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='mobileApp.company')),
                ('rut', models.CharField(max_length=10)),
                ('first_name', models.CharField(max_length=150)),
                ('last_name', models.CharField(max_length=150)),
                ('password_hash', models.CharField(max_length=256)),
            ],
        ),
        migrations.CreateModel(
            name='CompanyBrand',
            fields=[
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='mobileApp.company')),
                ('base_color', models.CharField(blank=True, max_length=7)),
                ('logo_png', models.BinaryField(blank=True, default=bytes)),
                ('revision', models.PositiveIntegerField(default=1)),
            ],
        ),
        migrations.CreateModel(
            name='CompanyDriveConnection',
            fields=[
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, serialize=False, to='mobileApp.company')),
                ('account_id', models.CharField(max_length=128, unique=True)),
                ('root_id', models.CharField(max_length=128, unique=True)),
                ('folder_ids', models.JSONField()),
                ('encrypted_credentials', models.BinaryField()),
                ('verified_at', models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name='CompanyAudit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company_code', models.UUIDField()),
                ('actor', models.CharField(max_length=200)),
                ('action', models.CharField(max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('company', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='mobileApp.company')),
            ],
        ),
    ]
