# plan_reader/management/commands/run_hyperlink_worker.py

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils import timezone

from client_submissions.automation.worker import \
    run_once as run_client_submission_once
from plan_reader.models import PlanReaderJob
from plan_reader.services.processor import process_plan_reader_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Worker compartido de Hyperlink.

    Atiende dos colas independientes:

    1. Plan Reader
       - Busca PlanReaderJob pendientes.
       - Procesa como máximo --plan-limit por ciclo.

    2. Client Submissions
       - Busca ClientSubmissionBatch pendientes.
       - Procesa como máximo un Batch por ciclo mediante
         client_submissions.automation.worker.run_once().

    Los procesos no se mezclan.

    Una solicitud de Client Submission no activa una lectura
    de planos. El worker simplemente revisa ambas colas y solo
    ejecuta la que tenga trabajo pendiente.
    """

    help = (
        "Runs the shared Hyperlink background worker for "
        "Plan Reader and Client Submissions."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--sleep",
            type=float,
            default=5.0,
            help=(
                "Seconds to wait when neither queue contains work. "
                "Default: 5 seconds."
            ),
        )

        parser.add_argument(
            "--plan-limit",
            type=int,
            default=1,
            help=("Maximum Plan Reader jobs processed per cycle. " "Default: 1."),
        )

        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one complete polling cycle and exit.",
        )

    def handle(
        self,
        *args,
        **options,
    ):
        sleep_seconds = max(
            float(
                options.get(
                    "sleep",
                    5.0,
                )
            ),
            0.5,
        )

        plan_limit = max(
            int(
                options.get(
                    "plan_limit",
                    1,
                )
            ),
            1,
        )

        run_only_once = bool(
            options.get(
                "once",
                False,
            )
        )

        self.stdout.write(
            self.style.SUCCESS("Hyperlink shared background worker started.")
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

                try:
                    # =========================================
                    # 1. Client Submissions
                    #
                    # run_once() reclama un único Batch PENDING
                    # usando select_for_update(skip_locked=True).
                    # =========================================

                    client_submission_processed = (
                        self._process_client_submission_queue()
                    )

                    if client_submission_processed:
                        processed_any = True

                except Exception:
                    logger.exception("Unexpected Client Submissions worker error.")

                    self.stderr.write(
                        self.style.ERROR(
                            "Client Submissions cycle failed. "
                            "Review the traceback above."
                        )
                    )

                finally:
                    close_old_connections()

                try:
                    # =========================================
                    # 2. Plan Reader
                    #
                    # Solo procesa jobs si realmente existen
                    # PlanReaderJob pendientes.
                    # =========================================

                    plan_reader_processed = self._process_plan_reader_queue(
                        limit=plan_limit,
                    )

                    if plan_reader_processed:
                        processed_any = True

                except Exception:
                    logger.exception("Unexpected Plan Reader worker error.")

                    self.stderr.write(
                        self.style.ERROR(
                            "Plan Reader cycle failed. " "Review the traceback above."
                        )
                    )

                finally:
                    close_old_connections()

                if run_only_once:
                    break

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

        Retorna:

            True:
                encontró y procesó un Batch pendiente.

            False:
                no existían Batches pendientes.
        """

        found_work = run_client_submission_once()

        if found_work:
            self.stdout.write(
                self.style.SUCCESS(
                    (f"[{timezone.now()}] " "Client Submission Batch processed.")
                )
            )

            return True

        return False

    # ========================================================
    # Plan Reader
    # ========================================================

    def _claim_pending_plan_reader_jobs(
        self,
        *,
        limit: int,
    ) -> list[PlanReaderJob]:
        """
        Reclama PlanReaderJob pendientes de forma segura.

        select_for_update(skip_locked=True) evita que otro
        worker tome los mismos jobs simultáneamente.
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

    def _process_plan_reader_queue(
        self,
        *,
        limit: int,
    ) -> bool:
        """
        Procesa los PlanReaderJob reclamados durante este ciclo.

        No se ejecuta ninguna lectura de plano si la cola está
        vacía.
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
