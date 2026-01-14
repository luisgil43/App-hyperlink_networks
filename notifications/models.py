from django.db import models
from django.utils import timezone


class CronDailyRun(models.Model):
    """
    Registra ejecuciones diarias para evitar duplicados (estilo GZ: CronDiarioEjecutado).
    """
    name = models.CharField(max_length=80, db_index=True)  # ej: "fleet_service_notifications"
    run_date = models.DateField(default=timezone.localdate, db_index=True)
    ok = models.BooleanField(default=True)
    log = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name", "run_date"], name="uniq_cron_name_date"),
        ]
        ordering = ("-run_date", "-id")

    def __str__(self):
        return f"{self.name} @ {self.run_date}"