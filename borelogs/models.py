# borelogs/models.py
from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class BoreLogTemplateConfig(models.Model):
    """
    Configuración global del template DOCX (1 sola).
    - Guardamos el cell_map y header_map para no escanear cada vez.
    - El template físico se mantiene fijo en /static/ (archivo en repo).
    """

    # Mapeo de rods: {"1": {"depth": [t,r,c], "pitch": [...], "station": [...]}, ...}
    rod_cell_map = models.JSONField("Rod Cell Map", blank=True, null=True)

    # Mapeo del header: {"rod_length": [t,r,c], "driller_name": [...], "vendor_name": [...], "project_name": [...]}
    header_cell_map = models.JSONField("Header Cell Map", blank=True, null=True)

    updated_at = models.DateTimeField("Updated At", auto_now=True)

    class Meta:
        verbose_name = "Bore Log Template Config"
        verbose_name_plural = "Bore Log Template Config"

    def __str__(self) -> str:
        return "BoreLogTemplateConfig"


class BoreLog(models.Model):
    """
    Bore Log principal.
    - Header se llena en la web y se usa para el DOCX
    - NO subimos template por BoreLog: es global/fijo
    - ✅ NUEVO: cada BoreLog pertenece a un Billing (SesionBilling)
    """

    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"

    STATUS_CHOICES = (
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
    )

    # ✅ RELACIÓN: BoreLog por Billing/Sesión
    # Lo dejamos nullable para migrar sin romper BoreLogs antiguos.
    sesion = models.ForeignKey(
        "operaciones.SesionBilling",
        on_delete=models.CASCADE,
        related_name="borelogs",
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Billing Session",
    )

    project_name = models.CharField("Project Name", max_length=255, db_index=True)
    rod_length = models.CharField("Rod Length", max_length=50, blank=True, default="")
    driller_name = models.CharField("Driller Name", max_length=255, blank=True, default="")
    vendor_name = models.CharField("Vendor Name", max_length=255, blank=True, default="")

    status = models.CharField("Status", max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    notes = models.TextField("Notes", blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="borelogs_created",
        verbose_name="Created By",
    )
    created_at = models.DateTimeField("Created At", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("Updated At", auto_now=True)

    class Meta:
        verbose_name = "Bore Log"
        verbose_name_plural = "Bore Logs"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"BoreLog #{self.pk} - {self.project_name}"


class BoreLogEntry(models.Model):
    """
    Historial por rod (auditoría).
    """

    SOURCE_WEB = "web"
    SOURCE_TELEGRAM = "telegram"

    SOURCE_CHOICES = (
        (SOURCE_WEB, "Web"),
        (SOURCE_TELEGRAM, "Telegram"),
    )

    borelog = models.ForeignKey(BoreLog, on_delete=models.CASCADE, related_name="entries", verbose_name="Bore Log")

    rod_number = models.PositiveSmallIntegerField(
        "Rod #",
        validators=[MinValueValidator(1), MaxValueValidator(500)],
        db_index=True,
    )

    depth = models.CharField("Depth", max_length=50, blank=True, default="")
    pitch = models.CharField("Pitch", max_length=50, blank=True, default="")
    station = models.CharField("Station", max_length=50, blank=True, default="")

    source = models.CharField("Source", max_length=20, choices=SOURCE_CHOICES, default=SOURCE_WEB, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="borelog_entries",
        verbose_name="Created By",
    )
    created_at = models.DateTimeField("Created At", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Bore Log Entry"
        verbose_name_plural = "Bore Log Entries"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"BoreLog #{self.borelog_id} Rod {self.rod_number} ({self.source})"


class BoreLogRodValue(models.Model):
    """
    Estado actual por rod.
    """

    borelog = models.ForeignKey(BoreLog, on_delete=models.CASCADE, related_name="rod_values", verbose_name="Bore Log")

    rod_number = models.PositiveSmallIntegerField(
        "Rod #",
        validators=[MinValueValidator(1), MaxValueValidator(500)],
        db_index=True,
    )

    depth = models.CharField("Depth", max_length=50, blank=True, default="")
    pitch = models.CharField("Pitch", max_length=50, blank=True, default="")
    station = models.CharField("Station", max_length=50, blank=True, default="")

    last_source = models.CharField("Last Source", max_length=20, choices=BoreLogEntry.SOURCE_CHOICES, default=BoreLogEntry.SOURCE_WEB)
    last_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="borelog_rodvalues_updated",
        verbose_name="Last Updated By",
    )
    updated_at = models.DateTimeField("Updated At", auto_now=True)

    class Meta:
        verbose_name = "Bore Log Rod Value"
        verbose_name_plural = "Bore Log Rod Values"
        unique_together = (("borelog", "rod_number"),)
        ordering = ("rod_number",)

    def __str__(self) -> str:
        return f"BoreLog #{self.borelog_id} Rod {self.rod_number}"