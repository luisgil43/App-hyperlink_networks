from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
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


def _row_allowed_shots(row):
    """
    Devuelve los shot_type que el técnico puede tomar para esta fila.

    Regla:
    - Si no hay fotos rechazadas, puede tomar solo las que faltan.
    - Si hay fotos rechazadas, puede volver a tomar SOLO esas rechazadas.
    - Las aprobadas no se pueden volver a tomar.
    - Las pendientes tampoco se pueden duplicar.
    """
    rejected_shots = set(
        CableEvidence.objects.filter(
            assignment_requirement=row,
            review_status=CableEvidence.REVIEW_REJECTED,
        )
        .exclude(shot_type="")
        .values_list("shot_type", flat=True)
        .distinct()
    )
    if rejected_shots:
        return [shot for shot in _required_shots() if shot in rejected_shots]

    return _pending_shots_for_row(row)


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
    estado = (a.estado or "").strip()
    return estado in {"en_proceso", "en_revision_supervisor", "rechazado_supervisor"}


def _parse_decimal(value):
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, TypeError, ValueError):
        return None


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
        return "Requirement"

    base = _row_label(row)

    if shot == CableEvidence.SHOT_START_CABLE:
        return f"{base} · Start Cable"
    if shot == CableEvidence.SHOT_END_CABLE:
        return f"{base} · End Cable"
    if shot == CableEvidence.SHOT_HANDHOLE:
        return f"{base} · Handhole"

    return base


def _expected_end_text(req) -> str:
    if not req or req.start_ft is None:
        return ""

    reserve = req.planned_reserve_ft or Decimal("0.00")
    expected = req.start_ft - reserve
    return str(expected)


def _required_shots():
    return [
        CableEvidence.SHOT_START_CABLE,
        CableEvidence.SHOT_END_CABLE,
        CableEvidence.SHOT_HANDHOLE,
    ]


def _shot_label(shot_type: str) -> str:
    if shot_type == CableEvidence.SHOT_START_CABLE:
        return "Start cable photo"
    if shot_type == CableEvidence.SHOT_END_CABLE:
        return "End cable photo"
    if shot_type == CableEvidence.SHOT_HANDHOLE:
        return "Handhole photo"
    return shot_type


def _present_shots_for_row(row: CableAssignmentRequirement) -> set[str]:
    return set(
        CableEvidence.objects.filter(assignment_requirement=row)
        .exclude(shot_type="")
        .exclude(review_status=CableEvidence.REVIEW_REJECTED)
        .values_list("shot_type", flat=True)
        .distinct()
    )


def _pending_shots_for_row(row: CableAssignmentRequirement) -> list[str]:
    present = _present_shots_for_row(row)
    return [shot for shot in _required_shots() if shot not in present]


def _row_has_rejected_photo(row: CableAssignmentRequirement) -> bool:
    return CableEvidence.objects.filter(
        assignment_requirement=row,
        review_status=CableEvidence.REVIEW_REJECTED,
    ).exists()


def _row_is_complete(row: CableAssignmentRequirement) -> bool:
    req = row.requirement
    pending_shots = _pending_shots_for_row(row)
    has_rejected = _row_has_rejected_photo(row)

    return (
        req.start_ft is not None
        and req.planned_reserve_ft is not None
        and req.end_ft is not None
        and len(pending_shots) == 0
        and not has_rejected
    )


def _refresh_row_status(row: CableAssignmentRequirement):
    is_complete = _row_is_complete(row)
    new_status = (
        CableAssignmentRequirement.STATUS_COMPLETED
        if is_complete
        else CableAssignmentRequirement.STATUS_PENDING
    )

    if row.status != new_status:
        row.status = new_status
        row.save(update_fields=["status", "updated_at"])

    return is_complete


def _pending_rows_for_assignment(a: SesionBillingTecnico):
    rows = (
        CableAssignmentRequirement.objects.select_related("requirement", "assignment")
        .filter(assignment=a)
        .order_by("requirement__order", "requirement__sequence_no", "id")
    )

    out = []
    for row in rows:
        _refresh_row_status(row)
        if row.status != CableAssignmentRequirement.STATUS_COMPLETED:
            out.append(row)
    return out


def _create_evidence_from_key(
    row: CableAssignmentRequirement,
    key: str,
    note: str,
    lat,
    lng,
    acc,
    taken_dt,
    shot_type: str,
):
    ev = CableEvidence(
        assignment_requirement=row,
        note=note or "",
        lat=lat or None,
        lng=lng or None,
        gps_accuracy_m=acc or None,
        taken_at=taken_dt or timezone.now(),
        shot_type=shot_type or "",
    )
    ev.image.name = key.strip()
    ev.save()
    return ev


@login_required
@rol_requerido("usuario")
@require_GET
def camera_take(request, asig_id: int):
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)

    if not _is_asig_active(a):
        raise Http404()

    row_id = (request.GET.get("row_id") or "").strip()
    shot = (request.GET.get("shot") or "").strip() or CableEvidence.SHOT_START_CABLE

    row = None
    if row_id:
        row = get_object_or_404(
            CableAssignmentRequirement.objects.select_related(
                "requirement", "assignment"
            ),
            pk=row_id,
            assignment=a,
        )

    if not row:
        pending_rows = list(_pending_rows_for_assignment(a))
        if pending_rows:
            row = pending_rows[0]

    if not row:
        raise Http404()

    allowed_shots = _row_allowed_shots(row)

    if shot not in _required_shots():
        shot = CableEvidence.SHOT_START_CABLE

    if shot not in allowed_shots:
        from django.contrib import messages
        from django.shortcuts import redirect

        messages.error(
            request,
            f"{_shot_label(shot)} is not available for upload right now.",
        )
        return redirect(
            "cable_installation:technician_requirements", assignment_id=a.pk
        )

    current_req = row.requirement
    selected_row_id = row.id
    row_title = _row_target_title(row, shot)

    ctx = {
        "a": a,
        "row": row,
        "row_id": selected_row_id,
        "req_id": row.requirement_id,
        "req_title": row_title,
        "shot_type": shot,
        "direct_uploads_folder": _build_direct_upload_folder(a),
        "project_id": getattr(a.sesion, "proyecto_id", "") or "",
        "cp_text": _cp_from_project_id(getattr(a.sesion, "proyecto_id", "") or ""),
        "can_delete": True,
        "row_options": [
            {
                "id": row.id,
                "title": _row_label(row),
            }
        ],
        "selected_row_id": selected_row_id,
        "shot_options": [{"value": s, "label": _shot_label(s)} for s in allowed_shots],
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

    pending_shots_by_row = {str(r.id): _pending_shots_for_row(r) for r in pending_rows}

    next_shot_type = None
    if pending_rows:
        first_pending_shots = pending_shots_by_row.get(str(pending_rows[0].id), [])
        next_shot_type = first_pending_shots[0] if first_pending_shots else None

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
            "next_shot_type": next_shot_type,
            "pending_shots_by_row": pending_shots_by_row,
            "shot_labels": {
                CableEvidence.SHOT_START_CABLE: _shot_label(
                    CableEvidence.SHOT_START_CABLE
                ),
                CableEvidence.SHOT_END_CABLE: _shot_label(CableEvidence.SHOT_END_CABLE),
                CableEvidence.SHOT_HANDHOLE: _shot_label(CableEvidence.SHOT_HANDHOLE),
            },
        },
        status=200,
    )


@login_required
@rol_requerido("usuario")
@require_POST
@csrf_protect
def camera_create_evidence_from_key(request, asig_id: int):
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

    if shot_type not in _required_shots():
        return JsonResponse({"ok": False, "error": "Invalid shot_type."}, status=400)

    allowed_shots = _row_allowed_shots(row)
    if shot_type not in allowed_shots:
        return JsonResponse(
            {
                "ok": False,
                "error": f"{_shot_label(shot_type)} is not available for upload right now.",
            },
            status=403,
        )

    already_taken = (
        CableEvidence.objects.filter(
            assignment_requirement=row,
            shot_type=shot_type,
        )
        .exclude(review_status=CableEvidence.REVIEW_REJECTED)
        .exists()
    )
    if already_taken:
        return JsonResponse(
            {
                "ok": False,
                "error": "This photo type is already uploaded for this handhole.",
            },
            status=409,
        )

    lat = payload.get("lat") or None
    lng = payload.get("lng") or None
    acc = payload.get("acc") or None

    taken = (payload.get("client_taken_at") or "").strip()
    taken_dt = None
    if taken:
        try:
            dt = datetime.fromisoformat(taken.replace("Z", "+00:00"))
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            taken_dt = timezone.localtime(dt)
        except Exception:
            taken_dt = None

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
        shot_type=shot_type,
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
                "can_delete": ev.review_status == CableEvidence.REVIEW_REJECTED,
                "shot_type": ev.shot_type,
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
                "pending_shots": _pending_shots_for_row(row),
            },
        }
    )
