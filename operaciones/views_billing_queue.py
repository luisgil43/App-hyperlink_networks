from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, OperationalError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from operaciones.models import SesionBilling, SesionBillingTecnico
from operaciones.models_billing_queue import (BillingAssignmentQueue,
                                              BillingWorkSession)
from operaciones.services.billing_technician_queue import (
    _project_label, _tech_name, apply_assignment_priority,
    preview_assignment_priority, show_now_project)

Usuario = get_user_model()

def _can_manage_billing_queue(user) -> bool:
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser or user.is_staff:
        return True

    candidates = []

    for attr in (
        "role",
        "rol",
        "user_type",
        "tipo_usuario",
    ):
        value = getattr(
            user,
            attr,
            None,
        )

        if value:
            candidates.append(str(value).strip().lower())

    allowed = {
        "admin",
        "administrator",
        "supervisor",
        "pm",
        "project manager",
        "project_manager",
    }

    return any(value in allowed for value in candidates)


def _forbidden():
    return JsonResponse(
        {
            "ok": False,
            "error": ("You do not have permission " "to manage Billing priority."),
        },
        status=403,
    )


def _parse_priority(request):
    raw = (request.POST.get("priority") or "").strip()

    if not raw:
        raise ValueError("Priority is required.")

    try:
        priority = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Priority must be a whole number.")

    if priority < 1:
        raise ValueError("Priority must be 1 or greater.")

    return priority


@login_required
@require_POST
def billing_assignment_set_priority(
    request,
    assignment_id,
):
    """
    Prioridad individual por técnico.

    action=preview
        no modifica nada.

    action=confirm
        reordena exclusivamente la cola
        del técnico de esta asignación.

    Solamente una asignación todavía pendiente
    (estado='asignado') puede recibir prioridad.

    NO toca timers.
    """

    if not _can_manage_billing_queue(request.user):
        return _forbidden()

    assignment = get_object_or_404(
        SesionBillingTecnico.objects.select_related(
            "sesion",
            "tecnico",
        ),
        pk=assignment_id,
        is_active=True,
    )

    if assignment.sesion.is_direct_discount:
        return JsonResponse(
            {
                "ok": False,
                "error": ("Direct Discounts do not use work priority."),
            },
            status=400,
        )

    if assignment.estado != "asignado":
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "Priority can only be assigned while the "
                    "technician assignment is in Assigned status."
                ),
            },
            status=400,
        )

    if assignment.sesion.estado != "asignado":
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "Priority can only be assigned while the "
                    "Billing is in Assigned status."
                ),
            },
            status=400,
        )

    action = (request.POST.get("action") or "preview").strip().lower()

    try:

        priority = _parse_priority(request)

        if action == "preview":

            result = preview_assignment_priority(
                assignment.pk,
                priority,
            )

            return JsonResponse(result)

        if action == "confirm":

            result = apply_assignment_priority(
                assignment.pk,
                priority,
            )

            return JsonResponse(result)

        return JsonResponse(
            {
                "ok": False,
                "error": ("Invalid priority action."),
            },
            status=400,
        )

    except ValueError as exc:

        return JsonResponse(
            {
                "ok": False,
                "error": str(exc),
            },
            status=400,
        )

    except IntegrityError:

        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "The technician queue changed "
                    "while priority was being updated. "
                    "Please try again."
                ),
            },
            status=409,
        )

    except OperationalError as exc:

        if "database is locked" in str(exc).lower():
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "The technician queue is being "
                        "updated by another request. "
                        "Please try again."
                    ),
                },
                status=409,
            )

        raise


@login_required
@require_POST
def billing_show_now(
    request,
    sesion_id,
):
    """
    Show Now a nivel del proyecto completo.

    - quita prioridad de todas las asignaciones activas;
    - compacta las colas afectadas;
    - lo deja disponible inmediatamente;
    - NO inicia ni pausa timers.
    """

    if not _can_manage_billing_queue(request.user):
        return _forbidden()

    sesion = get_object_or_404(
        SesionBilling,
        pk=sesion_id,
    )

    if sesion.is_direct_discount:
        return JsonResponse(
            {
                "ok": False,
                "error": ("Direct Discounts do not " "use Show Now."),
            },
            status=400,
        )

    try:

        result = show_now_project(
            sesion,
            request.user,
        )

        return JsonResponse(result)

    except ValueError as exc:

        return JsonResponse(
            {
                "ok": False,
                "error": str(exc),
            },
            status=400,
        )

    except IntegrityError:

        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "The technician queue changed "
                    "while Show Now was being applied. "
                    "Please try again."
                ),
            },
            status=409,
        )

    except OperationalError as exc:

        if "database is locked" in str(exc).lower():
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "The technician queue is being "
                        "updated by another request. "
                        "Please try again."
                    ),
                },
                status=409,
            )

        raise


@login_required
@require_GET
def billing_technician_queue_preview(
    request,
    technician_id,
):
    """
    Consulta la cola actual de UN técnico.

    Se usa al crear/editar un Billing para decidir
    en qué posición debe entrar la nueva asignación.

    NO modifica ninguna cola.
    NO inicia ni pausa timers.
    """

    if not _can_manage_billing_queue(request.user):
        return _forbidden()

    technician = get_object_or_404(
        Usuario,
        pk=technician_id,
        is_active=True,
    )

    queue_entries = list(
        BillingAssignmentQueue.objects.select_related(
            "assignment__sesion",
        )
        .filter(
            technician_id=technician.pk,
            queue_position__isnull=False,
            assignment__is_active=True,
        )
        .order_by(
            "queue_position",
            "id",
        )
    )

    queue = []

    for entry in queue_entries:
        sesion = entry.assignment.sesion

        queue.append(
            {
                "assignment_id": entry.assignment_id,
                "session_id": sesion.pk,
                "project_id": _project_label(sesion),
                "priority": entry.queue_position,
            }
        )

    running = (
        BillingWorkSession.objects.select_related(
            "assignment__sesion",
        )
        .filter(
            technician_id=technician.pk,
            ended_at__isnull=True,
        )
        .first()
    )

    running_project = None

    if running:
        running_session = running.assignment.sesion

        running_entry = BillingAssignmentQueue.objects.filter(
            assignment_id=running.assignment_id
        ).first()

        running_project = {
            "assignment_id": running.assignment_id,
            "session_id": running_session.pk,
            "project_id": _project_label(running_session),
            "priority": (running_entry.queue_position if running_entry else None),
            "started_at": running.started_at.isoformat(),
        }

    return JsonResponse(
        {
            "ok": True,
            "technician_id": technician.pk,
            "technician_name": _tech_name(technician),
            "queue": queue,
            "queue_count": len(queue),
            "next_priority": len(queue) + 1,
            "running_project": running_project,
        }
    )
