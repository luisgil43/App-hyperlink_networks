# fleet/views.py
from __future__ import annotations

import csv
from collections import defaultdict
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from usuarios.decoradores import rol_requerido

from .forms import (VehicleAssignmentForm, VehicleForm,
                    VehicleNotificationConfigForm, VehicleOdometerLogForm,
                    VehicleServiceForm, VehicleServiceTypeForm,
                    VehicleStatusForm)
from .models import (Vehicle, VehicleAssignment, VehicleNotificationConfig,
                     VehicleOdometerEvent, VehicleService, VehicleServiceType,
                     VehicleStatus)


def _is_fuel_service_type_name(name: str) -> bool:
    return (name or "").strip().lower() == "fuel"


def _service_type_label(s: VehicleService) -> str:
    if getattr(s, "service_type_obj_id", None):
        return s.service_type_obj.name
    return s.get_service_type_display()

# =========================
# Home
# =========================

@login_required
@rol_requerido("supervisor", "admin", "pm")
def fleet_home(request):
    return redirect("fleet:vehicles_list")


# =========================
# Helpers (labels)
# =========================

def _assigned_to_label(vehicle: Vehicle) -> str:
    asg = (
        VehicleAssignment.objects.select_related("user")
        .filter(vehicle=vehicle, is_active=True)
        .order_by("-assigned_at")
        .first()
    )
    if not asg:
        return "—"
    name = asg.user.get_full_name() or getattr(asg.user, "username", "") or str(asg.user)
    return name or "—"


def _last_movement_label(vehicle: Vehicle) -> str:
    if not vehicle.last_movement_at:
        return "—"
    return timezone.localtime(vehicle.last_movement_at).strftime("%Y-%m-%d %H:%M")


# =========================
# Vehicles (PRO list)
# =========================


@login_required
@rol_requerido("supervisor", "admin", "pm")
def vehicle_list(request):
    import json
    from datetime import datetime, timedelta

    # --- filters ---
    q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "active").strip().lower()

    per_page_str = request.GET.get("cantidad") or request.GET.get("per_page") or "20"
    try:
        per_page = 1000000 if per_page_str == "todos" else int(per_page_str)
    except Exception:
        per_page = 20
        per_page_str = "20"

    # ✅ Excel-like filters (server-side) via URL param: xfilters (JSON)
    # Format example: {"0":["ABC123"],"1":["1HG..."],"8":["Active"]}
    raw_xfilters = (request.GET.get("xfilters") or "").strip()
    xfilters = {}
    if raw_xfilters:
        try:
            xfilters = json.loads(raw_xfilters) or {}
            if not isinstance(xfilters, dict):
                xfilters = {}
        except Exception:
            xfilters = {}

    qs = Vehicle.objects.select_related("status").all().order_by("patente")

    # --- quick search (server) ---
    if q:
        qs = qs.filter(
            Q(patente__icontains=q)
            | Q(vin__icontains=q)
            | Q(marca__icontains=q)
            | Q(modelo__icontains=q)
            | Q(anio__icontains=q)
        )

    # Active/Inactive/All
    if status_filter == "active":
        qs = qs.filter(status__is_active=True)
    elif status_filter == "inactive":
        qs = qs.filter(Q(status__is_active=False) | Q(status__isnull=True))

    # ============================
    # ✅ Apply Excel column filters (ALL records, before pagination)
    # Column mapping:
    # 0 Plate -> patente
    # 1 VIN -> vin
    # 2 Make -> marca
    # 3 Model -> modelo
    # 4 Year -> anio
    # 5 Odometer -> kilometraje_actual
    # 6 Assigned to -> active assignment user (best-effort)
    # 7 Last movement -> last_movement_at (best-effort)
    # 8 Status -> status.name / null
    # ============================

    def _norm_values(vals):
        out = []
        for v in vals or []:
            s = (str(v) if v is not None else "").strip()
            if s == "":
                continue
            out.append(s)
        return out

    # 0 Plate
    vals = _norm_values(xfilters.get("0"))
    if vals:
        if "—" in vals:
            qs = qs.filter(
                Q(patente__isnull=True) | Q(patente="") | Q(patente__in=vals)
            )
        else:
            qs = qs.filter(patente__in=vals)

    # 1 VIN
    vals = _norm_values(xfilters.get("1"))
    if vals:
        if "—" in vals:
            qs = qs.filter(Q(vin__isnull=True) | Q(vin="") | Q(vin__in=vals))
        else:
            qs = qs.filter(vin__in=vals)

    # 2 Make
    vals = _norm_values(xfilters.get("2"))
    if vals:
        if "—" in vals:
            qs = qs.filter(Q(marca="") | Q(marca__isnull=True) | Q(marca__in=vals))
        else:
            qs = qs.filter(marca__in=vals)

    # 3 Model
    vals = _norm_values(xfilters.get("3"))
    if vals:
        if "—" in vals:
            qs = qs.filter(Q(modelo="") | Q(modelo__isnull=True) | Q(modelo__in=vals))
        else:
            qs = qs.filter(modelo__in=vals)

    # 4 Year
    vals = _norm_values(xfilters.get("4"))
    if vals:
        years = []
        has_blank = False
        for s in vals:
            if s == "—":
                has_blank = True
                continue
            try:
                years.append(int(s))
            except Exception:
                continue
        qy = Q()
        if years:
            qy |= Q(anio__in=years)
        if has_blank:
            qy |= Q(anio__isnull=True)
        qs = qs.filter(qy) if qy else qs

    # 5 Odometer
    vals = _norm_values(xfilters.get("5"))
    if vals:
        odos = []
        has_blank = False
        for s in vals:
            if s == "—":
                has_blank = True
                continue
            try:
                odos.append(int(str(s).replace(",", "").strip()))
            except Exception:
                continue
        qo = Q()
        if odos:
            qo |= Q(kilometraje_actual__in=odos)
        if has_blank:
            qo |= Q(kilometraje_actual__isnull=True)
        qs = qs.filter(qo) if qo else qs

    # 6 Assigned to (active assignment user)
    # Best-effort: match exact label text against (first_name, last_name, username)
    vals = _norm_values(xfilters.get("6"))
    if vals:
        has_blank = "—" in vals
        vals2 = [v for v in vals if v != "—"]

        qa = Q()
        if vals2:
            # match any selected label as icontains on name/username
            sub = Q()
            for name in vals2:
                sub |= (
                    Q(
                        assignments__is_active=True,
                        assignments__user__username__icontains=name,
                    )
                    | Q(
                        assignments__is_active=True,
                        assignments__user__first_name__icontains=name,
                    )
                    | Q(
                        assignments__is_active=True,
                        assignments__user__last_name__icontains=name,
                    )
                )
            qa |= sub
        if has_blank:
            qa |= Q(
                assignments__is_active=True
            )  # we'll invert below to keep unassigned
            qs = qs.filter(qa).distinct()
            # keep only those with NO active assignment
            qs = qs.exclude(assignments__is_active=True)
        else:
            qs = qs.filter(qa).distinct()

    # 7 Last movement (label is "YYYY-MM-DD HH:MM" or "—")
    vals = _norm_values(xfilters.get("7"))
    if vals:
        has_blank = "—" in vals
        vals2 = [v for v in vals if v != "—"]
        qlm = Q()
        if vals2:
            for s in vals2:
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
                    # match same minute
                    start = timezone.make_aware(dt, timezone.get_current_timezone())
                    end = start + timedelta(minutes=1)
                    qlm |= Q(last_movement_at__gte=start, last_movement_at__lt=end)
                except Exception:
                    # allow filtering by just date prefix if user selected something like "2026-04-07"
                    if len(s) == 10 and s[4] == "-" and s[7] == "-":
                        qlm |= Q(last_movement_at__date=s)
        if has_blank:
            qlm |= Q(last_movement_at__isnull=True)
        qs = qs.filter(qlm) if qlm else qs

    # 8 Status (name or blank)
    vals = _norm_values(xfilters.get("8"))
    if vals:
        has_blank = "—" in vals
        vals2 = [v for v in vals if v != "—"]
        qs2 = Q()
        if vals2:
            qs2 |= Q(status__name__in=vals2)
        if has_blank:
            qs2 |= Q(status__isnull=True)
        qs = qs.filter(qs2)

    # --- pagination ---
    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    # map labels (sin N+1 en assignments)
    vehicle_ids = [v.id for v in page.object_list]
    active_asg = (
        VehicleAssignment.objects.select_related("user", "vehicle")
        .filter(vehicle_id__in=vehicle_ids, is_active=True)
        .order_by("vehicle_id", "-assigned_at")
    )

    first_asg_by_vehicle: dict[int, VehicleAssignment] = {}
    for a in active_asg:
        if a.vehicle_id not in first_asg_by_vehicle:
            first_asg_by_vehicle[a.vehicle_id] = a

    for v in page.object_list:
        a = first_asg_by_vehicle.get(v.id)
        if a:
            v.assigned_to_label = (
                a.user.get_full_name() or getattr(a.user, "username", "") or str(a.user)
            ) or "—"
        else:
            v.assigned_to_label = "—"
        v.last_movement_label = _last_movement_label(v)

    statuses = VehicleStatus.objects.all().order_by("name")

    base_params = {}
    if q:
        base_params["q"] = q
    if status_filter:
        base_params["status"] = status_filter
    if per_page_str:
        base_params["cantidad"] = per_page_str
    if raw_xfilters:
        base_params["xfilters"] = raw_xfilters
    base_qs = urlencode(base_params)

    ctx = {
        "page": page,
        "q": q,
        "status": status_filter,
        "per_page": int(per_page) if per_page_str != "todos" else per_page_str,
        "cantidad": per_page_str,
        "base_qs": base_qs,
        "statuses": statuses,
        "xfilters": raw_xfilters,  # opcional si quieres mostrarlo/debug
    }
    return render(request, "fleet/vehicle_list.html", ctx)


@login_required
@rol_requerido("supervisor", "admin", "pm")
def vehicle_create(request):
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Vehicle created.")
            return redirect("fleet:vehicles_list")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleForm()

    return render(request, "fleet/vehicle_form.html", {"form": form, "mode": "create"})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def vehicle_edit(request, pk: int):
    vehicle = get_object_or_404(Vehicle, pk=pk)

    if request.method == "POST":
        form = VehicleForm(request.POST, instance=vehicle)
        if form.is_valid():
            form.save()
            messages.success(request, "Vehicle updated.")
            return redirect("fleet:vehicle_detail", pk=vehicle.pk)
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleForm(instance=vehicle)

    return render(request, "fleet/vehicle_form.html", {"form": form, "mode": "edit", "vehicle": vehicle})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def vehicle_detail(request, pk: int):
    vehicle = get_object_or_404(Vehicle.objects.select_related("status"), pk=pk)
    vehicle.assigned_to_label = _assigned_to_label(vehicle)
    vehicle.last_movement_label = _last_movement_label(vehicle)
    return render(request, "fleet/vehicle_detail.html", {"vehicle": vehicle})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def vehicle_delete(request, pk: int):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == "POST":
        vehicle.delete()
        messages.success(request, "Vehicle deleted.")
        return redirect("fleet:vehicles_list")
    return render(request, "fleet/vehicle_delete_confirm.html", {"vehicle": vehicle})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def vehicle_change_status(request, pk: int):
    vehicle = get_object_or_404(Vehicle, pk=pk)

    if request.method == "POST":
        status_id = (request.POST.get("status_id") or "").strip()

        if not status_id:
            vehicle.status = None
            vehicle.save(update_fields=["status"])
            messages.success(request, "Vehicle status cleared.")
            return redirect("fleet:vehicle_detail", pk=vehicle.pk)

        try:
            st = VehicleStatus.objects.get(pk=int(status_id))
        except Exception:
            messages.error(request, "Invalid status.")
            return redirect("fleet:vehicle_detail", pk=vehicle.pk)

        vehicle.status = st
        vehicle.save(update_fields=["status"])
        messages.success(request, "Vehicle status updated.")
        return redirect("fleet:vehicle_detail", pk=vehicle.pk)

    statuses = VehicleStatus.objects.filter(is_active=True).order_by("name")
    return render(request, "fleet/vehicle_change_status.html", {"vehicle": vehicle, "statuses": statuses})


# =========================
# Vehicle Statuses
# =========================

@login_required
@rol_requerido("supervisor", "admin", "pm")
def status_manage(request):
    statuses = VehicleStatus.objects.all().order_by("name")

    if request.method == "POST":
        form = VehicleStatusForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Status created.")
            return redirect("fleet:status_manage")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleStatusForm()

    return render(request, "fleet/status_manage.html", {"statuses": statuses, "form": form})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def status_edit(request, pk: int):
    st = get_object_or_404(VehicleStatus, pk=pk)

    if request.method == "POST":
        form = VehicleStatusForm(request.POST, instance=st)
        if form.is_valid():
            form.save()
            messages.success(request, "Status updated.")
            return redirect("fleet:status_manage")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleStatusForm(instance=st)

    return render(request, "fleet/status_form.html", {"form": form, "status": st, "is_edit": True})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def status_toggle(request, pk: int):
    st = get_object_or_404(VehicleStatus, pk=pk)
    st.is_active = not bool(st.is_active)
    st.save(update_fields=["is_active"])
    messages.success(request, "Status updated.")
    return redirect("fleet:status_manage")


@login_required
@rol_requerido("supervisor", "admin", "pm")
def status_delete(request, pk: int):
    st = get_object_or_404(VehicleStatus, pk=pk)
    if request.method == "POST":
        st.delete()
        messages.success(request, "Status deleted.")
        return redirect("fleet:status_manage")
    return render(request, "fleet/status_delete_confirm.html", {"status": st})


# =========================
# Assignments
# =========================


@login_required
@rol_requerido("supervisor", "admin", "pm")
def assignments_list(request):
    # 1) Activos (1 por vehículo, por constraint)
    active_qs = (
        VehicleAssignment.objects.select_related("vehicle", "user")
        .filter(is_active=True)
        .order_by("vehicle__patente")
    )

    active_vehicle_ids = list(active_qs.values_list("vehicle_id", flat=True))

    # 2) Para vehículos sin activo: último CLOSED (pausado) por vehículo
    closed_qs = (
        VehicleAssignment.objects.select_related("vehicle", "user")
        .filter(is_active=False)
        .exclude(vehicle_id__in=active_vehicle_ids)
        .order_by("vehicle_id", "-assigned_at", "-id")
    )

    latest_closed_by_vehicle = {}
    for a in closed_qs:
        if a.vehicle_id not in latest_closed_by_vehicle:
            latest_closed_by_vehicle[a.vehicle_id] = a

    assignments = list(active_qs) + list(latest_closed_by_vehicle.values())

    # Orden final (como te guste)
    assignments.sort(key=lambda x: (x.vehicle.patente or "").upper(), reverse=False)

    return render(request, "fleet/assignment_list.html", {"assignments": assignments})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def assignment_create(request):
    if request.method == "POST":
        form = VehicleAssignmentForm(request.POST)
        if form.is_valid():
            vehicle = form.cleaned_data["vehicle"]
            user = form.cleaned_data["user"]
            is_active = form.cleaned_data.get("is_active", True)

            if is_active:
                active = VehicleAssignment.objects.filter(vehicle=vehicle, is_active=True).first()
                if active:
                    messages.error(request, f"This vehicle already has an active assignment ({active.user}).")
                    return render(request, "fleet/assignment_form.html", {"form": form, "is_edit": False})

            VehicleAssignment.objects.create(vehicle=vehicle, user=user, is_active=is_active)
            messages.success(request, "Assignment created.")
            return redirect("fleet:assignments_list")

        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleAssignmentForm(initial={"is_active": True})

    return render(request, "fleet/assignment_form.html", {"form": form, "is_edit": False})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def assignment_edit(request, pk: int):
    asg = get_object_or_404(VehicleAssignment, pk=pk)

    if request.method == "POST":
        form = VehicleAssignmentForm(request.POST, instance=asg)
        if form.is_valid():
            new_vehicle = form.cleaned_data["vehicle"]
            new_user = form.cleaned_data["user"]
            new_is_active = bool(form.cleaned_data.get("is_active", True))

            changed_user = int(new_user.id) != int(asg.user_id)
            changed_vehicle = int(new_vehicle.id) != int(asg.vehicle_id)
            changed_owner_or_vehicle = changed_user or changed_vehicle

            # ✅ SOLO si cambió usuario o vehículo => transfer (nuevo registro)
            if changed_owner_or_vehicle:
                now = timezone.now()

                with transaction.atomic():
                    # 1) cerrar el assignment actual SOLO si estaba activo
                    if asg.is_active:
                        asg.is_active = False
                        asg.closed_at = now
                        asg.save(update_fields=["is_active", "closed_at"])

                    # 2) si el nuevo quedará activo, cerrar cualquier activo del vehículo destino
                    if new_is_active:
                        VehicleAssignment.objects.filter(
                            vehicle=new_vehicle, is_active=True
                        ).update(is_active=False, closed_at=now)

                    # 3) crear el nuevo assignment (assigned_at será NOW por auto_now_add)
                    VehicleAssignment.objects.create(
                        vehicle=new_vehicle,
                        user=new_user,
                        is_active=new_is_active,
                        closed_at=None,
                    )

                messages.success(
                    request, "Assignment transferred (new assignment created)."
                )
                return redirect("fleet:assignments_list")

            # ✅ Si NO cambió user/vehicle: update normal (NO tocar assigned_at)
            obj = form.save(commit=False)

            # coherencia: si queda activo, no puede tener closed_at
            if obj.is_active:
                obj.closed_at = None

            obj.save()
            messages.success(request, "Assignment updated.")
            return redirect("fleet:assignments_list")

        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleAssignmentForm(instance=asg)

    return render(
        request,
        "fleet/assignment_form.html",
        {"form": form, "assignment": asg, "is_edit": True},
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
def assignment_toggle(request, pk: int):
    asg = get_object_or_404(VehicleAssignment, pk=pk)

    # ✅ Pause: SOLO cerrar, NO borrar
    if asg.is_active:
        asg.close()  # is_active=False y closed_at=now
        messages.success(request, "Assignment paused.")
        return redirect("fleet:assignments_list")

    # ✅ Activate: (si está cerrado) reactivar
    # OJO: aquí tú decides si reabre el MISMO registro o si crea uno nuevo.
    # Por ahora lo dejo reabriendo el mismo.
    active_other = (
        VehicleAssignment.objects.filter(vehicle=asg.vehicle, is_active=True)
        .exclude(pk=asg.pk)
        .first()
    )
    if active_other:
        messages.error(
            request,
            f"Cannot activate. Vehicle already assigned to {active_other.user}.",
        )
        return redirect("fleet:assignments_list")

    asg.is_active = True
    asg.closed_at = None
    asg.save(update_fields=["is_active", "closed_at"])
    messages.success(request, "Assignment activated.")
    return redirect("fleet:assignments_list")


@login_required
@rol_requerido("supervisor", "admin", "pm")
def assignment_delete(request, pk: int):
    asg = get_object_or_404(VehicleAssignment, pk=pk)

    if request.method == "POST":
        vehicle_id = asg.vehicle_id

        # ✅ BORRAR TODAS las asignaciones de ese vehículo
        VehicleAssignment.objects.filter(vehicle_id=vehicle_id).delete()

        messages.success(request, "Assignment deleted.")
        return redirect("fleet:assignments_list")

    return render(request, "fleet/assignment_delete_confirm.html", {"assignment": asg})


# =========================
# Odometer Logs (miles)
# =========================
# fleet/views.py


# fleet/views.py (pega estas 2 funciones completas)

from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import VehicleOdometerLogForm
from .models import Vehicle, VehicleOdometerEvent


@login_required
@rol_requerido("supervisor", "admin", "pm")
def odometer_logs_list(request):
    q = (request.GET.get("q") or "").strip()
    vehicle_id = (request.GET.get("vehicle") or "").strip()

    per_page_str = (request.GET.get("per_page") or "10").strip()
    try:
        per_page = int(per_page_str)
    except Exception:
        per_page = 10

    qs = VehicleOdometerEvent.objects.select_related("vehicle", "project").order_by(
        "-event_at", "-id"
    )

    if vehicle_id:
        try:
            qs = qs.filter(vehicle_id=int(vehicle_id))
        except Exception:
            pass

    if q:
        qs = qs.filter(
            Q(vehicle__patente__icontains=q)
            | Q(vehicle__marca__icontains=q)
            | Q(vehicle__modelo__icontains=q)
            | Q(notes__icontains=q)
            | Q(project__nombre__icontains=q)
        )

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    vehicles_for_filter = Vehicle.objects.all().order_by("patente")

    base_params = {}
    if q:
        base_params["q"] = q
    if vehicle_id:
        base_params["vehicle"] = vehicle_id
    if per_page:
        base_params["per_page"] = per_page
    base_qs = urlencode(base_params)

    return render(
        request,
        "fleet/odometer_logs_list.html",
        {
            "page": page,
            "q": q,
            "vehicle_id": vehicle_id,
            "vehicles_for_filter": vehicles_for_filter,
            "per_page": per_page,
            "base_qs": base_qs,
        },
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
def odometer_log_create(request):
    if request.method == "POST":
        form = VehicleOdometerLogForm(request.POST, request.FILES)
        if form.is_valid():
            with transaction.atomic():
                ev: VehicleOdometerEvent = form.save(commit=False)

                # prev_odometer = odómetro actual del vehículo (sin queries extra)
                current = int(ev.vehicle.kilometraje_actual or 0)
                ev.prev_odometer = current

                if not (ev.source or "").strip():
                    ev.source = "manual"

                ev.save()

                # ✅ actualizar vehículo con la fecha del evento
                ev.vehicle.kilometraje_actual = int(ev.odometer)
                ev.vehicle.last_movement_at = ev.event_at
                ev.vehicle.save(
                    update_fields=["kilometraje_actual", "last_movement_at"]
                )

            messages.success(request, "Odometer entry saved.")
            return redirect("fleet:odometer_logs_list")

        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleOdometerLogForm()

    return render(request, "fleet/odometer_log_form.html", {"form": form})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def export_odometer_logs(request):
    vehicle_id = request.GET.get("vehicle")

    qs = VehicleOdometerEvent.objects.select_related("vehicle", "project").order_by(
        "-event_at", "-id"
    )

    if vehicle_id:
        try:
            qs = qs.filter(vehicle_id=int(vehicle_id))
        except Exception:
            pass

    resp = HttpResponse(content_type="application/vnd.ms-excel; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="odometer_logs.xls"'

    # Excel abre HTML table como .xls sin problema
    lines = []
    lines.append("<html><head><meta charset='utf-8'></head><body>")
    lines.append("<table border='1'>")
    lines.append(
        "<tr>"
        "<th>Date</th>"
        "<th>Vehicle</th>"
        "<th>Project</th>"
        "<th>Prev (miles)</th>"
        "<th>Odometer (miles)</th>"
        "<th>Delta (miles)</th>"
        "<th>Source</th>"
        "<th>Notes</th>"
        "</tr>"
    )

    for ev in qs.iterator():
        dt = (
            timezone.localtime(ev.event_at).strftime("%Y-%m-%d %H:%M")
            if ev.event_at
            else ""
        )
        plate = ev.vehicle.patente if ev.vehicle_id else ""
        project = ev.project.nombre if getattr(ev, "project_id", None) else ""
        prev_odo = int(ev.prev_odometer or 0)
        odo = int(ev.odometer or 0)
        delta = int(odo - prev_odo)
        source = ev.source or ""
        notes = (
            (ev.notes or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        lines.append(
            "<tr>"
            f"<td>{dt}</td>"
            f"<td>{plate}</td>"
            f"<td>{project}</td>"
            f"<td>{prev_odo}</td>"
            f"<td>{odo}</td>"
            f"<td>{delta}</td>"
            f"<td>{source}</td>"
            f"<td>{notes}</td>"
            "</tr>"
        )

    lines.append("</table></body></html>")
    resp.write("\n".join(lines))
    return resp


# =========================
# Notifications (per vehicle)
# =========================

@login_required
@rol_requerido("supervisor", "admin", "pm")
def notification_list(request):
    vehicles = Vehicle.objects.all().order_by("patente").select_related("status")

    cfgs = VehicleNotificationConfig.objects.filter(vehicle__in=vehicles).select_related("vehicle")
    cfg_by_vehicle_id = {c.vehicle_id: c for c in cfgs}

    rows = [{"vehicle": v, "cfg": cfg_by_vehicle_id.get(v.id)} for v in vehicles]
    return render(request, "fleet/notification_list.html", {"rows": rows})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def notification_edit(request, vehicle_id: int):
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id)
    cfg, _ = VehicleNotificationConfig.objects.get_or_create(vehicle=vehicle)

    # detectar email chofer asignado (si existe)
    asg = (
        VehicleAssignment.objects.select_related("user")
        .filter(vehicle=vehicle, is_active=True)
        .order_by("-assigned_at")
        .first()
    )
    assigned_email = None
    if asg and getattr(asg.user, "email", None):
        assigned_email = (asg.user.email or "").strip() or None

    if request.method == "POST":
        form = VehicleNotificationConfigForm(request.POST, instance=cfg)
        if form.is_valid():
            form.save()
            messages.success(request, "Notification settings saved.")
            return redirect("fleet:notification_list")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleNotificationConfigForm(instance=cfg)

    return render(
        request,
        "fleet/notification_form.html",
        {
            "vehicle": vehicle,
            "cfg": cfg,
            "form": form,
            "assigned_email": assigned_email,
        },
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
def service_type_manage(request):
    types = VehicleServiceType.objects.all().order_by("name")

    if request.method == "POST":
        form = VehicleServiceTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Service type saved.")
            return redirect("fleet:service_type_manage")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleServiceTypeForm()

    return render(request, "fleet/service_type_form.html", {"form": form, "types": types, "editing_type": None})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def service_type_edit(request, pk: int):
    editing = get_object_or_404(VehicleServiceType, pk=pk)
    types = VehicleServiceType.objects.all().order_by("name")

    if request.method == "POST":
        form = VehicleServiceTypeForm(request.POST, instance=editing)
        if form.is_valid():
            form.save()
            messages.success(request, "Service type updated.")
            return redirect("fleet:service_type_manage")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleServiceTypeForm(instance=editing)

    return render(request, "fleet/service_type_form.html", {"form": form, "types": types, "editing_type": editing})


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def service_type_toggle(request, pk: int):
    t = get_object_or_404(VehicleServiceType, pk=pk)
    t.is_active = not bool(t.is_active)
    t.save(update_fields=["is_active"])
    messages.success(request, "Service type updated.")
    return redirect("fleet:service_type_manage")


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def service_type_delete(request, pk: int):
    t = get_object_or_404(VehicleServiceType, pk=pk)
    try:
        t.delete()
        messages.success(request, "Service type deleted.")
    except Exception:
        messages.error(request, "This service type cannot be deleted because it is in use.")
    return redirect("fleet:service_type_manage")


@login_required
@rol_requerido("supervisor", "admin", "pm")
def service_list(request):
    services = (
        VehicleService.objects
        .select_related("vehicle", "service_type_obj")
        .order_by("vehicle_id", "-service_date", "-id")
    )

    grouped = {}
    for s in services:
        v = s.vehicle
        if v.id not in grouped:
            grouped[v.id] = {
                "vehicle": v,
                "types": defaultdict(list),
                "last_service_label": "—",
            }

        type_name = _service_type_label(s)
        grouped[v.id]["types"][type_name].append(s)

        if grouped[v.id]["last_service_label"] == "—":
            grouped[v.id]["last_service_label"] = f"{type_name} · {s.service_date.strftime('%m-%d-%Y')}"

    # incluir vehículos sin servicios
    for v in Vehicle.objects.all().order_by("patente"):
        if v.id not in grouped:
            grouped[v.id] = {"vehicle": v, "types": defaultdict(list), "last_service_label": "—"}

    for vid in list(grouped.keys()):
        grouped[vid]["types"] = dict(grouped[vid]["types"])

    return render(request, "fleet/service_list.html", {"grouped": grouped})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def service_create(request):
    if request.method == "POST":
        form = VehicleServiceForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Service created.")
            return redirect("fleet:service_list")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleServiceForm()

    return render(request, "fleet/service_form.html", {"form": form, "mode": "create"})


@login_required
@rol_requerido("supervisor", "admin", "pm")
def service_edit(request, pk: int):
    obj = get_object_or_404(VehicleService, pk=pk)

    if request.method == "POST":
        form = VehicleServiceForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Service updated.")
            return redirect("fleet:service_list")
        messages.error(request, "Please correct the errors.")
    else:
        form = VehicleServiceForm(instance=obj)

    return render(request, "fleet/service_form.html", {"form": form, "mode": "edit"})


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def service_delete(request, pk: int):
    s = get_object_or_404(VehicleService, pk=pk)
    s.delete()
    messages.success(request, "Service deleted.")
    return redirect("fleet:service_list")
