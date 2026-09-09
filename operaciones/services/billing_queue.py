from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from operaciones.models import BillingAssignmentQueue, SesionBillingTecnico

QUEUE_STATES = {
    "asignado",
    "en_proceso",
}


def _lock_technician(technician_id):
    """
    Punto estable de serialización por técnico.

    En bases que soportan SELECT FOR UPDATE (ej. PostgreSQL),
    evita que dos operaciones de cola del mismo técnico calculen
    posiciones simultáneamente.

    SQLite no aplica row-level SELECT FOR UPDATE, pero mantener
    esta llamada permite que el mismo servicio sea correcto en
    producción con una DB que sí lo soporte.
    """
    User = get_user_model()

    return User.objects.select_for_update().only("pk").get(pk=technician_id)


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
    """
    Todos los queue_state activos del técnico.

    Incluye:
    - unmanaged: queue_position=None
    - managed: queue_position=1..N
    """
    qs = BillingAssignmentQueue.objects.filter(
        technician_id=technician_id,
        assignment__estado__in=QUEUE_STATES,
        assignment__sesion__is_direct_discount=False,
    )

    try:
        SesionBillingTecnico._meta.get_field("is_active")
        qs = qs.filter(
            assignment__is_active=True,
        )
    except Exception:
        pass

    if lock:
        qs = qs.select_for_update()

    return qs


def unmanaged_queue_qs(technician_id, *, lock=False):
    """
    Asignaciones activas que existen fuera de la cola administrada.

    Estas asignaciones:
    - tienen queue_position=NULL
    - permanecen visibles
    - bloquean el avance AUTOMÁTICO de la cola managed
    """
    qs = active_queue_qs(
        technician_id,
        lock=lock,
    ).filter(
        queue_position__isnull=True,
    )

    return qs


def managed_queue_qs(technician_id, *, lock=False):
    """
    Cola administrada real del técnico.
    """
    qs = active_queue_qs(
        technician_id,
        lock=lock,
    ).filter(
        queue_position__isnull=False,
    )

    return qs


def technician_has_unmanaged_work(technician_id):
    return unmanaged_queue_qs(
        technician_id,
    ).exists()


def is_assignment_visible(assignment):
    """
    Regla única de visibilidad para estados accionables.

    Sin queue_state:
        visible por compatibilidad/transición.

    queue_position=NULL:
        visible porque es unmanaged.

    queue_position=N:
        visible únicamente cuando está released.

    IMPORTANTE:
    los estados históricos/revisión se manejan fuera de esta
    función porque deben seguir visibles según el workflow actual.
    """
    try:
        queue_state = assignment.queue_state
    except BillingAssignmentQueue.DoesNotExist:
        return True

    if queue_state.queue_position is None:
        return True

    return bool(queue_state.is_released)


@transaction.atomic
def create_legacy_queue_entry(assignment):
    """
    Registra una asignación preexistente sin inventarle prioridad.

    Legacy/unmanaged:
        queue_position=NULL
        is_released=True

    No crea BillingWorkSession.
    """
    _lock_technician(
        assignment.tecnico_id,
    )

    entry, created = BillingAssignmentQueue.objects.select_for_update().get_or_create(
        assignment=assignment,
        defaults={
            "technician": assignment.tecnico,
            "queue_position": None,
            "is_released": True,
            "released_manually": False,
            "released_at": None,
            "released_by": None,
        },
    )

    return entry, created


@transaction.atomic
def ensure_queue_entry(
    assignment,
    *,
    release_if_first=True,
):
    """
    Crea una entrada NUEVA dentro de la cola administrada.

    Nunca se utiliza para legacy.

    La nueva asignación entra al final de la cola managed.

    Liberación automática:
    - solamente si queda #1
    - y el técnico NO tiene trabajo unmanaged activo

    Si existen legacy/unmanaged:
    - se crea la prioridad
    - permanece hidden
    - Admin/Supervisor/PM puede usar Show Now
    """
    _lock_technician(
        assignment.tecnico_id,
    )

    existing = (
        BillingAssignmentQueue.objects.select_for_update()
        .filter(
            assignment=assignment,
        )
        .first()
    )

    if existing:
        return existing, False

    current = list(
        managed_queue_qs(
            assignment.tecnico_id,
            lock=True,
        ).order_by(
            "queue_position",
            "id",
        )
    )

    max_position = max(
        (entry.queue_position or 0 for entry in current),
        default=0,
    )

    position = max_position + 1

    has_unmanaged = unmanaged_queue_qs(
        assignment.tecnico_id,
        lock=True,
    ).exists()

    release = bool(release_if_first and position == 1 and not has_unmanaged)

    entry = BillingAssignmentQueue.objects.create(
        assignment=assignment,
        technician=assignment.tecnico,
        queue_position=position,
        is_released=release,
        released_manually=False,
        released_at=(timezone.now() if release else None),
        released_by=None,
    )

    return entry, True


@transaction.atomic
def normalize_queue(technician_id):
    """
    Compacta SOLAMENTE la cola managed:

        1, 2, 3, 4...

    Los unmanaged con queue_position=NULL no se tocan.
    """
    _lock_technician(
        technician_id,
    )

    entries = list(
        managed_queue_qs(
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

    # Liberamos posiciones temporalmente para evitar
    # colisiones con el UniqueConstraint.
    for entry in entries:
        entry.queue_position = None
        entry.save(
            update_fields=[
                "queue_position",
                "updated_at",
            ]
        )

    # Reconstruimos 1..N.
    for position, entry in enumerate(
        entries,
        start=1,
    ):
        entry.queue_position = position
        entry.save(
            update_fields=[
                "queue_position",
                "updated_at",
            ]
        )

    return entries


@transaction.atomic
def set_queue_position(
    assignment,
    position,
    *,
    changed_by=None,
):
    """
    Permite editar Priority inline desde Admin.

    position=None:
        saca la asignación de la cola managed
        y la convierte en unmanaged/visible.

    position=N:
        inserta/mueve la asignación a esa posición,
        desplazando las demás.

    Cambiar prioridad NO equivale a Show Now.
    """
    _lock_technician(
        assignment.tecnico_id,
    )

    entry = (
        BillingAssignmentQueue.objects.select_for_update()
        .filter(
            assignment=assignment,
        )
        .first()
    )

    if entry is None:
        entry = BillingAssignmentQueue.objects.create(
            assignment=assignment,
            technician=assignment.tecnico,
            queue_position=None,
            is_released=True,
            released_manually=False,
            released_at=None,
            released_by=None,
        )

    # ---------------------------------------------------------
    # PRIORITY VACÍA -> UNMANAGED
    # ---------------------------------------------------------
    if position in {
        None,
        "",
    }:
        was_managed = entry.queue_position is not None

        entry.queue_position = None

        # Unmanaged siempre debe permanecer visible.
        entry.is_released = True
        entry.released_manually = False
        entry.released_at = None
        entry.released_by = None

        entry.save(
            update_fields=[
                "queue_position",
                "is_released",
                "released_manually",
                "released_at",
                "released_by",
                "updated_at",
            ]
        )

        if was_managed:
            normalize_queue(
                assignment.tecnico_id,
            )

        return entry

    # ---------------------------------------------------------
    # PRIORITY NUMÉRICA -> MANAGED
    # ---------------------------------------------------------
    try:
        requested_position = int(position)
    except (
        TypeError,
        ValueError,
    ):
        raise ValueError("Priority must be a positive integer or empty.")

    if requested_position < 1:
        raise ValueError("Priority must be greater than or equal to 1.")

    managed = list(
        managed_queue_qs(
            assignment.tecnico_id,
            lock=True,
        )
        .exclude(
            pk=entry.pk,
        )
        .order_by(
            "queue_position",
            "id",
        )
    )

    # Si piden una posición superior al final,
    # simplemente se coloca al final.
    target_index = min(
        requested_position - 1,
        len(managed),
    )

    # IMPORTANTE:
    # primero quitamos temporalmente todas las posiciones.
    # También quitamos la posición del entry que estamos moviendo.
    if entry.queue_position is not None:
        entry.queue_position = None
        entry.save(
            update_fields=[
                "queue_position",
                "updated_at",
            ]
        )

    for other in managed:
        other.queue_position = None
        other.save(
            update_fields=[
                "queue_position",
                "updated_at",
            ]
        )

    ordered = list(managed)
    ordered.insert(
        target_index,
        entry,
    )

    for new_position, current in enumerate(
        ordered,
        start=1,
    ):
        current.queue_position = new_position

        # Si acaba de entrar desde unmanaged a managed,
        # NO hacemos Show Now implícito.
        #
        # La visibilidad managed se determinará abajo.
        current.save(
            update_fields=[
                "queue_position",
                "updated_at",
            ]
        )

    # Al convertir un unmanaged en managed, no queremos
    # mantener automáticamente el released=True heredado.
    #
    # Solo puede quedar automáticamente released si:
    # - quedó #1
    # - no existen otros unmanaged activos.
    #
    # Show Now es el mecanismo explícito para saltarse esto.
    has_unmanaged = (
        unmanaged_queue_qs(
            assignment.tecnico_id,
            lock=True,
        )
        .exclude(
            pk=entry.pk,
        )
        .exists()
    )

    if not entry.released_manually:
        auto_release = bool(entry.queue_position == 1 and not has_unmanaged)

        entry.is_released = auto_release
        entry.released_at = timezone.now() if auto_release else None
        entry.released_by = None

        entry.save(
            update_fields=[
                "is_released",
                "released_at",
                "released_by",
                "updated_at",
            ]
        )

    return entry


@transaction.atomic
def release_now(
    assignment,
    *,
    released_by=None,
):
    """
    Show Now.

    Excepción manual:
    - permite liberar una managed aunque existan unmanaged
    - NO cambia prioridad
    """
    _lock_technician(
        assignment.tecnico_id,
    )

    entry = (
        BillingAssignmentQueue.objects.select_for_update()
        .filter(
            assignment=assignment,
        )
        .first()
    )

    if entry is None:
        # Si no tenía queue_state, sigue siendo unmanaged.
        # Show Now no debe inventarle una prioridad.
        entry = BillingAssignmentQueue.objects.create(
            assignment=assignment,
            technician=assignment.tecnico,
            queue_position=None,
            is_released=True,
            released_manually=True,
            released_at=timezone.now(),
            released_by=released_by,
        )
        return entry

    if not entry.is_released or not entry.released_manually:
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
def release_next(
    technician_id,
):
    """
    Avance AUTOMÁTICO de la cola.

    REGLA DE TRANSICIÓN:
    si queda cualquier assignment unmanaged activo,
    no liberamos automáticamente ningún managed.

    Show Now puede saltarse esta regla porque es manual.
    """
    _lock_technician(
        technician_id,
    )

    if unmanaged_queue_qs(
        technician_id,
        lock=True,
    ).exists():
        return None

    entries = list(
        managed_queue_qs(
            technician_id,
            lock=True,
        ).order_by(
            "queue_position",
            "id",
        )
    )

    if not entries:
        return None

    # Puede haber más de un released por Show Now.
    # Si ya existe alguno visible/activo, no necesitamos
    # liberar automáticamente otro.
    if any(entry.is_released for entry in entries):
        return None

    first = entries[0]

    first.is_released = True
    first.released_manually = False
    first.released_at = timezone.now()
    first.released_by = None

    first.save(
        update_fields=[
            "is_released",
            "released_manually",
            "released_at",
            "released_by",
            "updated_at",
        ]
    )

    return first


@transaction.atomic
def retire_assignment_from_queue(
    assignment,
):
    """
    Retira una asignación terminada de la cola activa.

    El registro se conserva para trazabilidad,
    pero deja de tener posición y visibilidad activa.
    """
    _lock_technician(
        assignment.tecnico_id,
    )

    entry = (
        BillingAssignmentQueue.objects.select_for_update()
        .filter(
            assignment=assignment,
        )
        .first()
    )

    if not entry:
        return None

    entry.queue_position = None
    entry.is_released = False

    entry.save(
        update_fields=[
            "queue_position",
            "is_released",
            "updated_at",
        ]
    )

    return entry


@transaction.atomic
def finish_team_queue(
    assignments,
):
    """
    Finish sigue siendo por EQUIPO.

    Para cada técnico involucrado:
    1. retira su assignment del Billing terminado
    2. compacta la cola managed
    3. intenta release_next()

    release_next() decidirá:
    - si quedan unmanaged -> NO avanza
    - si no quedan unmanaged -> puede avanzar
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


def get_next_position(
    technician_id,
):
    max_position = (
        managed_queue_qs(
            technician_id,
        )
        .aggregate(value=Max("queue_position"))
        .get("value")
    )

    return (max_position or 0) + 1
