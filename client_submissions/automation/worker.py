# client_submissions/automation/worker.py

from __future__ import annotations

import asyncio
import logging
import os
import socket
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from client_submissions.automation.smartsheet_form import (
    SmartsheetVerificationRequired, run_smartsheet_dry_run)
from client_submissions.models import (ClientSubmission,
                                       ClientSubmissionAttempt,
                                       ClientSubmissionBatch,
                                       ClientSubmissionEvent)
from operaciones.views_fotos_zip import generar_fotos_zip_sesion

logger = logging.getLogger(__name__)


# ============================================================
# Worker identifier
# ============================================================


def get_worker_identifier() -> str:
    return (
        os.getenv("RENDER_INSTANCE_ID")
        or os.getenv("HOSTNAME")
        or socket.gethostname()
        or "client-submission-worker"
    )


# ============================================================
# Eventos
# ============================================================


def create_event(
    *,
    batch,
    submission=None,
    level=ClientSubmissionEvent.Level.INFO,
    event_type: str,
    message: str,
    metadata=None,
):
    return ClientSubmissionEvent.objects.create(
        batch=batch,
        submission=submission,
        level=level,
        event_type=event_type,
        message=message,
        metadata=metadata or {},
    )


# ============================================================
# Selección segura del próximo Batch
# ============================================================


@transaction.atomic
def claim_next_pending_batch():
    """
    Reclama un único Batch pendiente.

    select_for_update(skip_locked=True) evita que dos workers
    tomen el mismo Batch simultáneamente.
    """

    batch = (
        ClientSubmissionBatch.objects.select_for_update(skip_locked=True)
        .filter(
            status=ClientSubmissionBatch.Status.PENDING,
        )
        .order_by(
            "created_at",
            "id",
        )
        .first()
    )

    if not batch:
        return None

    worker_identifier = get_worker_identifier()

    batch.worker_identifier = worker_identifier

    batch.mark_running(save=False)

    batch.save(
        update_fields=[
            "worker_identifier",
            "status",
            "started_at",
            "paused_at",
            "last_activity_at",
            "updated_at",
        ]
    )

    create_event(
        batch=batch,
        event_type="worker_claimed_batch",
        message=(f"Worker {worker_identifier} " f"claimed Batch #{batch.pk}."),
        metadata={
            "worker_identifier": worker_identifier,
        },
    )

    return batch.pk


# ============================================================
# Próximo Submission
# ============================================================


def get_next_submission(
    batch: ClientSubmissionBatch,
):
    return (
        batch.submissions.filter(
            status=(ClientSubmission.Status.PENDING_CLIENT_SUBMISSION),
            validation_ok=True,
        )
        .order_by(
            "sequence_number",
            "id",
        )
        .first()
    )


# ============================================================
# Crear intento
# ============================================================


@transaction.atomic
def create_attempt(
    submission: ClientSubmission,
):
    submission = (
        ClientSubmission.objects.select_for_update()
        .select_related(
            "batch",
        )
        .get(pk=submission.pk)
    )

    next_attempt_number = submission.attempt_count + 1

    submission.attempt_count = next_attempt_number

    submission.mark_preparing(save=False)

    submission.save(
        update_fields=[
            "attempt_count",
            "status",
            "started_at",
            "last_error_code",
            "last_error_message",
            "last_error_at",
            "updated_at",
        ]
    )

    attempt = ClientSubmissionAttempt.objects.create(
        submission=submission,
        attempt_number=next_attempt_number,
        result=ClientSubmissionAttempt.Result.STARTED,
        form_url=submission.batch.form_url,
        form_payload_snapshot=submission.form_payload or {},
    )

    return attempt


# ============================================================
# Obtener ZIP local
# ============================================================


SAFE_REPLACEMENT = "–"


def _safe_component_preserve(
    value: str,
    fallback="(sin-titulo)",
    max_len=120,
) -> str:
    if not value:
        value = fallback

    value = "".join(ch for ch in str(value) if ch >= " " and ch != "\x7f")

    value = value.replace("/", SAFE_REPLACEMENT).replace("\\", SAFE_REPLACEMENT).strip()

    value = value or fallback

    if len(value) > max_len:
        value = value[:max_len].rstrip()

    return value


def _guess_ext(
    name_or_url: str,
    default=".jpg",
) -> str:
    if not name_or_url:
        return default

    try:
        path = urlparse(name_or_url).path if "://" in name_or_url else name_or_url
    except Exception:
        path = name_or_url

    _base, ext = os.path.splitext(os.path.basename(path))

    return ext or default


def _read_evidence_file(
    imagen_field,
):
    if not imagen_field:
        return None

    storage = getattr(
        imagen_field,
        "storage",
        None,
    )

    storage_name = (
        getattr(
            imagen_field,
            "name",
            "",
        )
        or ""
    )

    # 1. Django Storage
    if storage and storage_name:
        try:
            if storage.exists(storage_name):
                with storage.open(
                    storage_name,
                    "rb",
                ) as source:
                    return source.read()

        except Exception:
            logger.exception(
                "Could not read evidence from storage: %s",
                storage_name,
            )

    # 2. Fallback URL pública
    public_url = ""

    try:
        public_url = imagen_field.url or ""
    except Exception:
        public_url = ""

    if public_url.startswith(
        (
            "http://",
            "https://",
        )
    ):
        try:
            import requests

            response = requests.get(
                public_url,
                timeout=30,
            )

            response.raise_for_status()

            return response.content

        except Exception:
            logger.exception(
                "Could not download evidence URL: %s",
                public_url,
            )

    return None


def build_temporary_zip_from_submission(
    submission: ClientSubmission,
) -> str:
    """
    Construye temporalmente el mismo ZIP de fotografías
    que actualmente genera Operations.

    No guarda el ZIP permanentemente en el modelo.
    """

    billing = submission.billing_session

    root_name = _safe_component_preserve(
        (billing.proyecto_id or submission.project_id or f"Billing_{billing.pk}"),
        max_len=80,
    )

    assignments = (
        billing.tecnicos_sesion.select_related(
            "tecnico",
        )
        .prefetch_related(
            "evidencias__requisito",
        )
        .all()
    )

    temp = NamedTemporaryFile(
        suffix=".zip",
        delete=False,
    )

    temp_path = temp.name

    temp.close()

    used_paths = set()

    total_added = 0

    try:
        with zipfile.ZipFile(
            temp_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zf:

            for assignment in assignments:
                evidences = getattr(
                    assignment,
                    "evidencias",
                    None,
                )

                if not evidences:
                    continue

                for evidence in evidences.all():
                    image_field = getattr(
                        evidence,
                        "imagen",
                        None,
                    )

                    if not image_field:
                        continue

                    data = _read_evidence_file(image_field)

                    if data is None:
                        continue

                    storage_name = (
                        getattr(
                            image_field,
                            "name",
                            "",
                        )
                        or ""
                    )

                    public_url = ""

                    try:
                        public_url = image_field.url or ""
                    except Exception:
                        public_url = ""

                    if getattr(
                        billing,
                        "proyecto_especial",
                        False,
                    ) and not getattr(
                        evidence,
                        "requisito_id",
                        None,
                    ):
                        title = (
                            getattr(
                                evidence,
                                "titulo_manual",
                                "",
                            )
                            or "Extra"
                        )

                    else:
                        requisito = getattr(
                            evidence,
                            "requisito",
                            None,
                        )

                        title = (
                            getattr(
                                requisito,
                                "titulo",
                                "",
                            )
                            or "Extra"
                        )

                    extension = _guess_ext(
                        storage_name or public_url,
                        default=".jpg",
                    )

                    file_title = _safe_component_preserve(
                        title,
                        max_len=120,
                    )

                    arcname = f"{root_name}/" f"{file_title}" f"{extension}"

                    if arcname in used_paths:
                        arcname = (
                            f"{root_name}/"
                            f"{file_title} "
                            f"({evidence.pk})"
                            f"{extension}"
                        )

                        counter = 2

                        while arcname in used_paths:
                            arcname = (
                                f"{root_name}/"
                                f"{file_title} "
                                f"({evidence.pk})_"
                                f"{counter}"
                                f"{extension}"
                            )

                            counter += 1

                    used_paths.add(arcname)

                    zf.writestr(
                        arcname,
                        data,
                    )

                    total_added += 1

        if total_added == 0:
            Path(temp_path).unlink(
                missing_ok=True,
            )

            raise RuntimeError("No evidence photos could be added to the ZIP.")

        return temp_path

    except Exception:
        Path(temp_path).unlink(
            missing_ok=True,
        )

        raise


def prepare_local_zip(
    submission: ClientSubmission,
):
    """
    Devuelve:

        (
            local_path,
            temporary
        )

    Usa exactamente el mismo generador oficial de ZIP
    utilizado por Operations.

    El archivo temporal utilizado por Playwright se guarda
    con el Access Point ID real del proyecto.

    Ejemplo:

        Project ID:
            0913RA_04_5005-009-1

        Access Point ID:
            5005-009-1

        ZIP enviado a Smartsheet:
            5005-009-1.zip

    temporary=True significa que el worker debe eliminar
    el archivo temporal al terminar.
    """

    # ========================================================
    # Resolver Access Point ID para nombre del ZIP
    # ========================================================

    access_point_id = str(submission.access_point_id or "").strip()

    # ========================================================
    # Fallback desde form_payload
    # ========================================================

    if not access_point_id:
        payload = (
            submission.form_payload
            if isinstance(
                submission.form_payload,
                dict,
            )
            else {}
        )

        access_point_id = str(
            payload.get(
                "access_point_id",
                "",
            )
            or ""
        ).strip()

    # ========================================================
    # Último fallback
    # ========================================================

    if not access_point_id:
        access_point_id = str(
            submission.project_id or f"project_{submission.pk}"
        ).strip()

    # ========================================================
    # Nombre seguro conservando guiones
    # ========================================================

    safe_zip_name = _safe_component_preserve(
        access_point_id,
        fallback=f"project_{submission.pk}",
        max_len=120,
    )

    browser_zip_filename = f"{safe_zip_name}.zip"

    print(
        "CLIENT SUBMISSION ZIP NAME:",
        {
            "submission_id": submission.pk,
            "project_id": submission.project_id,
            "access_point_id": access_point_id,
            "browser_zip_filename": browser_zip_filename,
        },
    )

    # ========================================================
    # Crear ubicación temporal con nombre legible
    # ========================================================

    def create_named_temporary_zip() -> Path:
        temporary_directory = Path(
            "tmp/client_submissions/uploads",
        )

        temporary_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_path = temporary_directory / browser_zip_filename

        # ====================================================
        # El procesamiento actual es secuencial.
        #
        # Eliminamos cualquier archivo temporal previo con
        # el mismo nombre antes de escribir el nuevo.
        # ====================================================

        temporary_path.unlink(
            missing_ok=True,
        )

        return temporary_path

    # ========================================================
    # 1. ZIP ya guardado en FileField
    # ========================================================

    if submission.zip_file:
        try:
            local_path = submission.zip_file.path

            if (
                local_path
                and Path(
                    local_path,
                ).exists()
            ):
                source_path = Path(
                    local_path,
                )

                temporary_path = create_named_temporary_zip()

                with source_path.open(
                    "rb",
                ) as source:
                    with temporary_path.open(
                        "wb",
                    ) as destination:
                        while True:
                            chunk = source.read(
                                1024 * 1024,
                            )

                            if not chunk:
                                break

                            destination.write(
                                chunk,
                            )

                logger.info(
                    (
                        "Stored ZIP prepared "
                        "for client submission=%s "
                        "project=%s "
                        "access_point_id=%s "
                        "browser_filename=%s "
                        "path=%s"
                    ),
                    submission.pk,
                    submission.project_id,
                    access_point_id,
                    browser_zip_filename,
                    temporary_path,
                )

                return (
                    str(
                        temporary_path,
                    ),
                    True,
                )

        except Exception:
            logger.exception(
                ("Could not use local FileField ZIP " "path for submission %s."),
                submission.pk,
            )

        # ====================================================
        # FileField usando storage remoto
        # ====================================================

        try:
            temporary_path = create_named_temporary_zip()

            with submission.zip_file.open(
                "rb",
            ) as source:
                with temporary_path.open(
                    "wb",
                ) as destination:
                    while True:
                        chunk = source.read(
                            1024 * 1024,
                        )

                        if not chunk:
                            break

                        destination.write(
                            chunk,
                        )

            logger.info(
                (
                    "Remote stored ZIP prepared "
                    "for client submission=%s "
                    "project=%s "
                    "access_point_id=%s "
                    "browser_filename=%s "
                    "path=%s"
                ),
                submission.pk,
                submission.project_id,
                access_point_id,
                browser_zip_filename,
                temporary_path,
            )

            return (
                str(
                    temporary_path,
                ),
                True,
            )

        except Exception as exc:
            raise RuntimeError(
                "Could not prepare ZIP file " f"for browser upload: {exc}"
            ) from exc

    # ========================================================
    # 2. ZIP externo
    # ========================================================

    if submission.zip_source_url:
        raise RuntimeError(
            "zip_source_url exists but automatic URL "
            "download has not been implemented yet."
        )

    # ========================================================
    # 3. Generar exactamente el mismo ZIP que Operations
    # ========================================================

    billing = submission.billing_session

    spooled = None

    try:
        (
            spooled,
            official_filename,
            stats,
        ) = generar_fotos_zip_sesion(
            billing,
        )

        temporary_path = create_named_temporary_zip()

        with temporary_path.open(
            "wb",
        ) as destination:
            while True:
                chunk = spooled.read(
                    1024 * 1024,
                )

                if not chunk:
                    break

                destination.write(
                    chunk,
                )

        logger.info(
            (
                "Official Operations ZIP prepared "
                "for client submission=%s "
                "billing=%s "
                "official_filename=%s "
                "browser_filename=%s "
                "access_point_id=%s "
                "photos=%s "
                "failed=%s "
                "path=%s"
            ),
            submission.pk,
            billing.pk,
            official_filename,
            browser_zip_filename,
            access_point_id,
            stats.get(
                "total_agregadas",
                0,
            ),
            stats.get(
                "total_fallidas",
                0,
            ),
            temporary_path,
        )

        return (
            str(
                temporary_path,
            ),
            True,
        )

    except Exception as exc:
        raise RuntimeError(
            "Could not generate the official Operations ZIP: " f"{exc}"
        ) from exc

    finally:
        if spooled is not None:
            try:
                spooled.close()

            except Exception:
                pass


# ============================================================
# Guardar screenshot
# ============================================================


def save_attempt_screenshot(
    attempt: ClientSubmissionAttempt,
    screenshot_path: str,
):
    path = Path(screenshot_path)

    if not path.exists():
        return

    filename = (
        f"submission_"
        f"{attempt.submission_id}_"
        f"attempt_"
        f"{attempt.attempt_number}.png"
    )

    with path.open("rb") as source:
        attempt.screenshot.save(
            filename,
            File(source),
            save=False,
        )


# ============================================================
# Procesar Dry Run
# ============================================================


def process_dry_run_submission(
    submission: ClientSubmission,
):
    batch = submission.batch

    attempt = create_attempt(submission)

    # Refrescar después del lock/update.
    submission.refresh_from_db()

    local_zip_path = None
    temporary_zip = False

    try:
        # ====================================================
        # ZIP
        # ====================================================

        local_zip_path, temporary_zip = prepare_local_zip(submission)

        create_event(
            batch=batch,
            submission=submission,
            event_type="dry_run_started",
            message=(f"Dry Run started for " f"{submission.project_id}."),
            metadata={
                "attempt_number": attempt.attempt_number,
                "zip_filename": submission.zip_filename,
            },
        )

        # ====================================================
        # Browser visibility
        #
        # LOCAL / TEST:
        #     visible by default
        #
        # PRODUCTION:
        #     CLIENT_SUBMISSIONS_HEADLESS=1
        # ====================================================

        headless = (
            os.getenv(
                "CLIENT_SUBMISSIONS_HEADLESS",
                "0",
            ).strip()
            == "1"
        )

        logger.info(
            (
                "Starting Smartsheet Dry Run "
                "submission=%s "
                "project=%s "
                "headless=%s"
            ),
            submission.pk,
            submission.project_id,
            headless,
        )

        # ====================================================
        # Playwright
        # ====================================================

        result = asyncio.run(
            run_smartsheet_dry_run(
                submission=submission,
                attachment_path=local_zip_path,
                headless=headless,
            )
        )

        # ====================================================
        # Challenge
        # ====================================================

        if result.verification_required:
            submission.mark_awaiting_verification()

            batch.mark_awaiting_verification()

            attempt.result = ClientSubmissionAttempt.Result.AWAITING_VERIFICATION

            attempt.browser_url = result.final_url

            attempt.browser_title = result.page_title

            attempt.finished_at = timezone.now()

            attempt.save(
                update_fields=[
                    "result",
                    "browser_url",
                    "browser_title",
                    "finished_at",
                    "updated_at",
                ]
            )

            create_event(
                batch=batch,
                submission=submission,
                level=ClientSubmissionEvent.Level.WARNING,
                event_type="verification_required",
                message=(
                    f"Human verification is required " f"for {submission.project_id}."
                ),
            )

            return

        # ====================================================
        # Guardar intento
        # ====================================================

        attempt.result = ClientSubmissionAttempt.Result.DRY_RUN_COMPLETED

        attempt.browser_url = result.final_url

        attempt.browser_title = result.page_title

        attempt.page_html_snapshot = result.html_snapshot

        attempt.error_details = {
            "fields_filled": result.fields_filled,
            "attachment_uploaded": result.attachment_uploaded,
            "attachment_filename": result.attachment_filename,
            "metadata": result.metadata,
        }

        if result.screenshot_path:
            save_attempt_screenshot(
                attempt,
                result.screenshot_path,
            )

        attempt.finished_at = timezone.now()

        attempt.save()

        # ====================================================
        # Cerrar Submission
        # ====================================================

        submission.refresh_from_db()

        submission.mark_dry_run_completed()

        create_event(
            batch=batch,
            submission=submission,
            level=ClientSubmissionEvent.Level.SUCCESS,
            event_type="dry_run_completed",
            message=(
                f"Dry Run completed for "
                f"{submission.project_id}. "
                "The form was not submitted."
            ),
            metadata={
                "attempt_number": attempt.attempt_number,
                "fields_filled": result.fields_filled,
                "attachment_uploaded": result.attachment_uploaded,
            },
        )

    except Exception as exc:
        logger.exception(
            "Dry Run failed for submission %s",
            submission.pk,
        )

        submission.refresh_from_db()

        submission.mark_failed(
            str(exc),
            code=exc.__class__.__name__,
        )

        attempt.result = ClientSubmissionAttempt.Result.FAILED

        attempt.error_code = exc.__class__.__name__

        attempt.error_message = str(exc)

        attempt.finished_at = timezone.now()

        attempt.save(
            update_fields=[
                "result",
                "error_code",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )

        create_event(
            batch=batch,
            submission=submission,
            level=ClientSubmissionEvent.Level.ERROR,
            event_type="dry_run_failed",
            message=(f"Dry Run failed for " f"{submission.project_id}: " f"{exc}"),
            metadata={
                "error_type": exc.__class__.__name__,
                "attempt_number": attempt.attempt_number,
            },
        )

    finally:
        if temporary_zip and local_zip_path:
            try:
                Path(
                    local_zip_path,
                ).unlink(
                    missing_ok=True,
                )

            except Exception:
                logger.exception("Could not delete temporary ZIP.")


# ============================================================
# Procesar Batch
# ============================================================


def process_batch(
    batch_id: int,
):
    batch = ClientSubmissionBatch.objects.get(pk=batch_id)

    while True:
        batch.refresh_from_db()

        # ====================================================
        # Detenciones administrativas
        # ====================================================

        if batch.status in {
            ClientSubmissionBatch.Status.PAUSED,
            ClientSubmissionBatch.Status.CANCELLED,
            ClientSubmissionBatch.Status.AWAITING_VERIFICATION,
        }:
            return

        # ====================================================
        # Próximo proyecto
        # ====================================================

        submission = get_next_submission(batch)

        if not submission:
            batch.refresh_final_status()

            create_event(
                batch=batch,
                event_type=("batch_processing_finished"),
                message=(
                    f"Batch #{batch.pk} "
                    f"processing finished "
                    f"with status "
                    f"{batch.get_status_display()}."
                ),
            )

            return

        # ====================================================
        # Current submission
        # ====================================================

        batch.current_submission = submission

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "current_submission",
                "last_activity_at",
                "updated_at",
            ]
        )

        # ====================================================
        # Solo Dry Run por ahora
        # ====================================================

        if batch.is_dry_run:
            process_dry_run_submission(submission)

        else:
            submission.mark_failed(
                (
                    "Live submission is not enabled yet. "
                    "Use Dry Run while validating "
                    "the form automation."
                ),
                code=("LIVE_NOT_IMPLEMENTED"),
            )

            create_event(
                batch=batch,
                submission=submission,
                level=(ClientSubmissionEvent.Level.ERROR),
                event_type=("live_submission_not_implemented"),
                message=(
                    "Live submission was blocked because "
                    "the production submit step "
                    "is not enabled yet."
                ),
            )


# ============================================================
# Una iteración
# ============================================================


def run_once() -> bool:
    """
    Procesa un Batch pendiente.

    Retorna:
        True  -> encontró un Batch.
        False -> no había trabajo.
    """

    batch_id = claim_next_pending_batch()

    if not batch_id:
        return False

    process_batch(batch_id)

    return True
