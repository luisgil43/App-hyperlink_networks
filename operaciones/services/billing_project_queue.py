from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from operaciones.models import SesionBilling

QUEUE_ACTIVE_STATES = {
    "asignado",
    "en_proceso",
    "rechazado_supervisor",
}


def active_normal_projects():
    return SesionBilling.objects.filter(
        is_direct_discount=False,
        estado__in=QUEUE_ACTIVE_STATES,
    )


def managed_projects():
    return (
        active_normal_projects()
        .filter(queue_priority__isnull=False)
        .order_by("queue_priority", "id")
    )


def legacy_projects():
    """
    Proyectos activos anteriores al nuevo sistema.

    Un proyecto mostrado mediante Show Now también tiene priority=NULL,
    pero NO es legacy porque queue_shown_now_at tiene valor.
    """
    return active_normal_projects().filter(
        queue_priority__isnull=True,
        queue_shown_now_at__isnull=True,
    )


def has_legacy_barrier() -> bool:
    return legacy_projects().exists()


def next_priority() -> int:
    result = managed_projects().aggregate(max_priority=Max("queue_priority"))

    current = result["max_priority"]

    return 1 if current is None else int(current) + 1


@transaction.atomic
def normalize_priorities():
    """
    Compacta únicamente la secuencia:

        #1
        #2
        #3
        ...

    NO cambia visibility.
    NO cambia estado.
    NO cambia timers.
    """
    projects = list(managed_projects().select_for_update())

    if not projects:
        return []

    ids = [project.pk for project in projects]

    # Liberamos temporalmente las posiciones para evitar conflictos
    # con el unique constraint durante el reorder.
    SesionBilling.objects.filter(pk__in=ids).update(queue_priority=None)

    for position, project in enumerate(projects, start=1):
        project.queue_priority = position
        project.save(update_fields=["queue_priority"])

    return projects


@transaction.atomic
def add_project_to_sequence(sesion: SesionBilling):
    """
    Añade un Billing NUEVO a la secuencia.

    Direct Discount queda fuera.

    Importante:
    entrar en la secuencia NO significa comenzar a trabajar.
    """
    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    if sesion.is_direct_discount:
        sesion.queue_priority = None
        sesion.queue_visible = True
        sesion.queue_shown_now_at = None
        sesion.queue_shown_now_by = None

        sesion.save(
            update_fields=[
                "queue_priority",
                "queue_visible",
                "queue_shown_now_at",
                "queue_shown_now_by",
            ]
        )

        return sesion

    # No duplicar entrada en secuencia.
    if sesion.queue_priority is not None:
        return sesion

    sesion.queue_priority = next_priority()
    sesion.queue_shown_now_at = None
    sesion.queue_shown_now_by = None

    # Mientras existan proyectos históricos activos,
    # los nuevos quedan esperando.
    if has_legacy_barrier():
        sesion.queue_visible = False

    else:
        # Si no existe ningún proyecto managed actualmente visible,
        # el primero puede quedar visible.
        has_visible_managed = (
            managed_projects().exclude(pk=sesion.pk).filter(queue_visible=True).exists()
        )

        sesion.queue_visible = not has_visible_managed

    sesion.save(
        update_fields=[
            "queue_priority",
            "queue_visible",
            "queue_shown_now_at",
            "queue_shown_now_by",
        ]
    )

    return sesion


@transaction.atomic
def set_project_priority(
    sesion: SesionBilling,
    new_priority: int,
):
    """
    Reordena únicamente la secuencia global.

    Ejemplo:

        A #1
        B #2
        C #3
        D #4

    D -> #2

        A #1
        D #2
        B #3
        C #4

    NO modifica visibility.
    NO modifica timers.
    """
    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    if sesion.is_direct_discount:
        raise ValueError("Direct discounts do not use priority.")

    try:
        new_priority = int(new_priority)
    except (TypeError, ValueError):
        raise ValueError("Invalid priority.")

    if new_priority < 1:
        raise ValueError("Priority must be at least 1.")

    projects = list(managed_projects().select_for_update().exclude(pk=sesion.pk))

    insert_index = min(
        new_priority - 1,
        len(projects),
    )

    projects.insert(
        insert_index,
        sesion,
    )

    ids = [project.pk for project in projects]

    SesionBilling.objects.filter(pk__in=ids).update(queue_priority=None)

    for position, project in enumerate(
        projects,
        start=1,
    ):
        project.queue_priority = position

        update_fields = [
            "queue_priority",
        ]

        # Si estaba fuera de la secuencia por Show Now y se le vuelve
        # a asignar prioridad manualmente, deja de considerarse Show Now.
        if project.pk == sesion.pk:
            project.queue_shown_now_at = None
            project.queue_shown_now_by = None

            update_fields += [
                "queue_shown_now_at",
                "queue_shown_now_by",
            ]

        project.save(update_fields=update_fields)

    sesion.refresh_from_db()

    return sesion


@transaction.atomic
def show_now(
    sesion: SesionBilling,
    user,
):
    """
    SHOW NOW:

    - quita la prioridad;
    - saca el proyecto de la secuencia;
    - lo vuelve visible inmediatamente;
    - compacta los números restantes;
    - NO inicia timer;
    - NO pausa timer;
    - NO libera otro proyecto;
    - NO modifica otros proyectos visibles.
    """
    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    if sesion.is_direct_discount:
        return sesion

    had_priority = sesion.queue_priority is not None

    sesion.queue_priority = None
    sesion.queue_visible = True
    sesion.queue_shown_now_at = timezone.now()
    sesion.queue_shown_now_by = user

    sesion.save(
        update_fields=[
            "queue_priority",
            "queue_visible",
            "queue_shown_now_at",
            "queue_shown_now_by",
        ]
    )

    if had_priority:
        normalize_priorities()

    return sesion


@transaction.atomic
def release_first_waiting_project():
    """
    Hace visible el primer proyecto todavía oculto
    según la secuencia global.

    NO oculta nada que ya estuviera visible.
    NO toca timers.
    """
    if has_legacy_barrier():
        return None

    project = managed_projects().select_for_update().filter(queue_visible=False).first()

    if project is None:
        return None

    project.queue_visible = True

    project.save(update_fields=["queue_visible"])

    return project


@transaction.atomic
def finish_project_sequence(
    sesion: SesionBilling,
):
    """
    Se ejecuta cuando el flujo existente termina realmente un proyecto.

    Esta función:
    - NO cambia estado operativo;
    - NO cierra timers;
    - NO modifica asignaciones.

    Solamente actualiza la secuencia.
    """
    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    was_managed = sesion.queue_priority is not None

    was_visible = bool(sesion.queue_visible)

    was_show_now = (
        sesion.queue_priority is None and sesion.queue_shown_now_at is not None
    )

    sesion.queue_priority = None
    sesion.queue_visible = False

    sesion.save(
        update_fields=[
            "queue_priority",
            "queue_visible",
        ]
    )

    # Proyecto perteneciente a la secuencia normal.
    if was_managed:
        normalize_priorities()

        # Sólo consume turno si realmente había sido liberado.
        if was_visible:
            release_first_waiting_project()

        return sesion

    # Una excepción Show Now no consume la secuencia normal.
    if was_show_now:
        return sesion

    # Proyecto legacy.
    #
    # Cuando desaparezca el último legacy activo,
    # puede comenzar la secuencia nueva.
    if not has_legacy_barrier():

        has_visible_managed = managed_projects().filter(queue_visible=True).exists()

        if not has_visible_managed:
            release_first_waiting_project()

    return sesion


@transaction.atomic
def remove_project_from_sequence_without_advancing(
    sesion: SesionBilling,
):
    """
    Para operaciones administrativas como delete/conversión/reasignación
    donde quitar un proyecto NO significa que terminó su turno.

    Quita prioridad y compacta.
    NO libera el siguiente.
    """
    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    had_priority = sesion.queue_priority is not None

    sesion.queue_priority = None

    sesion.save(
        update_fields=[
            "queue_priority",
        ]
    )

    if had_priority:
        normalize_priorities()

    return sesion


@transaction.atomic
def convert_to_direct_discount(
    sesion: SesionBilling,
):
    """
    Direct Discount queda completamente fuera
    de la secuencia y visible inmediatamente.
    """
    sesion = SesionBilling.objects.select_for_update().get(pk=sesion.pk)

    had_priority = sesion.queue_priority is not None

    sesion.queue_priority = None
    sesion.queue_visible = True
    sesion.queue_shown_now_at = None
    sesion.queue_shown_now_by = None

    sesion.save(
        update_fields=[
            "queue_priority",
            "queue_visible",
            "queue_shown_now_at",
            "queue_shown_now_by",
        ]
    )

    if had_priority:
        normalize_priorities()

    return sesion
