# client_submissions/automation/worker.py
from __future__ import annotations

import asyncio
import logging
import os
import socket
from pathlib import Path

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from client_submissions.automation.smartsheet_form import \
    run_smartsheet_dry_run
from client_submissions.models import (ClientSubmission,
                                       ClientSubmissionAttempt,
                                       ClientSubmissionBatch,
                                       ClientSubmissionEvent)
from operaciones.views_fotos_zip import (SMARTSHEET_MAX_ZIP_PART_BYTES,
                                         SMARTSHEET_MAX_ZIP_PARTS,
                                         generar_fotos_zip_partes_smartsheet)

logger = logging.getLogger(__name__)


def prepare_local_zip_parts(
    submission: ClientSubmission,
) -> tuple[
    list[str],
    bool,
    dict,
]:
    """
    Genera las partes ZIP utilizadas exclusivamente por
    Client Submissions / Smartsheet.

    Cada parte:

    - Tiene un tamaño máximo de 29 MB.
    - Conserva el Access Point ID en el nombre.
    - Se guarda temporalmente en:
          tmp/client_submissions/uploads/
    - Debe eliminarse al terminar el procesamiento.

    Devuelve:

        (
            local_zip_paths,
            temporary_zips,
            stats,
        )

    Ejemplo:

        (
            [
                "tmp/client_submissions/uploads/8040-019-2_part_01.zip",
                "tmp/client_submissions/uploads/8040-019-2_part_02.zip",
            ],
            True,
            {
                ...
            },
        )
    """

    billing = submission.billing_session

    # ========================================================
    # Resolver Access Point ID
    # ========================================================

    access_point_id = str(
        submission.access_point_id or "",
    ).strip()

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

    if not access_point_id:
        access_point_id = str(
            submission.project_id or f"project_{submission.pk}",
        ).strip()

    safe_zip_name = _safe_component_preserve(
        access_point_id,
        fallback=f"project_{submission.pk}",
        max_len=120,
    )

    # ========================================================
    # Directorio temporal
    # ========================================================

    temporary_directory = Path(
        "tmp/client_submissions/uploads",
    )

    temporary_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ========================================================
    # Eliminar partes temporales anteriores del mismo proyecto
    # ========================================================

    previous_patterns = [
        f"{safe_zip_name}.zip",
        f"{safe_zip_name}_part_*.zip",
    ]

    for pattern in previous_patterns:
        for previous_file in temporary_directory.glob(
            pattern,
        ):
            try:
                previous_file.unlink(
                    missing_ok=True,
                )

            except Exception:
                logger.exception(
                    "Could not remove previous temporary ZIP: %s",
                    previous_file,
                )

    generated_parts = None

    local_zip_paths: list[str] = []

    opened_spooled_files = []

    try:
        # ====================================================
        # Generación exclusiva para Smartsheet
        #
        # La función debe devolver:
        #
        #     (
        #         parts,
        #         stats,
        #     )
        #
        # Cada elemento de parts puede ser:
        #
        #     (
        #         spooled_file,
        #         filename,
        #     )
        #
        # o:
        #
        #     {
        #         "file": spooled_file,
        #         "filename": filename,
        #         "size_bytes": int,
        #     }
        # ====================================================

        generated_parts = generar_fotos_zip_partes_smartsheet(
            billing,
        )

        if (
            not isinstance(
                generated_parts,
                tuple,
            )
            or len(
                generated_parts,
            )
            != 2
        ):
            raise RuntimeError(
                "generar_fotos_zip_partes_smartsheet() " "must return (parts, stats)."
            )

        parts, stats = generated_parts

        if not isinstance(
            parts,
            list,
        ):
            parts = list(
                parts or [],
            )

        if not isinstance(
            stats,
            dict,
        ):
            stats = {}

        if not parts:
            raise RuntimeError("No ZIP parts were generated for Smartsheet.")

        if len(parts) > SMARTSHEET_MAX_ZIP_PARTS:
            raise RuntimeError(
                (
                    "The Smartsheet ZIP part limit was exceeded. "
                    f"Generated: {len(parts)}. "
                    f"Maximum: {SMARTSHEET_MAX_ZIP_PARTS}."
                )
            )

        total_parts = len(
            parts,
        )

        part_metadata: list[dict] = []

        # ====================================================
        # Guardar cada SpooledTemporaryFile como archivo local
        # ========================================================

        for part_index, part in enumerate(
            parts,
            start=1,
        ):
            spooled_file = None
            original_filename = ""
            reported_size = None

            # ------------------------------------------------
            # Formato diccionario
            # ------------------------------------------------

            if isinstance(
                part,
                dict,
            ):
                spooled_file = (
                    part.get(
                        "file",
                    )
                    or part.get(
                        "spooled",
                    )
                    or part.get(
                        "spooled_file",
                    )
                )

                original_filename = str(
                    part.get(
                        "filename",
                        "",
                    )
                    or ""
                ).strip()

                reported_size = part.get(
                    "size_bytes",
                )

            # ------------------------------------------------
            # Formato tupla/lista
            # ------------------------------------------------

            elif isinstance(
                part,
                (
                    tuple,
                    list,
                ),
            ):
                if len(part) < 2:
                    raise RuntimeError(
                        (
                            "Invalid ZIP part returned by "
                            "generar_fotos_zip_partes_smartsheet(). "
                            f"Part #{part_index} does not contain "
                            "at least file and filename."
                        )
                    )

                spooled_file = part[0]

                original_filename = str(
                    part[1] or "",
                ).strip()

                if len(part) >= 3:
                    reported_size = part[2]

            else:
                raise RuntimeError(
                    (
                        "Invalid ZIP part type returned by "
                        "generar_fotos_zip_partes_smartsheet(): "
                        f"{type(part).__name__}."
                    )
                )

            if spooled_file is None:
                raise RuntimeError(f"ZIP part #{part_index} has no file object.")

            opened_spooled_files.append(
                spooled_file,
            )

            # =================================================
            # Nombre final para el navegador
            # =================================================

            if total_parts == 1:
                browser_filename = f"{safe_zip_name}.zip"

            else:
                browser_filename = f"{safe_zip_name}_part_" f"{part_index:02d}.zip"

            temporary_path = temporary_directory / browser_filename

            temporary_path.unlink(
                missing_ok=True,
            )

            # =================================================
            # Copiar archivo temporal
            # =================================================

            try:
                spooled_file.seek(
                    0,
                )

            except Exception as exc:
                raise RuntimeError(
                    (f"Could not rewind ZIP part " f"#{part_index}: {exc}")
                ) from exc

            try:
                with temporary_path.open(
                    "wb",
                ) as destination:
                    while True:
                        chunk = spooled_file.read(
                            1024 * 1024,
                        )

                        if not chunk:
                            break

                        destination.write(
                            chunk,
                        )

                actual_size = temporary_path.stat().st_size

            except Exception:
                temporary_path.unlink(
                    missing_ok=True,
                )

                raise

            # =================================================
            # Validación real del límite
            # =================================================

            if actual_size <= 0:
                temporary_path.unlink(
                    missing_ok=True,
                )

                raise RuntimeError(f"Generated ZIP part #{part_index} is empty.")

            if actual_size > SMARTSHEET_MAX_ZIP_PART_BYTES:
                temporary_path.unlink(
                    missing_ok=True,
                )

                raise RuntimeError(
                    (
                        f"Generated ZIP part "
                        f"{browser_filename} exceeds the "
                        "Smartsheet size limit. "
                        f"Size: {actual_size} bytes. "
                        "Maximum: "
                        f"{SMARTSHEET_MAX_ZIP_PART_BYTES} bytes."
                    )
                )

            local_zip_paths.append(
                str(
                    temporary_path,
                )
            )

            part_metadata.append(
                {
                    "part_number": part_index,
                    "filename": browser_filename,
                    "original_filename": original_filename,
                    "size_bytes": actual_size,
                    "reported_size_bytes": reported_size,
                }
            )

        final_stats = {
            **stats,
            "part_count": len(
                local_zip_paths,
            ),
            "max_part_bytes": (SMARTSHEET_MAX_ZIP_PART_BYTES),
            "max_parts": SMARTSHEET_MAX_ZIP_PARTS,
            "parts": part_metadata,
            "total_zip_bytes": sum(item["size_bytes"] for item in part_metadata),
        }

        print(
            "CLIENT SUBMISSION ZIP PARTS PREPARED:",
            {
                "submission_id": submission.pk,
                "billing_id": billing.pk,
                "project_id": submission.project_id,
                "access_point_id": access_point_id,
                "part_count": len(
                    local_zip_paths,
                ),
                "parts": part_metadata,
            },
        )

        logger.info(
            (
                "Smartsheet ZIP parts prepared "
                "submission=%s billing=%s "
                "project=%s access_point_id=%s "
                "part_count=%s total_zip_bytes=%s"
            ),
            submission.pk,
            billing.pk,
            submission.project_id,
            access_point_id,
            len(
                local_zip_paths,
            ),
            final_stats["total_zip_bytes"],
        )

        return (
            local_zip_paths,
            True,
            final_stats,
        )

    except Exception:
        # ====================================================
        # Eliminar cualquier parte creada parcialmente
        # ====================================================

        for local_zip_path in local_zip_paths:
            try:
                Path(
                    local_zip_path,
                ).unlink(
                    missing_ok=True,
                )

            except Exception:
                logger.exception(
                    "Could not delete incomplete ZIP part: %s",
                    local_zip_path,
                )

        raise

    finally:
        # ====================================================
        # Cerrar todos los SpooledTemporaryFile devueltos
        # por el generador
        # ====================================================

        for spooled_file in opened_spooled_files:
            try:
                spooled_file.close()

            except Exception:
                logger.exception("Could not close generated ZIP part.")


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

    attempt = create_attempt(
        submission,
    )

    submission.refresh_from_db()

    local_zip_paths: list[str] = []

    temporary_zips = False

    zip_stats: dict = {}

    zip_filenames: list[str] = []

    try:
        # ====================================================
        # ZIP divididos exclusivamente para Smartsheet
        # ====================================================

        (
            local_zip_paths,
            temporary_zips,
            zip_stats,
        ) = prepare_local_zip_parts(
            submission,
        )

        zip_filenames = [Path(path).name for path in local_zip_paths]

        if not local_zip_paths:
            raise RuntimeError("No ZIP files were prepared for Smartsheet.")

        if len(local_zip_paths) > SMARTSHEET_MAX_ZIP_PARTS:
            raise RuntimeError(
                (
                    "Too many ZIP parts were prepared. "
                    f"Received: {len(local_zip_paths)}. "
                    f"Maximum: {SMARTSHEET_MAX_ZIP_PARTS}."
                )
            )

        for local_zip_path in local_zip_paths:
            path = Path(
                local_zip_path,
            )

            if not path.exists():
                raise RuntimeError(f"Prepared ZIP part does not exist: {path}")

            file_size = path.stat().st_size

            if file_size > SMARTSHEET_MAX_ZIP_PART_BYTES:
                raise RuntimeError(
                    (
                        f"Prepared ZIP part {path.name} exceeds "
                        "the Smartsheet limit. "
                        f"Size: {file_size} bytes. "
                        "Maximum: "
                        f"{SMARTSHEET_MAX_ZIP_PART_BYTES} bytes."
                    )
                )

        create_event(
            batch=batch,
            submission=submission,
            event_type="dry_run_started",
            message=(f"Dry Run started for " f"{submission.project_id}."),
            metadata={
                "attempt_number": attempt.attempt_number,
                "zip_parts": zip_filenames,
                "zip_part_count": len(
                    zip_filenames,
                ),
                "zip_stats": zip_stats,
            },
        )

        # ====================================================
        # Browser visibility
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
                "submission=%s project=%s "
                "headless=%s zip_parts=%s"
            ),
            submission.pk,
            submission.project_id,
            headless,
            len(
                local_zip_paths,
            ),
        )

        # ====================================================
        # Playwright
        # ====================================================

        result = asyncio.run(
            run_smartsheet_dry_run(
                submission=submission,
                attachment_paths=local_zip_paths,
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

            attempt.error_details = {
                "zip_parts": zip_filenames,
                "zip_stats": zip_stats,
                "metadata": result.metadata,
            }

            attempt.finished_at = timezone.now()

            attempt.save(
                update_fields=[
                    "result",
                    "browser_url",
                    "browser_title",
                    "error_details",
                    "finished_at",
                    "updated_at",
                ]
            )

            create_event(
                batch=batch,
                submission=submission,
                level=(ClientSubmissionEvent.Level.WARNING),
                event_type="verification_required",
                message=(
                    "Human verification is required " f"for {submission.project_id}."
                ),
                metadata={
                    "zip_parts": zip_filenames,
                    "zip_stats": zip_stats,
                },
            )

            return

        # ====================================================
        # Validar resultado de archivos
        # ====================================================

        if not result.attachments_uploaded:
            raise RuntimeError(
                (
                    "Smartsheet Dry Run completed the fields, "
                    "but the ZIP attachments were not accepted."
                )
            )

        if len(
            result.attachment_filenames,
        ) != len(
            local_zip_paths,
        ):
            raise RuntimeError(
                (
                    "Smartsheet did not report all ZIP parts "
                    "as attached. "
                    f"Expected: {len(local_zip_paths)}. "
                    "Reported: "
                    f"{len(result.attachment_filenames)}."
                )
            )

        # ====================================================
        # Guardar intento
        # ====================================================

        attempt.result = ClientSubmissionAttempt.Result.DRY_RUN_COMPLETED

        attempt.browser_url = result.final_url

        attempt.browser_title = result.page_title

        attempt.page_html_snapshot = result.html_snapshot

        attempt.error_details = {
            "fields_filled": result.fields_filled,
            "attachments_uploaded": (result.attachments_uploaded),
            "attachment_filenames": (result.attachment_filenames),
            "attachment_count": len(
                result.attachment_filenames,
            ),
            "zip_stats": zip_stats,
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
            level=(ClientSubmissionEvent.Level.SUCCESS),
            event_type="dry_run_completed",
            message=(
                f"Dry Run completed for "
                f"{submission.project_id}. "
                f"{len(result.attachment_filenames)} "
                "ZIP file(s) were attached. "
                "The form was not submitted."
            ),
            metadata={
                "attempt_number": (attempt.attempt_number),
                "fields_filled": (result.fields_filled),
                "attachments_uploaded": (result.attachments_uploaded),
                "attachment_filenames": (result.attachment_filenames),
                "attachment_count": len(
                    result.attachment_filenames,
                ),
                "zip_stats": zip_stats,
            },
        )

    except Exception as exc:
        logger.exception(
            "Dry Run failed for submission %s",
            submission.pk,
        )

        submission.refresh_from_db()

        submission.mark_failed(
            str(
                exc,
            ),
            code=exc.__class__.__name__,
        )

        attempt.result = ClientSubmissionAttempt.Result.FAILED

        attempt.error_code = exc.__class__.__name__

        attempt.error_message = str(
            exc,
        )

        attempt.error_details = {
            "zip_parts": [Path(path).name for path in local_zip_paths],
            "zip_stats": zip_stats,
            "error_type": (exc.__class__.__name__),
        }

        attempt.finished_at = timezone.now()

        attempt.save(
            update_fields=[
                "result",
                "error_code",
                "error_message",
                "error_details",
                "finished_at",
                "updated_at",
            ]
        )

        create_event(
            batch=batch,
            submission=submission,
            level=(ClientSubmissionEvent.Level.ERROR),
            event_type="dry_run_failed",
            message=(f"Dry Run failed for " f"{submission.project_id}: " f"{exc}"),
            metadata={
                "error_type": (exc.__class__.__name__),
                "attempt_number": (attempt.attempt_number),
                "zip_parts": [Path(path).name for path in local_zip_paths],
                "zip_stats": zip_stats,
            },
        )

    finally:
        # ====================================================
        # Eliminar partes temporales
        # ====================================================

        if temporary_zips:
            for local_zip_path in local_zip_paths:
                try:
                    Path(
                        local_zip_path,
                    ).unlink(
                        missing_ok=True,
                    )

                except Exception:
                    logger.exception(
                        ("Could not delete temporary " "ZIP part: %s"),
                        local_zip_path,
                    )


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
