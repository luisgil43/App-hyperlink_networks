import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from plan_reader.models import PlanReaderJob
from plan_reader.services.processor import process_plan_reader_job


class Command(BaseCommand):
    help = "Runs the Plan Reader worker loop and processes pending jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sleep",
            type=int,
            default=10,
            help="Seconds to wait between polling cycles.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run one cycle and exit.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=1,
            help="Maximum pending jobs to process per cycle.",
        )

    def handle(self, *args, **options):
        sleep_seconds = int(options["sleep"] or 10)
        run_once = bool(options["once"])
        limit = int(options["limit"] or 1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Plan Reader worker started. sleep={sleep_seconds}s limit={limit}"
            )
        )

        while True:
            processed_any = self._run_cycle(limit=limit)

            if run_once:
                break

            if not processed_any:
                time.sleep(sleep_seconds)

    def _claim_pending_jobs(self, limit=1):
        """
        Toma jobs pending de forma segura para worker.

        Importante para producción:
        - Usa select_for_update(skip_locked=True)
        - Marca los jobs como processing dentro de la misma transacción
        - Evita que otro worker tome el mismo job
        """
        with transaction.atomic():
            jobs = list(
                PlanReaderJob.objects.select_for_update(skip_locked=True)
                .filter(status=PlanReaderJob.STATUS_PENDING)
                .order_by("created_at")[:limit]
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

    def _run_cycle(self, limit=1):
        jobs = self._claim_pending_jobs(limit=limit)

        if not jobs:
            return False

        for job in jobs:
            self.stdout.write(
                self.style.WARNING(
                    f"[{timezone.now()}] Processing PlanReaderJob #{job.id}"
                )
            )

            try:
                process_plan_reader_job(job.id)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{timezone.now()}] Finished PlanReaderJob #{job.id}"
                    )
                )

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f"[{timezone.now()}] Failed PlanReaderJob #{job.id}: {exc}"
                    )
                )

        return True
