from __future__ import annotations

from collections import defaultdict

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser

from .forms_herramientas_asignaciones import (
    HerramientaAsignacionCantidadForm, HerramientaAsignacionCerrarForm)
from .models import (Herramienta, HerramientaAsignacion,
                     HerramientaAsignacionLog, HerramientaInventario)


def _parse_ids_from_post(request) -> list[int]:
    ids = []

    for raw in request.POST.getlist("asignaciones"):
        try:
            ids.append(int(raw))
        except Exception:
            continue

    raw_csv = (request.POST.get("ids") or "").strip()
    if raw_csv:
        for part in raw_csv.split(","):
            try:
                ids.append(int(part.strip()))
            except Exception:
                continue

    return list(dict.fromkeys(ids))


def _close_asignacion_locked(
    a: HerramientaAsignacion,
    request_user,
    dev: int,
    comentario: str | None,
    just: str | None,
):
    """
    Closes an already-locked assignment and returns stock.
    Reusable for individual and bulk close.
    """
    h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

    before_stock = int(h.cantidad or 0)

    a.cantidad_devuelta = dev
    a.comentario_cierre = comentario
    a.justificacion_diferencia = just
    a.closed_at = timezone.now()
    a.closed_by = request_user
    a.active = False
    a.estado = "terminada"

    a.full_clean()
    a.save()

    if dev > 0:
        h.cantidad = int(h.cantidad or 0) + dev
        h.updated_at = timezone.now()

        if h.status == "asignada" and h.cantidad > 0:
            h.status = "bodega"

        h.save(update_fields=["cantidad", "status", "updated_at"])

    HerramientaAsignacionLog.objects.create(
        asignacion=a,
        accion="close",
        by_user=request_user,
        cambios={
            "cantidad_devuelta": dev,
            "comentario_cierre": comentario or "",
            "justificacion_diferencia": just or "",
            "stock": {
                "from": before_stock,
                "to": int(h.cantidad or 0),
            },
        },
        nota=None,
    )

    return a


def _is_ajax(request) -> bool:
    return (request.headers.get("x-requested-with") or "").lower() == "xmlhttprequest"


def _user_label(u) -> str:
    if not u:
        return ""
    name = (u.get_full_name() or "").strip()
    return name or (u.username or "")


def _build_inventory_payload_for_asignacion(request, a: HerramientaAsignacion) -> dict:
    """
    Returns JSON so the frontend can rebuild the Inventory cell
    without needing a partial template.
    """
    last_inv = (
        HerramientaInventario.objects.filter(asignacion=a)
        .select_related("revisado_por")
        .order_by("-created_at", "-id")
        .first()
    )

    inv = None
    if last_inv:
        inv = {
            "id": last_inv.id,
            "estado": last_inv.estado or "",
            "motivo_rechazo": last_inv.motivo_rechazo or "",
            "foto_url": last_inv.foto.url if getattr(last_inv, "foto", None) else "",
            "puede_aprobar": last_inv.estado == "pendiente",
            "aprobar_url": reverse("logistica:aprobar_inventario", args=[last_inv.id]),
            "rechazar_url": reverse(
                "logistica:rechazar_inventario", args=[last_inv.id]
            ),
        }

    can_request = (not last_inv) or (last_inv.estado != "pendiente")

    return {
        "ok": True,
        "asig_id": a.id,
        "tool_id": a.herramienta_id,
        "inv": inv,
        "puede_solicitar": can_request,
        "solicitar_url": reverse(
            "logistica:solicitar_inventario_asignacion", args=[a.id]
        ),
        "historial_url": reverse(
            "logistica:inventario_historial_asignacion_admin", args=[a.id]
        ),
        "prox_due": (
            a.herramienta.next_inventory_due.strftime("%m/%d/%Y")
            if a.herramienta.next_inventory_due
            else ""
        ),
    }


def _can_admin_logistica(user) -> bool:
    if getattr(user, "es_admin_general", False):
        return True

    if (
        getattr(user, "es_supervisor", False)
        or getattr(user, "es_pm", False)
        or getattr(user, "es_logistica", False)
    ):
        return True

    return False


def _is_admin_general(user) -> bool:
    return bool(getattr(user, "es_admin_general", False))


def _is_logistica_or_admin(user) -> bool:
    return bool(
        getattr(user, "es_logistica", False) or getattr(user, "es_admin_general", False)
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def asignaciones_panel(request):
    """
    Assignments panel:
    - Groups assignments by worker.
    - Shows the latest inventory for each assignment.
    - Shows latest assignment logs.
    - Hyperlink version: sends grouped_rows to the template to avoid using custom dictget filter.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    q = (request.GET.get("q") or "").strip().lower()

    asignaciones_qs = HerramientaAsignacion.objects.select_related(
        "herramienta",
        "asignado_a",
        "asignado_por",
        "herramienta__bodega",
    ).order_by("-active", "-asignado_at", "-id")

    if q:
        asignaciones_qs = asignaciones_qs.filter(
            models.Q(asignado_a__first_name__icontains=q)
            | models.Q(asignado_a__last_name__icontains=q)
            | models.Q(asignado_a__username__icontains=q)
            | models.Q(herramienta__nombre__icontains=q)
            | models.Q(herramienta__serial__icontains=q)
        )

    asignaciones = list(asignaciones_qs)
    asig_ids = [a.id for a in asignaciones]

    # ===== Latest inventory by assignment =====
    last_inv_by_asig = {}
    if asig_ids:
        invs = (
            HerramientaInventario.objects.filter(asignacion_id__in=asig_ids)
            .select_related("revisado_por")
            .order_by("-created_at", "-id")
        )
        for inv in invs:
            if inv.asignacion_id not in last_inv_by_asig:
                last_inv_by_asig[inv.asignacion_id] = inv

    # ===== Latest logs by assignment =====
    logs_by_asig = defaultdict(list)
    if asig_ids:
        logs = (
            HerramientaAsignacionLog.objects.filter(asignacion_id__in=asig_ids)
            .select_related("by_user")
            .order_by("-created_at", "-id")
        )
        for lg in logs:
            if len(logs_by_asig[lg.asignacion_id]) < 20:
                logs_by_asig[lg.asignacion_id].append(lg)

    grouped = defaultdict(list)
    users = {}

    for a in asignaciones:
        users[a.asignado_a_id] = a.asignado_a

        a.last_inv = last_inv_by_asig.get(a.id)
        a.logs_list = logs_by_asig.get(a.id, [])
        a.last_edit = a.logs_list[0] if a.logs_list else None

        grouped[a.asignado_a_id].append(a)

    user_ids_sorted = sorted(
        users.keys(),
        key=lambda uid: (
            users[uid].first_name or "",
            users[uid].last_name or "",
            users[uid].username or "",
        ),
    )

    # ✅ New structure for template.
    # This avoids using: users|dictget:uid and grouped|dictget:uid
    grouped_rows = []
    for uid in user_ids_sorted:
        grouped_rows.append(
            {
                "uid": uid,
                "user": users.get(uid),
                "assignments": grouped.get(uid, []),
            }
        )

    users_qs = list(
        CustomUser.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
    )

    return render(
        request,
        "logistica/admin_herramientas_asignaciones_panel.html",
        {
            "q": request.GET.get("q", ""),
            "grouped_rows": grouped_rows,
            "users_qs": users_qs,
            "can_edit": _is_logistica_or_admin(request.user),
            "can_delete": _is_admin_general(request.user),
        },
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def asignar_cantidad(request, herramienta_id: int):
    """
    Creates a quantity assignment:
    - Deducts stock from Herramienta.cantidad.
    - Allows multiple active assignments of the same tool to different people.
    - Records assignment log.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    herramienta = get_object_or_404(Herramienta, pk=herramienta_id)

    if request.method == "POST":
        form = HerramientaAsignacionCantidadForm(
            request.POST,
            herramienta=herramienta,
        )

        if form.is_valid():
            to_user = form.cleaned_data["asignado_a"]
            cantidad_entregada = int(form.cleaned_data["cantidad_entregada"])
            asignado_at = form.cleaned_data["asignado_at"]
            solicitar_inv = bool(form.cleaned_data.get("solicitar_inventario"))

            if cantidad_entregada <= 0:
                messages.error(request, "Invalid quantity.")
                return redirect("logistica:herramientas_asignaciones_panel")

            with transaction.atomic():
                herramienta = Herramienta.objects.select_for_update().get(
                    pk=herramienta.pk
                )

                if cantidad_entregada > int(herramienta.cantidad):
                    messages.error(
                        request,
                        f"Not enough stock. Available: {herramienta.cantidad}.",
                    )
                    return redirect("logistica:herramientas_asignaciones_panel")

                before_stock = int(herramienta.cantidad)
                herramienta.cantidad = before_stock - cantidad_entregada

                if herramienta.cantidad <= 0:
                    herramienta.status = "asignada"

                herramienta.updated_at = timezone.now()
                herramienta.save(update_fields=["cantidad", "status", "updated_at"])

                a = HerramientaAsignacion.objects.create(
                    herramienta=herramienta,
                    asignado_a=to_user,
                    asignado_por=request.user,
                    asignado_at=asignado_at,
                    cantidad_entregada=cantidad_entregada,
                    active=True,
                    estado="pendiente",
                )

                if solicitar_inv:
                    herramienta.inventory_required = True
                    if not herramienta.next_inventory_due:
                        herramienta.mark_inventory_due_default()
                    herramienta.save(
                        update_fields=[
                            "inventory_required",
                            "next_inventory_due",
                            "updated_at",
                        ]
                    )

                HerramientaAsignacionLog.objects.create(
                    asignacion=a,
                    accion="create",
                    by_user=request.user,
                    cambios={
                        "herramienta": str(herramienta),
                        "asignado_a": to_user.get_full_name() or to_user.username,
                        "cantidad_entregada": cantidad_entregada,
                        "stock": {
                            "from": before_stock,
                            "to": int(herramienta.cantidad),
                        },
                        "solicitar_inventario": bool(solicitar_inv),
                    },
                    nota=None,
                )

            messages.success(
                request,
                (
                    f"✅ Assignment created: "
                    f"{to_user.get_full_name() or to_user.username} • "
                    f"Quantity: {cantidad_entregada}."
                ),
            )
            return redirect("logistica:herramientas_asignaciones_panel")

        messages.error(request, "❌ Please review the fields.")

    else:
        form = HerramientaAsignacionCantidadForm(herramienta=herramienta)

    return render(
        request,
        "logistica/admin_herramientas_asignar_cantidad.html",
        {
            "herramienta": herramienta,
            "form": form,
        },
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def cerrar_asignacion(request, asignacion_id: int):
    """
    Closes an assignment:
    - Requests returned quantity.
    - Adds returned quantity back to stock.
    - Stores closure notes / difference justification.
    - Records close log.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    a = get_object_or_404(HerramientaAsignacion, pk=asignacion_id)
    nxt = (request.POST.get("next") or request.GET.get("next") or "").strip()

    if request.method == "POST":
        form = HerramientaAsignacionCerrarForm(request.POST, asignacion=a)

        if form.is_valid():
            dev = int(form.cleaned_data["cantidad_devuelta"])
            comentario = (
                form.cleaned_data.get("comentario_cierre") or ""
            ).strip() or None
            just = (
                form.cleaned_data.get("justificacion_diferencia") or ""
            ).strip() or None

            try:
                with transaction.atomic():
                    a = (
                        HerramientaAsignacion.objects.select_for_update()
                        .select_related("herramienta")
                        .get(pk=a.pk)
                    )

                    _close_asignacion_locked(
                        a=a,
                        request_user=request.user,
                        dev=dev,
                        comentario=comentario,
                        just=just,
                    )

                messages.success(request, "✅ Assignment closed and stock updated.")

                if nxt:
                    return redirect(nxt)

                return redirect("logistica:herramientas_asignaciones_panel")

            except ValidationError as e:
                messages.error(request, f"❌ {e}")

        else:
            messages.error(request, "❌ Please review the closure fields.")

    else:
        form = HerramientaAsignacionCerrarForm(asignacion=a)

    return render(
        request,
        "logistica/admin_herramientas_asignacion_cerrar.html",
        {
            "asignacion": a,
            "form": form,
            "next": nxt,
        },
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
@require_POST
def solicitar_inventario_asignaciones_masivo(request):
    """
    Requests inventory for several active assignments.
    Does not create inventory records; it only enables inventory_required
    on each related tool.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    ids = _parse_ids_from_post(request)

    if not ids:
        return JsonResponse(
            {"ok": False, "error": "Select at least one assignment."},
            status=400,
        )

    total = 0

    with transaction.atomic():
        asignaciones = list(
            HerramientaAsignacion.objects.select_for_update()
            .select_related("herramienta", "asignado_a")
            .filter(pk__in=ids, active=True)
            .order_by("id")
        )

        for a in asignaciones:
            h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

            was_required = bool(h.inventory_required)

            h.inventory_required = True
            if not h.next_inventory_due:
                h.mark_inventory_due_default()

            h.updated_at = timezone.now()
            h.save(
                update_fields=[
                    "inventory_required",
                    "next_inventory_due",
                    "updated_at",
                ]
            )

            HerramientaAsignacionLog.objects.create(
                asignacion=a,
                accion="inventario_solicitado",
                by_user=request.user,
                cambios={
                    "inventory_required": {
                        "from": was_required,
                        "to": True,
                    },
                    "next_inventory_due": (
                        h.next_inventory_due.strftime("%Y-%m-%d")
                        if h.next_inventory_due
                        else None
                    ),
                    "masivo": True,
                },
                nota="Bulk inventory request",
            )

            total += 1

    omitted = len(ids) - total

    return JsonResponse(
        {
            "ok": True,
            "message": f"📸 Inventory requested for {total} assignment(s).",
            "total": total,
            "omitidas": omitted,
            "reload": True,
        }
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
@require_POST
def cerrar_asignaciones_masivo(request):
    """
    Closes several active assignments.
    Each assignment receives its own returned quantity, comment and justification.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    ids = _parse_ids_from_post(request)

    if not ids:
        return JsonResponse(
            {"ok": False, "error": "Select at least one assignment."},
            status=400,
        )

    closed = 0
    errors = []

    try:
        with transaction.atomic():
            asignaciones = list(
                HerramientaAsignacion.objects.select_for_update()
                .select_related("herramienta", "asignado_a")
                .filter(pk__in=ids, active=True)
                .order_by("id")
            )

            asignaciones_by_id = {a.id: a for a in asignaciones}

            for asig_id in ids:
                a = asignaciones_by_id.get(asig_id)

                if not a:
                    errors.append(
                        f"Assignment #{asig_id}: it is not active or does not exist."
                    )
                    continue

                dev_raw = (request.POST.get(f"dev_{asig_id}") or "").strip()
                comentario = (
                    request.POST.get(f"comentario_{asig_id}") or ""
                ).strip() or None
                just = (
                    request.POST.get(f"justificacion_{asig_id}") or ""
                ).strip() or None

                try:
                    dev = int(dev_raw)
                except Exception:
                    errors.append(f"{a.herramienta.nombre}: invalid returned quantity.")
                    continue

                ent = int(a.cantidad_entregada or 0)

                if dev < 0:
                    errors.append(
                        f"{a.herramienta.nombre}: returned quantity cannot be negative."
                    )
                    continue

                if dev > ent:
                    errors.append(
                        (
                            f"{a.herramienta.nombre}: returned quantity cannot be "
                            f"greater than delivered quantity."
                        )
                    )
                    continue

                if dev < ent and not just:
                    errors.append(
                        f"{a.herramienta.nombre}: you must justify the difference."
                    )
                    continue

                if dev == 0 and not comentario:
                    errors.append(
                        f"{a.herramienta.nombre}: add a comment when returned quantity is 0."
                    )
                    continue

                _close_asignacion_locked(
                    a=a,
                    request_user=request.user,
                    dev=dev,
                    comentario=comentario,
                    just=just,
                )
                closed += 1

            if errors:
                raise ValidationError(" | ".join(errors))

    except ValidationError as e:
        return JsonResponse(
            {
                "ok": False,
                "error": str(e),
            },
            status=400,
        )

    return JsonResponse(
        {
            "ok": True,
            "message": f"✅ {closed} assignment(s) were closed.",
            "total": closed,
            "reload": True,
        }
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def reiniciar_estado_asignacion(request, asignacion_id: int):
    """
    Resets assignment status to pending only if the assignment is still active.
    Records reset log.
    Supports AJAX.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    if request.method != "POST":
        raise Http404()

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
    )

    if not a.active:
        if _is_ajax(request):
            return JsonResponse(
                {
                    "ok": False,
                    "error": "You cannot reset a closed assignment.",
                },
                status=400,
            )

        messages.error(request, "You cannot reset a closed assignment.")
        return redirect("logistica:herramientas_asignaciones_panel")

    prev_estado = a.estado

    a.estado = "pendiente"
    a.comentario_rechazo = None
    a.rechazado_at = None
    a.aceptado_at = None
    a.save(
        update_fields=[
            "estado",
            "comentario_rechazo",
            "rechazado_at",
            "aceptado_at",
        ]
    )

    HerramientaAsignacionLog.objects.create(
        asignacion=a,
        accion="reset",
        by_user=request.user,
        cambios={
            "estado": {
                "from": prev_estado,
                "to": "pendiente",
            }
        },
        nota=None,
    )

    if _is_ajax(request):
        estado_html = (
            '<span class="inline-block px-3 py-1 rounded-full text-xs font-medium '
            'bg-yellow-100 text-yellow-800">'
            f"Active • {a.get_estado_display()}"
            "</span>"
        )
        return JsonResponse(
            {
                "ok": True,
                "asig_id": a.id,
                "message": "✅ Assignment status reset to Pending.",
                "estado_html": estado_html,
            }
        )

    messages.success(request, "✅ Assignment status reset to Pending.")
    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def editar_asignacion(request, asignacion_id: int):
    """
    Edits an assignment. Logistics/admin only.
    Allows editing:
    - assigned user
    - assigned date
    - delivered quantity if active
    - note

    Adjusts stock if delivered quantity changes and the assignment is active.
    Records update log.
    """
    if not _is_logistica_or_admin(request.user):
        return HttpResponseForbidden("You do not have permission.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta", "asignado_a"),
        pk=asignacion_id,
    )

    if request.method != "POST":
        raise Http404()

    to_user_id = (request.POST.get("asignado_a") or "").strip()
    asignado_at_raw = (request.POST.get("asignado_at") or "").strip()
    cantidad_raw = (request.POST.get("cantidad_entregada") or "").strip()
    nota = (request.POST.get("nota") or "").strip() or None

    cambios = {}

    to_user = None
    if to_user_id:
        to_user = CustomUser.objects.filter(pk=to_user_id, is_active=True).first()

    asignado_at = None
    if asignado_at_raw:
        try:
            asignado_at = timezone.make_aware(
                timezone.datetime.strptime(asignado_at_raw, "%Y-%m-%dT%H:%M"),
                timezone.get_current_timezone(),
            )
        except Exception:
            messages.error(request, "❌ Invalid date.")
            return redirect("logistica:herramientas_asignaciones_panel")

    nueva_cantidad = None
    if cantidad_raw:
        try:
            nueva_cantidad = int(cantidad_raw)
        except Exception:
            messages.error(request, "❌ Invalid quantity.")
            return redirect("logistica:herramientas_asignaciones_panel")

    with transaction.atomic():
        a = (
            HerramientaAsignacion.objects.select_for_update()
            .select_related("herramienta", "asignado_a")
            .get(pk=a.pk)
        )
        h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

        if to_user and to_user.pk != a.asignado_a_id:
            cambios["asignado_a"] = {
                "from": a.asignado_a.get_full_name() or a.asignado_a.username,
                "to": to_user.get_full_name() or to_user.username,
            }
            a.asignado_a = to_user

        if asignado_at and (not a.asignado_at or asignado_at != a.asignado_at):
            cambios["asignado_at"] = {
                "from": (
                    timezone.localtime(a.asignado_at).strftime("%Y-%m-%d %H:%M")
                    if a.asignado_at
                    else None
                ),
                "to": timezone.localtime(asignado_at).strftime("%Y-%m-%d %H:%M"),
            }
            a.asignado_at = asignado_at

        if nueva_cantidad is not None:
            if nueva_cantidad <= 0:
                messages.error(request, "❌ Quantity must be greater than 0.")
                return redirect("logistica:herramientas_asignaciones_panel")

            actual = int(getattr(a, "cantidad_entregada", 0) or 0)

            if nueva_cantidad != actual:
                cambios["cantidad_entregada"] = {
                    "from": actual,
                    "to": nueva_cantidad,
                }

                if a.active:
                    delta = nueva_cantidad - actual

                    if delta > 0 and int(h.cantidad) < delta:
                        messages.error(
                            request,
                            f"❌ Not enough stock to increase quantity. Available: {h.cantidad}.",
                        )
                        return redirect("logistica:herramientas_asignaciones_panel")

                    before_stock = int(h.cantidad)
                    h.cantidad = int(h.cantidad) - delta
                    h.updated_at = timezone.now()
                    h.save(update_fields=["cantidad", "updated_at"])

                    cambios["stock"] = {
                        "from": before_stock,
                        "to": int(h.cantidad),
                    }

                a.cantidad_entregada = nueva_cantidad

        if cambios:
            a.save()

            HerramientaAsignacionLog.objects.create(
                asignacion=a,
                accion="update",
                by_user=request.user,
                cambios=cambios,
                nota=nota,
            )

            messages.success(request, "✅ Assignment updated successfully.")
        else:
            messages.info(request, "No changes to save.")

    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def eliminar_asignacion(request, asignacion_id: int):
    """
    Deletes an assignment. General admin only.
    If active, returns delivered quantity to stock before deleting.
    Records delete log before deleting.
    """
    if not _is_admin_general(request.user):
        return HttpResponseForbidden("Only general admin can delete assignments.")

    if request.method != "POST":
        raise Http404()

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta", "asignado_a"),
        pk=asignacion_id,
    )

    nota = (request.POST.get("nota") or "").strip() or None

    with transaction.atomic():
        a = (
            HerramientaAsignacion.objects.select_for_update()
            .select_related("herramienta", "asignado_a")
            .get(pk=a.pk)
        )
        h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

        before_stock = int(h.cantidad)

        if a.active:
            entregada = int(getattr(a, "cantidad_entregada", 0) or 0)
            if entregada > 0:
                h.cantidad = int(h.cantidad) + entregada
                h.updated_at = timezone.now()
                h.save(update_fields=["cantidad", "updated_at"])

        HerramientaAsignacionLog.objects.create(
            asignacion=a,
            accion="delete",
            by_user=request.user,
            cambios={
                "snapshot": {
                    "herramienta": str(a.herramienta),
                    "asignado_a": a.asignado_a.get_full_name() or a.asignado_a.username,
                    "cantidad_entregada": int(getattr(a, "cantidad_entregada", 0) or 0),
                    "active": bool(a.active),
                    "estado": a.estado,
                },
                "stock": {
                    "from": before_stock,
                    "to": int(h.cantidad),
                },
            },
            nota=nota,
        )

        a.delete()

    messages.success(request, "🗑️ Assignment deleted and stock adjusted if needed.")
    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def inventario_historial_asignacion_admin(request, asignacion_id: int):
    """
    Inventory history filtered by assignment.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related(
            "herramienta",
            "asignado_a",
            "asignado_por",
        ),
        pk=asignacion_id,
    )

    inventarios = (
        HerramientaInventario.objects.filter(asignacion=a)
        .select_related("revisado_por")
        .order_by("-created_at", "-id")
    )

    return render(
        request,
        "logistica/admin_inventario_historial_asignacion.html",
        {
            "asignacion": a,
            "herramienta": a.herramienta,
            "inventarios": list(inventarios),
        },
    )


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def solicitar_inventario_asignacion(request, asignacion_id: int):
    """
    Requests inventory for one specific assignment.
    Does not create inventory. The user uploads it later.
    Supports AJAX.
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    if request.method != "POST":
        raise Http404()

    with transaction.atomic():
        a = get_object_or_404(
            HerramientaAsignacion.objects.select_for_update().select_related(
                "herramienta", "asignado_a"
            ),
            pk=asignacion_id,
        )

        h = Herramienta.objects.select_for_update().get(pk=a.herramienta_id)

        was_required = bool(h.inventory_required)

        h.inventory_required = True

        if not h.next_inventory_due:
            h.mark_inventory_due_default()

        h.updated_at = timezone.now()
        h.save(
            update_fields=[
                "inventory_required",
                "next_inventory_due",
                "updated_at",
            ]
        )

        HerramientaAsignacionLog.objects.create(
            asignacion=a,
            accion="inventario_solicitado",
            by_user=request.user,
            cambios={
                "inventory_required": {
                    "from": was_required,
                    "to": True,
                },
                "next_inventory_due": (
                    h.next_inventory_due.strftime("%Y-%m-%d")
                    if h.next_inventory_due
                    else None
                ),
            },
            nota=None,
        )

        a.herramienta = h

    if _is_ajax(request):
        payload = _build_inventory_payload_for_asignacion(request, a)
        payload["message"] = "Inventory requested."
        return JsonResponse(payload)

    messages.success(request, "📸 Inventory requested for this assignment.")

    nxt = (request.GET.get("next") or "").strip()

    if nxt:
        return redirect(nxt)

    return redirect("logistica:herramientas_asignaciones_panel")


@staff_member_required
@rol_requerido("admin", "pm", "supervisor", "logistica")
def herramientas_asignacion_masiva(request):
    """
    Bulk assignment preview matrix:
    - Receives selected tools through querystring: ?ids=1,2,3.
    - Allows selecting workers.
    - Allows entering quantities per cell.
    - Frontend and backend validation.
    - On save, creates assignments and deducts stock.

    Expected POST:
      users = [<user_id>, ...]
      qty_<tool_id>_<user_id> = int
    """
    if not _can_admin_logistica(request.user):
        return HttpResponseForbidden("You do not have permission.")

    def _parse_ids(raw: str) -> list[int]:
        out = []

        for part in (raw or "").split(","):
            part = (part or "").strip()
            if not part:
                continue

            try:
                out.append(int(part))
            except Exception:
                continue

        seen = set()
        uniq = []
        for x in out:
            if x not in seen:
                seen.add(x)
                uniq.append(x)

        return uniq

    if request.method == "GET":
        ids = _parse_ids(request.GET.get("ids") or "")

        if not ids:
            messages.error(request, "❌ Select at least one tool for bulk assignment.")
            return redirect("logistica:herramientas_list")

        herramientas = list(
            Herramienta.objects.filter(id__in=ids)
            .select_related("bodega")
            .order_by("nombre", "id")
        )

        if not herramientas:
            messages.error(request, "❌ No tools were found for assignment.")
            return redirect("logistica:herramientas_list")

        users_qs = list(
            CustomUser.objects.filter(is_active=True).order_by(
                "first_name", "last_name", "username"
            )
        )

        tools_payload = []
        for h in herramientas:
            tools_payload.append(
                {
                    "id": h.id,
                    "nombre": h.nombre or "",
                    "serial": h.serial or "",
                    "stock": int(h.cantidad or 0),
                    "bodega": h.bodega.nombre if h.bodega else "",
                }
            )

        users_payload = []
        for u in users_qs:
            users_payload.append(
                {
                    "id": u.id,
                    "label": (
                        u.get_full_name() or u.username or f"User#{u.id}"
                    ).strip(),
                }
            )

        return render(
            request,
            "logistica/admin_herramientas_asignacion_masiva.html",
            {
                "tools": herramientas,
                "tools_payload": tools_payload,
                "users_qs": users_qs,
                "users_payload": users_payload,
                "ids_str": ",".join(str(x) for x in ids),
            },
        )

    ids = _parse_ids(request.POST.get("ids") or "")

    if not ids:
        messages.error(request, "❌ Missing selected tools.")
        return redirect("logistica:herramientas_list")

    user_ids_raw = request.POST.getlist("users")
    user_ids = []

    for x in user_ids_raw:
        try:
            user_ids.append(int(x))
        except Exception:
            continue

    user_ids = list(dict.fromkeys(user_ids))

    if not user_ids:
        messages.error(request, "❌ Select at least one worker.")
        return redirect(
            f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}"
        )

    users_by_id = {
        u.id: u for u in CustomUser.objects.filter(id__in=user_ids, is_active=True)
    }

    if len(users_by_id) != len(user_ids):
        messages.error(request, "❌ One or more workers are invalid or inactive.")
        return redirect(
            f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}"
        )

    def _to_int(v) -> int:
        try:
            if v is None:
                return 0
            v = str(v).strip()
            if v == "":
                return 0
            n = int(v)
            return n if n > 0 else 0
        except Exception:
            return 0

    requested = {}
    for tool_id in ids:
        requested[tool_id] = {}

        for uid in user_ids:
            key = f"qty_{tool_id}_{uid}"
            requested[tool_id][uid] = _to_int(request.POST.get(key))

    any_qty = False
    for tool_id in ids:
        if sum(requested[tool_id].values()) > 0:
            any_qty = True
            break

    if not any_qty:
        messages.error(request, "❌ No quantities were entered.")
        return redirect(
            f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}"
        )

    created = 0

    try:
        with transaction.atomic():
            tools_locked = list(
                Herramienta.objects.select_for_update().filter(id__in=ids)
            )
            tools_by_id = {h.id: h for h in tools_locked}

            for tool_id in ids:
                h = tools_by_id.get(tool_id)

                if not h:
                    raise ValidationError("Invalid tool in bulk assignment.")

                total = int(sum(requested[tool_id].values()) or 0)

                if total <= 0:
                    continue

                stock = int(h.cantidad or 0)

                if total > stock:
                    raise ValidationError(
                        (
                            f"Not enough stock for '{h.nombre}'. "
                            f"Stock: {stock} • Assigning: {total}."
                        )
                    )

            for tool_id in ids:
                h = tools_by_id.get(tool_id)

                if not h:
                    continue

                total = int(sum(requested[tool_id].values()) or 0)

                if total <= 0:
                    continue

                before_stock = int(h.cantidad or 0)
                new_stock = before_stock - total

                for uid in user_ids:
                    qty = int(requested[tool_id].get(uid) or 0)

                    if qty <= 0:
                        continue

                    to_user = users_by_id[uid]

                    a = HerramientaAsignacion.objects.create(
                        herramienta=h,
                        asignado_a=to_user,
                        asignado_por=request.user,
                        asignado_at=timezone.now(),
                        cantidad_entregada=qty,
                        active=True,
                        estado="pendiente",
                    )
                    created += 1

                    HerramientaAsignacionLog.objects.create(
                        asignacion=a,
                        accion="create",
                        by_user=request.user,
                        cambios={
                            "herramienta": str(h),
                            "asignado_a": to_user.get_full_name() or to_user.username,
                            "cantidad_entregada": qty,
                            "stock": {
                                "from": before_stock,
                                "to": new_stock,
                            },
                            "massive": True,
                        },
                        nota="Bulk assignment",
                    )

                h.cantidad = new_stock

                if h.cantidad <= 0:
                    h.status = "asignada"

                h.updated_at = timezone.now()
                h.save(update_fields=["cantidad", "status", "updated_at"])

        messages.success(
            request,
            f"✅ Bulk assignment completed. Assignments created: {created}.",
        )
        return redirect("logistica:herramientas_asignaciones_panel")

    except ValidationError as e:
        messages.error(request, f"❌ {e}")
        return redirect(
            f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}"
        )

    except Exception as e:
        messages.error(
            request, f"❌ Unexpected error while saving bulk assignment: {e}"
        )
        return redirect(
            f"{reverse('logistica:herramientas_asignacion_masiva')}?ids={','.join(str(i) for i in ids)}"
        )
