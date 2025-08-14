import os
from uuid import uuid4
from decimal import Decimal

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.text import slugify

from usuarios.models import CustomUser  # si no lo usas, puedes quitarlo
from utils.paths import upload_to       # si no lo usas, puedes quitarlo


class PrecioActividadTecnico(models.Model):
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ciudad = models.CharField(max_length=100)
    proyecto = models.CharField(max_length=200)
    oficina = models.CharField(max_length=100, default="-")  # Office
    cliente = models.CharField(max_length=100, default="-")  # Client
    tipo_trabajo = models.CharField(max_length=100, default="-")  # Work Type
    codigo_trabajo = models.CharField(max_length=50)  # Job Code
    descripcion = models.TextField()
    unidad_medida = models.CharField(max_length=20)
    precio_tecnico = models.DecimalField(max_digits=10, decimal_places=2)
    precio_empresa = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_creacion = models.DateField(auto_now_add=True)

    class Meta:
        unique_together = ('tecnico', 'ciudad', 'proyecto', 'codigo_trabajo')
        verbose_name = 'Precio por Actividad'
        verbose_name_plural = 'Precios por Actividad'

    def __str__(self):
        return f"{self.tecnico} - {self.codigo_trabajo}"


# Storage Wasabi (fuerza S3 para estos campos)
WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()

# ---------------------------- Sesiones / Ítems ---------------------------- #

ESTADOS_PROY = (
    ("asignado", "Assigned"),
    ("en_proceso", "In progress"),
    ("en_revision_supervisor", "Submitted — supervisor review"),
    ("rechazado_supervisor", "Rejected by supervisor"),
    ("aprobado_supervisor", "Approved by supervisor"),
    ("rechazado_pm", "Rejected by PM"),
    ("aprobado_pm", "Approved by PM"),
)


def upload_to_project_report(instance, filename: str) -> str:
    """
    Reporte ÚNICO por proyecto (Sesión). Mantener ruta determinística.
    """
    proj_slug = slugify(
        instance.proyecto_id or f"billing-{instance.id}") or f"billing-{instance.id}"
    return f"operaciones/reporte_fotografico/{proj_slug}/project/{proj_slug}.xlsx"


# 🔁 Alias con el NOMBRE que espera la migración 0009
def upload_to_reporte_fotografico_proyecto(instance, filename: str) -> str:
    return upload_to_project_report(instance, filename)


class SesionBilling(models.Model):
    creado_en = models.DateTimeField(default=timezone.now)
    proyecto_id = models.CharField(max_length=64)
    cliente = models.CharField(max_length=120)
    ciudad = models.CharField(max_length=120)
    proyecto = models.CharField(max_length=120)
    oficina = models.CharField(max_length=120)

    # Estado y reporte a nivel PROYECTO
    estado = models.CharField(
        max_length=32, choices=ESTADOS_PROY, default="asignado", db_index=True)
    reporte_fotografico = models.FileField(
        upload_to=upload_to_project_report,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["xlsx", "xls", "pdf"])],
        null=True,
        blank=True,
    )

    subtotal_tecnico = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    subtotal_empresa = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    real_company_billing = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ("-creado_en",)
        indexes = [
            models.Index(fields=["proyecto_id"]),
            models.Index(fields=["cliente", "ciudad", "proyecto", "oficina"]),
            models.Index(fields=["estado"]),
        ]

    @property
    def diferencia(self):
        if self.real_company_billing is None:
            return None
        return (self.subtotal_empresa or Decimal("0.00")) - self.real_company_billing

    def __str__(self):
        return f"Billing #{self.id} - {self.cliente} / {self.proyecto_id}"

    def recomputar_estado_desde_asignaciones(self, save: bool = True) -> str:
        """
        Recalcula y sincroniza el estado del proyecto con las asignaciones.
        Prioridad: en_revision_supervisor > en_proceso > aprob/rechazos > asignado.
        """
        estados = list(self.tecnicos_sesion.values_list("estado", flat=True))
        nuevo = "asignado"
        if estados:
            if any(e == "en_revision_supervisor" for e in estados):
                nuevo = "en_revision_supervisor"
            elif any(e == "en_proceso" for e in estados):
                nuevo = "en_proceso"
            elif all(e == "aprobado_pm" for e in estados):
                nuevo = "aprobado_pm"
            elif any(e == "rechazado_pm" for e in estados):
                nuevo = "rechazado_pm"
            elif all(e == "aprobado_supervisor" for e in estados):
                nuevo = "aprobado_supervisor"
            elif any(e == "rechazado_supervisor" for e in estados):
                nuevo = "rechazado_supervisor"
            else:
                nuevo = "asignado"

        if self.estado != nuevo:
            self.estado = nuevo
            if save:
                self.save(update_fields=["estado"])
        return self.estado


class ItemBilling(models.Model):
    sesion = models.ForeignKey(
        SesionBilling, on_delete=models.CASCADE, related_name="items")
    codigo_trabajo = models.CharField(max_length=60)
    tipo_trabajo = models.CharField(max_length=120)
    descripcion = models.CharField(max_length=255)
    unidad_medida = models.CharField(max_length=40)
    cantidad = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    precio_empresa = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    subtotal_empresa = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    subtotal_tecnico = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=["sesion", "codigo_trabajo"])]

    def __str__(self):
        return f"Item {self.codigo_trabajo} (sesión {self.sesion_id})"


class ItemBillingTecnico(models.Model):
    item = models.ForeignKey(
        ItemBilling, on_delete=models.CASCADE, related_name="desglose_tecnico")
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # precios
    tarifa_base = models.DecimalField(
        # precio_tecnico
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    porcentaje = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"))       # 100/n
    tarifa_efectiva = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))  # base * %
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal(
        "0.00"))        # cantidad * efectiva

    class Meta:
        ordering = ("id",)
        indexes = [models.Index(fields=["item", "tecnico"])]

    def __str__(self):
        return f"{self.tecnico_id} @ {self.item_id} -> {self.subtotal}"


# ------------------------ Asignación por técnico ------------------------- #

ESTADOS_TEC = (
    ("asignado", "Asignado"),                     # ← agregado
    ("en_proceso", "En proceso"),
    ("en_revision_supervisor", "En revisión supervisor"),
    ("rechazado_supervisor", "Rechazado por supervisor"),
    ("aprobado_supervisor", "Aprobado por supervisor"),
    ("aprobado_pm", "Aprobado por PM"),
    ("rechazado_pm", "Rechazado por PM"),
)


def upload_to_reporte_fotografico(instance, filename: str) -> str:
    # Project ID
    proj_id = (getattr(instance.sesion, "proyecto_id", "")
               or "proyecto").strip()
    proj_slug = slugify(proj_id) or "proyecto"

    # Nombre del técnico (o username si no hay nombre completo)
    tech_name = (
        getattr(instance.tecnico, "get_full_name", lambda: "")()
        or getattr(instance.tecnico, "username", "")
        or f"user-{instance.tecnico_id}"
    )
    tech_slug = slugify(tech_name) or f"user-{instance.tecnico_id}"

    # operaciones/reporte_fotografico/ProyectID/NombreTecnico/ProyectID.xlsx
    return f"operaciones/reporte_fotografico/{proj_slug}/{tech_slug}/{proj_slug}.xlsx"


class SesionBillingTecnico(models.Model):
    sesion = models.ForeignKey(
        SesionBilling, on_delete=models.CASCADE, related_name="tecnicos_sesion")
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    porcentaje = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("100.00"))
    estado = models.CharField(
        max_length=32,
        choices=ESTADOS_TEC,
        default="asignado",                   # ← ahora arranca como “asignado”
        db_index=True,
    )
    aceptado_en = models.DateTimeField(
        null=True, blank=True)        # al dar Start
    finalizado_en = models.DateTimeField(
        null=True, blank=True)      # al Finish

    supervisor_comentario = models.TextField(blank=True)
    supervisor_revisado_en = models.DateTimeField(null=True, blank=True)

    pm_comentario = models.TextField(blank=True)
    pm_revisado_en = models.DateTimeField(null=True, blank=True)

    # (histórico) archivo por técnico si lo mantienes
    reporte_fotografico = models.FileField(
        upload_to=upload_to_reporte_fotografico,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["xlsx", "xls", "pdf"])],
        null=True,
        blank=True,
    )

    # Permite reabrir carga tras rechazo
    reintento_habilitado = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["sesion", "tecnico"], name="uniq_sesion_tecnico"),
        ]
        ordering = ("id",)

    def __str__(self):
        return f"Asig sesión {self.sesion_id} / técnico {self.tecnico_id} ({self.get_estado_display()})"


# ------------------------ Requisitos y evidencias ------------------------ #

def upload_to_evidencia(instance, filename: str) -> str:
    # Project ID
    proj_id = (getattr(instance.tecnico_sesion.sesion,
               "proyecto_id", "") or "proyecto").strip()
    proj_slug = slugify(proj_id) or "proyecto"

    # Nombre del técnico
    tech = getattr(instance.tecnico_sesion, "tecnico", None)
    tech_name = (
        getattr(tech, "get_full_name", lambda: "")()
        or getattr(tech, "username", "")
        or f"user-{getattr(instance.tecnico_sesion, 'tecnico_id', '0')}"
    )
    tech_slug = slugify(
        tech_name) or f"user-{getattr(instance.tecnico_sesion, 'tecnico_id', '0')}"

    # Nombre del archivo
    base, ext = os.path.splitext(filename or "")
    ext = (ext or ".jpg").lower()
    safe_base = slugify(base) or "foto"
    # Para evitar colisiones, podrías usar:
    # safe_base = f"{uuid4().hex}-{safe_base}"

    return f"operaciones/reporte_fotografico/{proj_slug}/{tech_slug}/evidencia/{safe_base}{ext}"


class RequisitoFotoBilling(models.Model):
    tecnico_sesion = models.ForeignKey(
        SesionBillingTecnico, on_delete=models.CASCADE, related_name="requisitos")
    titulo = models.CharField(max_length=150)
    descripcion = models.CharField(max_length=300, blank=True)
    obligatorio = models.BooleanField(default=True)
    orden = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("orden", "id")
        indexes = [models.Index(fields=["tecnico_sesion", "orden"])]

    def __str__(self):
        return f"[{self.tecnico_sesion_id}] {self.orden}. {self.titulo}"


class EvidenciaFotoBilling(models.Model):
    tecnico_sesion = models.ForeignKey(
        SesionBillingTecnico, on_delete=models.CASCADE, related_name="evidencias")
    requisito = models.ForeignKey(
        RequisitoFotoBilling,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="evidencias",
    )
    imagen = models.ImageField(
        upload_to=upload_to_evidencia,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    nota = models.CharField(max_length=255, blank=True)
    tomada_en = models.DateTimeField(default=timezone.now)

    # Metadatos cliente (opcional)
    lat = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True)
    gps_accuracy_m = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True)
    client_taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("requisito__orden", "tomada_en", "id")
        indexes = [
            models.Index(fields=["tecnico_sesion"]),
            models.Index(fields=["requisito"]),
        ]

    def __str__(self):
        tag = self.requisito.titulo if self.requisito_id else "Extra"
        return f"Evidencia {tag} ({self.tecnico_sesion_id})"
