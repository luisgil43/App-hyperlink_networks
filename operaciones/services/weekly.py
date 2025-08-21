from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from ..models import WeeklyPayment, ItemBillingTecnico

ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}


@transaction.atomic
def sync_weekly_totals_no_create(*, week: str | None = None, technician_id: int | None = None) -> dict:
    agg = (ItemBillingTecnico.objects
           .filter(item__sesion__semana_pago_real__gt="")
           .filter(item__sesion__estado__in=ESTADOS_OK)
           .values("tecnico_id", "item__sesion__semana_pago_real")
           .annotate(total=Sum("subtotal")))
    if week:
        agg = agg.filter(item__sesion__semana_pago_real=week)
    if technician_id:
        agg = agg.filter(tecnico_id=technician_id)

    prod = {(r["tecnico_id"], r["item__sesion__semana_pago_real"]): (r["total"] or Decimal("0"))
            for r in agg if (r["total"] or Decimal("0")) > 0}

    qs = WeeklyPayment.objects.select_for_update()
    if week:
        qs = qs.filter(week=week)
    if technician_id:
        qs = qs.filter(technician_id=technician_id)

    updated = deleted = 0
    for wp in qs:
        key = (wp.technician_id, wp.week)
        if key not in prod:
            if wp.status != "paid":
                wp.delete()
                deleted += 1
            continue
        new_total = prod[key]
        if wp.amount != new_total:
            wp.amount = new_total
            fields = ["amount", "updated_at"]
            if wp.status == "approved_user":
                wp.status = "pending_payment"
                fields.append("status")
            wp.save(update_fields=fields)
            updated += 1
    return {"updated": updated, "deleted": deleted}


@transaction.atomic
def materialize_week_for_payments(*, week: str, technician_id: int | None = None) -> dict:
    """
    Crea/actualiza SOLO la semana indicada a partir de producción aprobada.
    - Considera únicamente sesiones en ESTADOS_OK.
    - Crea WeeklyPayment si falta (status: pending_user).
    - Si cambia el monto y estaba approved_user -> pasa a pending_payment.
    - No toca los registros 'paid'.
    - Elimina weeklies huérfanos (sin producción) de ESA semana (y técnico si se pasa).
    """
    # 1) Agregados de producción aprobada por técnico
    agg = (
        ItemBillingTecnico.objects
        .filter(item__sesion__semana_pago_real=week)
        .filter(item__sesion__estado__in=ESTADOS_OK)
        .values("tecnico_id")
        .annotate(total=Sum("subtotal"))
    )
    if technician_id:
        agg = agg.filter(tecnico_id=technician_id)

    created = updated = deleted = 0
    seen_techs: set[int] = set()

    for row in agg:
        tech_id = row["tecnico_id"]
        total = row["total"] or Decimal("0")
        if total <= 0:
            continue

        seen_techs.add(tech_id)

        wp, was_created = WeeklyPayment.objects.get_or_create(
            technician_id=tech_id,
            week=week,
            defaults={"amount": total, "status": "pending_user"},
        )
        if was_created:
            created += 1
        else:
            if wp.amount != total:
                wp.amount = total
                fields = ["amount", "updated_at"]
                if wp.status == "approved_user":
                    wp.status = "pending_payment"
                    fields.append("status")
                wp.save(update_fields=fields)
                updated += 1

    # 2) Podar huérfanos de ESA semana (sin producción aprobada > 0)
    prune_qs = WeeklyPayment.objects.filter(week=week)
    if technician_id:
        prune_qs = prune_qs.filter(technician_id=technician_id)

    for wp in prune_qs.exclude(status="paid"):
        if wp.technician_id not in seen_techs:
            wp.delete()
            deleted += 1

    return {"created": created, "updated": updated, "deleted": deleted}
