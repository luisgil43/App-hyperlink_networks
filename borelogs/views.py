# borelogs/views.py
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import BoreLogForm
from .models import (BoreLog, BoreLogEntry, BoreLogRodValue,
                     BoreLogTemplateConfig)
from .services.docx_borelog import (build_header_cell_map, build_rod_cell_map,
                                    render_borelog_docx)


def _ensure_rod_rows(borelog: BoreLog, max_rods: int = 50) -> None:
    existing = set(borelog.rod_values.values_list("rod_number", flat=True))
    missing = [i for i in range(1, max_rods + 1) if i not in existing]
    BoreLogRodValue.objects.bulk_create(
        [BoreLogRodValue(borelog=borelog, rod_number=i) for i in missing],
        ignore_conflicts=True,
    )


def _get_default_template_bytes() -> bytes:
    """
    Template fijo dentro del repo (static).
    """
    template_path = Path(settings.BASE_DIR) / "borelogs" / "static" / "borelogs" / "templates" / "Bore_Log_20251229Vermeer.docx"
    return template_path.read_bytes()


def _get_or_create_template_config() -> BoreLogTemplateConfig:
    obj, _created = BoreLogTemplateConfig.objects.get_or_create(pk=1)
    return obj


def _redirect_back_for_borelog(obj: BoreLog) -> HttpResponse:
    """
    Si el BoreLog pertenece a un Billing, volvemos a su lista.
    Si no, volvemos a lista global.
    """
    if obj.sesion_id:
        return redirect("borelogs:borelog_list_for_billing", sesion_id=obj.sesion_id)
    return redirect("borelogs:borelog_list")


# ✅ NUEVO: Lista por Billing
@login_required
def borelog_list_for_billing(request: HttpRequest, sesion_id: int) -> HttpResponse:
    # Import local para evitar problemas de circularidad
    from operaciones.models import \
        SesionBilling  # ajusta si tu app/modelo está en otro módulo

    sesion = get_object_or_404(SesionBilling, pk=sesion_id)
    qs = BoreLog.objects.filter(sesion_id=sesion.id).order_by("-created_at")
    return render(request, "borelogs/borelog_list_for_billing.html", {"sesion": sesion, "borelogs": qs})


# ✅ NUEVO: Crear BoreLog dentro de un Billing
@login_required
def borelog_create_for_billing(request: HttpRequest, sesion_id: int) -> HttpResponse:
    from operaciones.models import \
        SesionBilling  # ajusta si tu app/modelo está en otro módulo

    sesion = get_object_or_404(SesionBilling, pk=sesion_id)

    if request.method == "POST":
        form = BoreLogForm(request.POST)
        if form.is_valid():
            obj: BoreLog = form.save(commit=False)
            obj.created_by = request.user
            obj.sesion_id = sesion.id  # ✅ clave: amarrarlo al billing
            # Si quieres prellenar project_name con el proyecto del billing cuando venga vacío:
            if not (obj.project_name or "").strip():
                obj.project_name = (getattr(sesion, "proyecto_id", "") or f"Billing #{sesion.id}").strip()
            obj.save()
            _ensure_rod_rows(obj, max_rods=50)
            messages.success(request, "Bore Log created successfully for this billing.")
            return redirect("borelogs:borelog_detail", pk=obj.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        initial = {
            "project_name": (getattr(sesion, "proyecto_id", "") or f"Billing #{sesion.id}").strip(),
        }
        form = BoreLogForm(initial=initial)

    return render(
        request,
        "borelogs/borelog_form.html",
        {
            "form": form,
            "mode": "create",
            "sesion": sesion,  # ✅ para que el template sepa a dónde volver
        },
    )


# -------------------------
# VISTAS GLOBALES (las de siempre)
# -------------------------

@login_required
def borelog_list(request: HttpRequest) -> HttpResponse:
    qs = BoreLog.objects.all().order_by("-created_at")
    return render(request, "borelogs/borelog_list.html", {"borelogs": qs})


@login_required
def borelog_create(request: HttpRequest) -> HttpResponse:
    """
    ⚠️ Opcional: mantener create global, pero la idea nueva es crearlo desde un Billing.
    Para evitar BoreLogs "huérfanos", aquí redirigimos a la lista de billings.
    """
    messages.error(request, "Please create Bore Logs from a Billing (Photo Requirements page).")
    return redirect("operaciones:listar_billing")


@login_required
def borelog_edit(request: HttpRequest, pk: int) -> HttpResponse:
    obj = get_object_or_404(BoreLog, pk=pk)
    if request.method == "POST":
        form = BoreLogForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Bore Log updated successfully.")
            return redirect("borelogs:borelog_detail", pk=obj.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = BoreLogForm(instance=obj)

    return render(
        request,
        "borelogs/borelog_form.html",
        {
            "form": form,
            "mode": "edit",
            "object": obj,
            "sesion_id": obj.sesion_id,  # ✅ para el back/cancel
        },
    )


@login_required
def borelog_delete(request: HttpRequest, pk: int) -> HttpResponse:
    obj = get_object_or_404(BoreLog, pk=pk)
    if request.method != "POST":
        return _redirect_back_for_borelog(obj)

    obj.delete()
    messages.success(request, "Bore Log deleted successfully.")
    # Ojo: si borramos, ya no existe obj, pero aún tenemos sesion_id
    if obj.sesion_id:
        return redirect("borelogs:borelog_list_for_billing", sesion_id=obj.sesion_id)
    return redirect("borelogs:borelog_list")


@login_required
def borelog_detail(request: HttpRequest, pk: int) -> HttpResponse:
    obj = get_object_or_404(BoreLog, pk=pk)
    _ensure_rod_rows(obj, max_rods=50)
    rod_values = list(obj.rod_values.all().order_by("rod_number"))

    if request.method == "POST":
        with transaction.atomic():
            for rv in rod_values:
                depth = request.POST.get(f"depth_{rv.rod_number}", "").strip()
                pitch = request.POST.get(f"pitch_{rv.rod_number}", "").strip()
                station = request.POST.get(f"station_{rv.rod_number}", "").strip()

                changed = (depth != rv.depth) or (pitch != rv.pitch) or (station != rv.station)
                if not changed:
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
                rv.save(update_fields=["depth", "pitch", "station", "last_source", "last_updated_by", "updated_at"])

        messages.success(request, "Bore Log values saved successfully.")
        return redirect("borelogs:borelog_detail", pk=obj.pk)

    return render(
        request,
        "borelogs/borelog_detail.html",
        {"borelog": obj, "rod_values": rod_values},
    )


@login_required
def borelog_download_docx(request: HttpRequest, pk: int) -> HttpResponse:
    """
    ✅ Aquí hacemos todo automático:
    - Lee template fijo
    - Si no hay mapas guardados, los genera 1 vez y los guarda en BoreLogTemplateConfig
    - Genera DOCX con header + rods
    """
    obj = get_object_or_404(BoreLog, pk=pk)

    template_bytes = _get_default_template_bytes()
    cfg = _get_or_create_template_config()

    # Auto-cache de mapas (1 sola vez)
    if not cfg.rod_cell_map:
        cfg.rod_cell_map = build_rod_cell_map(template_bytes, max_rods=50)

    if not cfg.header_cell_map:
        cfg.header_cell_map = build_header_cell_map(template_bytes)

    cfg.save()

    # Header values desde lo que ya llenaste en el sistema
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

    filename = f"Bore_Log_{(obj.project_name or 'Project').replace(' ', '_')}_{obj.pk}.docx"
    resp = HttpResponse(
        out_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp