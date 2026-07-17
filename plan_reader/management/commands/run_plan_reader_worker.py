from __future__ import annotations

import logging
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils import timezone

from client_submissions.automation.worker import \
    run_once as run_client_submission_once
from plan_reader.models import PlanReaderJob
from plan_reader.services.processor import process_plan_reader_job

logger = logging.getLogger(__name__)


def _env_bool(
    name: str,
    *,
    default: bool,
) -> bool:
    """
    Lee una variable booleana desde:

    1. Variables de entorno.
    2. Django settings.
    3. Valor predeterminado.

    Valores verdaderos:
        1, true, yes, on, y

    Valores falsos:
        0, false, no, off, n
    """

    raw_value = os.getenv(name)

    if raw_value is None:
        raw_value = getattr(
            settings,
            name,
            default,
        )

    if isinstance(raw_value, bool):
        return raw_value

    normalized_value = str(raw_value).strip().lower()

    if normalized_value in {
        "1",
        "true",
        "yes",
        "on",
        "y",
    }:
        return True

    if normalized_value in {
        "0",
        "false",
        "no",
        "off",
        "n",
        "",
    }:
        return False

    logger.warning(
        "Invalid boolean value for %s=%r. Using default=%s.",
        name,
        raw_value,
        default,
    )

    return default


class Command(BaseCommand):
    """
    Worker compartido de Hyperlink.

    Atiende dos colas completamente independientes:

    1. Client Submissions
       - Busca ClientSubmissionBatch pendientes.
       - Procesa como máximo un Batch por ciclo.
       - Ejecuta Playwright y genera/sube el ZIP correspondiente.

    2. Plan Reader
       - Busca PlanReaderJob pendientes.
       - Procesa como máximo --limit jobs por ciclo.

    Una solicitud de Client Submission no activa una lectura
    de planos.

    El worker revisa ambas colas y únicamente procesa aquella
    que tenga trabajo pendiente.

    Variables disponibles:

        CLIENT_SUBMISSION_WORKER_ENABLED
        PLAN_READER_WORKER_ENABLED
    """

    help = (
        "Runs the shared Hyperlink background worker for "
        "Client Submissions and Plan Reader."
    )

    # ========================================================
    # Argumentos
    # ========================================================

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--sleep",
            type=float,
            default=10.0,
            help=(
                "Seconds to wait when neither queue contains work. "
                "Default: 10 seconds."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=1,
            help=("Maximum Plan Reader jobs processed per cycle. " "Default: 1."),
        )

        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one complete polling cycle and exit.",
        )

    # ========================================================
    # Worker principal
    # ========================================================

    def handle(
        self,
        *args,
        **options,
    ):
        sleep_seconds = max(
            float(
                options.get(
                    "sleep",
                    10.0,
                )
                or 10.0
            ),
            0.5,
        )

        plan_limit = max(
            int(
                options.get(
                    "limit",
                    1,
                )
                or 1
            ),
            1,
        )

        run_only_once = bool(
            options.get(
                "once",
                False,
            )
        )

        client_submission_enabled = _env_bool(
            "CLIENT_SUBMISSION_WORKER_ENABLED",
            default=True,
        )

        plan_reader_enabled = _env_bool(
            "PLAN_READER_WORKER_ENABLED",
            default=True,
        )

        if not client_submission_enabled and not plan_reader_enabled:
            self.stdout.write(
                self.style.WARNING(
                    "Client Submissions and Plan Reader are both disabled. "
                    "Worker will exit."
                )
            )
            return

        enabled_queues = []

        if client_submission_enabled:
            enabled_queues.append(
                "Client Submissions",
            )

        if plan_reader_enabled:
            enabled_queues.append(
                "Plan Reader",
            )

        disabled_queues = []

        if not client_submission_enabled:
            disabled_queues.append(
                "Client Submissions",
            )

        if not plan_reader_enabled:
            disabled_queues.append(
                "Plan Reader",
            )

        self.stdout.write(
            self.style.SUCCESS("Hyperlink shared background worker started.")
        )

        self.stdout.write(
            self.style.SUCCESS(f"Enabled queues: {', '.join(enabled_queues)}.")
        )

        if disabled_queues:
            self.stdout.write(
                self.style.WARNING(f"Disabled queues: {', '.join(disabled_queues)}.")
            )

        self.stdout.write(
            (
                f"Idle sleep: {sleep_seconds} second(s). "
                f"Plan Reader limit: {plan_limit}. "
                f"Mode: "
                f"{'single cycle' if run_only_once else 'continuous'}."
            )
        )

        try:
            while True:
                processed_any = False

                close_old_connections()

                # ====================================================
                # 1. Client Submissions
                # ====================================================

                if client_submission_enabled:
                    try:
                        client_submission_processed = (
                            self._process_client_submission_queue()
                        )

                        if client_submission_processed:
                            processed_any = True

                    except Exception:
                        logger.exception("Unexpected Client Submissions worker error.")

                        self.stderr.write(
                            self.style.ERROR(
                                (
                                    "Client Submissions cycle failed. "
                                    "Review the traceback above."
                                )
                            )
                        )

                    finally:
                        close_old_connections()

                # ====================================================
                # 2. Plan Reader
                # ====================================================

                if plan_reader_enabled:
                    try:
                        plan_reader_processed = self._process_plan_reader_queue(
                            limit=plan_limit,
                        )

                        if plan_reader_processed:
                            processed_any = True

                    except Exception:
                        logger.exception("Unexpected Plan Reader worker error.")

                        self.stderr.write(
                            self.style.ERROR(
                                (
                                    "Plan Reader cycle failed. "
                                    "Review the traceback above."
                                )
                            )
                        )

                    finally:
                        close_old_connections()

                # ====================================================
                # Una sola iteración
                # ====================================================

                if run_only_once:
                    break

                # ====================================================
                # Esperar solamente cuando no hubo trabajo
                # ====================================================

                if not processed_any:
                    time.sleep(
                        sleep_seconds,
                    )

        except KeyboardInterrupt:
            self.stdout.write("")

            self.stdout.write(
                self.style.WARNING("Hyperlink shared background worker stopped.")
            )

        finally:
            close_old_connections()

    # ========================================================
    # Client Submissions
    # ========================================================

    def _process_client_submission_queue(
        self,
    ) -> bool:
        """
        Ejecuta una iteración de Client Submissions.

        run_client_submission_once() busca y reclama un único
        ClientSubmissionBatch con estado PENDING.

        Retorna:

            True:
                encontró y procesó un Batch.

            False:
                no existían Batches pendientes.
        """

        found_work = run_client_submission_once()

        if not found_work:
            return False

        self.stdout.write(
            self.style.SUCCESS(
                (f"[{timezone.now()}] " "Client Submission Batch processed.")
            )
        )

        return True

    # ========================================================
    # Plan Reader — reclamar jobs
    # ========================================================

    def _claim_pending_plan_reader_jobs(
        self,
        *,
        limit: int,
    ) -> list[PlanReaderJob]:
        """
        Reclama PlanReaderJob pendientes de forma segura.

        Protecciones:

        - select_for_update()
        - skip_locked=True
        - cambio a PROCESSING dentro de la transacción

        Esto evita que dos workers procesen el mismo job.
        """

        with transaction.atomic():
            jobs = list(
                PlanReaderJob.objects.select_for_update(
                    skip_locked=True,
                )
                .filter(
                    status=PlanReaderJob.STATUS_PENDING,
                )
                .order_by(
                    "created_at",
                    "id",
                )[:limit]
            )

            if not jobs:
                return []

            now = timezone.now()

            for job in jobs:
                job.status = PlanReaderJob.STATUS_PROCESSING

                job.started_at = now

                job.completed_at = None

                job.error_message = ""

                job.save(
                    update_fields=[
                        "status",
                        "started_at",
                        "completed_at",
                        "error_message",
                        "updated_at",
                    ]
                )

            return jobs

    # ========================================================
    # Plan Reader — procesar cola
    # ========================================================

    def _process_plan_reader_queue(
        self,
        *,
        limit: int,
    ) -> bool:
        """
        Procesa los PlanReaderJob reclamados durante este ciclo.

        No se inicia ninguna lectura de planos cuando la cola
        de Plan Reader está vacía.
        """

        jobs = self._claim_pending_plan_reader_jobs(
            limit=limit,
        )

        if not jobs:
            return False

        for job in jobs:
            close_old_connections()

            self.stdout.write(
                self.style.WARNING(
                    (f"[{timezone.now()}] " f"Processing PlanReaderJob #{job.pk}.")
                )
            )

            try:
                process_plan_reader_job(
                    job.pk,
                    allow_processing=True,
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        (f"[{timezone.now()}] " f"Finished PlanReaderJob #{job.pk}.")
                    )
                )

            except Exception as exc:
                logger.exception(
                    "PlanReaderJob #%s failed.",
                    job.pk,
                )

                self.stderr.write(
                    self.style.ERROR(
                        (
                            f"[{timezone.now()}] "
                            f"Failed PlanReaderJob #{job.pk}: "
                            f"{exc}"
                        )
                    )
                )

            finally:
                close_old_connections()

        return True
