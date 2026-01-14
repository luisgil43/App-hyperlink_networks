# fleet/views.py
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from usuarios.decoradores import rol_requerido

from .forms import VehicleAssignmentForm, VehicleForm, VehicleOdometerLogForm
from .models import Vehicle, VehicleAssignment, VehicleOdometerLog


# -----------------------------
# Access helpers (compatible)
# -----------------------------
def _can_access_fleet(user) -> bool:
    return bool(
        getattr(user, "es_admin_general", False)
        or getattr(user, "es_logistica", False)
        or getattr(user, "es_pm", False)
        or getattr(user, "es_supervisor", False)
        or getattr(user, "is_superuser", False)
    )


def _try_import_access_helpers():
    """
    We re-use your existing access layer (projects_ids_for_user).
    This tries common paths. If your project uses a different module,
    just adjust the import in one place.
    """
    candidates = [
        ("utils.access", "projects_ids_for_user"),
        ("utils.access", "filter_queryset_by_access"),
        ("usuarios.access", "projects_ids_for_user"),
        ("usuarios.access", "filter_queryset_by_access"),
        ("facturacion.access", "projects_ids_for_user"),
        ("facturacion.access", "filter_queryset_by_access"),
        ("facturacion.utils", "projects_ids_for_user"),
        ("facturacion.utils", "filter_queryset_by_access"),
    ]

    found = {}
    for mod, fn in candidates:
        try:
            m = __import__(mod, fromlist=[fn])
            if hasattr(m, fn):
                found[fn] = getattr(m, fn)
        except Exception:
            continue

    return found.get("projects_ids_for_user"), found.get("filter_queryset_by_access")


_projects_ids_for_user, _filter_queryset_by_access = _try_import_access_helpers()


def projects_ids_for_user(user):
    # Fallback: superusers/admin_general see all; others none (until import is fixed)
    if _projects_ids_for_user:
        return _projects_ids_for_user(user)
    if getattr(user, "is_superuser", False) or getattr(user, "es_admin_general", False):
        from facturacion.models import Proyecto
        return list(Proyecto.objects.values_list("id", flat=True))
    return []


# -----------------------------
# Fleet Home
# -----------------------------
@login_required
@rol_requerido("pm", "admin", "supervisor")
def fleet_home(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")
    return redirect("fleet:vehicles_list")


# -----------------------------
# Vehicles
# -----------------------------
@login_required
@rol_requerido("pm", "admin", "supervisor")
def vehicles_list(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "active").strip()  # active|inactive|all

    qs = Vehicle.objects.all()

    # Project-based visibility (same behavior as your ops screens)
    # - Admin general / superuser can see all
    # - Otherwise: only vehicles assigned to allowed projects
    if not (getattr(request.user, "is_superuser", False) or getattr(request.user, "es_admin_general", False)):
        allowed_ids = projects_ids_for_user(request.user)
        qs = qs.filter(assignments__is_active=True, assignments__project_id__in=allowed_ids).distinct()

    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(make__icontains=q)
            | Q(model__icontains=q)
            | Q(plate__icontains=q)
            | Q(vin__icontains=q)
            | Q(fleet_id__icontains=q)
        )

    qs = qs.order_by("-is_active", "name", "id")

    try:
        per_page = int(request.GET.get("per_page") or 10)
    except Exception:
        per_page = 10
    per_page = max(5, min(per_page, 100))

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    base_qs = request.GET.copy()
    base_qs.pop("page", None)
    base_qs = base_qs.urlencode()

    return render(request, "fleet/vehicles_list.html", {
        "page": page,
        "q": q,
        "status": status,
        "per_page": per_page,
        "base_qs": base_qs,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
def vehicle_create(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            v: Vehicle = form.save(commit=False)
            v.last_odometer = form.cleaned_data.get("initial_odometer") or 0
            v.save()
            messages.success(request, "Vehicle created successfully.")
            return redirect("fleet:vehicle_detail", pk=v.pk)
    else:
        form = VehicleForm()

    return render(request, "fleet/vehicle_form.html", {
        "mode": "create",
        "form": form,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
def vehicle_edit(request, pk: int):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    vehicle = get_object_or_404(Vehicle, pk=pk)

    if request.method == "POST":
        form = VehicleForm(request.POST, instance=vehicle)
        if form.is_valid():
            v: Vehicle = form.save(commit=False)
            # last_odometer should be driven later by mileage logs, not by editing
            v.save()
            messages.success(request, "Vehicle updated successfully.")
            return redirect("fleet:vehicle_detail", pk=v.pk)
    else:
        form = VehicleForm(instance=vehicle)

    return render(request, "fleet/vehicle_form.html", {
        "mode": "edit",
        "vehicle": vehicle,
        "form": form,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
def vehicle_detail(request, pk: int):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    vehicle = get_object_or_404(Vehicle, pk=pk)
    active_assignment = vehicle.assignments.filter(is_active=True).select_related("project", "assigned_to", "pm", "supervisor").first()

    return render(request, "fleet/vehicle_detail.html", {
        "vehicle": vehicle,
        "active_assignment": active_assignment,
    })


# -----------------------------
# Assignments
# -----------------------------
@login_required
@rol_requerido("pm", "admin", "supervisor")
def assignments_list(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    only_active = (request.GET.get("only_active") or "1").strip()  # 1|0
    q = (request.GET.get("q") or "").strip()

    qs = VehicleAssignment.objects.select_related("vehicle", "project", "assigned_to", "pm", "supervisor")

    # Project-based visibility
    if not (getattr(request.user, "is_superuser", False) or getattr(request.user, "es_admin_general", False)):
        allowed_ids = projects_ids_for_user(request.user)
        qs = qs.filter(project_id__in=allowed_ids)

    if only_active == "1":
        qs = qs.filter(is_active=True)

    if q:
        qs = qs.filter(
            Q(vehicle__name__icontains=q)
            | Q(vehicle__plate__icontains=q)
            | Q(vehicle__fleet_id__icontains=q)
            | Q(project__nombre__icontains=q)
            | Q(assigned_to__username__icontains=q)
            | Q(assigned_to__first_name__icontains=q)
            | Q(assigned_to__last_name__icontains=q)
        )

    qs = qs.order_by("-is_active", "-created_at", "-id")

    try:
        per_page = int(request.GET.get("per_page") or 10)
    except Exception:
        per_page = 10
    per_page = max(5, min(per_page, 100))

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    base_qs = request.GET.copy()
    base_qs.pop("page", None)
    base_qs = base_qs.urlencode()

    return render(request, "fleet/assignments_list.html", {
        "page": page,
        "q": q,
        "only_active": only_active,
        "per_page": per_page,
        "base_qs": base_qs,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
def assignment_create(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    if request.method == "POST":
        form = VehicleAssignmentForm(request.POST)
        _limit_assignment_form_querysets(request.user, form)

        if form.is_valid():
            a = form.save()
            messages.success(request, "Vehicle assignment created successfully.")
            return redirect("fleet:assignments_list")
    else:
        form = VehicleAssignmentForm()
        _limit_assignment_form_querysets(request.user, form)

    return render(request, "fleet/assignment_form.html", {
        "mode": "create",
        "form": form,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
def assignment_edit(request, pk: int):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    assignment = get_object_or_404(VehicleAssignment, pk=pk)

    # Project-based protection
    if not (getattr(request.user, "is_superuser", False) or getattr(request.user, "es_admin_general", False)):
        allowed_ids = projects_ids_for_user(request.user)
        if assignment.project_id not in allowed_ids:
            return HttpResponseForbidden("You do not have access to this assignment.")

    if request.method == "POST":
        form = VehicleAssignmentForm(request.POST, instance=assignment)
        _limit_assignment_form_querysets(request.user, form)

        if form.is_valid():
            form.save()
            messages.success(request, "Vehicle assignment updated successfully.")
            return redirect("fleet:assignments_list")
    else:
        form = VehicleAssignmentForm(instance=assignment)
        _limit_assignment_form_querysets(request.user, form)

    return render(request, "fleet/assignment_form.html", {
        "mode": "edit",
        "assignment": assignment,
        "form": form,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
@require_POST
def assignment_end(request, pk: int):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    assignment = get_object_or_404(VehicleAssignment, pk=pk)

    # Project-based protection
    if not (getattr(request.user, "is_superuser", False) or getattr(request.user, "es_admin_general", False)):
        allowed_ids = projects_ids_for_user(request.user)
        if assignment.project_id not in allowed_ids:
            return HttpResponseForbidden("You do not have access to this assignment.")

    assignment.close()
    messages.success(request, "Assignment ended successfully.")
    return redirect("fleet:assignments_list")


def _limit_assignment_form_querysets(user, form: VehicleAssignmentForm):
    """
    Limit projects dropdown to allowed projects (like operations).
    Also optionally limits supervisor/pm lists if those flags exist.
    """
    # Project dropdown restriction
    allowed_ids = projects_ids_for_user(user)
    try:
        from facturacion.models import Proyecto
        form.fields["project"].queryset = Proyecto.objects.filter(id__in=allowed_ids).order_by("nombre")
    except Exception:
        pass

    # Optional: filter supervisors and PMs (only if your user model has these flags)
    User = form.fields["assigned_to"].queryset.model

    try:
        form.fields["supervisor"].queryset = User.objects.filter(Q(es_supervisor=True) | Q(is_superuser=True)).order_by("first_name", "last_name", "username")
    except Exception:
        pass

    try:
        form.fields["pm"].queryset = User.objects.filter(Q(es_pm=True) | Q(is_superuser=True)).order_by("first_name", "last_name", "username")
    except Exception:
        pass


# fleet/views.py  (ADD AT END)
import csv
from io import StringIO

from django.http import HttpResponse


@login_required
@rol_requerido("pm", "admin", "supervisor")
def odometer_logs_list(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    vehicle_id = (request.GET.get("vehicle") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = VehicleOdometerLog.objects.select_related("vehicle", "project", "created_by")

    # Project-based visibility
    if not (getattr(request.user, "is_superuser", False) or getattr(request.user, "es_admin_general", False)):
        allowed_ids = projects_ids_for_user(request.user)
        qs = qs.filter(
            Q(project_id__in=allowed_ids) |
            Q(vehicle__assignments__is_active=True, vehicle__assignments__project_id__in=allowed_ids)
        ).distinct()

    if vehicle_id.isdigit():
        qs = qs.filter(vehicle_id=int(vehicle_id))

    if q:
        qs = qs.filter(
            Q(vehicle__name__icontains=q)
            | Q(vehicle__plate__icontains=q)
            | Q(vehicle__fleet_id__icontains=q)
            | Q(project__nombre__icontains=q)
            | Q(notes__icontains=q)
        )

    qs = qs.order_by("-date", "-id")

    try:
        per_page = int(request.GET.get("per_page") or 10)
    except Exception:
        per_page = 10
    per_page = max(5, min(per_page, 100))

    paginator = Paginator(qs, per_page)
    page = paginator.get_page(request.GET.get("page"))

    base_qs = request.GET.copy()
    base_qs.pop("page", None)
    base_qs = base_qs.urlencode()

    vehicles_for_filter = Vehicle.objects.order_by("name", "id")

    return render(request, "fleet/odometer_logs_list.html", {
        "page": page,
        "q": q,
        "vehicle_id": vehicle_id,
        "per_page": per_page,
        "base_qs": base_qs,
        "vehicles_for_filter": vehicles_for_filter,
    })


@login_required
@rol_requerido("pm", "admin", "supervisor")
def odometer_log_create(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    if request.method == "POST":
        form = VehicleOdometerLogForm(request.POST, request.FILES)
        _limit_odometer_form_querysets(request.user, form)

        if form.is_valid():
            log = form.save(commit=False)
            log.created_by = request.user
            log.save()
            messages.success(request, "Odometer entry created successfully.")
            return redirect("fleet:odometer_logs_list")
    else:
        form = VehicleOdometerLogForm()
        _limit_odometer_form_querysets(request.user, form)

    return render(request, "fleet/odometer_log_form.html", {
        "mode": "create",
        "form": form,
    })


def _limit_odometer_form_querysets(user, form: VehicleOdometerLogForm):
    # Limit projects to allowed
    allowed_ids = projects_ids_for_user(user)
    try:
        from facturacion.models import Proyecto
        form.fields["project"].queryset = Proyecto.objects.filter(id__in=allowed_ids).order_by("nombre")
    except Exception:
        pass

    # Limit vehicles visible based on assignments for non admin
    if not (getattr(user, "is_superuser", False) or getattr(user, "es_admin_general", False)):
        qs = Vehicle.objects.filter(assignments__is_active=True, assignments__project_id__in=allowed_ids).distinct()
        form.fields["vehicle"].queryset = qs.order_by("name", "id")
    else:
        form.fields["vehicle"].queryset = Vehicle.objects.order_by("name", "id")


@login_required
@rol_requerido("pm", "admin", "supervisor")
def odometer_logs_export_csv(request):
    if not _can_access_fleet(request.user):
        return HttpResponseForbidden("You do not have access to Fleet.")

    vehicle_id = (request.GET.get("vehicle") or "").strip()

    qs = VehicleOdometerLog.objects.select_related("vehicle", "project", "created_by")

    # Project-based visibility
    if not (getattr(request.user, "is_superuser", False) or getattr(request.user, "es_admin_general", False)):
        allowed_ids = projects_ids_for_user(request.user)
        qs = qs.filter(
            Q(project_id__in=allowed_ids) |
            Q(vehicle__assignments__is_active=True, vehicle__assignments__project_id__in=allowed_ids)
        ).distinct()

    if vehicle_id.isdigit():
        qs = qs.filter(vehicle_id=int(vehicle_id))

    qs = qs.order_by("-date", "-id")

    # CSV export
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="odometer_logs.csv"'

    writer = csv.writer(resp)
    writer.writerow(["Date", "Vehicle", "Fleet ID", "Plate", "Project", "Odometer", "Unit", "Delta", "Notes", "Created by"])

    for r in qs:
        writer.writerow([
            r.date,
            r.vehicle.name,
            r.vehicle.fleet_id,
            f"{r.vehicle.plate_state} {r.vehicle.plate}",
            getattr(r.project, "nombre", "") if r.project_id else "",
            r.odometer,
            r.vehicle.unit_label,
            r.delta_since_last,
            r.notes or "",
            r.created_by.get_full_name() if r.created_by else "",
        ])

    return resp