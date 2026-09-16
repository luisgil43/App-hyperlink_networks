# operaciones/views_project_downloads.py

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse

from cable_installation.models import CableEvidence, CableRequirement
from core.permissions import user_has_project_access
from facturacion.models import Proyecto
from operaciones.models import EvidenciaFotoBilling, SesionBilling
from operaciones.views_billing_exec import storage_file_exists

PROJECT_DOWNLOAD_APPROVED_STATUSES = {
    "aprobado_supervisor",
    "aprobado_pm",
}


PROJECT_DOWNLOAD_STATUS_LABELS = {
    "asignado": "Assigned",
    "en_proceso": "In progress",
    "en_revision_supervisor": "Submitted — supervisor review",
    "rechazado_supervisor": "Rejected by supervisor",
    "aprobado_supervisor": "Approved by Supervisor",
    "rechazado_pm": "Rejected by PM",
    "aprobado_pm": "Approved by PM",
}


PROJECT_DOWNLOAD_FINANCE_STATUS_LABELS = {
    "none": "Not sent to Finance",
    "sent": "Sent to Finance",
    "pending": "Pending",
    "in_review": "In review",
    "rejected": "Rejected by Finance",
    "review_discount": "Discount review",
    "sent_to_client": "Sent to Client",
    "pending_invoice": "Pending invoicing",
    "invoiced": "Invoiced",
    "paid": "Collected",
}


def _normalize_project_value(value):
    return str(value or "").strip()


def _find_billing_session(project_id):
    """
    Busca el Project ID directamente en SesionBilling.

    IMPORTANTE:
    - No aplica los filtros de List Billing.
    - No aplica los filtros de Invoice List.
    - No depende de finance_status.
    - Por lo tanto, encuentra la sesión independientemente
      de dónde se esté mostrando actualmente.
    """
    project_id = _normalize_project_value(project_id)

    if not project_id:
        return None

    return (
        SesionBilling.objects.filter(
            proyecto_id__iexact=project_id,
        )
        .order_by(
            "-creado_en",
            "-id",
        )
        .first()
    )


def _resolve_project(session):
    """
    Resuelve el Proyecto real asociado a SesionBilling.

    SesionBilling guarda proyecto/proyecto_id como texto,
    mientras que ProyectoAsignacion trabaja contra el PK
    real de facturacion.Proyecto.

    Se intenta resolver usando:
    - Proyecto.id
    - Proyecto.codigo
    - Proyecto.nombre

    y usando tanto session.proyecto como
    session.proyecto_id.
    """
    if session is None:
        return None

    raw_values = []

    for value in (
        getattr(session, "proyecto", None),
        getattr(session, "proyecto_id", None),
    ):
        normalized = _normalize_project_value(value)

        if normalized and normalized not in raw_values:
            raw_values.append(normalized)

    if not raw_values:
        return None

    query = Q()

    for value in raw_values:
        query |= Q(codigo__iexact=value)
        query |= Q(nombre__iexact=value)

        try:
            project_pk = int(value)
        except (TypeError, ValueError):
            project_pk = None

        if project_pk is not None:
            query |= Q(pk=project_pk)

    if not query:
        return None

    return Proyecto.objects.filter(query).order_by("id").first()


def _user_can_access_session(user, session):
    """
    Valida acceso al proyecto usando el sistema central
    de permisos existente.

    Nunca utiliza solamente el Project ID textual de
    SesionBilling para conceder acceso.
    """
    project = _resolve_project(session)

    if project is None:
        return False, None

    allowed = user_has_project_access(
        user,
        project.pk,
    )

    return allowed, project


def _status_label(status):
    status = _normalize_project_value(status)

    if not status:
        return "Unknown"

    return PROJECT_DOWNLOAD_STATUS_LABELS.get(
        status,
        status.replace("_", " ").title(),
    )


def _finance_status_label(status):
    status = _normalize_project_value(status)

    if not status:
        return "Not sent to Finance"

    return PROJECT_DOWNLOAD_FINANCE_STATUS_LABELS.get(
        status,
        status.replace("_", " ").title(),
    )


def _project_is_downloadable(session):
    if session is None:
        return False

    return (
        _normalize_project_value(session.estado) in PROJECT_DOWNLOAD_APPROVED_STATUSES
    )


def _is_cable_session(session):
    """
    La fuente oficial para decidir el flujo es
    SesionBilling.is_cable_installation.

    No se intenta inferir Cable por texto del proyecto,
    cliente o Project ID.
    """
    if session is None:
        return False

    return bool(
        getattr(
            session,
            "is_cable_installation",
            False,
        )
    )


def _session_has_fiber_photos(session):
    """
    Determina si existen fotografías Fiber para la sesión.

    No genera ningún ZIP ni ningún reporte.
    """
    if session is None:
        return False

    return (
        EvidenciaFotoBilling.objects.filter(
            tecnico_sesion__sesion_id=session.id,
        )
        .exclude(
            imagen="",
        )
        .exists()
    )


def _session_has_light_levels(session):
    """
    Light Levels está disponible únicamente si existen
    datos reales de Power o Light Source.

    Esto replica la fuente de datos usada por
    bulk_export_light_levels_xlsx().
    """
    if session is None:
        return False

    return (
        EvidenciaFotoBilling.objects.filter(
            tecnico_sesion__sesion_id=session.id,
        )
        .filter(Q(power_dbm__isnull=False) | Q(light_source_dbm__isnull=False))
        .exists()
    )


def _fiber_photo_report_exists(session):
    """
    Comprueba que el reporte fotográfico final exista
    realmente en storage.

    No intenta regenerarlo.
    """
    if session is None:
        return False

    report = getattr(
        session,
        "reporte_fotografico",
        None,
    )

    if not report:
        return False

    return storage_file_exists(report)


def _session_has_cable_requirements(session):
    """
    Determina si existen datos Cable que puedan alimentar
    los exports de Measurements / Client Report.
    """
    if session is None:
        return False

    return CableRequirement.objects.filter(
        billing_id=session.id,
    ).exists()


def _session_has_cable_photos(session):
    """
    Determina si existen fotografías Cable.

    No genera ZIP ni Cable Photo Report.
    """
    if session is None:
        return False

    return (
        CableEvidence.objects.filter(
            assignment_requirement__assignment__sesion_id=session.id,
        )
        .exclude(
            image="",
        )
        .exists()
    )


def _cable_photo_report_exists(session):
    """
    Comprueba que el Cable Photo Report final exista
    realmente en storage.

    Actualmente Cable reutiliza
    SesionBilling.reporte_fotografico para almacenar
    el reporte final.
    """
    if session is None:
        return False

    report = getattr(
        session,
        "reporte_fotografico",
        None,
    )

    if not report:
        return False

    return storage_file_exists(report)


def _deliverable(
    *,
    key,
    title,
    description,
    url,
    file_type,
):
    return {
        "key": key,
        "title": title,
        "description": description,
        "url": url,
        "file_type": file_type,
    }


def _build_fiber_deliverables(session):
    """
    Entregables reales del flujo Fiber.

    Solamente se agrega un botón cuando los datos/archivo
    necesarios realmente existen.
    """
    deliverables = []

    if _fiber_photo_report_exists(session):
        deliverables.append(
            _deliverable(
                key="fiber_photo_report",
                title="Photo Report",
                description=(
                    "Final photographic report generated " "from the project evidence."
                ),
                url=reverse(
                    "operaciones:descargar_reporte_fotos_proyecto",
                    args=[session.id],
                ),
                file_type="XLSX",
            )
        )

    if _session_has_light_levels(session):
        deliverables.append(
            _deliverable(
                key="fiber_light_levels",
                title="Light Levels",
                description=(
                    "Light levels Excel report generated "
                    "from the recorded fiber measurements."
                ),
                url=(
                    reverse(
                        "operaciones:bulk_export_light_levels_xlsx",
                    )
                    + f"?ids={session.id}"
                ),
                file_type="XLSX",
            )
        )

    if _session_has_fiber_photos(session):
        deliverables.append(
            _deliverable(
                key="fiber_photos_zip",
                title="Project Photos",
                description=(
                    "ZIP file containing the available " "project evidence photos."
                ),
                url=reverse(
                    "operaciones:descargar_fotos_zip",
                    args=[session.id],
                ),
                file_type="ZIP",
            )
        )

    return deliverables


def _build_cable_deliverables(session):
    """
    Entregables reales del flujo Cable.

    Cable Client Report y Cable Measurements se generan
    desde datos existentes de CableRequirement.

    Cable Photo Report solo se muestra si el archivo final
    existe realmente en storage.
    """
    deliverables = []

    has_requirements = _session_has_cable_requirements(
        session,
    )

    has_photos = _session_has_cable_photos(
        session,
    )

    if has_requirements:
        deliverables.append(
            _deliverable(
                key="cable_client_report",
                title="Cable Client Report",
                description=(
                    "Client report generated from the "
                    "Cable Installation project data."
                ),
                url=reverse(
                    "cable_installation:export_client_excel",
                    args=[session.id],
                ),
                file_type="XLSX",
            )
        )

        deliverables.append(
            _deliverable(
                key="cable_measurements",
                title="Cable Measurements",
                description=(
                    "Cable measurements and installation " "values for the project."
                ),
                url=reverse(
                    "cable_installation:export_measurements_excel",
                    args=[session.id],
                ),
                file_type="XLSX",
            )
        )

    if _cable_photo_report_exists(session):
        deliverables.append(
            _deliverable(
                key="cable_photo_report",
                title="Cable Photo Report",
                description=(
                    "Final Cable photographic report "
                    "generated from project evidence."
                ),
                url=reverse(
                    "cable_installation:download_cable_photo_report",
                    args=[session.id],
                ),
                file_type="XLSX",
            )
        )

    if has_photos:
        deliverables.append(
            _deliverable(
                key="cable_photos_zip",
                title="Project Photos",
                description=(
                    "ZIP file containing the available "
                    "Cable project evidence photos."
                ),
                url=reverse(
                    "operaciones:descargar_fotos_zip",
                    args=[session.id],
                ),
                file_type="ZIP",
            )
        )

    return deliverables


def _build_available_deliverables(session):
    """
    Router de entregables por tipo real de sesión.

    No genera archivos durante la búsqueda.
    """
    if session is None:
        return []

    if _is_cable_session(session):
        return _build_cable_deliverables(
            session,
        )

    return _build_fiber_deliverables(
        session,
    )


@login_required
def project_downloads(request):
    """
    Centro de descargas por Project ID.

    Reglas:
    1. Busca SesionBilling sin importar si actualmente
       aparece en List Billing o Invoice List.
    2. El usuario debe tener acceso al Proyecto.
    3. El proyecto debe estar Approved by Supervisor
       o en una etapa operativa superior.
    4. finance_status NO determina si los entregables
       pueden descargarse.
    5. Solamente se muestran entregables realmente
       disponibles.
    6. La búsqueda nunca genera reportes ni ZIPs.
    """
    searched = "project_id" in request.GET

    project_id = _normalize_project_value(
        request.GET.get(
            "project_id",
            "",
        )
    )

    session = None
    project = None

    result_state = None
    result_message = ""

    can_download = False
    project_type = ""
    deliverables = []

    if searched:
        if not project_id:
            result_state = "not_found"
            result_message = "Enter a Project ID to search."

        else:
            session = _find_billing_session(
                project_id,
            )

            if session is None:
                result_state = "not_found"
                result_message = "Project not found."

            else:
                has_access, project = _user_can_access_session(
                    request.user,
                    session,
                )

                if not has_access:
                    result_state = "access_denied"
                    result_message = (
                        "You do not have access to this project. "
                        "Please contact an administrator and request "
                        "access to the project."
                    )

                    # Seguridad:
                    # no exponer absolutamente ningún dato interno
                    # del proyecto si el usuario no tiene acceso.
                    session = None
                    project = None

                elif not _project_is_downloadable(
                    session,
                ):
                    result_state = "not_approved"

                    result_message = (
                        "This project is currently "
                        f"{_status_label(session.estado)}. "
                        "The project must be reviewed and approved "
                        "by the supervisor before its downloads "
                        "are available."
                    )

                else:
                    project_type = "Cable" if _is_cable_session(session) else "Fiber"

                    deliverables = _build_available_deliverables(
                        session,
                    )

                    result_state = "available"
                    can_download = bool(deliverables)

                    if deliverables:
                        result_message = (
                            "Available project downloads " "are shown below."
                        )
                    else:
                        result_message = (
                            "This project is approved, but "
                            "there are currently no available "
                            "downloads."
                        )

    context = {
        "page_title": "Project Downloads",
        "searched": searched,
        "project_id": project_id,
        "session": session,
        "project": project,
        "result_state": result_state,
        "result_message": result_message,
        "can_download": can_download,
        "project_type": project_type,
        "deliverables": deliverables,
        "status_label": (_status_label(session.estado) if session is not None else ""),
        "finance_status_label": (
            _finance_status_label(
                session.finance_status,
            )
            if session is not None
            else ""
        ),
    }

    return render(
        request,
        "operaciones/project_downloads.html",
        context,
    )
