from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from operaciones.models import SesionBillingTecnico
from usuarios.decoradores import rol_requerido

from .models import CableAssignmentRequirement, CableEvidence


def _parse_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _sync_cable_requirements_to_assignments(billing):
    assignments = list(billing.tecnicos_sesion.all())
    requirements = list(
        billing.cable_requirements.all().order_by("order", "sequence_no", "id")
    )

    if not assignments or not requirements:
        return

    existing_pairs = set(
        CableAssignmentRequirement.objects.filter(
            assignment__in=assignments,
            requirement__in=requirements,
        ).values_list("assignment_id", "requirement_id")
    )

    to_create = []
    for assignment in assignments:
        for requirement in requirements:
            key = (assignment.id, requirement.id)
            if key in existing_pairs:
                continue
            to_create.append(
                CableAssignmentRequirement(
                    assignment=assignment,
                    requirement=requirement,
                    status=CableAssignmentRequirement.STATUS_PENDING,
                )
            )

    if to_create:
        CableAssignmentRequirement.objects.bulk_create(to_create)


def _requirement_label(req):
    return f"PK {req.sequence_no} - {req.handhole}"


def _required_shots():
    return [
        CableEvidence.SHOT_START_CABLE,
        CableEvidence.SHOT_END_CABLE,
        CableEvidence.SHOT_HANDHOLE,
    ]


def _shot_label(shot_type: str):
    if shot_type == CableEvidence.SHOT_START_CABLE:
        return "Start cable"
    if shot_type == CableEvidence.SHOT_END_CABLE:
        return "End cable"
    if shot_type == CableEvidence.SHOT_HANDHOLE:
        return "Handhole / Camera"
    return shot_type or "Photo"


def _requirement_has_measurement(req):
    return (
        req.start_ft is not None
        and req.planned_reserve_ft is not None
        and req.end_ft is not None
    )


def _present_shots_for_row(row):
    return set(
        CableEvidence.objects.filter(
            assignment_requirement=row,
        )
        .exclude(shot_type="")
        .exclude(review_status=CableEvidence.REVIEW_REJECTED)
        .values_list("shot_type", flat=True)
        .distinct()
    )


def _pending_shots_for_row(row):
    present = _present_shots_for_row(row)
    return [shot for shot in _required_shots() if shot not in present]


def _row_latest_rejection_comment(row):
    ev = (
        CableEvidence.objects.filter(
            assignment_requirement=row,
            review_status=CableEvidence.REVIEW_REJECTED,
        )
        .exclude(review_comment="")
        .order_by("-reviewed_at", "-id")
        .first()
    )
    return ev.review_comment if ev else ""


def _row_has_rejected_photo(row):
    return CableEvidence.objects.filter(
        assignment_requirement=row,
        review_status=CableEvidence.REVIEW_REJECTED,
    ).exists()


def _row_is_complete(row):
    req = row.requirement
    has_measurements = _requirement_has_measurement(req)
    pending_shots = _pending_shots_for_row(row)
    has_rejected = _row_has_rejected_photo(row)

    return has_measurements and not pending_shots and not has_rejected


def _refresh_row_status(row):
    is_complete = _row_is_complete(row)
    new_status = (
        CableAssignmentRequirement.STATUS_COMPLETED
        if is_complete
        else CableAssignmentRequirement.STATUS_PENDING
    )

    latest_rejection = _row_latest_rejection_comment(row)
    supervisor_note = latest_rejection or ""

    updates = []

    if row.status != new_status:
        row.status = new_status
        updates.append("status")

    if row.supervisor_note != supervisor_note:
        row.supervisor_note = supervisor_note
        updates.append("supervisor_note")

    if updates:
        updates.append("updated_at")
        row.save(update_fields=updates)

    return is_complete


def _requirement_has_any_evidence(req):
    return (
        CableEvidence.objects.filter(assignment_requirement__requirement=req)
        .exclude(review_status=CableEvidence.REVIEW_REJECTED)
        .exists()
    )


def _faltantes_global_labels(billing):
    labels = []
    requirements = billing.cable_requirements.filter(required=True).order_by(
        "order", "sequence_no", "id"
    )

    rows = (
        CableAssignmentRequirement.objects.filter(
            assignment__sesion=billing,
            requirement__in=requirements,
        )
        .select_related("requirement", "assignment")
        .order_by("requirement__order", "requirement__sequence_no", "id")
    )

    row_by_req = {}
    for row in rows:
        row_by_req.setdefault(row.requirement_id, row)

    for req in requirements:
        sample_row = row_by_req.get(req.id)
        missing_measurement = not _requirement_has_measurement(req)

        present_shots = set()
        has_rejected = False
        if sample_row:
            present_shots = _present_shots_for_row(sample_row)
            has_rejected = _row_has_rejected_photo(sample_row)

        missing_shots = [
            shot for shot in _required_shots() if shot not in present_shots
        ]

        if missing_measurement or missing_shots or has_rejected:
            suffix = []
            if missing_measurement:
                suffix.append("measurement")
            if missing_shots:
                suffix.append("photos")
            if has_rejected:
                suffix.append("review")
            labels.append(f"{_requirement_label(req)} ({', '.join(suffix)})")

    return labels


def _pendientes_aceptar_names(billing):
    names = []
    for a in billing.tecnicos_sesion.select_related("tecnico").all():
        if (a.estado or "").strip() == "asignado":
            tech = a.tecnico
            names.append(
                (
                    getattr(tech, "get_full_name", lambda: "")() or tech.username or ""
                ).strip()
            )
    return names


def _assignment_can_finish(assignment):
    billing = assignment.sesion
    faltantes = _faltantes_global_labels(billing)
    pendientes = _pendientes_aceptar_names(billing)
    return not faltantes and not pendientes


def _row_photo_cards(row, evidences):
    cards = []
    by_shot = {}

    for ev in evidences:
        by_shot.setdefault(ev.shot_type or "", []).append(ev)

    for shot in _required_shots():
        shot_evs = by_shot.get(shot, [])
        latest = shot_evs[0] if shot_evs else None
        cards.append(
            {
                "shot_type": shot,
                "shot_label": _shot_label(shot),
                "exists": latest is not None,
                "pending": (
                    latest.review_status == CableEvidence.REVIEW_PENDING
                    if latest
                    else False
                ),
                "approved": (
                    latest.review_status == CableEvidence.REVIEW_APPROVED
                    if latest
                    else False
                ),
                "rejected": (
                    latest.review_status == CableEvidence.REVIEW_REJECTED
                    if latest
                    else False
                ),
                "review_comment": latest.review_comment if latest else "",
                "evidence": latest,
            }
        )

    return cards


@login_required
@rol_requerido("usuario")
def technician_requirements(request, assignment_id):
    assignment = get_object_or_404(
        SesionBillingTecnico.objects.select_related("sesion", "tecnico"),
        pk=assignment_id,
    )

    if assignment.tecnico_id != request.user.id:
        raise Http404()

    if not assignment.sesion.is_cable_installation:
        raise Http404()

    billing = assignment.sesion
    _sync_cable_requirements_to_assignments(billing)

    own_rows = list(
        CableAssignmentRequirement.objects.filter(assignment=assignment)
        .select_related("requirement", "assignment", "assignment__tecnico")
        .order_by("requirement__order", "requirement__sequence_no", "id")
    )
    own_row_by_requirement = {row.requirement_id: row for row in own_rows}

    all_evidences = list(
        CableEvidence.objects.filter(
            assignment_requirement__requirement__billing=billing
        )
        .select_related(
            "assignment_requirement",
            "assignment_requirement__assignment",
            "assignment_requirement__assignment__tecnico",
            "assignment_requirement__requirement",
        )
        .order_by("-id")
    )

    evidences_by_requirement = {}
    my_counts = {}
    for ev in all_evidences:
        rid = ev.assignment_requirement.requirement_id
        evidences_by_requirement.setdefault(rid, []).append(ev)
        if ev.assignment_requirement.assignment_id == assignment.id:
            my_counts[rid] = my_counts.get(rid, 0) + 1

    requirements = list(
        billing.cable_requirements.all().order_by("order", "sequence_no", "id")
    )

    blocks = []
    for req in requirements:
        row = own_row_by_requirement.get(req.id)
        if not row:
            continue

        row_evidences = evidences_by_requirement.get(req.id, [])
        is_done = _refresh_row_status(row)
        row.refresh_from_db()

        uploaded = my_counts.get(req.id, 0)
        pending_shots = _pending_shots_for_row(row)
        locked = is_done
        rejection_comment = row.supervisor_note or ""

        blocks.append(
            {
                "requirement": req,
                "row": row,
                "uploaded": uploaded,
                "is_done": is_done,
                "locked": locked,
                "evidences": row_evidences,
                "pending_shots": pending_shots,
                "photo_cards": _row_photo_cards(row, row_evidences),
                "rejection_comment": rejection_comment,
            }
        )

    faltantes_global = _faltantes_global_labels(billing)
    pendientes_aceptar = _pendientes_aceptar_names(billing)
    can_finish = _assignment_can_finish(assignment)

    return render(
        request,
        "cable_installation/technician_requirements.html",
        {
            "assignment": assignment,
            "a": assignment,
            "billing": billing,
            "blocks": blocks,
            "faltantes_global": faltantes_global,
            "pendientes_aceptar": pendientes_aceptar,
            "can_finish": can_finish,
        },
    )


@login_required
@rol_requerido("usuario")
@require_POST
@csrf_protect
def update_assignment_requirement(request, row_id):
    row = get_object_or_404(
        CableAssignmentRequirement.objects.select_related(
            "assignment", "requirement", "assignment__sesion"
        ),
        pk=row_id,
    )

    if row.assignment.tecnico_id != request.user.id:
        return JsonResponse({"ok": False, "error": "No permission."}, status=403)

    requirement = row.requirement

    start_ft = _parse_decimal(request.POST.get("start_ft"))
    reserve_ft = _parse_decimal(request.POST.get("reserve_ft"))
    end_ft = _parse_decimal(request.POST.get("end_ft"))
    status = (request.POST.get("status") or "").strip()
    note = (request.POST.get("note") or "").strip()
    override_confirmed = (request.POST.get("override_confirmed") or "").strip() in (
        "1",
        "true",
        "True",
    )

    if status and status not in dict(CableAssignmentRequirement.STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)

    for label, value in (
        ("Start ft", start_ft),
        ("Reserve ft", reserve_ft),
        ("End ft", end_ft),
    ):
        if value is not None and value < 0:
            return JsonResponse(
                {"ok": False, "error": f"{label} cannot be negative."},
                status=400,
            )

    reserve = (
        reserve_ft
        if reserve_ft is not None
        else (requirement.planned_reserve_ft or Decimal("0.00"))
    )

    mismatch = False
    mismatch_message = ""
    expected_low = None
    expected_high = None
    installed_ft = Decimal("0.00")

    if start_ft is not None:
        expected_low = start_ft - reserve
        expected_high = start_ft + reserve

    if start_ft is not None and end_ft is not None:
        installed_ft = abs(end_ft - start_ft)
        if installed_ft != reserve:
            mismatch = True
            mismatch_message = (
                f"Expected End ft could be {expected_low} or {expected_high} "
                f"(difference {reserve} ft). You entered {end_ft} "
                f"(difference {installed_ft} ft)."
            )

    if mismatch and not override_confirmed:
        return JsonResponse(
            {
                "ok": False,
                "requires_confirmation": True,
                "error": mismatch_message,
                "expected_low": "" if expected_low is None else str(expected_low),
                "expected_high": "" if expected_high is None else str(expected_high),
                "installed_ft": str(installed_ft),
            },
            status=409,
        )

    with transaction.atomic():
        requirement.start_ft = start_ft
        requirement.end_ft = end_ft
        if reserve_ft is not None:
            requirement.planned_reserve_ft = reserve_ft
        requirement.save()

        row.note = note
        row.save(
            update_fields=[
                "note",
                "updated_at",
            ]
        )

        is_complete = _refresh_row_status(row)

    requirement.refresh_from_db()
    row.refresh_from_db()

    return JsonResponse(
        {
            "ok": True,
            "row": {
                "id": row.id,
                "status": row.status,
                "note": row.note,
                "supervisor_note": row.supervisor_note or "",
                "is_complete": is_complete,
                "pending_shots": _pending_shots_for_row(row),
            },
            "requirement": {
                "id": requirement.id,
                "start_ft": (
                    "" if requirement.start_ft is None else str(requirement.start_ft)
                ),
                "reserve_ft": (
                    ""
                    if requirement.planned_reserve_ft is None
                    else str(requirement.planned_reserve_ft)
                ),
                "end_ft": "" if requirement.end_ft is None else str(requirement.end_ft),
                "installed_ft": str(requirement.installed_ft),
                "expected_low": (
                    ""
                    if requirement.expected_end_ft_low is None
                    else str(requirement.expected_end_ft_low)
                ),
                "expected_high": (
                    ""
                    if requirement.expected_end_ft_high is None
                    else str(requirement.expected_end_ft_high)
                ),
                "end_ft_overridden": requirement.end_ft_overridden,
                "warning_text": requirement.measurement_warning_text,
            },
        }
    )


@login_required
@rol_requerido("usuario")
@require_POST
def upload_requirement_evidence_ajax(request, assignment_id):
    assignment = get_object_or_404(
        SesionBillingTecnico.objects.select_related("sesion", "tecnico"),
        pk=assignment_id,
    )

    if assignment.tecnico_id != request.user.id:
        return JsonResponse({"ok": False, "error": "No permission."}, status=403)

    if not assignment.sesion.is_cable_installation:
        return JsonResponse({"ok": False, "error": "Invalid assignment."}, status=400)

    req_id = request.POST.get("req_id")
    row_id = request.POST.get("row_id")
    shot_type = (request.POST.get("shot_type") or "").strip()
    image = request.FILES.get("imagen")
    note = (request.POST.get("nota") or "").strip()
    lat = _parse_decimal(request.POST.get("lat"))
    lng = _parse_decimal(request.POST.get("lng"))
    acc = _parse_decimal(request.POST.get("acc"))
    client_taken_at_raw = (request.POST.get("client_taken_at") or "").strip()

    if not req_id or not str(req_id).isdigit():
        return JsonResponse(
            {"ok": False, "error": "Requirement is required."}, status=400
        )
    if not image:
        return JsonResponse({"ok": False, "error": "Image is required."}, status=400)
    if shot_type and shot_type not in _required_shots():
        return JsonResponse({"ok": False, "error": "Invalid shot type."}, status=400)

    row_qs = CableAssignmentRequirement.objects.select_related(
        "requirement", "assignment"
    )
    if row_id and str(row_id).isdigit():
        row = get_object_or_404(
            row_qs,
            pk=int(row_id),
            assignment=assignment,
            requirement_id=int(req_id),
        )
    else:
        row = get_object_or_404(
            row_qs,
            assignment=assignment,
            requirement_id=int(req_id),
        )

    if shot_type:
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
                    "error": f"{_shot_label(shot_type)} already uploaded for this requirement.",
                },
                status=409,
            )

    taken_at = timezone.now()
    if client_taken_at_raw:
        try:
            dt = timezone.datetime.fromisoformat(
                client_taken_at_raw.replace("Z", "+00:00")
            )
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            taken_at = dt
        except Exception:
            pass

    ev = CableEvidence.objects.create(
        assignment_requirement=row,
        image=image,
        note=note,
        taken_at=taken_at,
        lat=lat,
        lng=lng,
        gps_accuracy_m=acc,
        shot_type=shot_type or "",
    )

    is_complete = _refresh_row_status(row)
    row.refresh_from_db()

    return JsonResponse(
        {
            "ok": True,
            "evidencia": {
                "id": ev.id,
                "req_id": row.requirement_id,
                "row_id": row.id,
                "url": ev.image.url,
                "titulo": _requirement_label(row.requirement),
                "fecha": timezone.localtime(ev.taken_at).strftime("%Y-%m-%d %H:%M"),
                "uploader": (
                    getattr(assignment.tecnico, "get_full_name", lambda: "")()
                    or assignment.tecnico.username
                ),
                "review_status": ev.review_status,
                "review_comment": ev.review_comment,
                "shot_type": ev.shot_type,
                "shot_label": _shot_label(ev.shot_type),
                "can_delete": True,
            },
            "row": {
                "id": row.id,
                "status": row.status,
                "supervisor_note": row.supervisor_note or "",
                "is_complete": is_complete,
                "pending_shots": _pending_shots_for_row(row),
            },
        }
    )


@login_required
@rol_requerido("usuario")
@require_POST
def delete_own_evidence(request, evidence_id):
    ev = get_object_or_404(
        CableEvidence.objects.select_related(
            "assignment_requirement",
            "assignment_requirement__assignment",
            "assignment_requirement__requirement",
        ),
        pk=evidence_id,
    )

    row = ev.assignment_requirement
    if row.assignment.tecnico_id != request.user.id:
        return JsonResponse({"ok": False, "error": "No permission."}, status=403)

    if ev.review_status != CableEvidence.REVIEW_REJECTED:
        return JsonResponse(
            {
                "ok": False,
                "error": "Only rejected photos can be deleted.",
            },
            status=403,
        )

    image_name = ev.image.name
    try:
        storage = ev.image.storage
        ev.delete()
        if image_name:
            try:
                storage.delete(image_name)
            except Exception:
                pass
    except Exception:
        return JsonResponse(
            {"ok": False, "error": "Could not delete photo."}, status=400
        )

    is_complete = _refresh_row_status(row)
    row.refresh_from_db()

    return JsonResponse(
        {
            "ok": True,
            "row": {
                "id": row.id,
                "status": row.status,
                "supervisor_note": row.supervisor_note or "",
                "is_complete": is_complete,
                "pending_shots": _pending_shots_for_row(row),
            },
        }
    )


@login_required
@rol_requerido("usuario")
@require_GET
def technician_requirements_status_json(request, assignment_id):
    assignment = get_object_or_404(
        SesionBillingTecnico.objects.select_related("sesion", "tecnico"),
        pk=assignment_id,
    )

    if assignment.tecnico_id != request.user.id:
        return JsonResponse({"ok": False, "error": "No permission."}, status=403)

    billing = assignment.sesion
    _sync_cable_requirements_to_assignments(billing)

    after = request.GET.get("after")
    try:
        after_id = int(after or 0)
    except Exception:
        after_id = 0

    requisitos = []
    own_rows = (
        CableAssignmentRequirement.objects.filter(assignment=assignment)
        .select_related("requirement")
        .order_by("requirement__order", "requirement__sequence_no", "id")
    )

    for row in own_rows:
        is_complete = _refresh_row_status(row)
        row.refresh_from_db()

        req = row.requirement
        my_count = CableEvidence.objects.filter(assignment_requirement=row).count()
        pending_shots = _pending_shots_for_row(row)
        rejected = _row_has_rejected_photo(row)

        requisitos.append(
            {
                "id": req.id,
                "row_id": row.id,
                "my_count": my_count,
                "global_done": is_complete,
                "locked": is_complete,
                "pending_shots": pending_shots,
                "has_rejected": rejected,
                "supervisor_note": row.supervisor_note or "",
                "start_ft": "" if req.start_ft is None else str(req.start_ft),
                "reserve_ft": (
                    ""
                    if req.planned_reserve_ft is None
                    else str(req.planned_reserve_ft)
                ),
                "end_ft": "" if req.end_ft is None else str(req.end_ft),
                "installed_ft": (
                    "" if req.installed_ft is None else str(req.installed_ft)
                ),
                "warning_text": req.measurement_warning_text or "",
            }
        )

    nuevas = list(
        CableEvidence.objects.filter(
            assignment_requirement__requirement__billing=billing,
            id__gt=after_id,
        )
        .select_related(
            "assignment_requirement",
            "assignment_requirement__assignment",
            "assignment_requirement__assignment__tecnico",
            "assignment_requirement__requirement",
        )
        .order_by("id")
    )

    evidencias_nuevas = []
    for ev in nuevas:
        tech = ev.assignment_requirement.assignment.tecnico
        evidencias_nuevas.append(
            {
                "id": ev.id,
                "req_id": ev.assignment_requirement.requirement_id,
                "row_id": ev.assignment_requirement.id,
                "url": ev.image.url,
                "titulo": _requirement_label(ev.assignment_requirement.requirement),
                "fecha": timezone.localtime(ev.taken_at).strftime("%Y-%m-%d %H:%M"),
                "uploader": (
                    getattr(tech, "get_full_name", lambda: "")() or tech.username
                ),
                "review_status": ev.review_status,
                "review_comment": ev.review_comment,
                "shot_type": ev.shot_type,
                "shot_label": _shot_label(ev.shot_type),
                "can_delete": ev.assignment_requirement.assignment_id == assignment.id,
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "requisitos": requisitos,
            "faltantes_global": _faltantes_global_labels(billing),
            "pendientes_aceptar": _pendientes_aceptar_names(billing),
            "can_finish": _assignment_can_finish(assignment),
            "evidencias_nuevas": evidencias_nuevas,
        }
    )
