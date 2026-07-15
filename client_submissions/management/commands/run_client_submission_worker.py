# client_submissions/management/commands/run_client_submission_worker.py

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from client_submissions.automation.worker import run_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Runs the Client Submissions worker continuously "
        "and processes pending batches."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--sleep",
            type=float,
            default=3.0,
            help=(
                "Seconds to wait when there are no pending batches. "
                "Default: 3 seconds."
            ),
        )

        parser.add_argument(
            "--once",
            action="store_true",
            help=("Run a single worker iteration and exit."),
        )

    def handle(
        self,
        *args,
        **options,
    ):
        sleep_seconds = max(
            float(
                options["sleep"],
            ),
            0.5,
        )

        run_only_once = bool(
            options["once"],
        )

        self.stdout.write(self.style.SUCCESS("Client Submissions worker started."))

        self.stdout.write(
            ("Mode: " f"{'single iteration' if run_only_once else 'continuous'}")
        )

        self.stdout.write(("Idle sleep: " f"{sleep_seconds} second(s)"))

        try:
            while True:
                try:
                    found_work = run_once()

                    if found_work:
                        self.stdout.write(
                            self.style.SUCCESS("Client Submission Batch processed.")
                        )

                    else:
                        self.stdout.write("No pending Client Submission Batch.")

                except Exception:
                    logger.exception("Unexpected Client Submissions worker error.")

                    self.stderr.write(
                        self.style.ERROR(
                            "Worker iteration failed. " "Review the traceback above."
                        )
                    )

                if run_only_once:
                    break

                time.sleep(
                    sleep_seconds,
                )

        except KeyboardInterrupt:
            self.stdout.write("")

            self.stdout.write(self.style.WARNING("Client Submissions worker stopped."))
