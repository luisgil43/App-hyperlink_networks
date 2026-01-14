from django.core.management.base import BaseCommand
from django.utils import timezone

from notifications.models import CronDailyRun

JOB_NAME = "fleet_service_notifications"

class Command(BaseCommand):
    help = "Daily notifications job (stub). Prevents duplicates per day. Phase 0."

    def handle(self, *args, **options):
        today = timezone.localdate()

        obj, created = CronDailyRun.objects.get_or_create(
            name=JOB_NAME,
            run_date=today,
            defaults={"ok": True, "log": "Phase 0 stub executed (no deliveries)."},
        )

        if not created:
            self.stdout.write(self.style.WARNING(
                f"[SKIP] {JOB_NAME} already executed for {today}."
            ))
            return

        # ✅ Aquí en Phase 7+ llamaremos el motor real:
        # - evaluar servicios due soon/due/overdue
        # - crear eventos
        # - enviar email/telegram
        self.stdout.write(self.style.SUCCESS(
            f"[OK] {JOB_NAME} executed for {today}. (stub)"
        ))