from collections import OrderedDict

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from access_control.services import user_can as access_user_can
from maps.models import BillingBoxAssignment, GeographicBox, GeographicDFN
from maps.services.box_locations import (remove_official_box_location,
                                         set_official_box_location)
from maps.services.project_identifiers import parse_project_identifier
from operaciones.models import SesionBilling

MAP_PERMISSION = "billing.create_billing"

LOCATION_ASSIGNABLE_STATUSES = {
    "asignado",
    "en_proceso",
}


STATUS_PRESENTATION = {
    "asignado": {
        "label": "Assigned",
        "color": "#f59e0b",
        "group": "assigned",
    },
    "en_proceso": {
        "label": "In progress",
        "color": "#2563eb",
        "group": "in_progress",
    },
    "en_revision_supervisor": {
        "label": "Supervisor review",
        "color": "#7c3aed",
        "group": "review",
    },
    "rechazado_supervisor": {
        "label": "Rejected by supervisor",
        "color": "#dc2626",
        "group": "rejected",
    },
    "aprobado_supervisor": {
        "label": "Approved by supervisor",
        "color": "#16a34a",
        "group": "approved",
    },
    "rechazado_pm": {
        "label": "Rejected by PM",
        "color": "#b91c1c",
        "group": "rejected",
    },
    "aprobado_pm": {
        "label": "Approved by PM",
        "color": "#047857",
        "group": "approved",
    },
}


def _status_presentation(status):
    return STATUS_PRESENTATION.get(
        status,
        {
            "label": status or "Unknown",
            "color": "#6b7280",
            "group": "unknown",
        },
    )


def _user_can_manage_map(user):
    return access_user_can(
        user,
        MAP_PERMISSION,
    )


def _permission_denied_json():
    return JsonResponse(
        {
            "ok": False,
            "error": (
                "You do not have permission to manage "
                "project locations."
            ),
        },
        status=403,
    )


def _validation_error_message(exc):
    if hasattr(exc, "message_dict"):
        messages = []

        for field_messages in exc.message_dict.values():
            messages.extend(field_messages)

        if messages:
            return " ".join(str(item) for item in messages)

    if hasattr(exc, "messages") and exc.messages:
        return " ".join(str(item) for item in exc.messages)

    return str(exc)


def _latest_billing_sessions_by_project():
    """
    Return the newest operational SesionBilling for every Project ID.
    """

    sessions = (
        SesionBilling.objects
        .exclude(proyecto_id="")
        .exclude(proyecto_id__isnull=True)
        .order_by("proyecto_id", "-id")
    )

    result = OrderedDict()

    for session in sessions:
        project_id = (
            session.proyecto_id or ""
        ).strip()

        if not project_id:
            continue

        if project_id not in result:
            result[project_id] = session

    return list(result.values())


def _session_is_visible_in_billing_list(session):
    """
    Reproduce la regla base de visibilidad operacional usada por
    operaciones.views.listar_billing().

    No aplica permisos del usuario ni filtros de pantalla.

    Una SesionBilling continúa perteneciendo a List Billing cuando:
      - finance_sent_at es NULL
      - finance_status es NULL, "", "none",
        "review_discount" o "rejected"
    """

    finance_sent_at = getattr(
        session,
        "finance_sent_at",
        None,
    )

    finance_status = (
        getattr(
            session,
            "finance_status",
            "",
        )
        or ""
    ).strip()

    operations_visible_statuses = {
        "",
        "none",
        "review_discount",
        "rejected",
    }

    return finance_sent_at is None and finance_status in operations_visible_statuses


def _box_for_project_id(project_id):
    project_id = (project_id or "").strip()

    if not project_id:
        return None

    return (
        GeographicBox.objects
        .select_related("dfn")
        .filter(
            identifier=project_id,
            active=True,
        )
        .first()
    )


def _build_project_record(session):
    parsed = parse_project_identifier(session.proyecto_id)

    status = _status_presentation(session.estado)

    box = _box_for_project_id(parsed["full"])

    located = bool(box and box.has_official_location)

    can_add_location = session.estado in LOCATION_ASSIGNABLE_STATUSES and not located

    in_billing_list = _session_is_visible_in_billing_list(session)

    return {
        "billing_id": session.id,
        "full_id": parsed["full"],
        "display_id": parsed["display"],
        "dfn": parsed["dfn"],
        "has_dfn": parsed["has_dfn"],
        "estado": session.estado,
        "status_label": status["label"],
        "status_color": status["color"],
        "status_group": status["group"],
        "box_id": box.id if box else None,
        "latitude": (
            str(box.official_latitude)
            if (box and box.official_latitude is not None)
            else ""
        ),
        "longitude": (
            str(box.official_longitude)
            if (box and box.official_longitude is not None)
            else ""
        ),
        "validation_radius_m": (box.validation_radius_m if box else 30),
        "located": located,
        "can_add_location": can_add_location,
        "in_billing_list": in_billing_list,
    }


def _ensure_assignment(
    *,
    session,
    box,
    user,
):
    assignment, _ = (
        BillingBoxAssignment.objects
        .get_or_create(
            billing_session=session,
            box=box,
            defaults={
                "active": True,
                "assigned_by": user,
            },
        )
    )

    fields_to_update = []

    if not assignment.active:
        assignment.active = True
        fields_to_update.append("active")

    if not assignment.assigned_by_id:
        assignment.assigned_by = user
        fields_to_update.append("assigned_by")

    if fields_to_update:
        assignment.save(
            update_fields=fields_to_update
        )

    return assignment


def _ensure_box_for_session(
    *,
    session,
    user,
):
    parsed = parse_project_identifier(
        session.proyecto_id
    )

    if not parsed["full"]:
        raise ValidationError(
            "This billing has no Project ID."
        )

    dfn = None

    if parsed["has_dfn"]:
        dfn, _ = (
            GeographicDFN.objects
            .get_or_create(
                code=parsed["dfn"],
                defaults={
                    "active": True,
                },
            )
        )

        if not dfn.active:
            dfn.active = True
            dfn.save(
                update_fields=[
                    "active",
                    "updated_at",
                ]
            )

    box, created = (
        GeographicBox.objects
        .get_or_create(
            identifier=parsed["full"],
            defaults={
                "dfn": dfn,
                "validation_radius_m": 30,
                "active": True,
            },
        )
    )

    fields_to_update = []

    if box.dfn_id != (
        dfn.id if dfn else None
    ):
        box.dfn = dfn
        fields_to_update.append("dfn")

    if not box.active:
        box.active = True
        fields_to_update.append("active")

    if fields_to_update:
        fields_to_update.append("updated_at")

        box.save(
            update_fields=fields_to_update
        )

    _ensure_assignment(
        session=session,
        box=box,
        user=user,
    )

    return parsed, box, created


@login_required
def map_home(request):
    if not _user_can_manage_map(request.user):
        return render(
            request,
            "maps/map_access_denied.html",
            status=403,
        )

    selected_scope = (request.GET.get("scope") or "all").strip()

    include_mapped_history = request.GET.get("include_mapped_history") == "1"

    # ============================================================
    # Universo completo
    #
    # Tomamos la SesionBilling más reciente de cada Project ID.
    #
    # Este universo completo se conserva porque sigue siendo
    # necesario para reconstruir un DFN completo cuando el usuario
    # selecciona un DFN que ya comenzó a utilizar Maps.
    # ============================================================

    sessions = _latest_billing_sessions_by_project()

    all_projects = [_build_project_record(session) for session in sessions]

    # ============================================================
    # DFNs actualmente operativos
    #
    # Por defecto, el selector solamente muestra DFNs que tengan
    # al menos un proyecto que todavía pertenezca al flujo de
    # List Billing.
    #
    # Esto evita llenar el selector con DFNs históricos que ya
    # avanzaron completamente hacia Invoice / Finance.
    # ============================================================

    current_dfn_codes = {
        project["dfn"]
        for project in all_projects
        if (project["has_dfn"] and project["dfn"] and project["in_billing_list"])
    }

    # ============================================================
    # DFNs históricos realmente mapeados
    #
    # Un DFN histórico solamente merece aparecer en Maps si tiene
    # al menos una Box / CTO con ubicación oficial actualmente
    # guardada.
    #
    # No mostramos todos los DFNs antiguos de Invoice / Finance.
    # Solamente conservamos como histórico navegable aquellos que
    # realmente contienen información geográfica útil.
    # ============================================================

    mapped_history_dfn_codes = {
        project["dfn"]
        for project in all_projects
        if (
            project["has_dfn"]
            and project["dfn"]
            and project["located"]
            and not project["in_billing_list"]
        )
    }

    # ============================================================
    # DFNs disponibles en el selector
    #
    # Vista normal:
    #   List Billing solamente.
    #
    # Include mapped history:
    #   List Billing
    #   +
    #   DFNs históricos que tengan ubicaciones oficiales.
    # ============================================================

    if include_mapped_history:
        dfn_codes = sorted(current_dfn_codes | mapped_history_dfn_codes)
    else:
        dfn_codes = sorted(current_dfn_codes)

    # ============================================================
    # Scope: All Projects
    #
    # All Projects conserva exactamente su comportamiento actual:
    # solamente las sesiones que cumplen la regla base de
    # visibilidad operacional de List Billing.
    #
    # Activar Include mapped history NO convierte All Projects
    # en una vista gigante del histórico.
    #
    # El checkbox solamente amplía los DFNs disponibles para que
    # el usuario pueda entrar explícitamente a un DFN histórico
    # que sí tenga información geográfica.
    # ============================================================

    if selected_scope == "all":

        filtered_projects = [
            project for project in all_projects if project["in_billing_list"]
        ]

    # ============================================================
    # Scope: DFN específico
    #
    # Cuando se selecciona un DFN válido mostramos el DFN completo:
    #
    #   List Billing
    #   +
    #   Invoice / Finance
    #
    # Esto conserva la visualización geográfica completa que ya
    # teníamos y no modifica el comportamiento interno del DFN.
    # ============================================================

    elif selected_scope in dfn_codes:

        filtered_projects = [
            project for project in all_projects if (project["dfn"] == selected_scope)
        ]

    # ============================================================
    # Scope inválido
    #
    # Si un DFN histórico estaba seleccionado y el usuario
    # desactiva Include mapped history, ese DFN deja de pertenecer
    # al selector.
    #
    # Volvemos de forma segura a All Projects.
    # ============================================================

    else:

        selected_scope = "all"

        filtered_projects = [
            project for project in all_projects if project["in_billing_list"]
        ]

    # ============================================================
    # Candidatos para primera ubicación
    #
    # Conservamos exactamente la regla existente:
    #
    #   Assigned / In progress
    #   +
    #   Not located
    #
    # Un proyecto ya ubicado nunca vuelve a aparecer aquí.
    # ============================================================

    location_candidates = [
        project for project in filtered_projects if project["can_add_location"]
    ]

    # ============================================================
    # Estadísticas del scope actualmente visible
    # ============================================================

    located_count = sum(1 for project in filtered_projects if project["located"])

    project_count = len(filtered_projects)

    unlocated_count = project_count - located_count

    # ============================================================
    # Context
    # ============================================================

    context = {
        "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
        "selected_scope": selected_scope,
        "include_mapped_history": include_mapped_history,
        "dfn_codes": dfn_codes,
        "projects": filtered_projects,
        "location_candidates": location_candidates,
        "project_count": project_count,
        "located_count": located_count,
        "unlocated_count": unlocated_count,
        "status_presentation": STATUS_PRESENTATION,
    }

    return render(
        request,
        "maps/map_home.html",
        context,
    )


@login_required
@require_POST
def save_project_location(request):
    if not _user_can_manage_map(
        request.user
    ):
        return _permission_denied_json()

    billing_id = (
        request.POST.get("billing_id")
        or ""
    ).strip()

    latitude = (
        request.POST.get("latitude")
        or ""
    ).strip()

    longitude = (
        request.POST.get("longitude")
        or ""
    ).strip()

    reason = (
        request.POST.get("reason")
        or ""
    ).strip()

    mode = (
        request.POST.get("mode")
        or "add"
    ).strip().lower()

    if mode not in {
        "add",
        "move",
    }:
        return JsonResponse(
            {
                "ok": False,
                "error": "Invalid location mode.",
            },
            status=400,
        )

    if not billing_id:
        return JsonResponse(
            {
                "ok": False,
                "error":
                    "Billing session is required.",
            },
            status=400,
        )

    session = get_object_or_404(
        SesionBilling,
        pk=billing_id,
    )

    try:
        with transaction.atomic():
            parsed = (
                parse_project_identifier(
                    session.proyecto_id
                )
            )

            existing_box = (
                _box_for_project_id(
                    parsed["full"]
                )
            )

            existing_location = bool(
                existing_box
                and existing_box.has_official_location
            )

            if mode == "add":
                if (
                    session.estado
                    not in LOCATION_ASSIGNABLE_STATUSES
                ):
                    raise ValidationError(
                        (
                            "A new location can only be assigned "
                            "when the project is Assigned or "
                            "In progress."
                        )
                    )

                if existing_location:
                    raise ValidationError(
                        (
                            "This project already has an official "
                            "location. Use Move instead."
                        )
                    )

            if mode == "move":
                if not existing_location:
                    raise ValidationError(
                        (
                            "This project does not currently have "
                            "an official location."
                        )
                    )

                if not reason:
                    raise ValidationError(
                        (
                            "A reason is required to move an "
                            "existing location."
                        )
                    )

            parsed, box, created = (
                _ensure_box_for_session(
                    session=session,
                    user=request.user,
                )
            )

            box, history = (
                set_official_box_location(
                    box=box,
                    latitude=latitude,
                    longitude=longitude,
                    changed_by=request.user,
                    reason=reason,
                    require_reason_for_move=True,
                )
            )

    except ValidationError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error":
                    _validation_error_message(exc),
            },
            status=400,
        )

    status = _status_presentation(
        session.estado
    )

    return JsonResponse(
        {
            "ok": True,
            "created": created,
            "location_changed":
                history is not None,
            "project": {
                "billing_id":
                    session.id,
                "full_id":
                    parsed["full"],
                "display_id":
                    parsed["display"],
                "dfn":
                    parsed["dfn"],
                "estado":
                    session.estado,
                "status_label":
                    status["label"],
                "status_color":
                    status["color"],
                "latitude":
                    str(box.official_latitude),
                "longitude":
                    str(box.official_longitude),
                "validation_radius_m":
                    box.validation_radius_m,
            },
        }
    )


@login_required
@require_POST
def remove_project_location(request):
    if not _user_can_manage_map(
        request.user
    ):
        return _permission_denied_json()

    billing_id = (
        request.POST.get("billing_id")
        or ""
    ).strip()

    reason = (
        request.POST.get("reason")
        or ""
    ).strip()

    if not billing_id:
        return JsonResponse(
            {
                "ok": False,
                "error":
                    "Billing session is required.",
            },
            status=400,
        )

    if not reason:
        return JsonResponse(
            {
                "ok": False,
                "error":
                    "A reason is required to remove the location.",
            },
            status=400,
        )

    session = get_object_or_404(
        SesionBilling,
        pk=billing_id,
    )

    parsed = parse_project_identifier(
        session.proyecto_id
    )

    box = _box_for_project_id(
        parsed["full"]
    )

    if not box:
        return JsonResponse(
            {
                "ok": False,
                "error":
                    "This project has no geographic Box / CTO.",
            },
            status=400,
        )

    try:
        box, history = (
            remove_official_box_location(
                box=box,
                changed_by=request.user,
                reason=reason,
            )
        )

    except ValidationError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error":
                    _validation_error_message(exc),
            },
            status=400,
        )

    return JsonResponse(
        {
            "ok": True,
            "location_removed":
                history is not None,
            "project": {
                "billing_id":
                    session.id,
                "full_id":
                    parsed["full"],
                "display_id":
                    parsed["display"],
            },
        }
    )
