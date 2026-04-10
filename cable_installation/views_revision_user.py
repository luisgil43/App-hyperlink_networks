from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
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


def _requirement_has_measurement(req):
    return req.start_ft is not None and req.end_ft is not None


def _requirement_has_any_evidence(req):
    return CableEvidence.objects.filter(
        assignment_requirement__requirement=req
    ).exists()


def _faltantes_global_labels(billing):
    labels = []
    requirements = billing.cable_requirements.filter(required=True).order_by(
        "order", "sequence_no", "id"
    )
    for req in requirements:
        missing_measurement = not _requirement_has_measurement(req)
        missing_photo = not _requirement_has_any_evidence(req)
        if missing_measurement or missing_photo:
            suffix = []
            if missing_measurement:
                suffix.append("measurement")
            if missing_photo:
                suffix.append("photo")
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

        uploaded = my_counts.get(req.id, 0)
        is_done = (
            uploaded > 0 or row.status == CableAssignmentRequirement.STATUS_COMPLETED
        )

        blocks.append(
            {
                "requirement": req,
                "row": row,
                "uploaded": uploaded,
                "is_done": is_done,
                "evidences": evidences_by_requirement.get(req.id, []),
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

    reserve = requirement.planned_reserve_ft or Decimal("0.00")
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
        requirement.save()

        if status:
            row.status = status
        row.note = note
        row.save(
            update_fields=[
                "status",
                "note",
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
            },
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

    row = get_object_or_404(
        CableAssignmentRequirement.objects.select_related("requirement", "assignment"),
        assignment=assignment,
        requirement_id=int(req_id),
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
    )

    return JsonResponse(
        {
            "ok": True,
            "evidencia": {
                "id": ev.id,
                "req_id": row.requirement_id,
                "url": ev.image.url,
                "titulo": _requirement_label(row.requirement),
                "fecha": timezone.localtime(ev.taken_at).strftime("%Y-%m-%d %H:%M"),
                "uploader": (
                    getattr(assignment.tecnico, "get_full_name", lambda: "")()
                    or assignment.tecnico.username
                ),
                "review_status": ev.review_status,
                "review_comment": ev.review_comment,
                "can_delete": True,
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

    return JsonResponse({"ok": True})


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
        req = row.requirement
        my_count = CableEvidence.objects.filter(assignment_requirement=row).count()
        global_done = (
            my_count > 0 or row.status == CableAssignmentRequirement.STATUS_COMPLETED
        )
        requisitos.append(
            {
                "id": req.id,
                "my_count": my_count,
                "global_done": global_done,
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
                "url": ev.image.url,
                "titulo": _requirement_label(ev.assignment_requirement.requirement),
                "fecha": timezone.localtime(ev.taken_at).strftime("%Y-%m-%d %H:%M"),
                "uploader": (
                    getattr(tech, "get_full_name", lambda: "")() or tech.username
                ),
                "review_status": ev.review_status,
                "review_comment": ev.review_comment,
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
