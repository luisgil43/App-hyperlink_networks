# fleet/signals.py
from __future__ import annotations

from django.apps import apps
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=None)
def _noop(sender, **kwargs):
    # placeholder para evitar warnings si alguien importa signals directo
    return


def connect_fleet_signals():
    CartolaMovimiento = apps.get_model("facturacion", "CartolaMovimiento")
    VehicleAssignment = apps.get_model("fleet", "VehicleAssignment")

    @receiver(post_save, sender=CartolaMovimiento, dispatch_uid="fleet_cartola_fuel_update_odometer")
    def on_cartola_saved(sender, instance, created, **kwargs):
        if not created:
            return

        tipo = getattr(instance, "tipo", None)
        tipo_nombre = (getattr(tipo, "nombre", "") or "").strip().lower()

        # Solo Fuel actualiza odómetro (millas)
        if tipo_nombre != "fuel":
            return

        odo = getattr(instance, "kilometraje", None)
        if odo is None:
            return

        user = getattr(instance, "usuario", None)
        if not user:
            return

        asig = (
            VehicleAssignment.objects
            .select_related("vehicle")
            .filter(user=user, is_active=True)
            .order_by("-assigned_at")
            .first()
        )
        if not asig:
            return

        asig.vehicle.update_kilometraje(odo, strict=False, source="fuel_expense")