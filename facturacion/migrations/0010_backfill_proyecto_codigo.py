from django.db import migrations
from django.db.models import Q


def backfill_codigo(apps, schema_editor):
    Proyecto = apps.get_model("facturacion", "Proyecto")

    # Conjunto de códigos ya usados (normalizados a mayúsculas y sin espacios)
    usados = set(
        c.strip().upper()
        for (c,) in Proyecto.objects
            .exclude(Q(codigo__isnull=True) | Q(codigo=""))
            .values_list("codigo")
    )

    batch = []
    BATCH = 500

    # Primero, normaliza y deduplica posibles duplicados existentes
    for p in Proyecto.objects.all().only("id", "codigo", "nombre"):
        base = (p.codigo or "").strip().upper()

        if not base:
            # Genera un código determinista y único por PK
            candidate = f"PRJ-{p.id:06d}"
        else:
            candidate = base

        # Si choca con otro, sufija con el id para garantizar unicidad
        if candidate in usados:
            candidate = f"{candidate}-{p.id:06d}"

        if p.codigo != candidate:
            p.codigo = candidate
            batch.append(p)

        usados.add(candidate)

        if len(batch) >= BATCH:
            Proyecto.objects.bulk_update(batch, ["codigo"])
            batch.clear()

    if batch:
        Proyecto.objects.bulk_update(batch, ["codigo"])

class Migration(migrations.Migration):
    dependencies = [
        ("facturacion", "0009_backfill_proyecto_timestamps"),
    ]

    operations = [
        migrations.RunPython(backfill_codigo, migrations.RunPython.noop),
    ]