# facturacion/migrations/0012_alter_proyecto_ciudad_alter_proyecto_estado_and_more.py
from django.db import migrations, models


def fill_proyecto_text_fields(apps, schema_editor):
    Proyecto = apps.get_model('facturacion', 'Proyecto')

    # Cambia '' por 'Unknown' si prefieres texto explícito
    defaults = {
        'mandante': '',
        'ciudad':   '',
        'estado':   '',
        'oficina':  '',
    }

    for field, default in defaults.items():
        # poner default donde hoy está NULL
        Proyecto.objects.filter(**{f'{field}__isnull': True}).update(**{field: default})

class Migration(migrations.Migration):

    dependencies = [
        ('facturacion', '0011_alter_proyecto_ciudad_alter_proyecto_codigo_and_more'),
    ]

    operations = [
        # 1) primero limpiamos datos nulos
        migrations.RunPython(fill_proyecto_text_fields, migrations.RunPython.noop),

        # 2) ahora sí, dejamos los campos como NOT NULL
        migrations.AlterField(
            model_name='proyecto',
            name='mandante',
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name='proyecto',
            name='ciudad',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='proyecto',
            name='estado',
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name='proyecto',
            name='oficina',
            field=models.CharField(max_length=128),
        ),
    ]