from django.db import migrations, models


def null_out_existing(apps, schema_editor):
    CartolaMovimiento = apps.get_model("facturacion", "CartolaMovimiento")

    # Opción 1 (RECOMENDADA): deja NULL solo los que quedaron "contaminados"
    # (típico: real_consumption_date = hoy, pero fecha NO es hoy)
    from django.utils import timezone
    today = timezone.localdate()
    CartolaMovimiento.objects.filter(
        real_consumption_date=today
    ).exclude(
        fecha__date=today
    ).update(real_consumption_date=None)

    # Opción 2 (si tú quieres que TODO lo histórico quede en "—"):
    # CartolaMovimiento.objects.all().update(real_consumption_date=None)


class Migration(migrations.Migration):

    dependencies = [
        ("facturacion", "0014_real_consumption_date"),  # <-- ajusta esto
    ]

    operations = [
        migrations.AlterField(
            model_name="cartolamovimiento",
            name="real_consumption_date",
            field=models.DateField(null=True, blank=True, verbose_name="Real consumption date"),
        ),
        migrations.RunPython(null_out_existing, migrations.RunPython.noop),
    ]