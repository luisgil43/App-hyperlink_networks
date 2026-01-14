from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class Route(models.Model):
    """
    Ruta lineal (tubería) definida por start_ft y end_ft (en pies).
    La UI la renderiza como segmentos, por ejemplo cada 50 ft.
    """
    name = models.CharField(max_length=200)
    start_ft = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    end_ft = models.DecimalField(max_digits=10, decimal_places=2)
    segment_length_ft = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("50"))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-id",)

    def __str__(self) -> str:
        return f"{self.name} ({self.total_length_ft} ft)"

    @property
    def total_length_ft(self) -> Decimal:
        return (self.end_ft or Decimal("0")) - (self.start_ft or Decimal("0"))

    def clean(self):
        if self.end_ft is None:
            raise ValidationError("end_ft es requerido.")
        if self.end_ft <= self.start_ft:
            raise ValidationError("end_ft debe ser mayor que start_ft.")
        if self.segment_length_ft <= 0:
            raise ValidationError("segment_length_ft debe ser mayor a 0.")

    def regenerate_segments(self):
        """
        (Re)crea segmentos en base a start/end/segment_length_ft.
        Si ya existían, los borra y crea de nuevo (simple y robusto para MVP).
        """
        total = self.total_length_ft
        if total <= 0:
            return

        seg_len = self.segment_length_ft
        if seg_len <= 0:
            seg_len = Decimal("50")

        with transaction.atomic():
            RouteSegment.objects.filter(route=self).delete()

            idx = 1
            current = self.start_ft
            while current < self.end_ft:
                nxt = current + seg_len
                if nxt > self.end_ft:
                    nxt = self.end_ft

                RouteSegment.objects.create(
                    route=self,
                    index=idx,
                    from_ft=current,
                    to_ft=nxt,
                )
                idx += 1
                current = nxt


class Stage(models.Model):
    """
    Etapas de cadena de producción (Marking, Drill, Duct, etc.)
    Puedes definir un 'requires_prev_stage' para aplicar gating.
    """
    code = models.SlugField(max_length=60, unique=True)  # ej: "marking", "drill"
    name = models.CharField(max_length=120)              # ej: "Marking / Locate"
    order = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    # Gating simple: para marcar esta etapa como Done, requiere que la etapa previa esté Done.
    requires_prev_stage = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="unlocks_stages",
    )

    class Meta:
        ordering = ("order", "id")

    def __str__(self) -> str:
        return self.name


class RouteSegment(models.Model):
    """
    Segmento de la ruta.
    """
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="segments")
    index = models.PositiveIntegerField()
    from_ft = models.DecimalField(max_digits=10, decimal_places=2)
    to_ft = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = (("route", "index"),)
        ordering = ("index",)

    def __str__(self) -> str:
        return f"{self.route.name} - Seg {self.index}: {self.from_ft}-{self.to_ft} ft"

    @property
    def length_ft(self) -> Decimal:
        return (self.to_ft or Decimal("0")) - (self.from_ft or Decimal("0"))


class SegmentStageProgress(models.Model):
    """
    Progreso por etapa y por segmento.
    """
    STATUS_NOT_STARTED = "not_started"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_DONE = "done"
    STATUS_BLOCKED = "blocked"

    STATUS_CHOICES = (
        (STATUS_NOT_STARTED, "Not started"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_DONE, "Done"),
        (STATUS_BLOCKED, "Blocked"),
    )

    segment = models.ForeignKey(RouteSegment, on_delete=models.CASCADE, related_name="progress_items")
    stage = models.ForeignKey(Stage, on_delete=models.CASCADE, related_name="progress_items")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NOT_STARTED)

    notes = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("segment", "stage"),)
        ordering = ("segment__index", "stage__order")

    def __str__(self) -> str:
        return f"{self.segment} | {self.stage.code}={self.status}"


def seed_default_stages():
    """
    Crea etapas base si no existen (MVP).
    Marking -> Drill -> Duct -> Backfill -> Restoration -> QC/As-built
    """
    # Crear/obtener primero
    marking, _ = Stage.objects.get_or_create(code="marking", defaults={"name": "Marking / Locate", "order": 10})
    drill, _ = Stage.objects.get_or_create(code="drill", defaults={"name": "Drill / Trenching", "order": 20})
    duct, _ = Stage.objects.get_or_create(code="duct", defaults={"name": "Duct Install", "order": 30})
    backfill, _ = Stage.objects.get_or_create(code="backfill", defaults={"name": "Backfill / Compaction", "order": 40})
    restoration, _ = Stage.objects.get_or_create(code="restoration", defaults={"name": "Restoration", "order": 50})
    qc, _ = Stage.objects.get_or_create(code="qc", defaults={"name": "QC / As-built", "order": 60})

    # Gating
    if drill.requires_prev_stage_id != marking.id:
        drill.requires_prev_stage = marking
        drill.save(update_fields=["requires_prev_stage"])

    if duct.requires_prev_stage_id != drill.id:
        duct.requires_prev_stage = drill
        duct.save(update_fields=["requires_prev_stage"])

    if backfill.requires_prev_stage_id != duct.id:
        backfill.requires_prev_stage = duct
        backfill.save(update_fields=["requires_prev_stage"])

    if restoration.requires_prev_stage_id != backfill.id:
        restoration.requires_prev_stage = backfill
        restoration.save(update_fields=["requires_prev_stage"])

    if qc.requires_prev_stage_id != restoration.id:
        qc.requires_prev_stage = restoration
        qc.save(update_fields=["requires_prev_stage"])


@receiver(post_save, sender=Route)
def _route_post_save(sender, instance: Route, created: bool, **kwargs):
    # auto-seed stages y segmentos al crear ruta
    if created:
        seed_default_stages()
        instance.regenerate_segments()