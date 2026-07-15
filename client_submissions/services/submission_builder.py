from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from client_submissions.models import (ClientSubmission, ClientSubmissionBatch,
                                       ClientSubmissionEvent)
from client_submissions.services.billing_mapper import (
    build_billing_submission_snapshot, normalize_job_code,
    validate_required_billing_mapping)
from client_submissions.services.project_parser import parse_project_id_safe
from client_submissions.services.zip_resolver import build_zip_snapshot

# ============================================================
# Excepciones
# ============================================================


class SubmissionBuilderError(Exception):
    """
    Error base del proceso de creación de Client Submissions.
    """


class InvalidBatchConfigurationError(SubmissionBuilderError):
    """
    La configuración general del lote es inválida.
    """


class EmptyBillingSelectionError(SubmissionBuilderError):
    """
    No se recibieron Billings para procesar.
    """


# ============================================================
# Resultados estructurados
# ============================================================


@dataclass
class SubmissionBuildItemResult:
    billing_session_id: int
    project_id: str

    created: bool
    submission_id: int | None

    validation_ok: bool

    dfn_name: str = ""
    access_point_id: str = ""

    zip_available: bool = False
    zip_filename: str = ""
    evidence_count: int = 0

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "billing_session_id": self.billing_session_id,
            "project_id": self.project_id,
            "created": self.created,
            "submission_id": self.submission_id,
            "validation_ok": self.validation_ok,
            "dfn_name": self.dfn_name,
            "access_point_id": self.access_point_id,
            "zip_available": self.zip_available,
            "zip_filename": self.zip_filename,
            "evidence_count": self.evidence_count,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class SubmissionBatchBuildResult:
    batch_id: int
    batch_public_id: str

    total_received: int
    total_created: int
    total_ready: int
    total_with_errors: int

    items: list[SubmissionBuildItemResult]

    def as_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "batch_public_id": self.batch_public_id,
            "total_received": self.total_received,
            "total_created": self.total_created,
            "total_ready": self.total_ready,
            "total_with_errors": self.total_with_errors,
            "items": [item.as_dict() for item in self.items],
        }


# ============================================================
# Helpers
# ============================================================


def clean_text(value) -> str:
    if value is None:
        return ""

    return str(value).strip()


def normalize_email_list(values) -> list[str]:
    """
    Normaliza correos adicionales.

    Acepta:
    - lista;
    - tupla;
    - string separado por coma;
    - string separado por punto y coma.
    """

    if not values:
        return []

    if isinstance(values, str):
        raw_values = values.replace(";", ",").split(",")
    else:
        raw_values = list(values)

    result = []
    seen = set()

    for value in raw_values:
        email = clean_text(value).lower()

        if not email:
            continue

        if email in seen:
            continue

        seen.add(email)
        result.append(email)

    return result


def get_billing_project_id(
    billing_session,
) -> str:
    return clean_text(
        getattr(
            billing_session,
            "proyecto_id",
            "",
        )
    )


def get_billing_client(
    billing_session,
) -> str:
    return clean_text(
        getattr(
            billing_session,
            "cliente",
            "",
        )
    )


def get_billing_city(
    billing_session,
) -> str:
    return clean_text(
        getattr(
            billing_session,
            "ciudad",
            "",
        )
    )


def get_billing_project_name(
    billing_session,
) -> str:
    return clean_text(
        getattr(
            billing_session,
            "proyecto",
            "",
        )
    )


def get_billing_office(
    billing_session,
) -> str:
    return clean_text(
        getattr(
            billing_session,
            "oficina",
            "",
        )
    )


def get_billing_finish_date(
    billing_session,
):
    """
    Obtiene la fecha real de finalización utilizada en Invoices.

    Fuente:

        SesionBilling.finance_finish_date

    Esta fecha es individual por proyecto.
    """

    return getattr(
        billing_session,
        "finance_finish_date",
        None,
    )


def get_billing_finish_date_iso(
    billing_session,
) -> str:
    finish_date = get_billing_finish_date(
        billing_session,
    )

    if not finish_date:
        return ""

    try:
        return finish_date.isoformat()
    except Exception:
        return clean_text(
            finish_date,
        )


def get_billing_item_codes(
    billing_snapshot: dict,
) -> set[str]:
    """
    Obtiene los códigos reales guardados en el snapshot
    de los Items del Billing.

    Ejemplos:

        C-108-UG
        C-108-AER
        C-108.1
        C-109
        C-110.2
    """

    items = billing_snapshot.get(
        "items",
        [],
    )

    if not isinstance(
        items,
        list,
    ):
        return set()

    result = set()

    for item in items:
        if not isinstance(
            item,
            dict,
        ):
            continue

        raw_code = clean_text(
            item.get(
                "codigo_trabajo",
                "",
            )
        )

        if not raw_code:
            continue

        normalized_code = normalize_job_code(
            raw_code,
        )

        if normalized_code:
            result.add(
                normalized_code,
            )

    return result


def get_billing_code_flags(
    billing_snapshot: dict,
) -> dict:
    """
    Resuelve reglas automáticas a partir de los códigos reales
    aprobados en Invoice.

    Reglas:

        C-108-AER
            -> Aerial Case = YES

        C-108.1
            -> Re-Entry = YES

    Si los códigos no existen:

        Aerial Case = NO
        Re-Entry = NO
    """

    codes = get_billing_item_codes(
        billing_snapshot,
    )

    return {
        "codes": codes,
        "aerial_case": ("C-108-AER" in codes),
        "re_entry": ("C-108.1" in codes),
    }


def resolve_common_production_completed_date(
    billing_sessions: Iterable,
):
    """
    Determina si todos los proyectos seleccionados comparten
    exactamente la misma finance_finish_date.

    Ejemplos:

        Project A -> 2026-07-10
        Project B -> 2026-07-10
        Project C -> 2026-07-10

            Resultado:
                2026-07-10

        Project A -> 2026-07-10
        Project B -> 2026-07-11

            Resultado:
                None

    También retorna None si al menos un Billing no tiene fecha.
    """

    billing_sessions = list(
        billing_sessions,
    )

    if not billing_sessions:
        return None

    finish_dates = [
        get_billing_finish_date(
            billing_session,
        )
        for billing_session in billing_sessions
    ]

    if any(finish_date is None for finish_date in finish_dates):
        return None

    unique_dates = set(
        finish_dates,
    )

    if (
        len(
            unique_dates,
        )
        != 1
    ):
        return None

    return finish_dates[0]


def resolve_submission_work_configuration(
    *,
    batch: ClientSubmissionBatch,
    billing_session=None,
    billing_snapshot: dict | None = None,
    submission_config: dict | None = None,
    existing_payload: dict | None = None,
) -> dict:
    """
    Resuelve la configuración final de trabajo para un proyecto.

    Prioridad para Work Types:

    1. submission_config
    2. existing_payload
    3. Batch

    Aerial Case y Re-Entry NO utilizan configuración común.

    Se resuelven automáticamente desde los códigos reales
    aprobados en el Invoice:

        C-108-AER -> Aerial Case YES
        C-108.1   -> Re-Entry YES

    Los valores:

        Aerial Sequential IN
        Aerial Sequential OUT

    siguen siendo individuales por proyecto y se preservan
    durante Revalidate.
    """

    submission_config = (
        submission_config
        if isinstance(
            submission_config,
            dict,
        )
        else {}
    )

    existing_payload = (
        existing_payload
        if isinstance(
            existing_payload,
            dict,
        )
        else {}
    )

    billing_snapshot = (
        billing_snapshot
        if isinstance(
            billing_snapshot,
            dict,
        )
        else {}
    )

    configuration_keys = (
        "fiber_placed",
        "splicing",
        "testing",
        "aerial_case_value_1",
        "aerial_case_value_2",
        "configuration_mode",
    )

    has_submission_config = any(key in submission_config for key in configuration_keys)

    has_existing_config = any(key in existing_payload for key in configuration_keys)

    # ========================================================
    # Fuente de configuración
    # ========================================================

    if has_submission_config:
        configuration_source = "submission_config"

    elif has_existing_config:
        configuration_source = existing_payload.get(
            "work_configuration_source",
            "existing_payload",
        )

    else:
        configuration_source = "batch"

    # ========================================================
    # Helpers
    # ========================================================

    def resolve_boolean(
        key: str,
        batch_value: bool,
    ) -> bool:
        if key in submission_config:
            return bool(
                submission_config.get(
                    key,
                )
            )

        if key in existing_payload:
            return bool(
                existing_payload.get(
                    key,
                )
            )

        return bool(
            batch_value,
        )

    def resolve_text(
        key: str,
        default: str = "",
    ) -> str:
        if key in submission_config:
            return clean_text(
                submission_config.get(
                    key,
                )
            )

        if key in existing_payload:
            return clean_text(
                existing_payload.get(
                    key,
                )
            )

        return clean_text(
            default,
        )

    # ========================================================
    # Configuración automática por códigos del Invoice
    # ========================================================

    code_flags = get_billing_code_flags(
        billing_snapshot,
    )

    aerial_case = bool(
        code_flags.get(
            "aerial_case",
            False,
        )
    )

    re_entry = bool(
        code_flags.get(
            "re_entry",
            False,
        )
    )

    # ========================================================
    # Aerial Sequential individual
    # ========================================================

    aerial_case_value_1 = resolve_text(
        "aerial_case_value_1",
    )

    aerial_case_value_2 = resolve_text(
        "aerial_case_value_2",
    )

    if not aerial_case:
        aerial_case_value_1 = ""
        aerial_case_value_2 = ""

    # ========================================================
    # Modo de configuración
    # ========================================================

    configuration_mode = resolve_text(
        "configuration_mode",
        "common",
    ).lower()

    if configuration_mode not in {
        "common",
        "individual",
    }:
        configuration_mode = "common"

    return {
        "configuration_mode": configuration_mode,
        "fiber_placed": resolve_boolean(
            "fiber_placed",
            batch.fiber_placed,
        ),
        "splicing": resolve_boolean(
            "splicing",
            batch.splicing,
        ),
        "testing": resolve_boolean(
            "testing",
            batch.testing,
        ),
        "aerial_case": aerial_case,
        "re_entry": re_entry,
        "aerial_case_value_1": aerial_case_value_1,
        "aerial_case_value_2": aerial_case_value_2,
        "source": configuration_source,
        "billing_session_id": (
            getattr(
                billing_session,
                "pk",
                None,
            )
            if billing_session is not None
            else None
        ),
        "billing_code_flags": {
            "aerial_case_from_code": aerial_case,
            "re_entry_from_code": re_entry,
        },
    }


# ============================================================
# Validación de configuración general
# ============================================================


def validate_batch_configuration(
    *,
    submitted_by_email: str,
    copy_email: str,
    subcontractor_name: str,
    production_completed_date,
    market: str,
    fiber_placed: bool,
    splicing: bool,
    testing: bool,
) -> list[str]:
    """
    Valida únicamente valores administrativos comunes.

    Production Completed Date ya NO es obligatoria a nivel Batch.

    Cada ClientSubmission utilizará:

        SesionBilling.finance_finish_date

    como fecha individual real.
    """

    errors = []

    if not clean_text(
        submitted_by_email,
    ):
        errors.append("Submitted by email is required.")

    if not clean_text(
        copy_email,
    ):
        errors.append("Copy email is required.")

    if not clean_text(
        subcontractor_name,
    ):
        errors.append("Sub Contractor Name is required.")

    if not clean_text(
        market,
    ):
        errors.append("Market is required.")

    if not any(
        [
            fiber_placed,
            splicing,
            testing,
        ]
    ):
        errors.append(
            (
                "At least one work type must be selected: "
                "Fiber Placed, Splicing, or Testing."
            )
        )

    return errors


# ============================================================
# Form payload
# ============================================================


def build_form_payload(
    *,
    batch: ClientSubmissionBatch,
    billing_session,
    parsed_project: dict,
    billing_snapshot: dict,
    zip_snapshot: dict,
    work_configuration: dict,
) -> dict:
    """
    Construye el snapshot completo de datos que el worker
    utilizará para llenar el formulario de Smartsheet.

    Production Completed Date se obtiene directamente del
    finance_finish_date del Billing individual.

    Aerial Case y Re-Entry llegan resueltos por códigos
    reales del Invoice.
    """

    quantities = dict(
        billing_snapshot.get(
            "fields",
            {},
        )
        or {}
    )

    # ========================================================
    # Resolver configuración de trabajo
    # ========================================================

    configuration_mode = clean_text(
        work_configuration.get(
            "configuration_mode",
            "common",
        )
    ).lower()

    if configuration_mode not in {
        "common",
        "individual",
    }:
        configuration_mode = "common"

    fiber_placed = bool(
        work_configuration.get(
            "fiber_placed",
            False,
        )
    )

    splicing = bool(
        work_configuration.get(
            "splicing",
            False,
        )
    )

    testing = bool(
        work_configuration.get(
            "testing",
            False,
        )
    )

    aerial_case = bool(
        work_configuration.get(
            "aerial_case",
            False,
        )
    )

    re_entry = bool(
        work_configuration.get(
            "re_entry",
            False,
        )
    )

    aerial_case_value_1 = clean_text(
        work_configuration.get(
            "aerial_case_value_1",
            "",
        )
    )

    aerial_case_value_2 = clean_text(
        work_configuration.get(
            "aerial_case_value_2",
            "",
        )
    )

    if not aerial_case:
        aerial_case_value_1 = ""
        aerial_case_value_2 = ""

    work_configuration_source = clean_text(
        work_configuration.get(
            "source",
            "batch",
        )
    )

    production_completed_date = get_billing_finish_date_iso(
        billing_session,
    )

    # ========================================================
    # Form payload
    # ========================================================

    return {
        # ----------------------------------------------------
        # Fuente
        # ----------------------------------------------------
        "billing_session_id": billing_session.pk,
        # ----------------------------------------------------
        # Identificación
        # ----------------------------------------------------
        "project_id": get_billing_project_id(
            billing_session,
        ),
        "dfn_name": parsed_project.get(
            "dfn_name",
            "",
        ),
        "access_point_id": parsed_project.get(
            "access_point_id",
            "",
        ),
        # ----------------------------------------------------
        # Información interna del Billing
        # ----------------------------------------------------
        "billing": {
            "client": get_billing_client(
                billing_session,
            ),
            "city": get_billing_city(
                billing_session,
            ),
            "project": get_billing_project_name(
                billing_session,
            ),
            "office": get_billing_office(
                billing_session,
            ),
            "finance_finish_date": (production_completed_date),
        },
        # ----------------------------------------------------
        # Datos comunes del formulario
        # ----------------------------------------------------
        "submitted_by_email": clean_text(
            batch.submitted_by_email,
        ),
        "is_subcontractor": bool(
            batch.is_subcontractor,
        ),
        "subcontractor_name": clean_text(
            batch.subcontractor_name,
        ),
        # ----------------------------------------------------
        # Fecha individual del proyecto
        # ----------------------------------------------------
        "production_completed_date": (production_completed_date),
        "market": clean_text(
            batch.market,
        ),
        # ----------------------------------------------------
        # Configuración individual de Work Completed
        # ----------------------------------------------------
        "configuration_mode": configuration_mode,
        "fiber_placed": fiber_placed,
        "splicing": splicing,
        "testing": testing,
        "aerial_case": aerial_case,
        "re_entry": re_entry,
        "aerial_case_value_1": aerial_case_value_1,
        "aerial_case_value_2": aerial_case_value_2,
        "work_configuration_source": (work_configuration_source),
        "billing_code_flags": dict(
            work_configuration.get(
                "billing_code_flags",
                {},
            )
            or {}
        ),
        # ----------------------------------------------------
        # Email copy
        # ----------------------------------------------------
        "send_copy_of_responses": bool(
            batch.send_copy_of_responses,
        ),
        "copy_email": clean_text(
            batch.copy_email,
        ),
        "additional_copy_emails": list(batch.additional_copy_emails or []),
        # ----------------------------------------------------
        # Quantities
        # ----------------------------------------------------
        "quantities": quantities,
        # ----------------------------------------------------
        # ZIP
        # ----------------------------------------------------
        "zip": {
            "available": bool(
                zip_snapshot.get(
                    "available",
                    False,
                )
            ),
            "filename": clean_text(
                zip_snapshot.get(
                    "zip_filename",
                    "",
                )
            ),
            "evidence_count": int(
                zip_snapshot.get(
                    "evidence_count",
                    0,
                )
                or 0
            ),
        },
    }


# ============================================================
# Validación individual
# ============================================================


def build_submission_validation(
    *,
    billing_session,
    parsed_project: dict,
    billing_validation: dict,
    zip_snapshot: dict,
    work_configuration: dict,
) -> tuple[
    bool,
    list[str],
    list[str],
]:
    errors = []
    warnings = []

    # --------------------------------------------------------
    # Project ID
    # --------------------------------------------------------

    if not parsed_project.get(
        "ok",
        False,
    ):
        errors.append(
            parsed_project.get(
                "error",
                "Invalid Project ID.",
            )
        )

    # --------------------------------------------------------
    # Production Completed Date
    # --------------------------------------------------------

    if not get_billing_finish_date(
        billing_session,
    ):
        errors.append(
            (
                "Production Completed Date is missing. "
                "The Invoice does not have a Finish date."
            )
        )

    # --------------------------------------------------------
    # Billing mapping
    # --------------------------------------------------------

    errors.extend(
        billing_validation.get(
            "errors",
            [],
        )
        or []
    )

    warnings.extend(
        billing_validation.get(
            "warnings",
            [],
        )
        or []
    )

    # --------------------------------------------------------
    # Aerial Case
    # --------------------------------------------------------

    if work_configuration.get(
        "aerial_case",
        False,
    ):
        aerial_case_value_1 = clean_text(
            work_configuration.get(
                "aerial_case_value_1",
                "",
            )
        )

        aerial_case_value_2 = clean_text(
            work_configuration.get(
                "aerial_case_value_2",
                "",
            )
        )

        if not aerial_case_value_1:
            errors.append(
                (
                    "Aerial Sequential IN is required because "
                    "the Invoice contains C-108-AER."
                )
            )

        if not aerial_case_value_2:
            errors.append(
                (
                    "Aerial Sequential OUT is required because "
                    "the Invoice contains C-108-AER."
                )
            )

    # --------------------------------------------------------
    # ZIP
    # --------------------------------------------------------

    if not zip_snapshot.get(
        "available",
        False,
    ):
        errors.append(
            zip_snapshot.get(
                "error",
                ("No photos are available to generate " "the ZIP for this project."),
            )
        )

    evidence_count = int(
        zip_snapshot.get(
            "evidence_count",
            0,
        )
        or 0
    )

    if evidence_count <= 0:
        errors.append(
            ("The project does not contain any " "available evidence photos.")
        )

    warnings.extend(
        zip_snapshot.get(
            "warnings",
            [],
        )
        or []
    )

    # --------------------------------------------------------
    # Eliminar duplicados
    # --------------------------------------------------------

    errors = list(
        dict.fromkeys(
            clean_text(
                value,
            )
            for value in errors
            if clean_text(
                value,
            )
        )
    )

    warnings = list(
        dict.fromkeys(
            clean_text(
                value,
            )
            for value in warnings
            if clean_text(
                value,
            )
        )
    )

    return (
        not errors,
        errors,
        warnings,
    )


# ============================================================
# Eventos
# ============================================================


def create_event(
    *,
    batch: ClientSubmissionBatch,
    submission: ClientSubmission | None = None,
    event_type: str,
    message: str,
    level: str = ClientSubmissionEvent.Level.INFO,
    metadata: dict | None = None,
) -> ClientSubmissionEvent:
    return ClientSubmissionEvent.objects.create(
        batch=batch,
        submission=submission,
        level=level,
        event_type=event_type,
        message=message,
        metadata=metadata or {},
    )


# ============================================================
# Creación individual
# ============================================================


def build_single_submission(
    *,
    batch: ClientSubmissionBatch,
    billing_session,
    sequence_number: int,
    submission_config: dict | None = None,
) -> SubmissionBuildItemResult:
    project_id = get_billing_project_id(
        billing_session,
    )

    errors = []
    warnings = []

    # --------------------------------------------------------
    # 1. Parsear Project ID
    # --------------------------------------------------------

    parsed_project = parse_project_id_safe(
        project_id,
    )

    # --------------------------------------------------------
    # 2. Leer Billing codes
    # --------------------------------------------------------

    billing_snapshot = build_billing_submission_snapshot(
        billing_session,
    )

    # --------------------------------------------------------
    # 3. Resolver configuración individual/común
    #
    # Aerial Case y Re-Entry se calculan usando códigos.
    # --------------------------------------------------------

    work_configuration = resolve_submission_work_configuration(
        batch=batch,
        billing_session=billing_session,
        billing_snapshot=billing_snapshot,
        submission_config=submission_config,
    )

    # --------------------------------------------------------
    # 4. Validar Billing mapping
    # --------------------------------------------------------

    billing_validation = validate_required_billing_mapping(
        billing_session,
        splicing=work_configuration["splicing"],
        testing=work_configuration["testing"],
        fiber_placed=work_configuration["fiber_placed"],
    )

    # --------------------------------------------------------
    # 5. Inspeccionar ZIP
    # --------------------------------------------------------

    zip_snapshot = build_zip_snapshot(
        billing_session,
    )

    # --------------------------------------------------------
    # 6. Validación completa
    # --------------------------------------------------------

    (
        validation_ok,
        validation_errors,
        validation_warnings,
    ) = build_submission_validation(
        billing_session=billing_session,
        parsed_project=parsed_project,
        billing_validation=billing_validation,
        zip_snapshot=zip_snapshot,
        work_configuration=work_configuration,
    )

    errors.extend(
        validation_errors,
    )

    warnings.extend(
        validation_warnings,
    )

    # --------------------------------------------------------
    # 7. Form payload
    # --------------------------------------------------------

    form_payload = build_form_payload(
        batch=batch,
        billing_session=billing_session,
        parsed_project=parsed_project,
        billing_snapshot=billing_snapshot,
        zip_snapshot=zip_snapshot,
        work_configuration=work_configuration,
    )

    # --------------------------------------------------------
    # 8. Crear ClientSubmission
    # --------------------------------------------------------

    submission = ClientSubmission.objects.create(
        batch=batch,
        billing_session=billing_session,
        project_id=project_id,
        dfn_name=parsed_project.get(
            "dfn_name",
            "",
        ),
        access_point_id=parsed_project.get(
            "access_point_id",
            "",
        ),
        status=(ClientSubmission.Status.PENDING_CLIENT_SUBMISSION),
        sequence_number=sequence_number,
        form_payload=form_payload,
        billing_codes_snapshot=(
            billing_snapshot.get(
                "items",
                [],
            )
            or []
        ),
        zip_filename=zip_snapshot.get(
            "zip_filename",
            "",
        ),
        zip_size=None,
        zip_available=bool(
            zip_snapshot.get(
                "available",
                False,
            )
        ),
        validation_ok=validation_ok,
        validation_errors=errors,
        validation_warnings=warnings,
        validated_at=timezone.now(),
    )

    # --------------------------------------------------------
    # 9. Eventos
    # --------------------------------------------------------

    create_event(
        batch=batch,
        submission=submission,
        event_type="submission_created",
        message=(
            f"Client submission created for Project ID "
            f"{project_id or billing_session.pk}."
        ),
        metadata={
            "billing_session_id": billing_session.pk,
            "sequence_number": sequence_number,
            "production_completed_date": (
                form_payload.get(
                    "production_completed_date",
                    "",
                )
            ),
            "work_configuration_source": (
                work_configuration.get(
                    "source",
                    "batch",
                )
            ),
            "work_configuration": {
                "fiber_placed": (work_configuration["fiber_placed"]),
                "splicing": (work_configuration["splicing"]),
                "testing": (work_configuration["testing"]),
                "aerial_case": (work_configuration["aerial_case"]),
                "re_entry": (work_configuration["re_entry"]),
            },
        },
    )

    if validation_ok:
        create_event(
            batch=batch,
            submission=submission,
            event_type="submission_validation_completed",
            message=(f"Project {project_id} " "is ready for client submission."),
            level=ClientSubmissionEvent.Level.SUCCESS,
            metadata={
                "zip_filename": zip_snapshot.get(
                    "zip_filename",
                    "",
                ),
                "evidence_count": zip_snapshot.get(
                    "evidence_count",
                    0,
                ),
            },
        )

    else:
        create_event(
            batch=batch,
            submission=submission,
            event_type="submission_validation_failed",
            message=(
                f"Project {project_id or billing_session.pk} " "has validation errors."
            ),
            level=ClientSubmissionEvent.Level.ERROR,
            metadata={
                "errors": errors,
                "warnings": warnings,
            },
        )

    return SubmissionBuildItemResult(
        billing_session_id=billing_session.pk,
        project_id=project_id,
        created=True,
        submission_id=submission.pk,
        validation_ok=validation_ok,
        dfn_name=submission.dfn_name,
        access_point_id=submission.access_point_id,
        zip_available=submission.zip_available,
        zip_filename=submission.zip_filename,
        evidence_count=int(
            zip_snapshot.get(
                "evidence_count",
                0,
            )
            or 0
        ),
        errors=errors,
        warnings=warnings,
    )


# ============================================================
# Creación del Batch
# ============================================================


@transaction.atomic
def create_submission_batch(
    *,
    created_by,
    billing_sessions: Iterable,
    name: str = "",
    form_url: str | None = None,
    execution_mode: str = (ClientSubmissionBatch.ExecutionMode.DRY_RUN),
    submitted_by_email: str = ("l.suarez@hyperlink-networks.com"),
    send_copy_of_responses: bool = True,
    copy_email: str = ("l.suarez@hyperlink-networks.com"),
    additional_copy_emails=None,
    is_subcontractor: bool = True,
    subcontractor_name: str = "Hyperlink",
    production_completed_date=None,
    market: str = "",
    fiber_placed: bool = False,
    splicing: bool = False,
    testing: bool = False,
    aerial_case: bool = False,
    re_entry: bool = False,
    submission_configs: dict | None = None,
    notes: str = "",
) -> SubmissionBatchBuildResult:
    """
    Crea un Batch completo y todos sus ClientSubmission.

    La fecha real de Production Completed Date es individual
    por Billing:

        SesionBilling.finance_finish_date

    Si todos los proyectos tienen la misma fecha, esa fecha
    también se almacena en ClientSubmissionBatch como valor
    representativo común.

    Si existen fechas distintas, el Batch queda con:

        production_completed_date = None

    Cada ClientSubmission conserva siempre su fecha individual.

    Aerial Case y Re-Entry se resuelven automáticamente:

        C-108-AER -> Aerial Case YES
        C-108.1   -> Re-Entry YES

    No ejecuta Playwright.
    """

    billing_sessions = list(
        billing_sessions,
    )

    submission_configs = (
        submission_configs
        if isinstance(
            submission_configs,
            dict,
        )
        else {}
    )

    if not billing_sessions:
        raise EmptyBillingSelectionError("At least one Billing must be selected.")

    # --------------------------------------------------------
    # Resolver fecha común real
    # --------------------------------------------------------

    common_production_completed_date = resolve_common_production_completed_date(
        billing_sessions,
    )

    # --------------------------------------------------------
    # Validar configuración general
    # --------------------------------------------------------

    config_errors = validate_batch_configuration(
        submitted_by_email=submitted_by_email,
        copy_email=copy_email,
        subcontractor_name=subcontractor_name,
        production_completed_date=(common_production_completed_date),
        market=market,
        fiber_placed=(
            fiber_placed
            or bool(
                submission_configs,
            )
        ),
        splicing=splicing,
        testing=testing,
    )

    if config_errors:
        raise InvalidBatchConfigurationError(
            " | ".join(
                config_errors,
            )
        )

    normalized_additional_emails = normalize_email_list(
        additional_copy_emails,
    )

    # --------------------------------------------------------
    # Crear Batch
    # --------------------------------------------------------

    batch_kwargs = {
        "created_by": created_by,
        "name": clean_text(
            name,
        ),
        "execution_mode": execution_mode,
        "status": (ClientSubmissionBatch.Status.DRAFT),
        "submitted_by_email": clean_text(
            submitted_by_email,
        ),
        "send_copy_of_responses": bool(
            send_copy_of_responses,
        ),
        "copy_email": clean_text(
            copy_email,
        ),
        "additional_copy_emails": (normalized_additional_emails),
        "is_subcontractor": bool(
            is_subcontractor,
        ),
        "subcontractor_name": clean_text(
            subcontractor_name,
        ),
        "production_completed_date": (common_production_completed_date),
        "market": clean_text(
            market,
        ),
        "fiber_placed": bool(
            fiber_placed,
        ),
        "splicing": bool(
            splicing,
        ),
        "testing": bool(
            testing,
        ),
        # Estos campos permanecen por compatibilidad del modelo.
        # Los valores reales son resueltos por proyecto.
        "aerial_case": bool(
            aerial_case,
        ),
        "re_entry": bool(
            re_entry,
        ),
        "notes": clean_text(
            notes,
        ),
    }

    if form_url:
        batch_kwargs["form_url"] = clean_text(
            form_url,
        )

    batch = ClientSubmissionBatch.objects.create(**batch_kwargs)

    create_event(
        batch=batch,
        event_type="batch_created",
        message=(
            f"Client submission batch #{batch.pk} created "
            f"with {len(billing_sessions)} selected project(s)."
        ),
        metadata={
            "execution_mode": execution_mode,
            "selected_count": len(
                billing_sessions,
            ),
            "individual_configuration_count": len(
                submission_configs,
            ),
            "common_production_completed_date": (
                common_production_completed_date.isoformat()
                if common_production_completed_date
                else ""
            ),
            "uses_individual_production_dates": (
                common_production_completed_date is None
            ),
        },
    )

    # --------------------------------------------------------
    # Crear cada Submission
    # --------------------------------------------------------

    results = []

    for sequence_number, billing_session in enumerate(
        billing_sessions,
        start=1,
    ):
        try:
            submission_config = submission_configs.get(
                billing_session.pk,
            )

            if submission_config is None:
                submission_config = submission_configs.get(
                    str(
                        billing_session.pk,
                    )
                )

            result = build_single_submission(
                batch=batch,
                billing_session=billing_session,
                sequence_number=sequence_number,
                submission_config=submission_config,
            )

        except Exception as exc:
            project_id = get_billing_project_id(
                billing_session,
            )

            result = SubmissionBuildItemResult(
                billing_session_id=billing_session.pk,
                project_id=project_id,
                created=False,
                submission_id=None,
                validation_ok=False,
                errors=[
                    str(
                        exc,
                    )
                ],
            )

            create_event(
                batch=batch,
                event_type="submission_creation_failed",
                message=(
                    "Could not create client submission "
                    "for Project ID "
                    f"{project_id or billing_session.pk}."
                ),
                level=ClientSubmissionEvent.Level.ERROR,
                metadata={
                    "billing_session_id": billing_session.pk,
                    "error": str(
                        exc,
                    ),
                },
            )

        results.append(
            result,
        )

    # --------------------------------------------------------
    # Totales
    # --------------------------------------------------------

    total_created = sum(1 for item in results if item.created)

    total_ready = sum(1 for item in results if (item.created and item.validation_ok))

    total_with_errors = sum(1 for item in results if not item.validation_ok)

    # --------------------------------------------------------
    # El Batch queda en Draft para revisión
    # --------------------------------------------------------

    batch.status = ClientSubmissionBatch.Status.DRAFT

    batch.last_activity_at = timezone.now()

    batch.save(
        update_fields=[
            "status",
            "last_activity_at",
            "updated_at",
        ]
    )

    create_event(
        batch=batch,
        event_type="batch_build_completed",
        message=(
            "Batch preparation completed. "
            f"{total_ready} project(s) ready and "
            f"{total_with_errors} project(s) with errors."
        ),
        level=(
            ClientSubmissionEvent.Level.SUCCESS
            if total_with_errors == 0
            else ClientSubmissionEvent.Level.WARNING
        ),
        metadata={
            "total_received": len(
                billing_sessions,
            ),
            "total_created": total_created,
            "total_ready": total_ready,
            "total_with_errors": total_with_errors,
        },
    )

    return SubmissionBatchBuildResult(
        batch_id=batch.pk,
        batch_public_id=str(
            batch.public_id,
        ),
        total_received=len(
            billing_sessions,
        ),
        total_created=total_created,
        total_ready=total_ready,
        total_with_errors=total_with_errors,
        items=results,
    )


# ============================================================
# Revalidación
# ============================================================


@transaction.atomic
def revalidate_submission(
    submission: ClientSubmission,
) -> SubmissionBuildItemResult:
    """
    Recalcula:

    - Project ID;
    - Billing codes;
    - cantidades;
    - finance_finish_date;
    - Aerial Case desde C-108-AER;
    - Re-Entry desde C-108.1;
    - ZIP availability;
    - form_payload.

    Mantiene:

    - Work Types configurados;
    - Aerial Sequential IN;
    - Aerial Sequential OUT.

    De esta forma Revalidate vuelve a leer el Invoice real.
    """

    batch = submission.batch

    billing_session = submission.billing_session

    existing_payload = (
        submission.form_payload
        if isinstance(
            submission.form_payload,
            dict,
        )
        else {}
    )

    project_id = get_billing_project_id(
        billing_session,
    )

    parsed_project = parse_project_id_safe(
        project_id,
    )

    # --------------------------------------------------------
    # Leer nuevamente Billing real
    # --------------------------------------------------------

    billing_snapshot = build_billing_submission_snapshot(
        billing_session,
    )

    # --------------------------------------------------------
    # Mantener configuración existente y recalcular flags
    # automáticos por código.
    # --------------------------------------------------------

    work_configuration = resolve_submission_work_configuration(
        batch=batch,
        billing_session=billing_session,
        billing_snapshot=billing_snapshot,
        existing_payload=existing_payload,
    )

    billing_validation = validate_required_billing_mapping(
        billing_session,
        splicing=work_configuration["splicing"],
        testing=work_configuration["testing"],
        fiber_placed=work_configuration["fiber_placed"],
    )

    zip_snapshot = build_zip_snapshot(
        billing_session,
    )

    (
        validation_ok,
        errors,
        warnings,
    ) = build_submission_validation(
        billing_session=billing_session,
        parsed_project=parsed_project,
        billing_validation=billing_validation,
        zip_snapshot=zip_snapshot,
        work_configuration=work_configuration,
    )

    form_payload = build_form_payload(
        batch=batch,
        billing_session=billing_session,
        parsed_project=parsed_project,
        billing_snapshot=billing_snapshot,
        zip_snapshot=zip_snapshot,
        work_configuration=work_configuration,
    )

    submission.project_id = project_id

    submission.dfn_name = parsed_project.get(
        "dfn_name",
        "",
    )

    submission.access_point_id = parsed_project.get(
        "access_point_id",
        "",
    )

    submission.form_payload = form_payload

    submission.billing_codes_snapshot = (
        billing_snapshot.get(
            "items",
            [],
        )
        or []
    )

    submission.zip_filename = zip_snapshot.get(
        "zip_filename",
        "",
    )

    submission.zip_available = bool(
        zip_snapshot.get(
            "available",
            False,
        )
    )

    submission.validation_ok = validation_ok

    submission.validation_errors = errors

    submission.validation_warnings = warnings

    submission.validated_at = timezone.now()

    submission.save(
        update_fields=[
            "project_id",
            "dfn_name",
            "access_point_id",
            "form_payload",
            "billing_codes_snapshot",
            "zip_filename",
            "zip_available",
            "validation_ok",
            "validation_errors",
            "validation_warnings",
            "validated_at",
            "updated_at",
        ]
    )

    create_event(
        batch=batch,
        submission=submission,
        event_type="submission_revalidated",
        message=(f"Project {project_id} was revalidated."),
        level=(
            ClientSubmissionEvent.Level.SUCCESS
            if validation_ok
            else ClientSubmissionEvent.Level.WARNING
        ),
        metadata={
            "validation_ok": validation_ok,
            "errors": errors,
            "warnings": warnings,
            "production_completed_date": (
                form_payload.get(
                    "production_completed_date",
                    "",
                )
            ),
            "work_configuration_source": (
                work_configuration.get(
                    "source",
                    "batch",
                )
            ),
            "work_configuration": {
                "fiber_placed": (work_configuration["fiber_placed"]),
                "splicing": (work_configuration["splicing"]),
                "testing": (work_configuration["testing"]),
                "aerial_case": (work_configuration["aerial_case"]),
                "re_entry": (work_configuration["re_entry"]),
            },
        },
    )

    return SubmissionBuildItemResult(
        billing_session_id=billing_session.pk,
        project_id=project_id,
        created=True,
        submission_id=submission.pk,
        validation_ok=validation_ok,
        dfn_name=submission.dfn_name,
        access_point_id=submission.access_point_id,
        zip_available=submission.zip_available,
        zip_filename=submission.zip_filename,
        evidence_count=int(
            zip_snapshot.get(
                "evidence_count",
                0,
            )
            or 0
        ),
        errors=errors,
        warnings=warnings,
    )


# ============================================================
# Revalidación completa del Batch
# ============================================================


def revalidate_batch(
    batch: ClientSubmissionBatch,
) -> list[SubmissionBuildItemResult]:
    results = []

    submissions = batch.submissions.select_related(
        "billing_session",
    ).order_by(
        "sequence_number",
        "id",
    )

    for submission in submissions:
        results.append(
            revalidate_submission(
                submission,
            )
        )

    # ========================================================
    # Recalcular fecha común del Batch
    #
    # Si después de Revalidate todas las fechas son iguales,
    # se almacena la fecha.
    #
    # Si son distintas, Batch queda en NULL.
    # ========================================================

    billing_sessions = [submission.billing_session for submission in submissions]

    common_production_completed_date = resolve_common_production_completed_date(
        billing_sessions,
    )

    if batch.production_completed_date != common_production_completed_date:
        batch.production_completed_date = common_production_completed_date

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "production_completed_date",
                "last_activity_at",
                "updated_at",
            ]
        )

    return results
