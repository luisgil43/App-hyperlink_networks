from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from operaciones.models import BillingWorkSession


def get_running_session(technician_id):
    return (
        BillingWorkSession.objects.filter(
            technician_id=technician_id,
            ended_at__isnull=True,
        )
        .select_related(
            "assignment",
            "assignment__sesion",
        )
        .order_by("-started_at", "-id")
        .first()
    )


@transaction.atomic
def start_or_resume(assignment):
    """
    Inicia el cronómetro de esta asignación.

    Si el técnico tenía otro Billing corriendo:
    lo pausa automáticamente.

    NO cambia los estados Billing existentes.
    """

    now = timezone.now()

    running_sessions = list(
        BillingWorkSession.objects.select_for_update()
        .filter(
            technician_id=assignment.tecnico_id,
            ended_at__isnull=True,
        )
        .order_by("-started_at", "-id")
    )

    # Protección adicional:
    # si por datos antiguos hubiera más de uno abierto,
    # cerramos todos excepto el correspondiente.
    same_assignment = None

    for running in running_sessions:
        if running.assignment_id == assignment.id and same_assignment is None:
            same_assignment = running
            continue

        running.ended_at = now
        running.save(update_fields=["ended_at"])

    if same_assignment:
        return same_assignment, False

    work_session = BillingWorkSession.objects.create(
        assignment=assignment,
        technician=assignment.tecnico,
        started_at=now,
    )

    return work_session, True


@transaction.atomic
def pause_assignment(assignment):
    now = timezone.now()

    running = (
        BillingWorkSession.objects.select_for_update()
        .filter(
            assignment=assignment,
            technician_id=assignment.tecnico_id,
            ended_at__isnull=True,
        )
        .order_by("-started_at", "-id")
        .first()
    )

    if not running:
        return None

    running.ended_at = now
    running.save(update_fields=["ended_at"])

    return running


@transaction.atomic
def close_team_sessions(assignments):
    """
    El Finish actual termina el Billing para todo el equipo.
    Por ello cerramos cualquier tramo activo perteneciente
    a cualquiera de esas asignaciones.
    """

    assignment_ids = [assignment.id for assignment in assignments]

    if not assignment_ids:
        return 0

    return BillingWorkSession.objects.filter(
        assignment_id__in=assignment_ids,
        ended_at__isnull=True,
    ).update(
        ended_at=timezone.now(),
    )


def total_seconds(assignment):
    """
    Tiempo efectivo acumulado.
    """

    now = timezone.now()
    total = 0

    sessions = BillingWorkSession.objects.filter(assignment=assignment).only(
        "started_at", "ended_at"
    )

    for work_session in sessions:
        end = work_session.ended_at or now

        total += max(
            0,
            int((end - work_session.started_at).total_seconds()),
        )

    return total
