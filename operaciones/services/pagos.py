# -*- coding: utf-8 -*-
"""
Sincroniza WeeklyPayment.amount a partir de tu producción:
suma ItemBillingTecnico.subtotal agrupado por (tecnico, SesionBilling.semana_pago_real).
- Crea el registro WeeklyPayment si no existe.
- Actualiza el monto si cambió.
- Borra semanas huérfanas (cuando ya no hay producción) siempre que no estén pagadas.
- No toca el comprobante ni las semanas ya pagadas.
- Mantiene/ajusta el status:
    * pending_user / rejected_user: se conservan.
    * approved_user -> pasa a pending_payment (porque ya aprobó).
    * pending_payment: se mantiene.
    * paid: no se modifica.
"""

from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from ..models import WeeklyPayment, ItemBillingTecnico


def sync_weekly_totals(*, week: str | None = None) -> dict:
    """
    Recalcula los totales por técnico y semana (YYYY-Www).
    Si se pasa week, limita a esa semana; si no, procesa todas las que tengan valor.
    Retorna métricas: {"created": X, "updated": Y, "deleted": Z}
    """
    # Base: sumar subtotales por técnico y semana real de pago (en SesionBilling)
    qs = (
        ItemBillingTecnico.objects
        .filter(item__sesion__semana_pago_real__gt="")  # no vacío
        .values("tecnico_id", "item__sesion__semana_pago_real")
        .annotate(total=Sum("subtotal"))
    )
    if week:
        qs = qs.filter(item__sesion__semana_pago_real=week)

    created = updated = deleted = 0

    # Construimos un set con las claves (tech_id, week) que existen en producción
    agg_keys: set[tuple[int, str]] = set()

    with transaction.atomic():
        for row in qs:
            tech_id = row["tecnico_id"]
            wk = row["item__sesion__semana_pago_real"]
            total = row["total"] or Decimal("0")

            agg_keys.add((tech_id, wk))

            wp, was_created = WeeklyPayment.objects.get_or_create(
                technician_id=tech_id,
                week=wk,
                defaults={"amount": total, "status": "pending_user"},
            )

            if was_created:
                created += 1
            else:
                # Si ya existe y el monto cambió, actualiza
                if wp.amount != total:
                    wp.amount = total
                    # Si el usuario ya aprobó, asegúrate de que quede esperando pago
                    if wp.status == "approved_user":
                        wp.status = "pending_payment"
                    # No tocar 'paid' ni 'rejected_user'; solo actualizamos monto.
                    save_fields = ["amount", "updated_at"]
                    if wp.status.startswith("pending_") or wp.status == "approved_user":
                        save_fields.append("status")
                    wp.save(update_fields=save_fields)
                    updated += 1

        # ---- Prune: eliminar semanas huérfanas (ya no hay producción) ----
        # Ámbito de búsqueda: todo o sólo la semana indicada
        existing_qs = WeeklyPayment.objects.all()
        if week:
            existing_qs = existing_qs.filter(week=week)

        # No eliminamos pagos ya 'paid'
        for wp in existing_qs.exclude(status="paid"):
            key = (wp.technician_id, wp.week)
            if key not in agg_keys:
                # Ya no hay producción para esa (técnico, semana) -> eliminar
                wp.delete()
                deleted += 1

    return {"created": created, "updated": updated, "deleted": deleted}
