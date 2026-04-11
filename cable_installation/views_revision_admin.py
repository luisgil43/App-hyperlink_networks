from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from operaciones.models import SesionBilling
from usuarios.decoradores import rol_requerido

from .models import CableAssignmentRequirement, CableEvidence, CableRequirement


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


def _status_badge_label(status):
    mapping = {
        "asignado": "Assigned",
        "en_proceso": "In progress",
        "finalizado": "Finished",
        "en_revision_supervisor": "In supervisor review",
        "rechazado_supervisor": "Rejected by supervisor",
        "aprobado_supervisor": "Approved by supervisor",
        "rechazado_pm": "Rejected by PM",
        "aprobado_pm": "Approved by PM",
    }
    return mapping.get(status, status or "—")


def _required_shots():
    return [
        CableEvidence.SHOT_START_CABLE,
        CableEvidence.SHOT_END_CABLE,
        CableEvidence.SHOT_HANDHOLE,
    ]


def _shot_label(shot_type: str):
    if shot_type == CableEvidence.SHOT_START_CABLE:
        return "Start cable photo"
    if shot_type == CableEvidence.SHOT_END_CABLE:
        return "End cable photo"
    if shot_type == CableEvidence.SHOT_HANDHOLE:
        return "Handhole / Camera photo"
    return "Photo"


def _missing_measurement_fields(requirement):
    missing = []
    if requirement.start_ft is None:
        missing.append("Shared Start ft")
    if requirement.end_ft is None:
        missing.append("Shared End ft")
    return missing


def _requirement_measurement_complete(requirement):
    return requirement.start_ft is not None and requirement.end_ft is not None


def _requirement_present_non_rejected_shots(requirement):
    return set(
        CableEvidence.objects.filter(assignment_requirement__requirement=requirement)
        .exclude(shot_type="")
        .exclude(review_status=CableEvidence.REVIEW_REJECTED)
        .values_list("shot_type", flat=True)
        .distinct()
    )


def _billing_review_progress(billing):
    requirements = list(
        billing.cable_requirements.all().order_by("order", "sequence_no", "id")
    )

    details = {}
    completed = 0
    total = len(requirements)

    for req in requirements:
        present_shots = _requirement_present_non_rejected_shots(req)
        measurement_complete = _requirement_measurement_complete(req)
        missing_measurement_fields = _missing_measurement_fields(req)
        missing_shots = [
            shot for shot in _required_shots() if shot not in present_shots
        ]
        is_complete = measurement_complete and not missing_shots

        if is_complete:
            completed += 1

        details[req.id] = {
            "measurement_complete": measurement_complete,
            "missing_measurement_fields": missing_measurement_fields,
            "missing_shots": missing_shots,
            "is_complete": is_complete,
        }

    percent = int(round((completed / total) * 100)) if total > 0 else 0

    return {
        "total": total,
        "completed": completed,
        "percent": percent,
        "can_approve": total > 0 and completed == total,
        "details": details,
    }


@login_required
@rol_requerido("supervisor", "admin", "pm")
def review_requirements(request, billing_id):
    billing = get_object_or_404(
        SesionBilling, pk=billing_id, is_cable_installation=True
    )

    _sync_cable_requirements_to_assignments(billing)

    rows = list(
        CableAssignmentRequirement.objects.filter(assignment__sesion=billing)
        .select_related("assignment", "assignment__tecnico", "requirement")
        .order_by(
            "requirement__order",
            "requirement__sequence_no",
            "assignment__id",
            "id",
        )
    )

    evidences = list(
        CableEvidence.objects.filter(assignment_requirement__assignment__sesion=billing)
        .select_related(
            "assignment_requirement",
            "assignment_requirement__assignment",
            "assignment_requirement__assignment__tecnico",
            "assignment_requirement__requirement",
        )
        .order_by("-taken_at", "-id")
    )

    progress = _billing_review_progress(billing)

    grouped = {}
    for row in rows:
        grouped.setdefault(
            row.requirement_id,
            {
                "requirement": row.requirement,
                "rows": [],
                "evidences": [],
            },
        )
        grouped[row.requirement_id]["rows"].append(row)

    for ev in evidences:
        tech = ev.assignment_requirement.assignment.tecnico
        uploader_name = (
            getattr(tech, "get_full_name", lambda: "")() or tech.username or "—"
        ).strip()

        evidence_item = {
            "id": ev.id,
            "image": ev.image,
            "taken_at": ev.taken_at,
            "review_status": ev.review_status,
            "note": ev.note,
            "review_comment": ev.review_comment,
            "uploader_name": uploader_name,
            "shot_type": ev.shot_type,
            "shot_label": _shot_label(ev.shot_type),
        }

        grouped.setdefault(
            ev.assignment_requirement.requirement_id,
            {
                "requirement": ev.assignment_requirement.requirement,
                "rows": [],
                "evidences": [],
            },
        )
        grouped[ev.assignment_requirement.requirement_id]["evidences"].append(
            evidence_item
        )

    grouped_requirements = list(grouped.values())
    grouped_requirements.sort(
        key=lambda x: (
            x["requirement"].order,
            x["requirement"].sequence_no,
            x["requirement"].id,
        )
    )

    for block in grouped_requirements:
        req = block["requirement"]
        req_progress = progress["details"].get(
            req.id,
            {
                "measurement_complete": False,
                "missing_measurement_fields": ["Shared Start ft", "Shared End ft"],
                "missing_shots": _required_shots(),
                "is_complete": False,
            },
        )

        block["is_complete"] = req_progress["is_complete"]
        block["measurement_complete"] = req_progress["measurement_complete"]
        block["missing_measurement_fields"] = req_progress["missing_measurement_fields"]
        block["missing_shots"] = [_shot_label(s) for s in req_progress["missing_shots"]]

    billing_status_label = _status_badge_label(getattr(billing, "estado", ""))

    can_review_project = getattr(billing, "estado", "") not in {
        "aprobado_supervisor",
        "aprobado_pm",
    }

    can_approve_project = can_review_project and progress["can_approve"]

    return render(
        request,
        "cable_installation/review_requirements.html",
        {
            "billing": billing,
            "grouped_requirements": grouped_requirements,
            "billing_status_label": billing_status_label,
            "can_review_project": can_review_project,
            "can_approve_project": can_approve_project,
            "progress_percent": progress["percent"],
            "progress_completed": progress["completed"],
            "progress_total": progress["total"],
        },
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def reviewer_update_shared_requirement(request, requirement_id):
    requirement = get_object_or_404(CableRequirement, pk=requirement_id)

    start_ft = _parse_decimal(request.POST.get("start_ft"))
    end_ft = _parse_decimal(request.POST.get("end_ft"))

    reserve = requirement.planned_reserve_ft or Decimal("0.00")
    mismatch = False
    mismatch_message = ""
    expected_low = None
    expected_high = None
    installed_ft = Decimal("0.00")
    override_confirmed = (request.POST.get("override_confirmed") or "").strip() in (
        "1",
        "true",
        "True",
    )

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

    requirement.start_ft = start_ft
    requirement.end_ft = end_ft
    requirement.save()

    if mismatch and not override_confirmed:
        return JsonResponse(
            {
                "ok": False,
                "requires_confirmation": True,
                "error": mismatch_message,
                "requirement": {
                    "id": requirement.id,
                    "start_ft": (
                        ""
                        if requirement.start_ft is None
                        else str(requirement.start_ft)
                    ),
                    "end_ft": (
                        "" if requirement.end_ft is None else str(requirement.end_ft)
                    ),
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
                    "warning_text": requirement.measurement_warning_text,
                },
            },
            status=409,
        )

    return JsonResponse(
        {
            "ok": True,
            "message": "Shared measurement saved.",
            "requirement": {
                "id": requirement.id,
                "start_ft": (
                    "" if requirement.start_ft is None else str(requirement.start_ft)
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
                "warning_text": requirement.measurement_warning_text,
                "end_ft_overridden": requirement.end_ft_overridden,
            },
        }
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def reviewer_update_requirement(request, row_id):
    row = get_object_or_404(
        CableAssignmentRequirement.objects.select_related(
            "requirement", "assignment", "assignment__sesion"
        ),
        pk=row_id,
    )

    status = (request.POST.get("status") or "").strip()
    note = (request.POST.get("note") or "").strip()
    supervisor_note = (request.POST.get("supervisor_note") or "").strip()

    if status and status not in dict(CableAssignmentRequirement.STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)

    if status:
        row.status = status
    row.note = note
    row.supervisor_note = supervisor_note
    row.last_reviewed_at = timezone.now()
    row.last_reviewed_by = request.user
    row.save(
        update_fields=[
            "status",
            "note",
            "supervisor_note",
            "last_reviewed_at",
            "last_reviewed_by",
            "updated_at",
        ]
    )

    return JsonResponse(
        {
            "ok": True,
            "row": {
                "id": row.id,
                "status": row.status,
                "note": row.note,
                "supervisor_note": row.supervisor_note,
            },
        }
    )


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def approve_evidence(request, evidence_id):
    ev = get_object_or_404(
        CableEvidence.objects.select_related(
            "assignment_requirement",
            "assignment_requirement__assignment",
            "assignment_requirement__assignment__sesion",
        ),
        pk=evidence_id,
    )
    ev.approve(request.user)

    row = ev.assignment_requirement
    if row.status == CableAssignmentRequirement.STATUS_REJECTED:
        row.status = CableAssignmentRequirement.STATUS_PENDING
        row.save(update_fields=["status", "updated_at"])

    return JsonResponse({"ok": True})


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def reject_evidence(request, evidence_id):
    ev = get_object_or_404(
        CableEvidence.objects.select_related(
            "assignment_requirement",
            "assignment_requirement__assignment",
            "assignment_requirement__assignment__sesion",
        ),
        pk=evidence_id,
    )
    comment = (request.POST.get("comment") or "").strip()
    if not comment:
        return JsonResponse({"ok": False, "error": "Comment is required."}, status=400)

    ev.reject(request.user, comment)

    row = ev.assignment_requirement
    assignment = row.assignment
    billing = assignment.sesion
    now = timezone.now()

    with transaction.atomic():
        row.status = CableAssignmentRequirement.STATUS_REJECTED
        row.supervisor_note = comment
        row.last_reviewed_at = now
        row.last_reviewed_by = request.user
        row.save(
            update_fields=[
                "status",
                "supervisor_note",
                "last_reviewed_at",
                "last_reviewed_by",
                "updated_at",
            ]
        )

        billing.estado = "rechazado_supervisor"
        billing.save(update_fields=["estado"])

        for asg in billing.tecnicos_sesion.all():
            update_fields = ["estado"]
            asg.estado = "rechazado_supervisor"

            if hasattr(asg, "supervisor_revisado_en"):
                asg.supervisor_revisado_en = now
                update_fields.append("supervisor_revisado_en")

            if hasattr(asg, "supervisor_comentario"):
                asg.supervisor_comentario = comment
                update_fields.append("supervisor_comentario")

            if hasattr(asg, "reintento_habilitado"):
                asg.reintento_habilitado = True
                update_fields.append("reintento_habilitado")

            asg.save(update_fields=update_fields)

    return JsonResponse({"ok": True})


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def bulk_approve_evidences(request, billing_id):
    billing = get_object_or_404(
        SesionBilling, pk=billing_id, is_cable_installation=True
    )

    ids = request.POST.getlist("ids[]")
    if not ids:
        return JsonResponse(
            {"ok": False, "error": "No evidences selected."}, status=400
        )

    evidences = list(
        CableEvidence.objects.filter(
            id__in=ids,
            assignment_requirement__assignment__sesion=billing,
        )
    )

    for ev in evidences:
        ev.approve(request.user)
        row = ev.assignment_requirement
        if row.status == CableAssignmentRequirement.STATUS_REJECTED:
            row.status = CableAssignmentRequirement.STATUS_PENDING
            row.save(update_fields=["status", "updated_at"])

    return JsonResponse({"ok": True, "approved": len(evidences)})


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def approve_project_review(request, billing_id):
    billing = get_object_or_404(
        SesionBilling, pk=billing_id, is_cable_installation=True
    )

    progress = _billing_review_progress(billing)
    if not progress["can_approve"]:
        messages.error(
            request,
            "You cannot approve this project yet. All required handholes must be fully completed first.",
        )
        return redirect("cable_installation:review_requirements", billing_id=billing.id)

    now = timezone.now()

    with transaction.atomic():
        billing.estado = "aprobado_supervisor"
        billing.save(update_fields=["estado"])

        for assignment in billing.tecnicos_sesion.all():
            update_fields = ["estado"]
            assignment.estado = "aprobado_supervisor"

            if hasattr(assignment, "supervisor_revisado_en"):
                assignment.supervisor_revisado_en = now
                update_fields.append("supervisor_revisado_en")

            if hasattr(assignment, "reintento_habilitado"):
                assignment.reintento_habilitado = False
                update_fields.append("reintento_habilitado")

            assignment.save(update_fields=update_fields)

    messages.success(request, "Project approved by supervisor.")
    return redirect("cable_installation:review_requirements", billing_id=billing.id)


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_POST
def reject_project_review(request, billing_id):
    billing = get_object_or_404(
        SesionBilling, pk=billing_id, is_cable_installation=True
    )

    comment = (request.POST.get("comment") or "").strip()
    if not comment:
        messages.error(request, "Comment is required to reject the project.")
        return redirect("cable_installation:review_requirements", billing_id=billing.id)

    now = timezone.now()

    with transaction.atomic():
        billing.estado = "rechazado_supervisor"
        billing.save(update_fields=["estado"])

        for assignment in billing.tecnicos_sesion.all():
            update_fields = ["estado"]
            assignment.estado = "rechazado_supervisor"

            if hasattr(assignment, "supervisor_revisado_en"):
                assignment.supervisor_revisado_en = now
                update_fields.append("supervisor_revisado_en")

            if hasattr(assignment, "supervisor_comentario"):
                assignment.supervisor_comentario = comment
                update_fields.append("supervisor_comentario")

            if hasattr(assignment, "reintento_habilitado"):
                assignment.reintento_habilitado = True
                update_fields.append("reintento_habilitado")

            assignment.save(update_fields=update_fields)

    messages.warning(request, "Project rejected by supervisor.")
    return redirect("cable_installation:review_requirements", billing_id=billing.id)
