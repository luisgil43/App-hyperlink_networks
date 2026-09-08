from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from operaciones.models import BillingAssignmentQueue, SesionBillingTecnico

QUEUE_STATES = {
    "asignado",
    "en_proceso",
    "rechazado_supervisor",
}


def _active_assignment_qs(technician_id):
    qs = SesionBillingTecnico.objects.filter(
        tecnico_id=technician_id,
        estado__in=QUEUE_STATES,
        sesion__is_direct_discount=False,
    )

    try:
        SesionBillingTecnico._meta.get_field("is_active")
        qs = qs.filter(is_active=True)
    except Exception:
        pass

    return qs


def active_queue_qs(technician_id, *, lock=False):
    qs = BillingAssignmentQueue.objects.filter(
        technician_id=technician_id,
        assignment__estado__in=QUEUE_STATES,
        assignment__sesion__is_direct_discount=False,
    )

    try:
        SesionBillingTecnico._meta.get_field("is_active")
        qs = qs.filter(assignment__is_active=True)
    except Exception:
        pass

    if lock:
        qs = qs.select_for_update()

    return qs


@transaction.atomic
def ensure_queue_entry(
    assignment,
    *,
    release_if_first=True,
):
    """
    Crea la entrada de cola si todavía no existe.

    No modifica:
    - estado
    - aceptado_en
    - finalizado_en
    - reintento_habilitado
    - pagos
    - requisitos
    """

    existing = (
        BillingAssignmentQueue.objects.select_for_update()
        .filter(assignment=assignment)
        .first()
    )

    if existing:
        return existing, False

    # Bloqueamos las entradas existentes del técnico.
    current = list(
        active_queue_qs(
            assignment.tecnico_id,
            lock=True,
        ).order_by("queue_position", "id")
    )

    max_position = max(
        (entry.queue_position or 0 for entry in current),
        default=0,
    )

    position = max_position + 1
    release = bool(release_if_first and position == 1)

    entry = BillingAssignmentQueue.objects.create(
        assignment=assignment,
        technician=assignment.tecnico,
        queue_position=position,
        is_released=release,
        released_manually=False,
        released_at=timezone.now() if release else None,
        released_by=None,
    )

    return entry, True


@transaction.atomic
def normalize_queue(technician_id):
    """
    Deja la cola activa exactamente 1..N.

    No cambia qué proyectos están liberados.
    """

    entries = list(
        active_queue_qs(
            technician_id,
            lock=True,
        ).order_by(
            "queue_position",
            "assignment__sesion__creado_en",
            "id",
        )
    )

    if not entries:
        return []

    # Evita colisiones temporales con:
    # UNIQUE(technician, queue_position)
    offset = len(entries) + 10000

    for entry in entries:
        if entry.queue_position is not None:
            entry.queue_position += offset
            entry.save(
                update_fields=[
                    "queue_position",
                    "updated_at",
                ]
            )

    for position, entry in enumerate(entries, start=1):
        entry.queue_position = position
        entry.save(
            update_fields=[
                "queue_position",
                "updated_at",
            ]
        )

    return entries


@transaction.atomic
def release_now(
    assignment,
    *,
    released_by=None,
):
    """
    Show now.

    Hace visible ESTA asignación para ESTE técnico.

    No cambia su posición.
    No libera otras asignaciones.
    No oculta la que el técnico ya estaba trabajando.
    """

    entry, _ = ensure_queue_entry(
        assignment,
        release_if_first=False,
    )

    entry = BillingAssignmentQueue.objects.select_for_update().get(pk=entry.pk)

    if not entry.is_released:
        entry.is_released = True
        entry.released_manually = True
        entry.released_at = timezone.now()
        entry.released_by = released_by

        entry.save(
            update_fields=[
                "is_released",
                "released_manually",
                "released_at",
                "released_by",
                "updated_at",
            ]
        )

    return entry


@transaction.atomic
def release_next(technician_id):
    """
    Libera el primer proyecto OCULTO del técnico.

    Importante:
    no oculta proyectos previamente liberados.

    Por tanto puede existir:

        B visible/paused
        D visible/ready
        E hidden
    """

    entries = list(
        active_queue_qs(
            technician_id,
            lock=True,
        ).order_by("queue_position", "id")
    )

    for entry in entries:
        if entry.is_released:
            continue

        entry.is_released = True
        entry.released_manually = False
        entry.released_at = timezone.now()
        entry.released_by = None

        entry.save(
            update_fields=[
                "is_released",
                "released_manually",
                "released_at",
                "released_by",
                "updated_at",
            ]
        )

        return entry

    return None


@transaction.atomic
def retire_assignment_from_queue(assignment):
    """
    La asignación deja de ocupar posición activa.

    El registro NO se elimina porque contiene auditoría.
    """

    entry = (
        BillingAssignmentQueue.objects.select_for_update()
        .filter(assignment=assignment)
        .first()
    )

    if not entry:
        return

    entry.queue_position = None
    entry.is_released = False

    entry.save(
        update_fields=[
            "queue_position",
            "is_released",
            "updated_at",
        ]
    )


@transaction.atomic
def finish_team_queue(assignments):
    """
    El Finish actual es por equipo.

    Retira las asignaciones del Billing de las colas individuales
    y avanza UNA posición para cada técnico afectado.
    """

    assignments = list(assignments)

    if not assignments:
        return

    technician_ids = {assignment.tecnico_id for assignment in assignments}

    for assignment in assignments:
        retire_assignment_from_queue(assignment)

    for technician_id in technician_ids:
        normalize_queue(technician_id)
        release_next(technician_id)


def get_next_position(technician_id):
    """
    Posición automática propuesta para una asignación nueva.
    """

    max_position = (
        active_queue_qs(technician_id)
        .aggregate(value=Max("queue_position"))
        .get("value")
    )

    return (max_position or 0) + 1
