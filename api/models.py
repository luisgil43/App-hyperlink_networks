# api/models.py

from django.conf import settings
from django.db import models
from django.utils import timezone


class ApiFeature(models.Model):
    """
    Controla qué módulos de API están activos o desactivados.

    Ejemplos:
    - mobile_auth
    - mobile_billing
    - mobile_fleet
    - mobile_evidence

    Esto permite apagar/encender APIs desde una vista admin,
    sin tocar Render ni variables de entorno.
    """

    code = models.SlugField(
        max_length=80,
        unique=True,
        help_text="Código interno de la API. Ej: mobile_billing",
    )
    name = models.CharField(
        max_length=120,
        help_text="Nombre visible. Ej: Mobile Billing API",
    )
    description = models.TextField(
        blank=True,
        help_text="Descripción breve del uso de esta API.",
    )
    is_enabled = models.BooleanField(
        default=False,
        help_text="Indica si esta API está activa.",
    )
    only_superusers = models.BooleanField(
        default=False,
        help_text="Si está activo, solo superusers pueden usar esta API.",
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="api_features_updated",
    )

    class Meta:
        verbose_name = "API Feature"
        verbose_name_plural = "API Features"
        ordering = ["code"]

    def __str__(self):
        status = "ON" if self.is_enabled else "OFF"
        return f"{self.name} ({self.code}) - {status}"
