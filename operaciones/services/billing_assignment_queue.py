from __future__ import annotations

from django.db import transaction

from operaciones.models import BillingAssignmentQueue, SesionBillingTecnico

from .billing_queue import (ensure_queue_entry, normalize_queue, release_next,
                            retire_assignment_from_queue)


def _has_is_active_field() -> bool:
    try:
        SesionBillingTecnico._meta.get_field("is_active")
        return True
    except Exception:
        return False


def _active_assignments_for_session(sesion):
    qs = (
        SesionBillingTecnico.objects.filter(sesion=sesion)
        .select_related("tecnico", "sesion")
        .order_by("id")
    )

    if _has_is_active_field():
        qs = qs.filter(is_active=True)

    return qs


def get_active_technician_ids(sesion) -> set[int]:
    return set(
        _active_assignments_for_session(sesion).values_list("tecnico_id", flat=True)
    )


@transaction.atomic
def activate_assignment_in_managed_queue(assignment):
    """
    Hace que una asignación NUEVA o REACTIVADA entre a la cola managed.

    Casos:

    1. No tiene queue_state:
       -> nueva asignación
       -> ensure_queue_entry()
       -> obtiene siguiente priority.

    2. Ya tiene queue_position:
       -> ya está managed
       -> no tocar.

    3. queue_position=NULL + is_released=True:
       -> legacy/unmanaged activo
       -> NO convertir automáticamente.
       -> conserva su situación histórica.

    4. queue_position=NULL + is_released=False:
       -> assignment que había salido/inactivado
       -> al reactivarse entra nuevamente como managed al final.
    """

    if getattr(assignment.sesion, "is_direct_discount", False):
        return None, False

    try:
        queue_state = assignment.queue_state
    except BillingAssignmentQueue.DoesNotExist:
        return ensure_queue_entry(
            assignment,
            release_if_first=True,
        )

    if queue_state.queue_position is not None:
        return queue_state, False

    # Legacy/unmanaged todavía activo.
    # Nunca lo transformamos solo porque se editó el Billing.
    if queue_state.is_released:
        return queue_state, False

    # Assignment previamente retirado/inactivo:
    # lo hacemos entrar como una nueva unidad de trabajo managed.
    queue_state.delete()

    return ensure_queue_entry(
        assignment,
        release_if_first=True,
    )


@transaction.atomic
def remove_assignment_from_active_queue(
    assignment,
):
    """
    Retira una asignación que deja de estar activa.

    El assignment puede conservarse en DB porque tenga evidencias.
    En ese caso queue_state queda sin posición y no released.
    """
    technician_id = assignment.tecnico_id

    retire_assignment_from_queue(assignment)

    normalize_queue(technician_id)

    release_next(technician_id)


@transaction.atomic
def normalize_after_deleted_assignment(
    technician_id,
):
    """
    Cuando SesionBillingTecnico fue eliminado físicamente,
    su BillingAssignmentQueue desaparece por CASCADE.

    Solo resta compactar y evaluar avance.
    """
    normalize_queue(technician_id)

    release_next(technician_id)


@transaction.atomic
def sync_session_assignment_queue(
    sesion,
    *,
    previously_active_technician_ids,
):
    """
    Sincroniza la cola después de cambiar los técnicos de un Billing.

    Esta función NO altera:
    - porcentajes
    - evidencias
    - requirements
    - items
    - estados operativos

    Solo reacciona al cambio de membresía.

    Existing active assignment:
        conserva exactamente su queue_state.

    Newly active assignment:
        entra managed.

    Removed assignment:
        sale de cola y se compacta.

    Direct discount:
        no participa en Billing technician work queue.
    """
    previous_ids = {int(tid) for tid in (previously_active_technician_ids or set())}

    current_assignments = list(_active_assignments_for_session(sesion))

    current_by_tid = {
        assignment.tecnico_id: assignment for assignment in current_assignments
    }

    current_ids = set(current_by_tid.keys())

    # ---------------------------------------------------------
    # DIRECT DISCOUNT
    # ---------------------------------------------------------
    if getattr(
        sesion,
        "is_direct_discount",
        False,
    ):
        affected_technicians = set()

        all_assignments = list(
            SesionBillingTecnico.objects.filter(sesion=sesion).select_related(
                "tecnico",
                "sesion",
            )
        )

        for assignment in all_assignments:
            technician_id = assignment.tecnico_id

            queue_entry = (
                BillingAssignmentQueue.objects.select_for_update()
                .filter(
                    assignment=assignment,
                )
                .first()
            )

            if queue_entry is None:
                continue

            affected_technicians.add(technician_id)

            # Direct Discount está completamente fuera
            # del sistema de prioridad.
            queue_entry.delete()

        for technician_id in sorted(affected_technicians):
            normalize_queue(technician_id)

            release_next(technician_id)

        return {
            "new": [],
            "removed": sorted(previous_ids - current_ids),
            "current": sorted(current_ids),
            "direct_discount": True,
        }
        affected_technicians = set()

        # Retiramos cualquier queue_state que pudiera venir
        # de una versión anterior del Billing.
        all_assignments = list(
            SesionBillingTecnico.objects.filter(sesion=sesion).select_related(
                "tecnico", "sesion"
            )
        )

        for assignment in all_assignments:
            if hasattr(
                assignment,
                "queue_state",
            ):
                affected_technicians.add(assignment.tecnico_id)

                retire_assignment_from_queue(assignment)

        for technician_id in affected_technicians:
            normalize_queue(technician_id)
            release_next(technician_id)

        return {
            "new": [],
            "removed": list(previous_ids - current_ids),
            "current": list(current_ids),
        }

    # ---------------------------------------------------------
    # NUEVOS / REACTIVADOS
    # ---------------------------------------------------------
    new_ids = current_ids - previous_ids

    for technician_id in sorted(new_ids):
        activate_assignment_in_managed_queue(current_by_tid[technician_id])

    # ---------------------------------------------------------
    # REMOVIDOS
    # ---------------------------------------------------------
    removed_ids = previous_ids - current_ids

    for technician_id in sorted(removed_ids):
        # Puede existir todavía porque tenía evidencias y quedó
        # is_active=False.
        inactive_assignment = (
            SesionBillingTecnico.objects.filter(
                sesion=sesion,
                tecnico_id=technician_id,
            )
            .select_related(
                "tecnico",
                "sesion",
            )
            .first()
        )

        if inactive_assignment is not None:
            remove_assignment_from_active_queue(inactive_assignment)
        else:
            # Si fue eliminado físicamente, queue_state ya cayó
            # por CASCADE.
            normalize_after_deleted_assignment(technician_id)

    return {
        "new": sorted(new_ids),
        "removed": sorted(removed_ids),
        "current": sorted(current_ids),
    }
