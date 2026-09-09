from __future__ import annotations

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from operaciones.models import SesionBilling, SesionBillingTecnico
from operaciones.models_billing_queue import (BillingAssignmentQueue,
                                              BillingWorkSession)


def _tech_name(user) -> str:
    """
    Nombre compacto para Planning / Billing List.

    Ejemplos:
        Luis Enrique + Gil Moya       -> Luis G.
        David + Suarez Quevedo        -> David S.
        Edgardo + Zapata              -> Edgardo Z.

    Si first_name/last_name no están disponibles,
    usa username como fallback.
    """
    first_name = (getattr(user, "first_name", "") or "").strip()

    last_name = (getattr(user, "last_name", "") or "").strip()

    if first_name:
        first = first_name.split()[0]

        if last_name:
            first_last_name = last_name.split()[0]
            return f"{first} {first_last_name[0].upper()}."

        return first

    username = (getattr(user, "username", "") or f"User {user.pk}").strip()

    return username


def attach_priority_rows(sessions):
    """
    Prepara los datos visuales de Priority / Time para una colección
    de SesionBilling.

    No modifica la base de datos.

    A cada SesionBilling le agrega dinámicamente:

        session.priority_rows

    Cada técnico incluye:

        - assignment_id
        - technician_id
        - name
        - full_name
        - priority
        - is_running
        - running_started_at
        - timer_total_seconds
        - timer_started_at_iso
        - timer_is_running
        - queue_is_released
        - is_legacy

    BillingWorkSession es la fuente de verdad del tiempo.

    El tiempo mostrado es:

        sesiones cerradas acumuladas
        +
        sesión abierta actual, si existe

    Importante:
    - Sólo considera asignaciones activas.
    - Direct Discount no recibe prioridad.
    - Legacy puede tener priority=None.
    - Cada técnico tiene su propio cronómetro.
    - Hace consultas agrupadas para evitar un query por cada fila.
    """

    sessions = list(sessions)

    if not sessions:
        return sessions

    session_ids = [session.pk for session in sessions]

    # ---------------------------------------------------------
    # Asignaciones activas + queue_state.
    # ---------------------------------------------------------

    assignments = list(
        SesionBillingTecnico.objects.filter(
            sesion_id__in=session_ids,
            is_active=True,
        )
        .select_related(
            "sesion",
            "tecnico",
            "queue_state",
        )
        .order_by(
            "sesion_id",
            "id",
        )
    )

    assignment_ids = [assignment.pk for assignment in assignments]

    # ---------------------------------------------------------
    # TIMER
    #
    # Necesitamos TODAS las BillingWorkSession:
    #
    # cerradas -> tiempo acumulado
    # abierta  -> tiempo que continúa corriendo
    # ---------------------------------------------------------

    closed_seconds_by_assignment = defaultdict(float)

    open_work_by_assignment = {}

    if assignment_ids:

        work_sessions = (
            BillingWorkSession.objects.filter(
                assignment_id__in=assignment_ids,
            )
            .select_related(
                "assignment",
                "technician",
            )
            .order_by(
                "assignment_id",
                "started_at",
                "id",
            )
        )

        for work_session in work_sessions:

            # -------------------------------------------------
            # Sesión cerrada:
            # agregar duración al acumulado.
            # -------------------------------------------------

            if work_session.ended_at:

                seconds = (
                    work_session.ended_at - work_session.started_at
                ).total_seconds()

                if seconds > 0:

                    closed_seconds_by_assignment[work_session.assignment_id] += seconds

            # -------------------------------------------------
            # Sesión abierta:
            # esta es la que actualmente está corriendo.
            # -------------------------------------------------

            else:

                open_work_by_assignment[work_session.assignment_id] = work_session

    # ---------------------------------------------------------
    # Agrupar assignments por Billing.
    # ---------------------------------------------------------

    assignments_by_session = defaultdict(list)

    for assignment in assignments:

        assignments_by_session[assignment.sesion_id].append(assignment)

    # ---------------------------------------------------------
    # Adjuntar estructura lista para el template.
    # ---------------------------------------------------------

    for session in sessions:

        rows = []

        for assignment in assignments_by_session.get(
            session.pk,
            [],
        ):

            technician = assignment.tecnico

            full_name = (
                technician.get_full_name()
                or getattr(
                    technician,
                    "username",
                    "",
                )
                or f"User {technician.pk}"
            ).strip()

            queue_state = getattr(
                assignment,
                "queue_state",
                None,
            )

            priority = None

            if not session.is_direct_discount and queue_state is not None:
                priority = queue_state.queue_position

            # -------------------------------------------------
            # TIMER DE ESTE TÉCNICO / ASSIGNMENT
            # -------------------------------------------------

            work_session = open_work_by_assignment.get(assignment.pk)

            timer_total_seconds = int(
                closed_seconds_by_assignment.get(
                    assignment.pk,
                    0,
                )
            )

            timer_is_running = work_session is not None

            timer_started_at_iso = (
                work_session.started_at.isoformat() if work_session else ""
            )

            rows.append(
                {
                    "assignment_id": assignment.pk,
                    "technician_id": technician.pk,
                    "name": _tech_name(technician),
                    "full_name": full_name,
                    "priority": priority,
                    "is_running": timer_is_running,
                    "running_started_at": (
                        work_session.started_at if work_session else None
                    ),
                    # =========================================
                    # NUEVO — DATOS DEL CRONÓMETRO ADMIN
                    # =========================================
                    "timer_total_seconds": timer_total_seconds,
                    "timer_started_at_iso": timer_started_at_iso,
                    "timer_is_running": timer_is_running,
                    "queue_is_released": (
                        queue_state.is_released if queue_state else True
                    ),
                    "is_legacy": (
                        queue_state is None or queue_state.queue_position is None
                    ),
                }
            )

        # -----------------------------------------------------
        # Primero técnicos con prioridad.
        # Luego legacy / Show Now.
        # -----------------------------------------------------

        rows.sort(
            key=lambda row: (
                row["priority"] is None,
                (row["priority"] if row["priority"] is not None else 999999),
                row["name"].lower(),
            )
        )

        session.priority_rows = rows

        session.has_running_technician = any(row["timer_is_running"] for row in rows)

    return sessions


def _project_label(sesion: SesionBilling) -> str:
    return (sesion.proyecto_id or "").strip() or f"Billing #{sesion.pk}"


def _active_assignments(sesion: SesionBilling):
    """
    Técnicos actualmente asignados al Billing.

    Una asignación inactiva/histórica no participa
    en la planificación nueva.
    """
    return (
        SesionBillingTecnico.objects.filter(
            sesion=sesion,
            is_active=True,
        )
        .select_related(
            "tecnico",
            "sesion",
        )
        .order_by("id")
    )


def _technician_queue(technician_id: int):
    """
    Cola administrada de un técnico.

    Los históricos con queue_position=NULL quedan fuera
    de la secuencia numérica.
    """
    return (
        BillingAssignmentQueue.objects.filter(
            technician_id=technician_id,
            queue_position__isnull=False,
        )
        .select_related(
            "assignment",
            "assignment__sesion",
            "assignment__tecnico",
            "technician",
        )
        .order_by(
            "queue_position",
            "id",
        )
    )


def _running_work(technician_id: int):
    """
    Fuente de verdad para saber qué está ejecutando
    realmente el técnico ahora mismo.
    """
    return (
        BillingWorkSession.objects.filter(
            technician_id=technician_id,
            ended_at__isnull=True,
        )
        .select_related(
            "assignment",
            "assignment__sesion",
            "technician",
        )
        .order_by(
            "-started_at",
            "-id",
        )
        .first()
    )


def _queue_entry_for_assignment(
    assignment: SesionBillingTecnico,
):
    return BillingAssignmentQueue.objects.filter(
        assignment=assignment,
    ).first()


def get_project_priorities(
    sesion: SesionBilling,
):
    """
    Devuelve la posición del proyecto para cada técnico.

    Ejemplo:

        Luis     #1
        David    #3
        Edgardo  #2
    """
    result = []

    for assignment in _active_assignments(sesion):
        queue = _queue_entry_for_assignment(assignment)

        result.append(
            {
                "assignment_id": assignment.pk,
                "technician_id": assignment.tecnico_id,
                "technician_name": _tech_name(assignment.tecnico),
                "priority": (queue.queue_position if queue else None),
                "is_released": (bool(queue.is_released) if queue else True),
            }
        )

    return result


def _lock_technician_assignments(
    technician_ids,
):
    """
    En PostgreSQL/MySQL esto serializa las operaciones
    relevantes por técnico.

    En SQLite select_for_update no ofrece bloqueo real
    por fila; el unique constraint sigue siendo la red
    final y la UI evita doble POST.
    """
    list(
        SesionBillingTecnico.objects.select_for_update()
        .filter(
            tecnico_id__in=technician_ids,
            is_active=True,
        )
        .order_by(
            "tecnico_id",
            "id",
        )
        .values_list(
            "id",
            flat=True,
        )
    )


def _rebuild_technician_queue(
    technician_id: int,
    target_assignment: SesionBillingTecnico,
    requested_priority: int,
):
    """
    Inserta target_assignment dentro de la cola de UN técnico.

    Sólo modifica la cola de ese técnico.
    """
    existing = list(
        BillingAssignmentQueue.objects.select_for_update()
        .filter(
            technician_id=technician_id,
            queue_position__isnull=False,
        )
        .select_related(
            "assignment",
        )
        .order_by(
            "queue_position",
            "id",
        )
    )

    target_entry = next(
        (entry for entry in existing if entry.assignment_id == target_assignment.pk),
        None,
    )

    others = [
        entry for entry in existing if entry.assignment_id != target_assignment.pk
    ]

    if target_entry is None:
        (
            target_entry,
            _,
        ) = BillingAssignmentQueue.objects.select_for_update().get_or_create(
            assignment=target_assignment,
            defaults={
                "technician_id": technician_id,
                "queue_position": None,
                # Mantener visible el trabajo que ya
                # era visible durante la transición.
                "is_released": True,
            },
        )

    # Seguridad: assignment y technician deben coincidir.
    if target_entry.technician_id != technician_id:
        target_entry.technician_id = technician_id

        target_entry.save(
            update_fields=[
                "technician",
            ]
        )

    insert_index = min(
        requested_priority - 1,
        len(others),
    )

    ordered = list(others)

    ordered.insert(
        insert_index,
        target_entry,
    )

    ids = [entry.pk for entry in ordered]

    # Evita colisiones temporales contra:
    #
    # UNIQUE(technician, queue_position)
    BillingAssignmentQueue.objects.filter(pk__in=ids).update(queue_position=None)

    for position, entry in enumerate(
        ordered,
        start=1,
    ):
        entry.queue_position = position

        entry.save(
            update_fields=[
                "queue_position",
            ]
        )

    target_entry.refresh_from_db()

    return target_entry


def _get_active_assignment(assignment_id: int):
    """
    Obtiene una asignación activa Billing/Técnico.
    """
    return (
        SesionBillingTecnico.objects.select_related(
            "sesion",
            "tecnico",
        )
        .filter(
            pk=assignment_id,
            is_active=True,
        )
        .first()
    )


def preview_assignment_priority(
    assignment_id: int,
    priority: int,
):
    """
    Preview de prioridad para UN SOLO técnico.

    No modifica base de datos.

    Ejemplo:
        Billing P
        Luis #1
        David #4

    Si editamos solamente David -> #2,
    la cola de Luis no participa.
    """

    assignment = _get_active_assignment(assignment_id)

    if assignment is None:
        raise ValueError("Billing technician assignment not found.")

    sesion = assignment.sesion
    technician = assignment.tecnico

    if sesion.is_direct_discount:
        raise ValueError("Direct Discounts do not use work priority.")

    try:
        priority = int(priority)
    except (TypeError, ValueError):
        raise ValueError("Invalid priority.")

    if priority < 1:
        raise ValueError("Priority must be at least 1.")

    queue_entries = list(_technician_queue(technician.pk))

    own_entry = next(
        (entry for entry in queue_entries if entry.assignment_id == assignment.pk),
        None,
    )

    others = [entry for entry in queue_entries if entry.assignment_id != assignment.pk]

    current_priority = own_entry.queue_position if own_entry else None

    requested_index = min(
        priority - 1,
        len(others),
    )

    effective_priority = requested_index + 1

    # ---------------------------------------------------------
    # Simular cola final.
    # ---------------------------------------------------------

    simulated = list(others)

    simulated.insert(
        requested_index,
        None,
    )

    displaced = []

    for new_position, entry in enumerate(
        simulated,
        start=1,
    ):
        if entry is None:
            continue

        old_position = entry.queue_position

        if old_position != new_position:
            displaced.append(
                {
                    "assignment_id": entry.assignment_id,
                    "session_id": entry.assignment.sesion_id,
                    "project_id": _project_label(entry.assignment.sesion),
                    "old_priority": old_position,
                    "new_priority": new_position,
                }
            )

    # ---------------------------------------------------------
    # Proyecto que ocupa la posición solicitada.
    # ---------------------------------------------------------

    occupying_entry = next(
        (entry for entry in others if entry.queue_position == effective_priority),
        None,
    )

    # ---------------------------------------------------------
    # Trabajo realmente activo.
    # ---------------------------------------------------------

    running = _running_work(technician.pk)

    running_project = running.assignment.sesion if running else None

    running_queue = None

    if running:
        running_queue = next(
            (
                entry
                for entry in queue_entries
                if entry.assignment_id == running.assignment_id
            ),
            None,
        )

    running_priority = running_queue.queue_position if running_queue else None

    target_is_running = bool(running and running.assignment_id == assignment.pk)

    # ---------------------------------------------------------
    # Conflicto activo.
    #
    # Solo advertimos fuerte cuando el proyecto nuevo queda
    # DELANTE del proyecto actualmente ejecutado.
    #
    # Si el proyecto activo es legacy / NULL, no conocemos su
    # posición real y advertimos conservadoramente.
    # ---------------------------------------------------------

    active_work_conflict = False

    if running and not target_is_running:

        if running_priority is None:
            active_work_conflict = True

        elif effective_priority < running_priority:
            active_work_conflict = True

    queue_conflict = bool(occupying_entry or displaced)

    requires_confirmation = bool(queue_conflict or active_work_conflict)

    return {
        "ok": True,
        "assignment_id": assignment.pk,
        "session_id": sesion.pk,
        "project_id": _project_label(sesion),
        "technician_id": technician.pk,
        "technician_name": _tech_name(technician),
        "current_priority": current_priority,
        "requested_priority": priority,
        "effective_priority": effective_priority,
        "queue_conflict": queue_conflict,
        "occupying_project": (
            {
                "assignment_id": occupying_entry.assignment_id,
                "session_id": occupying_entry.assignment.sesion_id,
                "project_id": _project_label(occupying_entry.assignment.sesion),
                "priority": occupying_entry.queue_position,
            }
            if occupying_entry
            else None
        ),
        "running_project": (
            {
                "assignment_id": running.assignment_id,
                "session_id": running_project.pk,
                "project_id": _project_label(running_project),
                "priority": running_priority,
                "started_at": running.started_at.isoformat(),
            }
            if running
            else None
        ),
        "target_is_running": target_is_running,
        "active_work_conflict": active_work_conflict,
        "displaced": displaced,
        "requires_confirmation": requires_confirmation,
    }


@transaction.atomic
def apply_assignment_priority(
    assignment_id: int,
    priority: int,
):
    """
    Aplica prioridad exclusivamente a UNA asignación
    Billing/Técnico.

    Solo las asignaciones todavía pendientes de ejecución
    (estado='asignado') pueden recibir o cambiar prioridad.

    Solo reorganiza la cola de ese técnico.

    NO toca:
    - otros técnicos del Billing;
    - timers;
    - estados;
    - otras colas.
    """

    assignment = (
        SesionBillingTecnico.objects.select_for_update()
        .select_related(
            "sesion",
            "tecnico",
        )
        .filter(
            pk=assignment_id,
            is_active=True,
        )
        .first()
    )

    if assignment is None:
        raise ValueError("Billing technician assignment not found.")

    sesion = assignment.sesion

    if sesion.is_direct_discount:
        raise ValueError("Direct Discounts do not use work priority.")

    if assignment.estado != "asignado":
        raise ValueError(
            (
                "Priority can only be assigned while the technician "
                "assignment is in Assigned status."
            )
        )

    if sesion.estado != "asignado":
        raise ValueError(
            (
                "Priority can only be assigned while the Billing "
                "is in Assigned status."
            )
        )

    try:
        priority = int(priority)
    except (
        TypeError,
        ValueError,
    ):
        raise ValueError("Invalid priority.")

    if priority < 1:
        raise ValueError("Priority must be at least 1.")

    technician_id = assignment.tecnico_id

    _lock_technician_assignments(
        [
            technician_id,
        ]
    )

    entry = _rebuild_technician_queue(
        technician_id=technician_id,
        target_assignment=assignment,
        requested_priority=priority,
    )

    return {
        "ok": True,
        "assignment_id": assignment.pk,
        "session_id": sesion.pk,
        "project_id": _project_label(sesion),
        "technician_id": technician_id,
        "technician_name": _tech_name(assignment.tecnico),
        "priority": entry.queue_position,
    }


def _normalize_single_technician_queue(
    technician_id: int,
):
    """
    Compacta exclusivamente la cola numérica
    de UN técnico.

    Ejemplo:

        #1 A
        #3 C
        #4 D

    pasa a:

        #1 A
        #2 C
        #3 D
    """

    entries = list(
        BillingAssignmentQueue.objects.select_for_update()
        .filter(
            technician_id=technician_id,
            queue_position__isnull=False,
        )
        .order_by(
            "queue_position",
            "id",
        )
    )

    if not entries:
        return

    ids = [entry.pk for entry in entries]

    BillingAssignmentQueue.objects.filter(pk__in=ids).update(queue_position=None)

    for position, entry in enumerate(
        entries,
        start=1,
    ):
        entry.queue_position = position

        entry.save(
            update_fields=[
                "queue_position",
            ]
        )


@transaction.atomic
def show_now_project(
    sesion: SesionBilling,
    user,
):
    """
    SHOW NOW definitivo.

    El Billing completo se salta la planificación.

    Para todas sus asignaciones activas:
    - queue_position -> NULL
    - is_released -> True
    - released_manually -> True
    - released_at -> ahora
    - released_by -> usuario

    Luego compacta cada cola afectada.

    NO:
    - inicia timer;
    - pausa timer;
    - cierra BillingWorkSession;
    - cambia estado operativo.
    """

    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    if sesion.is_direct_discount:
        return {
            "ok": True,
            "session_id": sesion.pk,
            "shown_now": True,
            "affected_technicians": [],
        }

    assignments = list(
        SesionBillingTecnico.objects.select_for_update()
        .select_related(
            "tecnico",
        )
        .filter(
            sesion=sesion,
            is_active=True,
        )
        .order_by(
            "tecnico_id",
            "id",
        )
    )

    if not assignments:
        raise ValueError("This Billing has no active technicians.")

    technician_ids = sorted({assignment.tecnico_id for assignment in assignments})

    _lock_technician_assignments(technician_ids)

    now = timezone.now()

    affected = []

    for assignment in assignments:

        entry, _ = BillingAssignmentQueue.objects.select_for_update().get_or_create(
            assignment=assignment,
            defaults={
                "technician_id": assignment.tecnico_id,
                "queue_position": None,
                "is_released": True,
            },
        )

        if entry.technician_id != assignment.tecnico_id:
            entry.technician_id = assignment.tecnico_id

        old_priority = entry.queue_position

        entry.queue_position = None
        entry.is_released = True
        entry.released_manually = True
        entry.released_at = now
        entry.released_by = user

        entry.save(
            update_fields=[
                "technician",
                "queue_position",
                "is_released",
                "released_manually",
                "released_at",
                "released_by",
            ]
        )

        affected.append(
            {
                "assignment_id": assignment.pk,
                "technician_id": assignment.tecnico_id,
                "technician_name": _tech_name(assignment.tecnico),
                "old_priority": old_priority,
                "priority": None,
            }
        )

    # ---------------------------------------------------------
    # Compactar solo las colas de los técnicos involucrados.
    # ---------------------------------------------------------

    for technician_id in technician_ids:
        _normalize_single_technician_queue(technician_id)

    # ---------------------------------------------------------
    # Conservamos audit del Billing a nivel proyecto.
    #
    # El queue_priority global deja de ser canónico,
    # pero mientras exista el campo lo limpiamos para no dejar
    # un dato engañoso.
    # ---------------------------------------------------------

    update_fields = []

    if hasattr(
        sesion,
        "queue_priority",
    ):
        sesion.queue_priority = None
        update_fields.append("queue_priority")

    if hasattr(
        sesion,
        "queue_visible",
    ):
        sesion.queue_visible = True
        update_fields.append("queue_visible")

    if hasattr(
        sesion,
        "queue_shown_now_at",
    ):
        sesion.queue_shown_now_at = now
        update_fields.append("queue_shown_now_at")

    if hasattr(
        sesion,
        "queue_shown_now_by",
    ):
        sesion.queue_shown_now_by = user
        update_fields.append("queue_shown_now_by")

    if update_fields:
        sesion.save(update_fields=update_fields)

    return {
        "ok": True,
        "session_id": sesion.pk,
        "project_id": _project_label(sesion),
        "shown_now": True,
        "shown_now_at": now.isoformat(),
        "affected_technicians": affected,
    }


def finish_team_queue(assignments):
    """
    Finaliza la participación de un Billing en las colas individuales
    de los técnicos.

    IMPORTANTE:
    - La cola es POR TÉCNICO.
    - No modifica estados de SesionBilling ni SesionBillingTecnico.
    - No toca cronómetros.
    - No inicia automáticamente el siguiente proyecto.
    - Solo retira este Billing de cada cola y compacta.
    - Después de compactar, el antiguo #2 pasa a #1 y quedará
      disponible automáticamente en My Assignments.
    """
    from django.db import transaction
    from django.utils import timezone

    from operaciones.models_billing_queue import BillingAssignmentQueue

    assignments = list(assignments or [])

    if not assignments:
        return

    assignment_ids = [a.pk for a in assignments if getattr(a, "pk", None)]

    if not assignment_ids:
        return

    now = timezone.now()

    with transaction.atomic():

        finished_entries = list(
            BillingAssignmentQueue.objects.select_for_update()
            .filter(
                assignment_id__in=assignment_ids,
            )
            .order_by(
                "technician_id",
                "queue_position",
                "id",
            )
        )

        affected_technician_ids = {entry.technician_id for entry in finished_entries}

        # ------------------------------------------------------
        # Este Billing deja de ocupar una posición.
        #
        # NO es Show Now manual.
        # ------------------------------------------------------
        for entry in finished_entries:

            update_fields = []

            if entry.queue_position is not None:
                entry.queue_position = None
                update_fields.append("queue_position")

            if entry.is_released is not True:
                entry.is_released = True
                update_fields.append("is_released")

            if entry.released_manually is not False:
                entry.released_manually = False
                update_fields.append("released_manually")

            entry.released_at = now
            update_fields.append("released_at")

            if entry.released_by_id is not None:
                entry.released_by = None
                update_fields.append("released_by")

            if update_fields:
                entry.save(update_fields=list(dict.fromkeys(update_fields)))

        # ------------------------------------------------------
        # Compactar SOLO la cola del técnico afectado.
        #
        # Ej:
        #
        # A #1  <- terminado
        # B #2
        # C #3
        #
        # queda:
        #
        # B #1
        # C #2
        # ------------------------------------------------------
        for technician_id in affected_technician_ids:

            queue_rows = list(
                BillingAssignmentQueue.objects.select_for_update()
                .select_related(
                    "assignment",
                    "assignment__sesion",
                )
                .filter(
                    technician_id=technician_id,
                    queue_position__isnull=False,
                    assignment__is_active=True,
                    assignment__sesion__is_direct_discount=False,
                )
                .exclude(
                    assignment_id__in=assignment_ids,
                )
                .order_by(
                    "queue_position",
                    "id",
                )
            )

            if not queue_rows:
                continue

            row_ids = [row.pk for row in queue_rows]

            # Primero NULL para evitar choque con el UniqueConstraint
            # technician + queue_position.
            BillingAssignmentQueue.objects.filter(
                pk__in=row_ids,
            ).update(
                queue_position=None,
            )

            for position, row in enumerate(
                queue_rows,
                start=1,
            ):
                row.queue_position = position
                row.save(
                    update_fields=[
                        "queue_position",
                    ]
                )
