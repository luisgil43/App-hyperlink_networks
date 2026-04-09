# fleet/services.py
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from .models import Vehicle, VehicleService, VehicleServiceType


def create_vehicle_service_from_source(
    *,
    vehicle: Vehicle,
    service_type_obj: VehicleServiceType | None,
    service_date,
    service_time=None,
    kilometraje_declarado=None,
    monto=Decimal("0.00"),
    notes="",
    title=None,
    source_label="manual",
):
    """
    Crea un VehicleService usando la lógica base del módulo fleet.
    Devuelve: (service, messages_list)
    """

    legacy_map = {
        "Fuel": "combustible",
        "Combustible": "combustible",
        "Oil change": "aceite",
        "Cambio de aceite": "aceite",
        "Tires": "neumaticos",
        "Cambio de neumáticos": "neumaticos",
        "Technical inspection": "revision_tecnica",
        "Revisión técnica": "revision_tecnica",
        "Vehicle permit": "permiso_circulacion",
        "Permiso de circulación": "permiso_circulacion",
    }

    legacy_service_type = "otro"
    if service_type_obj and service_type_obj.name:
        legacy_service_type = legacy_map.get(service_type_obj.name.strip(), "otro")

    svc = VehicleService.objects.create(
        vehicle=vehicle,
        service_type=legacy_service_type,
        service_type_obj=service_type_obj,
        title=title or (service_type_obj.name if service_type_obj else "Service"),
        service_date=service_date,
        service_time=service_time,
        kilometraje_declarado=kilometraje_declarado,
        monto=monto or Decimal("0.00"),
        notes=notes or "",
    )

    messages_out = []
    type_name = service_type_obj.name if service_type_obj else svc.get_service_type_display()
    messages_out.append(f"✅ Service #{svc.service_code} ({type_name}) created for {vehicle.patente}.")

    if svc.kilometraje_declarado is not None:
        messages_out.append(f"📍 Odometer: {svc.kilometraje_declarado} miles.")

    if svc.next_due_km is not None:
        messages_out.append(f"⏭ Next due: {svc.next_due_km} miles.")
    if svc.next_due_date is not None:
        messages_out.append(f"📅 Next due date: {svc.next_due_date}.")

    return svc, messages_out