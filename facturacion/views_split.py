from __future__ import annotations

import json
from decimal import Decimal
from typing import Dict

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from operaciones.models import ItemBilling, SesionBilling
from operaciones.services.billing_split import split_billing_session


# Reemplaza por tu decorador real si lo tienes en otro módulo
def rol_requerido(*roles):
    def _wrap(view):
        def _inner(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return JsonResponse({"ok": False, "error": "Auth required."}, status=401)
            if getattr(user, "es_admin", False) or getattr(user, "es_facturacion", False):
                return view(request, *args, **kwargs)
            return JsonResponse({"ok": False, "error": "Forbidden."}, status=403)
        return _inner
    return _wrap


@login_required
@rol_requerido('facturacion', 'admin')
@require_GET
def duplicate_preview(request, session_id: int):
    """
    Devuelve JSON con los ítems del billing y flag de 'Paid'.
    {
      ok: true,
      session_id: ...,
      is_paid: bool,
      items: [{id, codigo_trabajo, descripcion, unidad_medida, cantidad}, ...]
    }
    """
    session = get_object_or_404(
        SesionBilling.objects.prefetch_related("items"),
        pk=session_id
    )

    items = list(
        ItemBilling.objects
        .filter(sesion=session)
        .values("id", "codigo_trabajo", "descripcion", "unidad_medida", "cantidad")
        .order_by("id")
    )

    is_paid = (str(getattr(session, "finance_status", "")).lower() == "paid")

    return JsonResponse({
        "ok": True,
        "session_id": session.id,
        "is_paid": is_paid,
        "items": items,
    })


@login_required
@rol_requerido('facturacion', 'admin')
@require_POST
def duplicate_commit(request, session_id: int):
    """
    Recibe JSON: { "moves": { "<item_id>": "<qty>", ... } }
    Valida que 0 <= qty <= original, y ejecuta el split.
    """
    session = get_object_or_404(
        SesionBilling.objects.prefetch_related("items"),
        pk=session_id
    )

    try:
        payload = json.loads(request.body.decode("utf-8"))
        moves_in: Dict[str, str] = (payload.get("moves") or {})
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload.")

    # Normalizar a {int(item_id): Decimal(qty)}
    moves: Dict[int, Decimal] = {}
    original_items = {
        it.id: it for it in ItemBilling.objects.filter(sesion=session).only("id", "cantidad")
    }

    for k, v in moves_in.items():
        try:
            item_id = int(k)
        except ValueError:
            return HttpResponseBadRequest(f"Invalid item id: {k}")

        if item_id not in original_items:
            return HttpResponseBadRequest(f"Item does not belong to this billing: {item_id}")

        try:
            qty = Decimal(str(v))
        except Exception:
            return HttpResponseBadRequest(f"Invalid quantity for item {item_id}")

        if qty < 0:
            return HttpResponseBadRequest(f"Negative quantity for item {item_id} is not allowed.")

        if qty > original_items[item_id].cantidad:
            return HttpResponseBadRequest(
                f"Quantity for item {item_id} exceeds original ({original_items[item_id].cantidad})."
            )

        moves[item_id] = qty

    if not moves or all(q == 0 for q in moves.values()):
        return JsonResponse({
            "ok": False,
            "error": "No items selected to move. Enter at least one quantity > 0."
        }, status=400)

    try:
        result = split_billing_session(session_id=session.id, item_qty_map=moves)
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    if result.child_id is None or result.moved_items_count == 0:
        return JsonResponse({
            "ok": False,
            "error": "No split was created."
        }, status=400)

    return JsonResponse({
        "ok": True,
        "original_id": result.original_id,
        "child_id": result.child_id,
        "moved_items_count": result.moved_items_count
    })

from django.db import transaction
from django.views.decorators.http import require_POST

# ...
from operaciones.services.billing_split import revert_split_child  # NUEVO


@login_required
@rol_requerido('facturacion', 'admin')
@require_POST
def delete_split_child(request, session_id: int):
    """
    Elimina una sesión hija de split y revierte cantidades al padre.
    Requiere que la sesión sea is_split_child=True y tenga split_from != None.
    """
    try:
        with transaction.atomic():
            result = revert_split_child(child_session_id=session_id)
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except Exception:
        return JsonResponse({"ok": False, "error": "Unexpected error."}, status=500)

    return JsonResponse({
        "ok": True,
        "parent_id": result["parent_id"],
        "restored_items": result["restored_items"],
        "deleted_child_id": result["deleted_child_id"],
    })