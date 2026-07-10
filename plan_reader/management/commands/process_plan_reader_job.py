from django.core.management.base import BaseCommand, CommandError

from plan_reader.models import PlanReaderJob
from plan_reader.services.processor import process_plan_reader_job


class Command(BaseCommand):
    help = "Process a Plan Reader job locally."

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    def handle(self, *args, **options):
        job_id = options["job_id"]

        if not PlanReaderJob.objects.filter(id=job_id).exists():
            raise CommandError(f"PlanReaderJob #{job_id} does not exist.")

        self.stdout.write(self.style.WARNING(f"Processing PlanReaderJob #{job_id}..."))

        try:
            job = process_plan_reader_job(job_id)
        except Exception as exc:
            raise CommandError(f"Processing failed: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"PlanReaderJob #{job.id} processed. "
                f"Status: {job.status}. "
                f"Pages: {job.processed_pages}/{job.total_pages}. "
                f"Failed: {job.failed_pages}. "
                f"Items: {job.items.count()}."
            )
        )
