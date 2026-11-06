import uuid

from django.db import migrations, models
from django.db.models.functions import Lower
import django.db.models.deletion


DEFAULT_CATEGORIES = (
    ('643ac8a7-0c92-4275-ab07-145cce6869e0', 'Motores'),
    ('f8e1774e-4bed-4195-b8d6-3841b82e095f', 'Controladores'),
)


def seed_categories(apps, schema_editor):
    Category = apps.get_model('mobileApp', 'ButtonCategory')
    Category.objects.using(schema_editor.connection.alias).bulk_create([
        Category(id=identifier, name=name) for identifier, name in DEFAULT_CATEGORIES
    ])


class Migration(migrations.Migration):
    dependencies = [('mobileApp', '0012_motor_area_color_motor_area_radius_x_and_more')]
    operations = [
        migrations.CreateModel(
            name='ButtonCategory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=80, unique=True, verbose_name='nombre')),
            ],
            options={
                'ordering': ['name'],
                'verbose_name': 'categoría de botón',
                'verbose_name_plural': 'categorías de botones',
                'constraints': [models.UniqueConstraint(Lower('name'), name='unique_button_category_name')],
            },
        ),
        migrations.AddField(
            model_name='motor', name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='cad_buttons', to='mobileApp.buttoncategory'),
        ),
        migrations.AddField(
            model_name='pdfmarker', name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='pdf_buttons', to='mobileApp.buttoncategory'),
        ),
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]
