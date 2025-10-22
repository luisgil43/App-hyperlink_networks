from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, Tuple

from django.db import transaction
from django.utils import timezone

from operaciones.models import (ItemBilling, ItemBillingTecnico, SesionBilling,
                                SesionBillingTecnico)


@dataclass(frozen=True)
class SplitResult:
    original_id: int
    child_id: int | None  # None si no se creó por cantidades = 0
    moved_items_count: int
    moved_total_company: Decimal
    moved_total_tech: Decimal


def _q(x) -> Decimal:
    return Decimal(str(x or "0")).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)


def _per_unit(value: Decimal, qty: Decimal) -> Decimal:
    if qty is None or qty == 0:
        return Decimal("0.00")
    return (value / qty).quantize(Decimal("0.0000001"))  # mayor precisión interna


@transaction.atomic
def split_billing_session(session_id: int, item_qty_map: Dict[int, Decimal]) -> SplitResult:
    """
    Crea un 'split/duplicate' de la SesionBilling indicada moviendo cantidades
    desde el billing original hacia un billing hijo. Si todas las cantidades por
    mover son 0, no se crea el hijo y no se altera el original.

    Reglas (según requerimiento de negocio):
    - No reinicia estados (finance ni operaciones) en ninguno de los dos.
    - Se permite split aunque el original esté Paid (UI mostrará advertencia).
    - Evidencias/requisitos permanecen en el original (no se duplican).
    - El hijo contiene solo los ítems con cantidad > 0 movida.
    - Ítems sin movimiento permanecen como están en el original.
    - Totales empresa/técnicos se prorratean linealmente por cantidad.
    - Se duplica la estructura de técnicos (SesionBillingTecnico) en el hijo.

    :param session_id: ID de la sesión original
    :param item_qty_map: { item_id: cantidad_a_mover (Decimal >= 0) }
    :return: SplitResult (child_id = None si no hubo movimiento)
    """
    original: SesionBilling = (
        SesionBilling.objects.select_for_update()
        .prefetch_related("items__desglose_tecnico", "tecnicos_sesion")
        .get(pk=session_id)
    )

    # Normaliza entradas (solo IDs que pertenecen al original)
    items_by_id = {it.id: it for it in original.items.all()}
    moves: Dict[int, Decimal] = {}
    for item_id, mv in (item_qty_map or {}).items():
        if item_id in items_by_id:
            mv_dec = _q(mv)
            if mv_dec < 0:
                raise ValueError(f"Invalid move for item #{item_id}: negative quantity")
            # No cap aquí; se valida contra qty original más abajo
            moves[item_id] = mv_dec

    if not moves:
        return SplitResult(
            original_id=original.id,
            child_id=None,
            moved_items_count=0,
            moved_total_company=_q("0"),
            moved_total_tech=_q("0"),
        )

    # Valida contra cantidades originales
    any_move = False
    for item_id, move_qty in moves.items():
        it = items_by_id[item_id]
        # ⛔️ No permitir mover igual o más que la cantidad original
        if move_qty >= it.cantidad:
            raise ValueError(
                f"Invalid move for item #{item_id}: move must be less than the original ({it.cantidad})."
            )
        if move_qty > 0:
            any_move = True

    if not any_move:
        # Nada que mover → no creamos hijo, no tocamos original
        return SplitResult(
            original_id=original.id,
            child_id=None,
            moved_items_count=0,
            moved_total_company=_q("0"),
            moved_total_tech=_q("0"),
        )

    # ====== Crear hijo (clon superficial de metadatos, sin evidencias) ======
    child = SesionBilling(
        # Flags de “split child”
        is_split_child=True,
        split_from=original,
        split_comment=f"Auto-split from #{original.id} at {timezone.now():%Y-%m-%d %H:%M:%S}",

        # Copiamos identidad/metadata (NO reiniciamos estados)
        proyecto_id=original.proyecto_id,
        cliente=original.cliente,
        ciudad=original.ciudad,
        proyecto=original.proyecto,
        oficina=original.oficina,
        direccion_proyecto=original.direccion_proyecto,
        semana_pago_proyectada=original.semana_pago_proyectada,
        semana_pago_real=original.semana_pago_real,

        # 👇 **FIX**: conservar estado operativo
        estado=original.estado,

        # Finanzas: copiar tal cual
        finance_status=original.finance_status,
        finance_note=original.finance_note,
        finance_sent_at=original.finance_sent_at,
        # finance_updated_at se setea solo

        # Totales se recalcularán abajo
        subtotal_empresa=_q("0"),
        subtotal_tecnico=_q("0"),
        real_company_billing=None,  # hijo parte sin “real” (se puede setear luego)
    )
    # Preservamos también flags de descuento directos del original, pero no los usamos
    child.is_direct_discount = False  # por definición, *no es* un descuento directo
    child.origin_session = None
    child.save()

    # Duplica estructura de técnicos (sesión)
    tech_rows = list(original.tecnicos_sesion.all())
    for r in tech_rows:
        SesionBillingTecnico.objects.create(
            sesion=child,
            tecnico=r.tecnico,
            porcentaje=r.porcentaje,
            estado=r.estado,
            aceptado_en=r.aceptado_en,
            finalizado_en=r.finalizado_en,
            supervisor_comentario=r.supervisor_comentario,
            supervisor_revisado_en=r.supervisor_revisado_en,
            pm_comentario=r.pm_comentario,
            pm_revisado_en=r.pm_revisado_en,
            reintento_habilitado=r.reintento_habilitado,
        )

    moved_items_count = 0
    moved_total_company = _q("0")
    moved_total_tech = _q("0")

    # ====== Mover cantidades y prorratear importes ======
    for it in items_by_id.values():
        move_qty = moves.get(it.id, _q("0"))
        if move_qty <= 0:
            # No se crea en el hijo; el original queda igual
            continue

        if move_qty > it.cantidad:
            raise ValueError("Concurrent modification detected; please retry.")

        # Proporciones por unidad desde el original
        per_unit_company = _per_unit(_q(it.subtotal_empresa), _q(it.cantidad))
        per_unit_tech = _per_unit(_q(it.subtotal_tecnico), _q(it.cantidad))

        # Nuevo item en hijo
        child_qty = move_qty
        child_company_sub = (per_unit_company * child_qty).quantize(Decimal("0.00"))
        child_tech_sub = (per_unit_tech * child_qty).quantize(Decimal("0.00"))

        child_item = ItemBilling.objects.create(
            sesion=child,
            codigo_trabajo=it.codigo_trabajo,
            tipo_trabajo=it.tipo_trabajo,
            descripcion=it.descripcion,
            unidad_medida=it.unidad_medida,
            cantidad=child_qty,
            precio_empresa=it.precio_empresa,             # mismo unit price
            subtotal_empresa=child_company_sub,
            subtotal_tecnico=child_tech_sub,
        )

        # Desglose técnico en proporción lineal
        for d in it.desglose_tecnico.all():
            # prorrateo simple por cantidad (mismo % / tarifas)
            per_unit_split = _per_unit(_q(d.subtotal), _q(it.cantidad))
            d_new_sub = (per_unit_split * child_qty).quantize(Decimal("0.00"))
            ItemBillingTecnico.objects.create(
                item=child_item,
                tecnico=d.tecnico,
                tarifa_base=d.tarifa_base,
                porcentaje=d.porcentaje,
                tarifa_efectiva=d.tarifa_efectiva,
                subtotal=d_new_sub,
            )

        # Reducir el original
        remaining_qty = _q(it.cantidad) - child_qty
        if remaining_qty < 0:
            remaining_qty = _q("0")

        if remaining_qty == 0:
            it.cantidad = _q("0")
            it.subtotal_empresa = _q("0")
            it.subtotal_tecnico = _q("0")
            it.save(update_fields=["cantidad", "subtotal_empresa", "subtotal_tecnico"])
        else:
            it.cantidad = remaining_qty
            it.subtotal_empresa = (per_unit_company * remaining_qty).quantize(Decimal("0.00"))
            it.subtotal_tecnico = (per_unit_tech * remaining_qty).quantize(Decimal("0.00"))
            it.save(update_fields=["cantidad", "subtotal_empresa", "subtotal_tecnico"])

        moved_items_count += 1
        moved_total_company += child_company_sub
        moved_total_tech += child_tech_sub

    # Si por validación o redondeo no se creó ningún item en hijo, revertimos
    if moved_items_count == 0:
        raise ValueError("No items were moved; aborting split.")

    # Recalcular totales de sesión (original e hijo)
    def _recompute_session_totals(sess: SesionBilling):
        qs = sess.items.all()
        company = qs.aggregate(total_company=models.Sum("subtotal_empresa"))["total_company"] or Decimal("0")
        tech = qs.aggregate(total_tech=models.Sum("subtotal_tecnico"))["total_tech"] or Decimal("0")
        sess.subtotal_empresa = _q(company)
        sess.subtotal_tecnico = _q(tech)
        sess.finance_updated_at = timezone.now()
        sess.save(update_fields=["subtotal_empresa", "subtotal_tecnico", "finance_updated_at"])

    from django.db import models  # local import para aggregate
    _recompute_session_totals(original)
    _recompute_session_totals(child)

    return SplitResult(
        original_id=original.id,
        child_id=child.id,
        moved_items_count=moved_items_count,
        moved_total_company=_q(moved_total_company),
        moved_total_tech=_q(moved_total_tech),
    )


from decimal import Decimal
from typing import Dict, List

from django.db import transaction

from operaciones.models import ItemBilling, SesionBilling


@transaction.atomic
def revert_split_child(child_session_id: int) -> Dict:
    """
    Reabsorbe cantidades del hijo al padre y elimina la sesión hija.
    Reglas:
      - child.is_split_child debe ser True y child.split_from no nulo.
      - Suma cada item del hijo a su homólogo del padre (por codigo_trabajo).
      - Si no existe el código en el padre, se crea el ItemBilling.
      - Luego borra todos los items del hijo y la sesión hija.
      - No toca estados ni notas del padre.
    """
    child = (SesionBilling.objects
             .select_for_update()
             .select_related("split_from")
             .prefetch_related("items")
             .get(pk=child_session_id))

    if not getattr(child, "is_split_child", False) or not child.split_from_id:
        raise ValueError("This session is not a split child.")

    parent = (SesionBilling.objects
              .select_for_update()
              .prefetch_related("items")
              .get(pk=child.split_from_id))

    # Index de items padre por codigo_trabajo (usa el campo correcto en tu modelo)
    parent_items_by_code: Dict[str, ItemBilling] = {
        ib.codigo_trabajo: ib for ib in parent.items.all()
    }

    restored: List[Dict] = []
    for cit in child.items.all():
        code = cit.codigo_trabajo
        qty  = Decimal(cit.cantidad)

        if code in parent_items_by_code:
            pit = parent_items_by_code[code]
            pit.cantidad = (pit.cantidad or Decimal("0")) + qty
            pit.subtotal_tecnico = (pit.subtotal_tecnico or Decimal("0")) + (cit.subtotal_tecnico or Decimal("0"))
            pit.subtotal_empresa = (pit.subtotal_empresa or Decimal("0")) + (cit.subtotal_empresa or Decimal("0"))
            pit.save(update_fields=["cantidad", "subtotal_tecnico", "subtotal_empresa"])
        else:
            # Crear item nuevo en el padre clonando atributos relevantes
            ItemBilling.objects.create(
                sesion=parent,
                codigo_trabajo=cit.codigo_trabajo,
                tipo_trabajo=cit.tipo_trabajo,
                descripcion=cit.descripcion,
                unidad_medida=cit.unidad_medida,
                cantidad=cit.cantidad,
                precio_empresa=cit.precio_empresa,
                subtotal_tecnico=cit.subtotal_tecnico,
                subtotal_empresa=cit.subtotal_empresa,
            )

        restored.append({"code": code, "qty": str(qty)})

    # Borrar hijo (items en cascade si FK on_delete=CASCADE)
    deleted_id = child.id
    child.delete()

    # (Opcional) Si llevas contadores/flags en el padre, ajusta aquí.
    # parent.updated_at = timezone.now()
    # parent.save(update_fields=["updated_at"])

    return {"parent_id": parent.id, "deleted_child_id": deleted_id, "restored_items": restored}