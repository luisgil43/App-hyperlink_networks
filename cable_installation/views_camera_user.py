from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from operaciones.models import SesionBillingTecnico
from usuarios.decoradores import rol_requerido

from .models import CableAssignmentRequirement, CableEvidence

SAFE_PREFIX = (
    getattr(settings, "DIRECT_UPLOADS_SAFE_PREFIX", "cable_installation/").rstrip("/")
    + "/"
)


def _safe_wasabi_key(key: str) -> bool:
    return (
        isinstance(key, str)
        and key.startswith(SAFE_PREFIX)
        and ".." not in key
        and not key.startswith("/")
    )


def _cp_from_project_id(project_id: str) -> str:
    s = (project_id or "").strip()
    m = re.search(r"(CP[-_ ]?\d+)", s, re.IGNORECASE)
    if m:
        val = m.group(1).upper().replace("_", "").replace(" ", "").replace("CP", "")
        val = val.replace("-", "")
        return f"CP-{val}"
    m2 = re.search(r"(\d{3,})$", s)
    if m2:
        return f"CP-{m2.group(1)}"
    return f"CP-{s}" if s else "CP-—"


def _is_asig_active(asig) -> bool:
    return getattr(asig, "is_active", True) is True


def _can_upload(a: SesionBillingTecnico) -> bool:
    return a.estado in ["en_proceso", "rechazado_supervisor"]


def _parse_decimal(value):
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _pending_rows_for_assignment(a: SesionBillingTecnico):
    """
    Pendientes = filas del técnico que aún no están completed.
    """
    return (
        CableAssignmentRequirement.objects.select_related("requirement", "assignment")
        .annotate(ev_count=Count("evidences"))
        .filter(assignment=a)
        .exclude(status=CableAssignmentRequirement.STATUS_COMPLETED)
        .order_by("requirement__order", "requirement__sequence_no", "id")
    )


def _build_direct_upload_folder(a: SesionBillingTecnico) -> str:
    billing = a.sesion

    project_id = (
        getattr(billing, "proyecto_id", "") or ""
    ).strip() or f"billing-{billing.id}"
    project_slug = (
        re.sub(r"[^a-zA-Z0-9_-]+", "-", project_id).strip("-").lower()
        or f"billing-{billing.id}"
    )

    tech = a.tecnico
    tech_name = (
        getattr(tech, "get_full_name", lambda: "")()
        or getattr(tech, "username", "")
        or f"user-{tech.id}"
    ).strip()
    tech_slug = (
        re.sub(r"[^a-zA-Z0-9_-]+", "-", tech_name).strip("-").lower()
        or f"user-{tech.id}"
    )

    return f"cable_installation/{project_slug}/billing_{billing.id}/{tech_slug}/requirements/"


def _row_label(row: CableAssignmentRequirement) -> str:
    req = row.requirement
    return f"PK {req.sequence_no} · {req.handhole}"


def _row_target_title(row: CableAssignmentRequirement | None, shot: str) -> str:
    if not row:
        return "Extra Photo"

    base = _row_label(row)

    if shot == "start_cable":
        return f"{base} · Start Cable"
    if shot == "end_cable":
        return f"{base} · End Cable"
    if shot == "handhole":
        return f"{base} · Handhole"

    return base


def _expected_end_text(req) -> str:
    if req.start_ft is None:
        return ""

    reserve = req.planned_reserve_ft or Decimal("0.00")
    low = req.start_ft - reserve
    high = req.start_ft + reserve
    return f"{low} or {high}"


def _create_evidence_from_key(
    row: CableAssignmentRequirement,
    key: str,
    note: str,
    lat,
    lng,
    acc,
    taken_dt,
):
    ev = CableEvidence(
        assignment_requirement=row,
        note=note or "",
        lat=lat or None,
        lng=lng or None,
        gps_accuracy_m=acc or None,
        taken_at=taken_dt or timezone.now(),
    )
    ev.image.name = key.strip()
    ev.save()
    return ev


def _refresh_row_status(row: CableAssignmentRequirement):
    """
    Con el modelo actual NO existe shot_type en CableEvidence,
    así que la validación posible es:
    - start_ft cargado
    - end_ft cargado
    - note opcional
    - al menos 3 fotos totales del row
      (start cable / end cable / handhole)
    """
    req = row.requirement
    photos_count = row.evidences.count()

    is_complete = (
        req.start_ft is not None and req.end_ft is not None and photos_count >= 3
    )

    new_status = (
        CableAssignmentRequirement.STATUS_COMPLETED
        if is_complete
        else CableAssignmentRequirement.STATUS_PENDING
    )

    if row.status != new_status:
        row.status = new_status
        row.save(update_fields=["status", "updated_at"])

    return is_complete


@login_required
@rol_requerido("usuario")
@require_GET
def camera_take(request, asig_id: int):
    """
    Cámara para cable_installation.
    Entra con:
      ?row_id=
      ?shot=start_cable|end_cable|handhole
      ?start_ft=
      ?reserve_ft=
      ?end_ft=
      ?note=
    """
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)

    if not _is_asig_active(a):
        raise Http404()

    if not _can_upload(a):
        return HttpResponseForbidden("This assignment is not open for uploads.")

    row_id = (request.GET.get("row_id") or "").strip()
    shot = (request.GET.get("shot") or "").strip() or "handhole"

    row = None
    if row_id:
        row = get_object_or_404(
            CableAssignmentRequirement.objects.select_related(
                "requirement", "assignment"
            ),
            pk=row_id,
            assignment=a,
        )

    pending_rows = list(_pending_rows_for_assignment(a))

    if not row and pending_rows:
        row = pending_rows[0]

    selected_row_id = row.id if row else ""
    row_title = _row_target_title(row, shot)

    current_req = row.requirement if row else None

    ctx = {
        "a": a,
        "row": row,
        "row_id": selected_row_id,
        "req_id": row.requirement_id if row else "",
        "req_title": row_title,
        "shot_type": shot,
        "direct_uploads_folder": _build_direct_upload_folder(a),
        "project_id": getattr(a.sesion, "proyecto_id", "") or "",
        "cp_text": _cp_from_project_id(getattr(a.sesion, "proyecto_id", "") or ""),
        "can_delete": True,
        "row_options": [
            {
                "id": r.id,
                "title": _row_label(r),
            }
            for r in pending_rows
        ],
        "selected_row_id": selected_row_id,
        # valores precargados para seguir editando dentro de cámara
        "start_ft": (
            (request.GET.get("start_ft") or "").strip()
            or (
                str(current_req.start_ft)
                if current_req and current_req.start_ft is not None
                else ""
            )
        ),
        "reserve_ft": (
            (request.GET.get("reserve_ft") or "").strip()
            or (str(current_req.planned_reserve_ft) if current_req else "")
        ),
        "end_ft": (
            (request.GET.get("end_ft") or "").strip()
            or (
                str(current_req.end_ft)
                if current_req and current_req.end_ft is not None
                else ""
            )
        ),
        "row_note": (
            (request.GET.get("note") or "").strip() or (row.note if row else "")
        ),
        "expected_end_text": _expected_end_text(current_req) if current_req else "",
        "measurement_warning_text": (
            current_req.measurement_warning_text if current_req else ""
        ),
    }
    return render(request, "cable_installation/camera_take.html", ctx)


@login_required
@rol_requerido("usuario")
@require_GET
def camera_requirements_status(request, asig_id: int):
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)

    if not _is_asig_active(a):
        return JsonResponse(
            {"ok": False, "error": "Assignment no longer available."},
            status=404,
        )

    if not _can_upload(a):
        return JsonResponse(
            {"ok": False, "error": "Assignment not open for uploads."},
            status=403,
        )

    pending_rows = list(_pending_rows_for_assignment(a))
    next_row_id = pending_rows[0].id if pending_rows else None

    return JsonResponse(
        {
            "ok": True,
            "pending": [
                {
                    "id": r.id,
                    "title": _row_label(r),
                }
                for r in pending_rows
            ],
            "next_row_id": next_row_id,
        },
        status=200,
    )


@login_required
@rol_requerido("usuario")
@require_POST
@csrf_protect
def camera_create_evidence_from_key(request, asig_id: int):
    """
    Confirma foto subida a Wasabi y crea CableEvidence.
    También guarda start/reserve/end/note sin salir de cámara.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)

    if not _is_asig_active(a):
        return JsonResponse(
            {"ok": False, "error": "Assignment no longer available."},
            status=404,
        )

    if not _can_upload(a):
        return JsonResponse(
            {"ok": False, "error": "Assignment not open for uploads."},
            status=403,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    key = (payload.get("key") or "").strip()
    if not _safe_wasabi_key(key):
        return JsonResponse({"ok": False, "error": "Invalid key."}, status=400)

    row_id = payload.get("row_id") or None
    if not row_id:
        return JsonResponse({"ok": False, "error": "row_id is required."}, status=400)

    row = get_object_or_404(
        CableAssignmentRequirement.objects.select_related("requirement", "assignment"),
        pk=int(row_id),
        assignment=a,
    )

    req = row.requirement

    note = (payload.get("note") or "").strip()
    shot_type = (payload.get("shot_type") or "").strip()

    lat = payload.get("lat") or None
    lng = payload.get("lng") or None
    acc = payload.get("acc") or None

    taken = (payload.get("client_taken_at") or "").strip()
    taken_dt = None
    if taken:
        try:
            taken_dt = timezone.make_aware(
                datetime.fromisoformat(taken.replace("Z", "+00:00"))
            )
            taken_dt = timezone.localtime(taken_dt)
        except Exception:
            taken_dt = None

    # guardar medición compartida desde cámara
    start_ft = _parse_decimal(payload.get("start_ft"))
    reserve_ft = _parse_decimal(payload.get("reserve_ft"))
    end_ft = _parse_decimal(payload.get("end_ft"))

    req_changed = False
    row_changed = False

    if start_ft is not None:
        req.start_ft = start_ft
        req_changed = True

    if reserve_ft is not None:
        req.planned_reserve_ft = reserve_ft
        req_changed = True

    if end_ft is not None:
        req.end_ft = end_ft
        req_changed = True

    if req_changed:
        req.save()

    if note != row.note:
        row.note = note
        row_changed = True

    if row_changed:
        row.save(update_fields=["note", "updated_at"])

    ev = _create_evidence_from_key(
        row=row,
        key=key,
        note=note,
        lat=lat,
        lng=lng,
        acc=acc,
        taken_dt=taken_dt,
    )

    is_complete = _refresh_row_status(row)

    req.refresh_from_db()
    row.refresh_from_db()

    fecha_txt = timezone.localtime(ev.taken_at or timezone.now()).strftime(
        "%Y-%m-%d %H:%M"
    )

    return JsonResponse(
        {
            "ok": True,
            "evidencia": {
                "id": ev.id,
                "url": ev.image.url,
                "titulo": _row_target_title(row, shot_type),
                "fecha": fecha_txt,
                "lat": str(ev.lat) if ev.lat is not None else None,
                "lng": str(ev.lng) if ev.lng is not None else None,
                "acc": (
                    str(ev.gps_accuracy_m) if ev.gps_accuracy_m is not None else None
                ),
                "req_id": row.requirement_id,
                "row_id": row.id,
                "can_delete": True,
            },
            "row": {
                "id": row.id,
                "start_ft": str(req.start_ft) if req.start_ft is not None else "",
                "reserve_ft": (
                    str(req.planned_reserve_ft)
                    if req.planned_reserve_ft is not None
                    else ""
                ),
                "end_ft": str(req.end_ft) if req.end_ft is not None else "",
                "installed_ft": (
                    str(req.installed_ft) if req.installed_ft is not None else ""
                ),
                "note": row.note or "",
                "status": row.status or "",
                "expected_end_text": _expected_end_text(req),
                "warning_text": req.measurement_warning_text or "",
                "is_complete": is_complete,
            },
        }
    )
