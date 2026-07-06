from urllib.parse import urlencode

from django.db.models import Q
from django.urls import NoReverseMatch, reverse

from .permissions import user_can_access_project_id

APPROVED_STATES = {
    "aprobado_supervisor",
    "aprobado_pm",
}

IN_PROCESS_LABELS = {
    "asignado": "Assigned",
    "en_proceso": "In progress",
    "finalizado": "Finished, pending supervisor review",
    "en_revision_supervisor": "In supervisor review",
    "rechazado_supervisor": "Rejected by supervisor",
    "rechazado_pm": "Rejected by PM",
}


def _try_reverse(name, args=None, kwargs=None, query=None):
    try:
        url = reverse(name, args=args or [], kwargs=kwargs or {})
    except NoReverseMatch:
        return ""

    if query:
        url = f"{url}?{urlencode(query)}"

    return url


def _try_reverse_first(candidates):
    """
    Intenta varias URL names y devuelve la primera que exista.
    Cada candidate puede ser:
      {
        "name": "...",
        "args": [...],
        "kwargs": {...},
        "query": {...}
      }
    """
    for candidate in candidates:
        url = _try_reverse(
            candidate.get("name"),
            args=candidate.get("args"),
            kwargs=candidate.get("kwargs"),
            query=candidate.get("query"),
        )

        if url:
            return url

    return ""


def _is_cable_session(session):
    if bool(getattr(session, "is_cable_installation", False)):
        return True

    project_text = (
        str(getattr(session, "proyecto", "") or "")
        + " "
        + str(getattr(session, "proyecto_id", "") or "")
        + " "
        + str(getattr(session, "cliente", "") or "")
    ).lower()

    return "cable" in project_text


def find_project_sessions(project_id):
    from operaciones.models import SesionBilling

    project_id = str(project_id or "").strip()

    if not project_id:
        return SesionBilling.objects.none()

    valid_finance_statuses = [
        "discount_applied",
        "sent",
        "in_review",
        "pending",
        "rejected",
        "paid",
    ]

    return (
        SesionBilling.objects.filter(
            Q(proyecto_id__iexact=project_id) | Q(proyecto__iexact=project_id)
        )
        .filter(
            Q(finance_status__in=valid_finance_statuses)
            | Q(finance_status="review_discount")
        )
        .exclude(finance_status__isnull=True)
        .exclude(finance_status="")
        .exclude(is_direct_discount=True)
        .order_by("-creado_en")
    )


def get_project_delivery_status(user, project_id):
    """

    Valida Project ID:

    - acceso por proyecto

    - existencia dentro del flujo de invoices/finance

    - estado operativo aprobado supervisor/PM

    """

    project_id = str(project_id or "").strip()

    if not project_id:
        return {
            "ok": False,
            "status": "empty",
            "message": "Enter a Project ID.",
            "sessions": [],
        }

    if not user_can_access_project_id(user, project_id):
        return {
            "ok": False,
            "status": "not_found",
            "message": "No project was found for this Project ID.",
            "sessions": [],
        }

    sessions = list(find_project_sessions(project_id))

    if not sessions:
        return {
            "ok": False,
            "status": "not_found",
            "message": "No project was found for this Project ID.",
            "sessions": [],
        }

    approved_sessions = [
        s for s in sessions if (getattr(s, "estado", "") or "") in APPROVED_STATES
    ]

    if not approved_sessions:
        latest = sessions[0]
        estado = getattr(latest, "estado", "") or ""
        label = IN_PROCESS_LABELS.get(estado, estado or "In process")

        return {
            "ok": False,
            "status": "in_process",
            "message": (
                "This project is not ready for client delivery yet. "
                f"Current status: {label}."
            ),
            "sessions": [],
        }

    return {
        "ok": True,
        "status": "approved",
        "message": "Project approved. Available deliverables loaded.",
        "sessions": approved_sessions,
    }


def _project_id_from_session(session, fallback=""):
    project_id = str(getattr(session, "proyecto_id", "") or "").strip()

    if not project_id:
        project_id = str(getattr(session, "proyecto", "") or "").strip()

    if not project_id:
        project_id = str(fallback or "").strip()

    return project_id


def _session_ids_csv(sessions):
    return ",".join(str(s.id) for s in sessions)


def build_available_deliverables_for_sessions(sessions, project_id):
    """
    Devuelve entregables disponibles para un Project ID.

    No sube archivos.
    Solo referencia reportes/archivos que ya existen o se generan desde Hyperlink.
    """
    sessions = list(sessions or [])

    if not sessions:
        return []

    first_session = sessions[0]
    resolved_project_id = _project_id_from_session(first_session, fallback=project_id)
    ids_csv = _session_ids_csv(sessions)
    is_cable = _is_cable_session(first_session)

    deliverables = []

    def add_item(
        key,
        file_type,
        title,
        description,
        source_url,
        icon,
        project_type,
        session_id=None,
    ):
        if not source_url:
            return

        if session_id:
            source_key = f"session:{session_id}:{key}"
        else:
            source_key = f"project:{resolved_project_id}:{key}:{ids_csv}"

        deliverables.append(
            {
                "key": key,
                "session_id": session_id or "",
                "project_id": resolved_project_id,
                "file_type": file_type,
                "title": title,
                "description": description,
                "source_url": source_url,
                "source_key": source_key,
                "icon": icon,
                "project_type": project_type,
            }
        )

    # ============================================================
    # Cable
    # ============================================================
    if is_cable:
        cable_client_report_url = _try_reverse(
            "cable_installation:bulk_export_client_excel",
            query={"ids": ids_csv},
        )

        add_item(
            key="cable_client_report",
            file_type="client_report",
            title="Cable Client Report",
            description="Client report generated from Cable Installation.",
            source_url=cable_client_report_url,
            icon="📄",
            project_type="Cable",
        )

        for session in sessions:
            photos_zip_url = _try_reverse(
                "operaciones:descargar_fotos_zip",
                args=[session.id],
            )

            add_item(
                key="photos_zip",
                file_type="photos_zip",
                title=f"Photos ZIP - Billing #{session.id}",
                description="ZIP file with all project evidence photos.",
                source_url=photos_zip_url,
                icon="🗂️",
                project_type="Cable",
                session_id=session.id,
            )

        return deliverables

    # ============================================================
    # Fiber
    # ============================================================
    client_report_url = _try_reverse(
        "operaciones:billing_merge_excel",
        query={"ids": ids_csv},
    )

    add_item(
        key="client_report",
        file_type="client_report",
        title="Client Report",
        description="Client report generated from Billing.",
        source_url=client_report_url,
        icon="📊",
        project_type="Fiber",
    )

    light_levels_url = _try_reverse(
        "operaciones:bulk_export_light_levels_xlsx",
        query={"ids": ids_csv},
    )

    add_item(
        key="light_levels",
        file_type="light_levels",
        title="Light Levels",
        description="Light levels Excel report for fiber work.",
        source_url=light_levels_url,
        icon="💡",
        project_type="Fiber",
    )

    # Si en tu sistema existe un reporte fotográfico con otro URL name,
    # se puede agregar aquí sin romper nada. Si no existe, no se muestra.
    photo_report_url = _try_reverse_first(
        [
            {
                "name": "operaciones:reporte_fotografico_pdf",
                "query": {"ids": ids_csv},
            },
            {
                "name": "operaciones:exportar_reporte_fotografico",
                "query": {"ids": ids_csv},
            },
            {
                "name": "operaciones:billing_photo_report",
                "query": {"ids": ids_csv},
            },
        ]
    )

    add_item(
        key="photo_report",
        file_type="photo_report",
        title="Photo Report",
        description="Photographic report generated from project evidence.",
        source_url=photo_report_url,
        icon="🖼️",
        project_type="Fiber",
    )

    for session in sessions:
        photos_zip_url = _try_reverse(
            "operaciones:descargar_fotos_zip",
            args=[session.id],
        )

        add_item(
            key="photos_zip",
            file_type="photos_zip",
            title=f"Photos ZIP - Billing #{session.id}",
            description="ZIP file with all project evidence photos.",
            source_url=photos_zip_url,
            icon="🗂️",
            project_type="Fiber",
            session_id=session.id,
        )

    return deliverables


def discover_project_deliverables(user, project_id):
    status = get_project_delivery_status(user, project_id)

    if not status["ok"]:
        return {
            "ok": False,
            "status": status["status"],
            "message": status["message"],
            "project_id": project_id,
            "project_type": "",
            "deliverables": [],
        }

    sessions = status["sessions"]
    first_session = sessions[0]
    is_cable = _is_cable_session(first_session)

    resolved_project_id = _project_id_from_session(first_session, fallback=project_id)

    deliverables = build_available_deliverables_for_sessions(
        sessions=sessions,
        project_id=resolved_project_id,
    )

    return {
        "ok": True,
        "status": "approved",
        "message": status["message"],
        "project_id": resolved_project_id,
        "project_type": "Cable" if is_cable else "Fiber",
        "deliverables": deliverables,
    }
