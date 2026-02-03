# borelogs/views.py
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from usuarios.decoradores import rol_requerido

from .forms import BoreLogForm
from .models import (BoreLog, BoreLogEntry, BoreLogRodValue,
                     BoreLogTemplateConfig)
from .services.docx_borelog import (build_header_cell_map, build_rod_cell_map,
                                    render_borelog_docx)

DEFAULT_MAX_RODS = 50

# ✅ Ajusta nombres exactos si tus grupos se llaman distinto
ADMIN_GROUPS = ["supervisor", "pm", "admin", "facturacion"]


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def _ensure_rod_rows(borelog: BoreLog, max_rods: int = DEFAULT_MAX_RODS) -> None:
    existing = set(borelog.rod_values.values_list("rod_number", flat=True))
    missing = [i for i in range(1, max_rods + 1) if i not in existing]
    if not missing:
        return

    BoreLogRodValue.objects.bulk_create(
        [BoreLogRodValue(borelog=borelog, rod_number=i) for i in missing],
        ignore_conflicts=True,
    )


def _get_default_template_bytes() -> bytes:
    template_path = (
        Path(settings.BASE_DIR)
        / "borelogs"
        / "static"
        / "borelogs"
        / "templates"
        / "Bore_Log_20251229Vermeer.docx"
    )
    if not template_path.exists():
        raise FileNotFoundError(f"No existe template DOCX en: {template_path}")
    return template_path.read_bytes()


def _get_or_create_template_config() -> BoreLogTemplateConfig:
    obj, _created = BoreLogTemplateConfig.objects.get_or_create(pk=1)
    return obj


def _get_sesion_billing_or_404(sesion_id: int):
    from operaciones.models import SesionBilling
    return get_object_or_404(SesionBilling, pk=sesion_id)


def _can_see_admin_ui(user) -> bool:
    """
    ✅ Admin UI para:
    - superuser
    - staff
    - grupos ADMIN_GROUPS
    """
    if not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "is_staff", False):
        return True
    return user.groups.filter(name__in=ADMIN_GROUPS).exists()


def _save_rod_values_from_post(request: HttpRequest, obj: BoreLog, rod_values: list[BoreLogRodValue]) -> None:
    with transaction.atomic():
        for rv in rod_values:
            depth = (request.POST.get(f"depth_{rv.rod_number}", "") or "").strip()
            pitch = (request.POST.get(f"pitch_{rv.rod_number}", "") or "").strip()
            station = (request.POST.get(f"station_{rv.rod_number}", "") or "").strip()

            if depth == (rv.depth or "") and pitch == (rv.pitch or "") and station == (rv.station or ""):
                continue

            BoreLogEntry.objects.create(
                borelog=obj,
                rod_number=rv.rod_number,
                depth=depth,
                pitch=pitch,
                station=station,
                source=BoreLogEntry.SOURCE_WEB,
                created_by=request.user,
            )

            rv.depth = depth
            rv.pitch = pitch
            rv.station = station
            rv.last_source = BoreLogEntry.SOURCE_WEB
            rv.last_updated_by = request.user
            rv.save(
                update_fields=[
                    "depth",
                    "pitch",
                    "station",
                    "last_source",
                    "last_updated_by",
                    "updated_at",
                ]
            )


def _redirect_back_for_borelog_user(obj: BoreLog) -> HttpResponse:
    if obj.sesion_id:
        return redirect("borelogs:borelog_list_for_billing_user", sesion_id=obj.sesion_id)
    return redirect("borelogs:borelog_list")


def _redirect_back_for_borelog_admin(obj: BoreLog) -> HttpResponse:
    if obj.sesion_id:
        return redirect("borelogs:borelog_list_for_billing_admin", sesion_id=obj.sesion_id)
    return redirect("borelogs:borelog_list")


# -----------------------------------------------------------------------------
# LISTAS POR BILLING
# -----------------------------------------------------------------------------

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion", "usuario")
def borelog_list_for_billing(request: HttpRequest, sesion_id: int) -> HttpResponse:
    """
    Router de lista:
    - si NO puede admin => user
    - si puede admin => admin
    """
    if _can_see_admin_ui(request.user):
        return redirect("borelogs:borelog_list_for_billing_admin", sesion_id=sesion_id)
    return redirect("borelogs:borelog_list_for_billing_user", sesion_id=sesion_id)


@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion", "usuario")
def borelog_list_for_billing_user(request: HttpRequest, sesion_id: int) -> HttpResponse:
    sesion = _get_sesion_billing_or_404(sesion_id)
    qs = BoreLog.objects.filter(sesion_id=sesion.id).order_by("-created_at")
    return render(
        request,
        "borelogs/borelog_list_for_billing_user.html",
        {"sesion": sesion, "borelogs": qs},
    )


@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_list_for_billing_admin(request: HttpRequest, sesion_id: int) -> HttpResponse:
    sesion = _get_sesion_billing_or_404(sesion_id)
    qs = BoreLog.objects.filter(sesion_id=sesion.id).order_by("-created_at")
    return render(
        request,
        "borelogs/borelog_list_for_billing_admin.html",
        {"sesion": sesion, "borelogs": qs},
    )


# -----------------------------------------------------------------------------
# CREATE POR BILLING
# -----------------------------------------------------------------------------

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_create_for_billing(request: HttpRequest, sesion_id: int) -> HttpResponse:
    """
    Crea BoreLog para un billing.
    Nota: esto normalmente es admin/roles elevados.
    """
    sesion = _get_sesion_billing_or_404(sesion_id)

    if request.method == "POST":
        form = BoreLogForm(request.POST)
        if form.is_valid():
            obj: BoreLog = form.save(commit=False)
            obj.created_by = request.user
            obj.sesion_id = sesion.id

            if not (obj.project_name or "").strip():
                obj.project_name = (getattr(sesion, "proyecto_id", "") or f"Billing #{sesion.id}").strip()

            obj.save()
            _ensure_rod_rows(obj, max_rods=DEFAULT_MAX_RODS)

            messages.success(request, "Bore Log created successfully for this billing.")
            return redirect("borelogs:borelog_detail_admin", pk=obj.pk)

        messages.error(request, "Please fix the errors below.")
    else:
        initial = {
            "project_name": (getattr(sesion, "proyecto_id", "") or f"Billing #{sesion.id}").strip(),
        }
        form = BoreLogForm(initial=initial)

    return render(
        request,
        "borelogs/borelog_form.html",
        {"form": form, "mode": "create", "sesion": sesion},
    )


# -----------------------------------------------------------------------------
# GLOBALES
# -----------------------------------------------------------------------------

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion", "usuario")
def borelog_list(request: HttpRequest) -> HttpResponse:
    qs = BoreLog.objects.all().order_by("-created_at")
    return render(request, "borelogs/borelog_list.html", {"borelogs": qs})


@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_create(request: HttpRequest) -> HttpResponse:
    messages.error(request, "Please create Bore Logs from a Billing (Photo Requirements page).")
    return redirect("operaciones:listar_billing")


# -----------------------------------------------------------------------------
# DETAIL SEPARADO
# -----------------------------------------------------------------------------

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion", "usuario")
def borelog_detail_user(request: HttpRequest, pk: int) -> HttpResponse:
    """
    ✅ Usuario SIEMPRE cae aquí desde el UI usuario.
    Preserva querystring (ej: back_asig=180) incluso después de Save (POST).
    """
    obj = get_object_or_404(BoreLog, pk=pk)

    _ensure_rod_rows(obj, max_rods=DEFAULT_MAX_RODS)
    rod_values = list(obj.rod_values.all().order_by("rod_number"))

    if request.method == "POST":
        _save_rod_values_from_post(request, obj, rod_values)
        messages.success(request, "Bore Log values saved successfully.")

        # ✅ Mantener querystring (back_asig, ui, etc.) después del Save
        url = reverse("borelogs:borelog_detail_user", kwargs={"pk": obj.pk})
        qs = request.GET.urlencode()
        if qs:
            url = f"{url}?{qs}"
        return redirect(url)

    return render(
        request,
        "borelogs/borelog_detail_user.html",
        {"borelog": obj, "rod_values": rod_values},
    )

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_detail_admin(request: HttpRequest, pk: int) -> HttpResponse:
    """
    ✅ Admin SIEMPRE cae aquí.
    """
    obj = get_object_or_404(BoreLog, pk=pk)

    _ensure_rod_rows(obj, max_rods=DEFAULT_MAX_RODS)
    rod_values = list(obj.rod_values.all().order_by("rod_number"))

    if request.method == "POST":
        _save_rod_values_from_post(request, obj, rod_values)
        messages.success(request, "Bore Log values saved successfully.")
        return redirect("borelogs:borelog_detail_admin", pk=obj.pk)

    return render(
        request,
        "borelogs/borelog_detail.html",
        {"borelog": obj, "rod_values": rod_values},
    )


@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion", "usuario")
def borelog_detail_router(request: HttpRequest, pk: int) -> HttpResponse:
    """
    Router opcional:
    /borelogs/<pk>/ -> manda a /admin/ si puede admin, si no a /user/
    """
    if _can_see_admin_ui(request.user):
        return redirect("borelogs:borelog_detail_admin", pk=pk)
    return redirect("borelogs:borelog_detail_user", pk=pk)


# -----------------------------------------------------------------------------
# EDIT / DELETE (admin)
# -----------------------------------------------------------------------------

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_edit(request: HttpRequest, pk: int) -> HttpResponse:
    obj = get_object_or_404(BoreLog, pk=pk)

    if request.method == "POST":
        form = BoreLogForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Bore Log updated successfully.")
            return redirect("borelogs:borelog_detail_admin", pk=obj.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = BoreLogForm(instance=obj)

    return render(
        request,
        "borelogs/borelog_form.html",
        {"form": form, "mode": "edit", "object": obj, "sesion_id": obj.sesion_id},
    )


@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_delete(request: HttpRequest, pk: int) -> HttpResponse:
    obj = get_object_or_404(BoreLog, pk=pk)

    if request.method != "POST":
        return _redirect_back_for_borelog_admin(obj)

    sesion_id = obj.sesion_id
    obj.delete()

    messages.success(request, "Bore Log deleted successfully.")
    if sesion_id:
        return redirect("borelogs:borelog_list_for_billing_admin", sesion_id=sesion_id)
    return redirect("borelogs:borelog_list")


# -----------------------------------------------------------------------------
# DOWNLOAD DOCX (admin)
# -----------------------------------------------------------------------------

@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def borelog_download_docx(request: HttpRequest, pk: int) -> HttpResponse:
    obj = get_object_or_404(BoreLog, pk=pk)

    try:
        template_bytes = _get_default_template_bytes()
    except FileNotFoundError as e:
        messages.error(request, str(e))
        return redirect("borelogs:borelog_detail_admin", pk=obj.pk)

    cfg = _get_or_create_template_config()

    changed = False
    if not cfg.rod_cell_map:
        cfg.rod_cell_map = build_rod_cell_map(template_bytes, max_rods=DEFAULT_MAX_RODS)
        changed = True

    if not cfg.header_cell_map:
        cfg.header_cell_map = build_header_cell_map(template_bytes)
        changed = True

    if changed:
        cfg.save(update_fields=["rod_cell_map", "header_cell_map", "updated_at"])

    header_values = {
        "rod_length": obj.rod_length or "",
        "driller_name": obj.driller_name or "",
        "vendor_name": obj.vendor_name or "",
        "project_name": obj.project_name or "",
    }

    rod_values_qs = obj.rod_values.all().order_by("rod_number")
    rod_values = {
        rv.rod_number: {"depth": rv.depth or "", "pitch": rv.pitch or "", "station": rv.station or ""}
        for rv in rod_values_qs
    }

    out_bytes = render_borelog_docx(
        template_bytes=template_bytes,
        rod_cell_map=cfg.rod_cell_map,
        header_cell_map=cfg.header_cell_map,
        header_values=header_values,
        rod_values=rod_values,
    )

    safe_project = (obj.project_name or "Project").replace(" ", "_")
    filename = f"Bore_Log_{safe_project}_{obj.pk}.docx"

    resp = HttpResponse(
        out_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp