from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from usuarios.decoradores import rol_requerido

from .forms_herramientas import InventarioUploadForm, RejectAssignmentForm
from .models import HerramientaAsignacion, HerramientaInventario


def _must_user(request):
    return bool(getattr(request.user, "es_usuario", False))


@login_required
@rol_requerido("usuario")
def mis_herramientas(request):
    """
    User sees ONLY their active assignments.
    Only assignments with cantidad_entregada > 0 are shown.
    Inventory status is based on the latest inventory record for that assignment.
    """
    if not _must_user(request):
        return HttpResponseForbidden("You do not have permission.")

    q = (request.GET.get("q") or "").strip()
    estado_asig = (request.GET.get("estado") or "").strip()
    cantidad = (request.GET.get("cantidad") or "20").strip()
    page_number = (request.GET.get("page") or "1").strip()

    try:
        per_page = int(cantidad)
    except Exception:
        per_page = 20

    if per_page not in (5, 10, 20, 50, 100):
        per_page = 20

    qs = (
        HerramientaAsignacion.objects.select_related("herramienta", "asignado_por")
        .filter(asignado_a=request.user, active=True)
        .filter(cantidad_entregada__gt=0)
        .order_by("-asignado_at", "-id")
    )

    if q:
        qs = qs.filter(
            Q(herramienta__nombre__icontains=q)
            | Q(herramienta__serial__icontains=q)
            | Q(herramienta__descripcion__icontains=q)
        )

    if estado_asig:
        qs = qs.filter(estado=estado_asig)

    paginator = Paginator(qs, per_page)
    pagina = paginator.get_page(page_number)

    asig_ids = [a.id for a in pagina.object_list]

    latest_inv_by_asig = {}
    if asig_ids:
        invs = (
            HerramientaInventario.objects.filter(asignacion_id__in=asig_ids)
            .select_related("revisado_por")
            .order_by("-created_at", "-id")
        )

        for inv in invs:
            if inv.asignacion_id not in latest_inv_by_asig:
                latest_inv_by_asig[inv.asignacion_id] = inv

    for a in pagina.object_list:
        a.last_inv = latest_inv_by_asig.get(a.id)

    pendientes = [a for a in pagina.object_list if a.estado == "pendiente"]

    return render(
        request,
        "logistica/user_mis_herramientas.html",
        {
            "pagina": pagina,
            "pendientes": pendientes,
            "q": q,
            "estado": estado_asig,
            "cantidad": str(per_page),
            "ESTADO_ASIG_CHOICES": HerramientaAsignacion.ESTADO_CHOICES,
        },
    )


@login_required
@rol_requerido("usuario")
def aceptar_herramientas(request):
    """
    Bulk or individual acceptance:
    - Receives POST with asignaciones[] ids.
    - If the user has no digital signature, redirect to register signature.
    - Assignment cannot be accepted without signature.
    """
    if request.method != "POST":
        raise Http404()

    if not _must_user(request):
        return HttpResponseForbidden("You do not have permission.")

    if not getattr(request.user, "firma_digital", None):
        next_url = (
            request.POST.get("next") or reverse("logistica:mis_herramientas")
        ).strip()

        messages.error(
            request,
            "You must register your digital signature before accepting tools.",
        )
        return redirect(
            f"{reverse('dashboard:registrar_firma_usuario')}?next={next_url}"
        )

    ids = request.POST.getlist("asignaciones")

    if not ids:
        messages.warning(request, "Select at least one tool.")
        return redirect("logistica:mis_herramientas")

    qs = HerramientaAsignacion.objects.select_related("herramienta").filter(
        pk__in=ids,
        asignado_a=request.user,
        active=True,
    )

    updated = 0
    now = timezone.now()

    for a in qs:
        if a.estado != "pendiente":
            continue

        a.estado = "aceptada"
        a.aceptado_at = now
        a.comentario_rechazo = None
        a.rechazado_at = None
        a.save(
            update_fields=[
                "estado",
                "aceptado_at",
                "comentario_rechazo",
                "rechazado_at",
            ]
        )

        h = a.herramienta
        if h.status != "asignada":
            h.status = "asignada"
            h.save(update_fields=["status", "updated_at"])

        updated += 1

    if updated:
        messages.success(
            request,
            f"✅ You accepted {updated} tool(s). Your digital signature was recorded.",
        )
    else:
        messages.info(request, "There were no pending tools to accept.")

    return redirect("logistica:mis_herramientas")


@login_required
@rol_requerido("usuario")
def rechazar_herramienta(request, asignacion_id: int):
    """
    Individual rejection:
    - Template modal sends POST with a comment.
    - Assignment remains rejected with comment visible to user and admin.
    """
    if not _must_user(request):
        return HttpResponseForbidden("You do not have permission.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
        asignado_a=request.user,
        active=True,
    )

    if request.method != "POST":
        raise Http404()

    form = RejectAssignmentForm(request.POST)

    if not form.is_valid():
        messages.error(request, "You must enter a comment to reject this tool.")
        return redirect("logistica:mis_herramientas")

    if a.estado == "aceptada":
        messages.error(
            request,
            "This tool has already been accepted. If there is an issue, please contact Logistics.",
        )
        return redirect("logistica:mis_herramientas")

    now = timezone.now()

    a.estado = "rechazada"
    a.comentario_rechazo = form.cleaned_data["comentario"]
    a.rechazado_at = now
    a.save(
        update_fields=[
            "estado",
            "comentario_rechazo",
            "rechazado_at",
        ]
    )

    messages.warning(
        request,
        "❌ Tool rejected. Logistics will be notified if applicable.",
    )
    return redirect("logistica:mis_herramientas")


@login_required
@rol_requerido("usuario")
def subir_inventario(request, asignacion_id: int):
    """
    User uploads an inventory photo.
    - Only allowed if inventory_required=True.
    - After upload, inventory remains pending review and the button is disabled.
    - If admin rejects it, the button is enabled again by reject().
    """
    if not _must_user(request):
        return HttpResponseForbidden("You do not have permission.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
        asignado_a=request.user,
        active=True,
    )

    h = a.herramienta

    if not h.inventory_required:
        messages.info(request, "This tool does not have an inventory request.")
        return redirect("logistica:mis_herramientas")

    already_pending = HerramientaInventario.objects.filter(
        asignacion=a,
        estado="pendiente",
    ).exists()

    if already_pending:
        messages.info(
            request,
            "You have already submitted an inventory photo and it is pending review.",
        )
        return redirect("logistica:mis_herramientas")

    if request.method == "POST":
        form = InventarioUploadForm(request.POST, request.FILES)

        if form.is_valid():
            inv = form.save(commit=False)
            inv.herramienta = h
            inv.asignacion = a
            inv.estado = "pendiente"
            inv.save()

            h.inventory_required = False
            h.save(update_fields=["inventory_required", "updated_at"])

            messages.success(
                request,
                "📸 Inventory submitted. It is now pending review.",
            )
            return redirect("logistica:mis_herramientas")

        messages.error(
            request,
            "The photo could not be uploaded. Please check the file.",
        )

    else:
        form = InventarioUploadForm()

    return render(
        request,
        "logistica/user_inventario_subir.html",
        {
            "asignacion": a,
            "herramienta": h,
            "form": form,
        },
    )


@login_required
@rol_requerido("usuario")
def historial_inventario(request, asignacion_id: int):
    """
    User sees inventory history ONLY for their own assignment.
    """
    if not _must_user(request):
        return HttpResponseForbidden("You do not have permission.")

    a = get_object_or_404(
        HerramientaAsignacion.objects.select_related("herramienta"),
        pk=asignacion_id,
        asignado_a=request.user,
        active=True,
    )

    invs = (
        HerramientaInventario.objects.filter(asignacion=a)
        .select_related("revisado_por")
        .order_by("-created_at")
    )

    return render(
        request,
        "logistica/user_inventario_historial.html",
        {
            "asignacion": a,
            "herramienta": a.herramienta,
            "inventarios": list(invs),
        },
    )
