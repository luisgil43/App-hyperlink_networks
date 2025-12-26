import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0013_proyecto_uq_proyecto_nombre_mandante_ciudad_estado_oficina"),  # <-- ajusta esto
    ]

    operations = [
        migrations.AddField(
            model_name="cartolamovimiento",
            name="real_consumption_date",
            field=models.DateField(
                default=django.utils.timezone.localdate,
                verbose_name="Real consumption date",
            ),
        ),
    ]