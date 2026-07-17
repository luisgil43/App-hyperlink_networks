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

from client_submissions.automation.smartsheet_form import (
    _detect_verification_challenge, close_active_browser, get_active_browser,
    run_smartsheet_dry_run, run_smartsheet_live)
from client_submissions.automation.smartsheet_state import \
    SmartsheetRestartSubmissionRequested
from client_submissions.models import (ClientSubmission,
                                       ClientSubmissionAttempt,
                                       ClientSubmissionBatch,
                                       ClientSubmissionEvent)
from operaciones.views_fotos_zip import (SMARTSHEET_MAX_ZIP_PART_BYTES,
                                         SMARTSHEET_MAX_ZIP_PARTS,
                                         generar_fotos_zip_partes_smartsheet)

logger = logging.getLogger(__name__)


@transaction.atomic
def requeue_single_submission_after_captcha_restart(
    submission: ClientSubmission,
    attempt: ClientSubmissionAttempt,
    *,
    reason: str,
):
    """
    Reinicia únicamente el Submission actual después de que el
    CAPTCHA venció o fue cerrado.

    No modifica submissions que ya fueron enviados o completados.

    El Batch permanece RUNNING para que process_batch() vuelva
    a tomar inmediatamente este mismo proyecto.
    """

    submission = (
        ClientSubmission.objects.select_for_update()
        .select_related(
            "batch",
        )
        .get(
            pk=submission.pk,
        )
    )

    batch = ClientSubmissionBatch.objects.select_for_update().get(
        pk=submission.batch_id,
    )

    now = timezone.now()

    # ========================================================
    # Cerrar el intento bloqueado
    # ========================================================

    attempt = ClientSubmissionAttempt.objects.select_for_update().get(
        pk=attempt.pk,
    )

    attempt.result = ClientSubmissionAttempt.Result.CANCELLED

    attempt.error_code = "CAPTCHA_SESSION_RESTARTED"

    attempt.error_message = reason

    attempt_details = (
        dict(
            attempt.error_details,
        )
        if isinstance(
            attempt.error_details,
            dict,
        )
        else {}
    )

    attempt.error_details = {
        **attempt_details,
        "captcha_restart_requested": True,
        "captcha_restart_requested_at": now.isoformat(),
        "restart_reason": reason,
    }

    attempt.finished_at = now

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

    # ========================================================
    # Reiniciar solamente este Submission
    # ========================================================

    browser_state = (
        dict(
            submission.browser_state,
        )
        if isinstance(
            submission.browser_state,
            dict,
        )
        else {}
    )

    verification_state = browser_state.get(
        "human_verification",
        {},
    )

    if not isinstance(
        verification_state,
        dict,
    ):
        verification_state = {}

    verification_state.update(
        {
            "session_available": False,
            "session_url": "",
            "captcha_cleared": False,
            "continue_requested_at": None,
            "continue_requested_by": None,
            "continue_requested_username": "",
            "cancel_requested_at": None,
            "cancel_requested_by": None,
            "cancel_requested_username": "",
            "retry_challenge_requested_at": None,
            "retry_challenge_requested_by": None,
            "retry_challenge_requested_username": "",
            "retry_challenge_processed_at": now.isoformat(),
            "retry_challenge_clicked_at": None,
            "retry_challenge_error": "",
            "worker_checked_at": now.isoformat(),
            "restart_queued_at": now.isoformat(),
            "message": (
                "The expired CAPTCHA session was closed. "
                "This project was queued to restart."
            ),
        }
    )

    browser_state["human_verification"] = verification_state

    submission.browser_state = browser_state

    submission.status = ClientSubmission.Status.PENDING_CLIENT_SUBMISSION

    submission.started_at = None

    submission.form_loaded_at = None

    submission.form_completed_at = None

    submission.verification_required_at = None

    submission.verification_completed_at = None

    submission.submit_clicked_at = None

    submission.finished_at = None

    submission.last_error_code = ""

    submission.last_error_message = ""

    submission.last_error_at = None

    submission.browser_session_key = ""

    submission.save(
        update_fields=[
            "status",
            "started_at",
            "form_loaded_at",
            "form_completed_at",
            "verification_required_at",
            "verification_completed_at",
            "submit_clicked_at",
            "finished_at",
            "last_error_code",
            "last_error_message",
            "last_error_at",
            "browser_session_key",
            "browser_state",
            "updated_at",
        ]
    )

    # ========================================================
    # Mantener el Batch procesándose
    #
    # No se devuelve a PENDING para evitar que otro worker
    # reclame el mismo Batch mientras process_batch() sigue vivo.
    # ========================================================

    batch.status = ClientSubmissionBatch.Status.RUNNING

    batch.paused_at = None

    batch.finished_at = None

    batch.current_submission = None

    batch.last_error = ""

    batch.last_activity_at = now

    batch.save(
        update_fields=[
            "status",
            "paused_at",
            "finished_at",
            "current_submission",
            "last_error",
            "last_activity_at",
            "updated_at",
        ]
    )

    create_event(
        batch=batch,
        submission=submission,
        level=ClientSubmissionEvent.Level.WARNING,
        event_type="captcha_session_restart_queued",
        message=(
            f"The expired CAPTCHA session for "
            f"{submission.project_id} was closed. "
            "Only this project will restart."
        ),
        metadata={
            "attempt_number": attempt.attempt_number,
            "restart_queued_at": now.isoformat(),
            "reason": reason,
        },
    )


# ============================================================
# Estado de verificación humana
# ============================================================


def get_human_verification_state(
    submission: ClientSubmission,
) -> dict:
    """
    Obtiene browser_state["human_verification"] de forma segura.
    """

    browser_state = (
        submission.browser_state
        if isinstance(
            submission.browser_state,
            dict,
        )
        else {}
    )

    verification_state = browser_state.get(
        "human_verification",
        {},
    )

    if not isinstance(
        verification_state,
        dict,
    ):
        verification_state = {}

    return verification_state


def set_human_verification_state(
    submission: ClientSubmission,
    **changes,
) -> dict:
    """
    Actualiza browser_state["human_verification"] sin eliminar
    el resto de la información guardada por Playwright.
    """

    browser_state = (
        dict(
            submission.browser_state,
        )
        if isinstance(
            submission.browser_state,
            dict,
        )
        else {}
    )

    verification_state = browser_state.get(
        "human_verification",
        {},
    )

    if not isinstance(
        verification_state,
        dict,
    ):
        verification_state = {}

    verification_state = {
        **verification_state,
        **changes,
    }

    browser_state["human_verification"] = verification_state

    submission.browser_state = browser_state

    return verification_state


def mark_verification_session_available(
    submission: ClientSubmission,
    *,
    stage: str,
    result_metadata: dict | None = None,
):
    """
    Registra que existe una sesión activa de navegador esperando
    que una persona resuelva el CAPTCHA.

    La URL almacenada aquí es informativa. No significa que el
    navegador pueda abrirse directamente desde otra máquina.
    """

    browser_data = get_active_browser(
        submission,
    )

    page = (
        browser_data.get(
            "page",
        )
        if isinstance(
            browser_data,
            dict,
        )
        else None
    )

    session_url = ""

    if page is not None:
        try:
            session_url = str(
                page.url or "",
            ).strip()

        except Exception:
            session_url = ""

    now = timezone.now()

    set_human_verification_state(
        submission,
        stage=stage,
        session_available=(browser_data is not None),
        session_url=session_url,
        detected_at=now.isoformat(),
        continue_requested_at=None,
        continue_requested_by=None,
        cancel_requested_at=None,
        cancel_requested_by=None,
        worker_checked_at=None,
        captcha_cleared=False,
        completed_at=None,
        result_metadata=(
            result_metadata
            if isinstance(
                result_metadata,
                dict,
            )
            else {}
        ),
        message=(
            "The browser is waiting for human verification."
            if browser_data is not None
            else "Verification was detected, but no active browser session was found."
        ),
    )

    submission.save(
        update_fields=[
            "browser_state",
            "updated_at",
        ]
    )


def get_latest_submission_attempt(
    submission: ClientSubmission,
):
    """
    Recupera el último intento creado para el Submission.
    """

    return submission.attempts.order_by(
        "-attempt_number",
        "-id",
    ).first()


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
    """
    Procesa un ClientSubmission en modo Dry Run.

    Completa el formulario y adjunta los archivos, pero no
    presiona Submit.

    Si Smartsheet presenta una verificación humana durante la
    carga o el llenado, registra la sesión activa para permitir
    continuarla desde el flujo de verificación.
    """

    batch = submission.batch

    attempt = create_attempt(
        submission,
    )

    submission.refresh_from_db()

    local_zip_paths: list[str] = []

    temporary_zips = False

    zip_stats: dict = {}

    zip_filenames: list[str] = []

    verification_session_active = False

    try:
        # ====================================================
        # Preparar ZIP divididos exclusivamente para Smartsheet
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

            if file_size <= 0:
                raise RuntimeError(f"Prepared ZIP part is empty: {path.name}")

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

        # ====================================================
        # Evento de inicio
        # ====================================================

        create_event(
            batch=batch,
            submission=submission,
            event_type="dry_run_started",
            message=(f"Dry Run started for {submission.project_id}."),
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
        # Visibilidad del navegador
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
        # Ejecutar Playwright
        # ====================================================

        result = asyncio.run(
            run_smartsheet_dry_run(
                submission=submission,
                attachment_paths=local_zip_paths,
                headless=headless,
                submit_form=False,
            )
        )

        if result is None:
            raise RuntimeError(
                (
                    "Smartsheet automation returned no result. "
                    "Check that run_smartsheet_dry_run() does not "
                    "execute 'return' from inside its finally block."
                )
            )

        # ====================================================
        # Verificación humana
        # ====================================================

        if result.verification_required:
            verification_session_active = True

            submission.refresh_from_db()

            submission.mark_awaiting_verification()

            batch.refresh_from_db()

            batch.mark_awaiting_verification()

            result_metadata = (
                result.metadata
                if isinstance(
                    result.metadata,
                    dict,
                )
                else {}
            )

            verification_stage = str(
                result_metadata.get(
                    "stage",
                    "before_form_fill",
                )
                or "before_form_fill"
            ).strip()

            attempt.result = ClientSubmissionAttempt.Result.AWAITING_VERIFICATION

            attempt.browser_url = result.final_url or ""

            attempt.browser_title = result.page_title or ""

            attempt.page_html_snapshot = result.html_snapshot or ""

            attempt.error_details = {
                "zip_parts": zip_filenames,
                "zip_stats": zip_stats,
                "verification_stage": verification_stage,
                "metadata": result_metadata,
            }

            if result.screenshot_path:
                save_attempt_screenshot(
                    attempt,
                    result.screenshot_path,
                )

            attempt.finished_at = timezone.now()

            attempt.save()

            # ================================================
            # Registrar la sesión viva en browser_state
            # ================================================

            submission.refresh_from_db()

            mark_verification_session_available(
                submission,
                stage=verification_stage,
                result_metadata=result_metadata,
            )

            create_event(
                batch=batch,
                submission=submission,
                level=ClientSubmissionEvent.Level.WARNING,
                event_type="verification_required",
                message=(
                    "Human verification is required for " f"{submission.project_id}."
                ),
                metadata={
                    "attempt_number": attempt.attempt_number,
                    "zip_parts": zip_filenames,
                    "zip_stats": zip_stats,
                    "verification_stage": verification_stage,
                    "result_metadata": result_metadata,
                },
            )

            return

        # ====================================================
        # Validar resultado general
        # ====================================================

        if not result.ok:
            raise RuntimeError(
                ("Smartsheet Dry Run returned " "an unsuccessful result.")
            )

        # ====================================================
        # Validar archivos
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
        # Validación específica de Dry Run
        # ====================================================

        if result.submit_clicked:
            raise RuntimeError(
                ("Dry Run unexpectedly reported that " "the Submit button was clicked.")
            )

        # ====================================================
        # Guardar intento
        # ====================================================

        attempt.result = ClientSubmissionAttempt.Result.DRY_RUN_COMPLETED

        attempt.browser_url = result.final_url or ""

        attempt.browser_title = result.page_title or ""

        attempt.page_html_snapshot = result.html_snapshot or ""

        attempt.error_details = {
            "fields_filled": result.fields_filled,
            "attachments_uploaded": (result.attachments_uploaded),
            "attachment_filenames": (result.attachment_filenames),
            "attachment_count": len(
                result.attachment_filenames,
            ),
            "submit_clicked": result.submit_clicked,
            "zip_stats": zip_stats,
            "metadata": (
                result.metadata
                if isinstance(
                    result.metadata,
                    dict,
                )
                else {}
            ),
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

        batch.refresh_from_db()

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "last_activity_at",
                "updated_at",
            ]
        )

        create_event(
            batch=batch,
            submission=submission,
            level=ClientSubmissionEvent.Level.SUCCESS,
            event_type="dry_run_completed",
            message=(
                f"Dry Run completed for "
                f"{submission.project_id}. "
                f"{len(result.attachment_filenames)} "
                "ZIP file(s) were attached. "
                "The form was not submitted."
            ),
            metadata={
                "attempt_number": attempt.attempt_number,
                "fields_filled": result.fields_filled,
                "attachments_uploaded": (result.attachments_uploaded),
                "attachment_filenames": (result.attachment_filenames),
                "attachment_count": len(
                    result.attachment_filenames,
                ),
                "submit_clicked": result.submit_clicked,
                "zip_stats": zip_stats,
            },
        )

    except Exception as exc:
        logger.exception(
            "Dry Run failed for submission %s",
            submission.pk,
        )

        submission.refresh_from_db()

        # No convertir en FAILED cuando ya quedó correctamente
        # esperando intervención humana.
        if submission.status != ClientSubmission.Status.AWAITING_VERIFICATION:
            submission.mark_failed(
                str(
                    exc,
                ),
                code=exc.__class__.__name__,
            )

        attempt.refresh_from_db()

        if attempt.result != ClientSubmissionAttempt.Result.AWAITING_VERIFICATION:
            attempt.result = ClientSubmissionAttempt.Result.FAILED

            attempt.error_code = exc.__class__.__name__

            attempt.error_message = str(
                exc,
            )

            attempt.error_details = {
                "zip_parts": [Path(path).name for path in local_zip_paths],
                "zip_stats": zip_stats,
                "error_type": exc.__class__.__name__,
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

        batch.refresh_from_db()

        batch.last_error = str(
            exc,
        )

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "last_error",
                "last_activity_at",
                "updated_at",
            ]
        )

        create_event(
            batch=batch,
            submission=submission,
            level=ClientSubmissionEvent.Level.ERROR,
            event_type="dry_run_failed",
            message=(f"Dry Run failed for " f"{submission.project_id}: {exc}"),
            metadata={
                "error_type": exc.__class__.__name__,
                "attempt_number": attempt.attempt_number,
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

        if verification_session_active:
            logger.info(
                ("Dry Run submission %s remains awaiting " "human verification."),
                submission.pk,
            )


# ============================================================
# Procesar Batch
# ============================================================


def process_batch(
    batch_id: int,
):
    batch = ClientSubmissionBatch.objects.get(
        pk=batch_id,
    )

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
        # Próximo proyecto pendiente y validado
        # ====================================================

        submission = get_next_submission(
            batch,
        )

        if not submission:
            batch.refresh_final_status()

            batch.refresh_from_db()

            create_event(
                batch=batch,
                event_type="batch_processing_finished",
                message=(
                    f"Batch #{batch.pk} processing finished "
                    f"with status "
                    f"{batch.get_status_display()}."
                ),
                metadata={
                    "execution_mode": batch.execution_mode,
                    "final_status": batch.status,
                },
            )

            return

        # ====================================================
        # Current submission
        # ====================================================

        batch.current_submission = submission

        batch.last_activity_at = timezone.now()

        batch.last_error = ""

        batch.save(
            update_fields=[
                "current_submission",
                "last_activity_at",
                "last_error",
                "updated_at",
            ]
        )

        # ====================================================
        # Ejecutar según modo
        # ====================================================

        if batch.is_dry_run:
            process_dry_run_submission(
                submission,
            )

        elif batch.is_live:
            process_live_submission(
                submission,
            )

        else:
            submission.mark_failed(
                (
                    "Unknown Client Submission execution mode: "
                    f"{batch.execution_mode!r}."
                ),
                code="INVALID_EXECUTION_MODE",
            )

            batch.last_error = (
                "Unknown Client Submission execution mode: "
                f"{batch.execution_mode!r}."
            )

            batch.last_activity_at = timezone.now()

            batch.save(
                update_fields=[
                    "last_error",
                    "last_activity_at",
                    "updated_at",
                ]
            )

            create_event(
                batch=batch,
                submission=submission,
                level=ClientSubmissionEvent.Level.ERROR,
                event_type="invalid_execution_mode",
                message=(
                    f"Submission {submission.project_id} "
                    "was blocked because the Batch has an "
                    "invalid execution mode."
                ),
                metadata={
                    "execution_mode": batch.execution_mode,
                },
            )

        # ====================================================
        # Revisar si el proceso debe detenerse
        # ====================================================

        batch.refresh_from_db()

        if batch.status in {
            ClientSubmissionBatch.Status.PAUSED,
            ClientSubmissionBatch.Status.CANCELLED,
            ClientSubmissionBatch.Status.AWAITING_VERIFICATION,
        }:
            return


def process_live_submission(
    submission: ClientSubmission,
):
    """
    Procesa un ClientSubmission en modo LIVE.

    Flujo:

    1. Crea el intento.
    2. Genera las partes ZIP.
    3. Abre y completa el formulario.
    4. Adjunta los ZIP.
    5. Presiona Submit.
    6. Espera la confirmación visual de Smartsheet.
    7. Si aparece CAPTCHA, conserva la sesión del navegador.
    8. Si el CAPTCHA vence o se cierra, reinicia solamente
       este proyecto.
    9. Marca la confirmación del navegador cuando corresponda.
    10. Deja el proyecto esperando confirmación por correo,
        salvo que esta ya exista.
    """

    batch = submission.batch

    attempt = create_attempt(
        submission,
    )

    submission.refresh_from_db()

    local_zip_paths: list[str] = []

    temporary_zips = False

    zip_stats: dict = {}

    zip_filenames: list[str] = []

    verification_session_active = False

    try:
        # ====================================================
        # Preparar ZIP divididos para Smartsheet
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

            if file_size <= 0:
                raise RuntimeError(f"Prepared ZIP part is empty: {path.name}")

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

        # ====================================================
        # Marcar Submission como submitting
        # ====================================================

        submission.mark_submitting()

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "last_activity_at",
                "updated_at",
            ]
        )

        # ====================================================
        # Evento de inicio
        # ====================================================

        create_event(
            batch=batch,
            submission=submission,
            event_type="live_submission_started",
            message=("Live submission started for " f"{submission.project_id}."),
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
        # Visibilidad del navegador
        # ====================================================

        headless = (
            os.getenv(
                "CLIENT_SUBMISSIONS_HEADLESS",
                "1",
            ).strip()
            == "1"
        )

        logger.info(
            (
                "Starting Smartsheet Live submission "
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
        # Ejecutar Playwright LIVE
        # ====================================================

        result = asyncio.run(
            run_smartsheet_live(
                submission=submission,
                attachment_paths=local_zip_paths,
                headless=headless,
            )
        )

        if result is None:
            raise RuntimeError(
                (
                    "Smartsheet automation returned no result. "
                    "Check that run_smartsheet_dry_run() does not "
                    "execute 'return' from inside its finally block."
                )
            )

        # ====================================================
        # Verificación humana
        # ====================================================

        if result.verification_required:
            verification_session_active = True

            submission.refresh_from_db()

            submission.mark_awaiting_verification()

            batch.refresh_from_db()

            batch.mark_awaiting_verification()

            result_metadata = (
                result.metadata
                if isinstance(
                    result.metadata,
                    dict,
                )
                else {}
            )

            verification_stage = str(
                result_metadata.get(
                    "stage",
                    "after_submit",
                )
                or "after_submit"
            ).strip()

            attempt.result = ClientSubmissionAttempt.Result.AWAITING_VERIFICATION

            attempt.browser_url = result.final_url or ""

            attempt.browser_title = result.page_title or ""

            attempt.page_html_snapshot = result.html_snapshot or ""

            attempt.error_details = {
                "zip_parts": zip_filenames,
                "zip_stats": zip_stats,
                "verification_stage": verification_stage,
                "metadata": result_metadata,
            }

            if result.screenshot_path:
                save_attempt_screenshot(
                    attempt,
                    result.screenshot_path,
                )

            attempt.finished_at = timezone.now()

            attempt.save()

            # ================================================
            # Registrar la sesión viva en browser_state
            # ================================================

            submission.refresh_from_db()

            mark_verification_session_available(
                submission,
                stage=verification_stage,
                result_metadata=result_metadata,
            )

            create_event(
                batch=batch,
                submission=submission,
                level=ClientSubmissionEvent.Level.WARNING,
                event_type="verification_required",
                message=(
                    "Human verification is required for " f"{submission.project_id}."
                ),
                metadata={
                    "attempt_number": attempt.attempt_number,
                    "zip_parts": zip_filenames,
                    "zip_stats": zip_stats,
                    "verification_stage": verification_stage,
                    "result_metadata": result_metadata,
                },
            )

            return

        # ====================================================
        # Validar resultado general
        # ====================================================

        if not result.ok:
            raise RuntimeError(
                ("Smartsheet Live submission returned " "an unsuccessful result.")
            )

        # ====================================================
        # Validar archivos adjuntos
        # ====================================================

        if not result.attachments_uploaded:
            raise RuntimeError(
                (
                    "The Smartsheet form was completed, "
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
        # Obtener metadata segura
        # ====================================================

        result_metadata = (
            result.metadata
            if isinstance(
                result.metadata,
                dict,
            )
            else {}
        )

        # ====================================================
        # Validar que Submit fue presionado
        # ====================================================

        submit_clicked = bool(
            result.submit_clicked
            or result_metadata.get(
                "submit_clicked",
                False,
            )
        )

        if not submit_clicked:
            raise RuntimeError(
                (
                    "Smartsheet Live submission did not confirm "
                    "that the Submit button was clicked."
                )
            )

        # ====================================================
        # Validar confirmación del navegador
        # ====================================================

        browser_confirmation_received = bool(
            result.browser_confirmation_received
            or result_metadata.get(
                "browser_confirmation_received",
                False,
            )
        )

        if not browser_confirmation_received:
            raise RuntimeError(
                (
                    "The Submit button was clicked, but "
                    "Smartsheet browser confirmation "
                    "was not detected."
                )
            )

        confirmation_reference = str(
            result.confirmation_reference
            or result_metadata.get(
                "confirmation_reference",
                "",
            )
            or ""
        ).strip()

        confirmation_text = str(
            result.confirmation_text
            or result_metadata.get(
                "confirmation_text",
                "",
            )
            or ""
        ).strip()

        # ====================================================
        # Guardar intento exitoso
        # ====================================================

        attempt.result = ClientSubmissionAttempt.Result.BROWSER_CONFIRMED

        attempt.browser_url = result.final_url or ""

        attempt.browser_title = result.page_title or ""

        attempt.page_html_snapshot = result.html_snapshot or ""

        attempt.error_details = {
            "fields_filled": result.fields_filled,
            "attachments_uploaded": (result.attachments_uploaded),
            "attachment_filenames": (result.attachment_filenames),
            "attachment_count": len(
                result.attachment_filenames,
            ),
            "submit_clicked": submit_clicked,
            "browser_confirmation_received": (browser_confirmation_received),
            "confirmation_reference": confirmation_reference,
            "confirmation_text": confirmation_text,
            "zip_stats": zip_stats,
            "metadata": result_metadata,
        }

        if result.screenshot_path:
            save_attempt_screenshot(
                attempt,
                result.screenshot_path,
            )

        attempt.finished_at = timezone.now()

        attempt.save()

        # ====================================================
        # Marcar confirmación del navegador
        # ====================================================

        submission.refresh_from_db()

        submission.mark_browser_confirmed(
            reference=confirmation_reference,
        )

        batch.refresh_from_db()

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "last_activity_at",
                "updated_at",
            ]
        )

        # ====================================================
        # Evento exitoso
        # ====================================================

        create_event(
            batch=batch,
            submission=submission,
            level=ClientSubmissionEvent.Level.SUCCESS,
            event_type="live_submission_browser_confirmed",
            message=(
                f"Live submission completed for "
                f"{submission.project_id}. "
                "Smartsheet confirmed the browser submission."
            ),
            metadata={
                "attempt_number": attempt.attempt_number,
                "fields_filled": result.fields_filled,
                "attachments_uploaded": (result.attachments_uploaded),
                "attachment_filenames": (result.attachment_filenames),
                "attachment_count": len(
                    result.attachment_filenames,
                ),
                "submit_clicked": submit_clicked,
                "browser_confirmation_received": (browser_confirmation_received),
                "confirmation_reference": (confirmation_reference),
                "submission_status": submission.status,
                "zip_stats": zip_stats,
            },
        )

    # ========================================================
    # Reiniciar solamente este proyecto por CAPTCHA vencido
    # ========================================================

    except SmartsheetRestartSubmissionRequested as exc:
        logger.warning(
            (
                "Restarting only submission %s after "
                "the CAPTCHA session expired or was closed."
            ),
            submission.pk,
        )

        restart_reason = str(
            exc,
        ).strip() or ("The CAPTCHA session expired or was closed.")

        requeue_single_submission_after_captcha_restart(
            submission,
            attempt,
            reason=restart_reason,
        )

        verification_session_active = False

        return

    # ========================================================
    # Error general
    # ========================================================

    except Exception as exc:
        logger.exception(
            "Live submission failed for submission %s",
            submission.pk,
        )

        submission.refresh_from_db()

        # No transformar en FAILED una sesión que sí quedó
        # esperando verificación humana.
        if submission.status != ClientSubmission.Status.AWAITING_VERIFICATION:
            submission.mark_failed(
                str(
                    exc,
                ),
                code=exc.__class__.__name__,
            )

        attempt.refresh_from_db()

        if attempt.result != ClientSubmissionAttempt.Result.AWAITING_VERIFICATION:
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

        batch.refresh_from_db()

        batch.last_error = str(
            exc,
        )

        batch.last_activity_at = timezone.now()

        batch.save(
            update_fields=[
                "last_error",
                "last_activity_at",
                "updated_at",
            ]
        )

        create_event(
            batch=batch,
            submission=submission,
            level=ClientSubmissionEvent.Level.ERROR,
            event_type="live_submission_failed",
            message=("Live submission failed for " f"{submission.project_id}: {exc}"),
            metadata={
                "error_type": (exc.__class__.__name__),
                "attempt_number": attempt.attempt_number,
                "zip_parts": [Path(path).name for path in local_zip_paths],
                "zip_stats": zip_stats,
            },
        )

    finally:
        # ====================================================
        # Eliminar ZIP temporales
        #
        # Si se solicita reiniciar este proyecto, estas partes
        # se eliminan y el próximo intento las generará otra vez.
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

        if verification_session_active:
            logger.info(
                ("Live submission %s remains awaiting " "human verification."),
                submission.pk,
            )


# ============================================================
# Confirmación posterior al CAPTCHA
# ============================================================


async def wait_for_confirmation_after_verification(
    page,
    *,
    timeout_ms: int = 90_000,
) -> dict:
    """
    Espera el resultado del formulario después de que una persona
    haya resuelto el CAPTCHA.

    Esta función NO vuelve a presionar Submit automáticamente.

    El Submit ya pudo haberse presionado antes de que Smartsheet
    mostrara el challenge. Por eso primero se espera una respuesta
    confiable del navegador.
    """

    success_markers = [
        "thank you",
        "your response has been recorded",
        "your response was submitted",
        "your response has been submitted",
        "response submitted",
        "submission received",
        "successfully submitted",
        "form submitted",
    ]

    error_markers = [
        "please complete all required fields",
        "please fill out this field",
        "there was an error submitting",
        "could not submit",
        "submission failed",
        "please try again",
        "exceeds the max file size",
        "file is too large",
    ]

    original_url = page.url

    original_form_count = await page.locator(
        'form[aria-label*="questions in this form" i]'
    ).count()

    submit_button = page.get_by_role(
        "button",
        name="Submit",
        exact=True,
    )

    elapsed_ms = 0

    poll_interval_ms = 1000

    final_body_text = ""

    final_form_count = original_form_count

    submit_button_visible = True

    while elapsed_ms < timeout_ms:
        await page.wait_for_timeout(
            poll_interval_ms,
        )

        elapsed_ms += poll_interval_ms

        challenge_visible = await _detect_verification_challenge(
            page,
        )

        if challenge_visible:
            return {
                "confirmed": False,
                "verification_required": True,
                "confirmation_reference": "",
                "confirmation_text": "",
                "final_url": page.url,
            }

        try:
            final_body_text = await page.locator(
                "body",
            ).inner_text(
                timeout=5000,
            )

        except Exception:
            final_body_text = ""

        normalized_body = final_body_text.lower()

        detected_error = next(
            (marker for marker in error_markers if marker in normalized_body),
            None,
        )

        if detected_error:
            raise RuntimeError(
                "Smartsheet showed an error after human verification. "
                f"Detected message: {detected_error!r}. "
                f"Visible body: {final_body_text[:5000]!r}"
            )

        detected_success = next(
            (marker for marker in success_markers if marker in normalized_body),
            None,
        )

        if detected_success:
            return {
                "confirmed": True,
                "verification_required": False,
                "confirmation_reference": detected_success,
                "confirmation_text": final_body_text[:5000],
                "final_url": page.url,
            }

        try:
            final_form_count = await page.locator(
                'form[aria-label*="questions in this form" i]'
            ).count()

        except Exception:
            final_form_count = 0

        try:
            submit_button_visible = await submit_button.is_visible()

        except Exception:
            submit_button_visible = False

        if (
            original_form_count > 0
            and final_form_count == 0
            and not submit_button_visible
        ):
            return {
                "confirmed": True,
                "verification_required": False,
                "confirmation_reference": ("form_disappeared_after_verification"),
                "confirmation_text": final_body_text[:5000],
                "final_url": page.url,
            }

        if page.url != original_url and not submit_button_visible:
            return {
                "confirmed": True,
                "verification_required": False,
                "confirmation_reference": ("url_changed_after_verification"),
                "confirmation_text": final_body_text[:5000],
                "final_url": page.url,
            }

    return {
        "confirmed": False,
        "verification_required": False,
        "confirmation_reference": "",
        "confirmation_text": final_body_text[:5000],
        "final_url": page.url,
        "final_form_count": final_form_count,
        "submit_button_visible": submit_button_visible,
    }


async def inspect_active_verification_browser(
    submission: ClientSubmission,
) -> dict:
    """
    Inspecciona el navegador vivo asociado al Submission.

    No modifica la base de datos. Solamente devuelve el estado
    actual de Playwright.
    """

    browser_data = get_active_browser(
        submission,
    )

    if not browser_data:
        return {
            "session_available": False,
            "captcha_visible": False,
            "page": None,
            "url": "",
            "title": "",
        }

    page = browser_data.get(
        "page",
    )

    if page is None:
        return {
            "session_available": False,
            "captcha_visible": False,
            "page": None,
            "url": "",
            "title": "",
        }

    try:
        captcha_visible = await _detect_verification_challenge(
            page,
        )

    except Exception:
        logger.exception(
            "Could not inspect CAPTCHA for submission %s.",
            submission.pk,
        )

        captcha_visible = True

    try:
        page_title = await page.title()

    except Exception:
        page_title = ""

    return {
        "session_available": True,
        "captcha_visible": captcha_visible,
        "page": page,
        "url": page.url,
        "title": page_title,
    }


def restart_submission_after_pre_submit_verification(
    submission: ClientSubmission,
):
    """
    Reinicia el proyecto cuando el CAPTCHA apareció antes de que
    el formulario pudiera enviarse.

    Como la ejecución async original ya terminó, el flujo completo
    debe iniciarse otra vez desde el worker.
    """

    now = timezone.now()

    attempt = get_latest_submission_attempt(
        submission,
    )

    if attempt:
        attempt.result = ClientSubmissionAttempt.Result.CANCELLED

        attempt.error_code = "RESTART_AFTER_VERIFICATION"

        attempt.error_message = (
            "The browser challenge was cleared. "
            "The submission will restart from the beginning."
        )

        attempt.finished_at = now

        attempt.save(
            update_fields=[
                "result",
                "error_code",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )

    submission.status = ClientSubmission.Status.PENDING_CLIENT_SUBMISSION

    submission.started_at = None

    submission.form_loaded_at = None

    submission.form_completed_at = None

    submission.verification_completed_at = now

    submission.verification_required_at = None

    submission.submit_clicked_at = None

    submission.finished_at = None

    submission.last_error_code = ""

    submission.last_error_message = ""

    submission.last_error_at = None

    submission.browser_session_key = ""

    set_human_verification_state(
        submission,
        session_available=False,
        session_url="",
        captcha_cleared=True,
        completed_at=now.isoformat(),
        continue_requested_at=None,
        continue_requested_by=None,
        worker_checked_at=now.isoformat(),
        message=("Verification was completed. " "The project was queued to restart."),
    )

    submission.save(
        update_fields=[
            "status",
            "started_at",
            "form_loaded_at",
            "form_completed_at",
            "verification_completed_at",
            "verification_required_at",
            "submit_clicked_at",
            "finished_at",
            "last_error_code",
            "last_error_message",
            "last_error_at",
            "browser_session_key",
            "browser_state",
            "updated_at",
        ]
    )

    batch = submission.batch

    batch.status = ClientSubmissionBatch.Status.PENDING

    batch.paused_at = None

    batch.finished_at = None

    batch.current_submission = None

    batch.last_error = ""

    batch.last_activity_at = now

    batch.worker_identifier = ""

    batch.save(
        update_fields=[
            "status",
            "paused_at",
            "finished_at",
            "current_submission",
            "last_error",
            "last_activity_at",
            "worker_identifier",
            "updated_at",
        ]
    )

    create_event(
        batch=batch,
        submission=submission,
        level=ClientSubmissionEvent.Level.INFO,
        event_type="verification_cleared_restart_queued",
        message=(
            "Human verification was completed for "
            f"{submission.project_id}. "
            "The project was queued to restart."
        ),
        metadata={
            "completed_at": now.isoformat(),
        },
    )


def complete_submission_after_verification(
    submission: ClientSubmission,
    confirmation_result: dict,
):
    """
    Registra una confirmación real de Smartsheet después de que
    el CAPTCHA fue resuelto.
    """

    now = timezone.now()

    confirmation_reference = str(
        confirmation_result.get(
            "confirmation_reference",
            "",
        )
        or ""
    ).strip()

    confirmation_text = str(
        confirmation_result.get(
            "confirmation_text",
            "",
        )
        or ""
    ).strip()

    final_url = str(
        confirmation_result.get(
            "final_url",
            "",
        )
        or ""
    ).strip()

    attempt = get_latest_submission_attempt(
        submission,
    )

    if attempt:
        attempt.result = ClientSubmissionAttempt.Result.BROWSER_CONFIRMED

        attempt.browser_url = final_url

        error_details = (
            dict(
                attempt.error_details,
            )
            if isinstance(
                attempt.error_details,
                dict,
            )
            else {}
        )

        attempt.error_details = {
            **error_details,
            "verification_completed_at": now.isoformat(),
            "browser_confirmation_received": True,
            "confirmation_reference": confirmation_reference,
            "confirmation_text": confirmation_text,
        }

        attempt.finished_at = now

        attempt.save(
            update_fields=[
                "result",
                "browser_url",
                "error_details",
                "finished_at",
                "updated_at",
            ]
        )

    set_human_verification_state(
        submission,
        session_available=False,
        session_url="",
        captcha_cleared=True,
        completed_at=now.isoformat(),
        continue_requested_at=None,
        continue_requested_by=None,
        worker_checked_at=now.isoformat(),
        message=(
            "Human verification was completed and "
            "Smartsheet confirmed the submission."
        ),
    )

    submission.verification_completed_at = now

    submission.save(
        update_fields=[
            "verification_completed_at",
            "browser_state",
            "updated_at",
        ]
    )

    submission.mark_browser_confirmed(
        reference=confirmation_reference,
    )

    batch = submission.batch

    batch.status = ClientSubmissionBatch.Status.PENDING

    batch.paused_at = None

    batch.current_submission = None

    batch.worker_identifier = ""

    batch.last_error = ""

    batch.last_activity_at = now

    batch.save(
        update_fields=[
            "status",
            "paused_at",
            "current_submission",
            "worker_identifier",
            "last_error",
            "last_activity_at",
            "updated_at",
        ]
    )

    create_event(
        batch=batch,
        submission=submission,
        level=ClientSubmissionEvent.Level.SUCCESS,
        event_type="verification_completed_browser_confirmed",
        message=(
            "Human verification was completed for "
            f"{submission.project_id}. "
            "Smartsheet confirmed the submission."
        ),
        metadata={
            "confirmation_reference": confirmation_reference,
            "final_url": final_url,
            "completed_at": now.isoformat(),
        },
    )


def process_single_pending_verification(
    submission: ClientSubmission,
) -> bool:
    """
    Procesa una solicitud de Continue o Cancel correspondiente
    a un Submission que quedó esperando CAPTCHA.
    """

    submission.refresh_from_db()

    verification_state = get_human_verification_state(
        submission,
    )

    continue_requested_at = verification_state.get(
        "continue_requested_at",
    )

    cancel_requested_at = verification_state.get(
        "cancel_requested_at",
    )

    session_available = bool(
        verification_state.get(
            "session_available",
            False,
        )
    )

    if cancel_requested_at:
        browser_data = get_active_browser(
            submission,
        )

        if browser_data:
            asyncio.run(
                close_active_browser(
                    submission,
                )
            )

        now = timezone.now()

        set_human_verification_state(
            submission,
            session_available=False,
            session_url="",
            worker_checked_at=now.isoformat(),
            message=("The active browser was closed after cancellation."),
        )

        submission.save(
            update_fields=[
                "browser_state",
                "updated_at",
            ]
        )

        return True

    if not continue_requested_at:
        return False

    if not session_available:
        now = timezone.now()

        set_human_verification_state(
            submission,
            worker_checked_at=now.isoformat(),
            message=(
                "Continuation was requested, but the active "
                "browser session is no longer available."
            ),
        )

        submission.save(
            update_fields=[
                "browser_state",
                "updated_at",
            ]
        )

        return False

    inspection = asyncio.run(
        inspect_active_verification_browser(
            submission,
        )
    )

    now = timezone.now()

    if not inspection.get(
        "session_available",
        False,
    ):
        set_human_verification_state(
            submission,
            session_available=False,
            session_url="",
            worker_checked_at=now.isoformat(),
            message=(
                "The browser session is no longer available. "
                "The project must be restarted."
            ),
        )

        submission.save(
            update_fields=[
                "browser_state",
                "updated_at",
            ]
        )

        return False

    if inspection.get(
        "captcha_visible",
        False,
    ):
        set_human_verification_state(
            submission,
            worker_checked_at=now.isoformat(),
            captcha_cleared=False,
            message=(
                "The CAPTCHA is still visible. "
                "Complete it before pressing Continue again."
            ),
        )

        submission.save(
            update_fields=[
                "browser_state",
                "updated_at",
            ]
        )

        return False

    stage = str(
        verification_state.get(
            "stage",
            "",
        )
        or ""
    ).strip()

    if stage == "after_submit":
        page = inspection["page"]

        confirmation_result = asyncio.run(
            wait_for_confirmation_after_verification(
                page,
            )
        )

        if confirmation_result.get(
            "verification_required",
            False,
        ):
            set_human_verification_state(
                submission,
                worker_checked_at=timezone.now().isoformat(),
                captcha_cleared=False,
                message=("Smartsheet is still requesting verification."),
            )

            submission.save(
                update_fields=[
                    "browser_state",
                    "updated_at",
                ]
            )

            return False

        if not confirmation_result.get(
            "confirmed",
            False,
        ):
            set_human_verification_state(
                submission,
                worker_checked_at=timezone.now().isoformat(),
                captcha_cleared=True,
                message=(
                    "The CAPTCHA is no longer visible, but "
                    "Smartsheet has not confirmed the submission yet."
                ),
            )

            submission.save(
                update_fields=[
                    "browser_state",
                    "updated_at",
                ]
            )

            return False

        complete_submission_after_verification(
            submission,
            confirmation_result,
        )

        asyncio.run(
            close_active_browser(
                submission,
            )
        )

        return True

    asyncio.run(
        close_active_browser(
            submission,
        )
    )

    restart_submission_after_pre_submit_verification(
        submission,
    )

    return True


def process_pending_verifications() -> bool:
    """
    Revisa submissions que están esperando una acción humana.

    También incluye submissions cancelados cuya sesión activa
    todavía deba cerrarse.
    """

    candidates = (
        ClientSubmission.objects.select_related(
            "batch",
        )
        .filter(
            status__in=[
                ClientSubmission.Status.AWAITING_VERIFICATION,
                ClientSubmission.Status.CANCELLED,
            ]
        )
        .order_by(
            "verification_required_at",
            "id",
        )
    )

    found_action = False

    for submission in candidates:
        verification_state = get_human_verification_state(
            submission,
        )

        has_continue_request = bool(
            verification_state.get(
                "continue_requested_at",
            )
        )

        has_cancel_request = bool(
            verification_state.get(
                "cancel_requested_at",
            )
        )

        session_available = bool(
            verification_state.get(
                "session_available",
                False,
            )
        )

        if not (
            has_continue_request
            or has_cancel_request
            or (
                submission.status == ClientSubmission.Status.CANCELLED
                and session_available
            )
        ):
            continue

        try:
            processed = process_single_pending_verification(
                submission,
            )

            if processed:
                found_action = True

        except Exception as exc:
            logger.exception(
                "Could not process human verification for submission %s.",
                submission.pk,
            )

            submission.refresh_from_db()

            now = timezone.now()

            set_human_verification_state(
                submission,
                worker_checked_at=now.isoformat(),
                message=(
                    "The worker could not continue verification. " f"Error: {exc}"
                ),
            )

            submission.last_error_code = exc.__class__.__name__

            submission.last_error_message = str(
                exc,
            )

            submission.last_error_at = now

            submission.save(
                update_fields=[
                    "browser_state",
                    "last_error_code",
                    "last_error_message",
                    "last_error_at",
                    "updated_at",
                ]
            )

            create_event(
                batch=submission.batch,
                submission=submission,
                level=ClientSubmissionEvent.Level.ERROR,
                event_type="verification_worker_failed",
                message=(
                    "The worker could not process human verification "
                    f"for {submission.project_id}: {exc}"
                ),
                metadata={
                    "error_type": exc.__class__.__name__,
                    "error": str(
                        exc,
                    ),
                },
            )

    return found_action


# ============================================================
# Una iteración
# ============================================================


def run_once() -> bool:
    """
    Ejecuta una iteración completa del worker.

    Orden:

    1. Revisa verificaciones humanas pendientes.
    2. Busca un Batch nuevo en estado PENDING.
    3. Procesa el Batch cuando exista.

    Retorna:
        True  -> realizó alguna acción.
        False -> no había trabajo.
    """

    verification_work_found = process_pending_verifications()

    batch_id = claim_next_pending_batch()

    if not batch_id:
        return verification_work_found

    process_batch(
        batch_id,
    )

    return True
