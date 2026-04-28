import os
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.text import slugify

from facturacion.models import Proyecto
from usuarios.models import CustomUser  # si no lo usas, puedes quitarlo
from utils.paths import upload_to  # si no lo usas, puedes quitarlo


class PrecioActividadTecnico(models.Model):
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        db_index=True,
    )

    proyecto = models.ForeignKey("facturacion.Proyecto",on_delete=models.CASCADE,related_name="precios_tecnico",db_index=True)
    ciudad = models.CharField(max_length=100)
    oficina = models.CharField(max_length=100, default="-")
    cliente = models.CharField(max_length=100, default="-")
    tipo_trabajo = models.CharField(max_length=100, default="-")
    codigo_trabajo = models.CharField(max_length=100)
    descripcion = models.TextField()
    unidad_medida = models.CharField(max_length=60)
    precio_tecnico = models.DecimalField(max_digits=10, decimal_places=2)
    precio_empresa = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_creacion = models.DateField(auto_now_add=True)

    class Meta:
        verbose_name = "Precio por Actividad"
        verbose_name_plural = "Precios por Actividad"
        # ahora la unicidad usa el FK "proyecto"
        unique_together = (
            "tecnico", "ciudad", "proyecto", "oficina", "cliente", "codigo_trabajo"
        )
        indexes = [
            models.Index(
                fields=[
                    "tecnico",
                    "ciudad",
                    "proyecto",
                    "oficina",
                    "cliente",
                    "codigo_trabajo",
                ]
            ),
        ]

    def __str__(self):
        # Django mostrará el __str__ de Proyecto
        return f"{self.tecnico} — {self.ciudad}/{self.proyecto} · {self.codigo_trabajo}"


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


# ----------------------------- Estados finanzas --------------------------- #

FINANCE_STATUS = [
    ("none", "—"),
    ("review_discount", "Review discount"),      # 👈 NUEVO
    ("discount_applied", "Discount applied"),    # 👈 NUEVO
    ("sent", "Enviado a Finanzas"),
    ("pending", "Pendiente por cobrar"),
    ("in_review", "En revisión"),
    ("rejected", "Rechazado"),
    ("paid", "Cobrado"),
]

indexes = [
    models.Index(fields=["proyecto_id"]),
    models.Index(fields=["cliente", "ciudad", "proyecto", "oficina"]),
    models.Index(fields=["estado"]),
    models.Index(fields=["is_direct_discount"]),  # ← NUEVO
]


class SesionBilling(models.Model):
    creado_en = models.DateTimeField(default=timezone.now)

    # ----- Descuentos directos -----
    is_direct_discount = models.BooleanField(default=False, db_index=True)
    is_cable_installation = models.BooleanField(
        default=False,
        db_index=True,
        help_text="If enabled, Requirements will use the Cable Installation workflow.",
    )
    origin_session = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="discounts",
        on_delete=models.SET_NULL,
        help_text="If set, this discount corrects the referenced session.",
    )

    # ----- Split / Duplicate (facturación parcial) -----
    is_split_child = models.BooleanField(default=False, db_index=True)
    split_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="split_children",
        on_delete=models.SET_NULL,
        help_text="If set, this billing session was created by splitting from the referenced session.",
    )
    split_comment = models.CharField(max_length=255, blank=True, default="")

    # ✅ NUEVO: Payment mode técnicos
    TECH_PAYMENT_MODE = [
        ("split", "Split (percentage)"),
        ("full", "Full quantity per technician"),
    ]
    tech_payment_mode = models.CharField(
        max_length=10,
        choices=TECH_PAYMENT_MODE,
        default="split",
        db_index=True,
        help_text="How technician amounts are computed. split = uses %; full = each tech uses full qty.",
    )

    # ----- Identificación del proyecto -----
    proyecto_id = models.CharField(max_length=64)
    cliente = models.CharField(max_length=120)
    ciudad = models.CharField(max_length=120)
    proyecto = models.CharField(max_length=120)
    oficina = models.CharField(max_length=120)

    # ----- Ubicación y semana proyectada de pago -----
    direccion_proyecto = models.CharField(
        "Project address / Google Maps link",
        max_length=500,
        blank=True,
        default="",
    )
    semana_pago_proyectada = models.CharField(
        "Projected pay week (ISO)",
        max_length=10,
        blank=True,
        default="",
    )

    proyecto_especial = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, 'extra' photos can include user-entered Title and "
            "Address; the report will use those fields."
        ),
    )

    # ----- Estado operativo y reporte único del proyecto -----
    estado = models.CharField(
        max_length=32,
        choices=ESTADOS_PROY,
        default="asignado",
        db_index=True,
    )
    reporte_fotografico = models.FileField(
        upload_to=upload_to_project_report,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["xlsx", "xls", "pdf"])],
        null=True,
        blank=True,
        max_length=1024,
    )

    # ----- Totales -----
    subtotal_tecnico = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    subtotal_empresa = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    real_company_billing = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    discount_week = models.CharField(
        "Discount week (ISO)",
        max_length=10,
        blank=True,
        default="",
    )

    semana_pago_real = models.CharField(
        "Real pay week (ISO)",
        max_length=10,
        blank=True,
        default="",
    )

    # =============================== FINANZAS ============================== #
    finance_status = models.CharField(
        max_length=20,
        choices=FINANCE_STATUS,
        default="none",
        db_index=True,
    )
    finance_note = models.TextField(blank=True, default="")
    finance_sent_at = models.DateTimeField(null=True, blank=True)
    finance_updated_at = models.DateTimeField(auto_now=True)
    finance_daily_number = models.CharField(
        "Daily Number", max_length=50, blank=True, default=""
    )
    finance_finish_date = models.DateField(
        "Finance finish date",
        null=True,
        blank=True,
        help_text="End date.",
    )
    # ====================================================================== #

    class Meta:
        ordering = ("-creado_en",)
        indexes = [
            models.Index(fields=["proyecto_id"]),
            models.Index(fields=["cliente", "ciudad", "proyecto", "oficina"]),
            models.Index(fields=["estado"]),
            models.Index(fields=["is_direct_discount"]),
            models.Index(fields=["is_cable_installation"]),
            models.Index(fields=["is_split_child"]),
            models.Index(fields=["tech_payment_mode"]),  # ✅ nuevo
        ]

    def sync_from_proyecto_codigo(self):
        from facturacion.models import Proyecto

        codigo = (self.proyecto_id or "").strip()

        if not codigo and (self.proyecto or "").strip().isdigit():
            codigo = self.proyecto.strip()

        if not codigo:
            self.cliente = ""
            self.ciudad = ""
            self.proyecto = ""
            self.oficina = ""
            return

        p = None
        try:
            p = Proyecto.objects.get(codigo__iexact=codigo)
        except Proyecto.DoesNotExist:
            p = None

        if p is None and codigo.isdigit():
            try:
                p = Proyecto.objects.get(pk=int(codigo))
            except Proyecto.DoesNotExist:
                p = None

        if p is None:
            return

        self.proyecto_id = p.codigo
        self.cliente = p.mandante
        self.ciudad = p.ciudad
        self.proyecto = p.nombre
        self.oficina = p.oficina

    @property
    def diferencia(self):
        if self.real_company_billing is None:
            return None
        return (self.subtotal_empresa or Decimal("0.00")) - self.real_company_billing

    @property
    def difference_is_zero(self):
        d = self.diferencia
        return d is not None and d == 0

    @property
    def maps_href(self) -> str:
        val = (self.direccion_proyecto or "").strip()
        if not val:
            return ""
        low = val.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return val
        from urllib.parse import quote_plus

        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(val)}"

    def __str__(self):
        return f"Billing #{self.id} - {self.cliente} / {self.proyecto_id}"

    def recomputar_estado_desde_asignaciones(self, save: bool = True) -> str:
        # NUEVO: si es descuento directo, no tocar el estado
        if self.is_direct_discount:
            return self.estado

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

        if self.estado != nuevo:
            self.estado = nuevo
            if save:
                self.save(update_fields=["estado"])
        return self.estado

    @property
    def can_mark_discount_applied(self) -> bool:
        return self.is_direct_discount and self.finance_status == "review_discount"

    def mark_discount_applied(self, note: str = ""):
        self.finance_status = "discount_applied"
        if note:
            self.finance_note = note
            self.save(
                update_fields=["finance_status", "finance_note", "finance_updated_at"]
            )
        else:
            self.save(update_fields=["finance_status", "finance_updated_at"])

    def save(self, *args, **kwargs):
        self.sync_from_proyecto_codigo()

        if self.is_direct_discount and self.finance_status in ("none", "", "sent"):
            self.finance_status = "review_discount"

        # ✅ normalizar payment mode
        if self.tech_payment_mode not in ("split", "full"):
            self.tech_payment_mode = "split"

        super().save(*args, **kwargs)


# ======================= Job de Reporte Fotográfico =======================


class ReporteFotograficoJob(models.Model):
    ESTADOS = [
        ("pendiente",  "Pending"),
        ("procesando", "Processing"),
        ("ok",         "OK"),
        ("error",      "Error"),
    ]

    # Relación directa a la sesión/proyecto
    sesion = models.ForeignKey(
        SesionBilling,
        on_delete=models.CASCADE,
        related_name="jobs_reporte",
        db_index=True,
    )

    # Estado y metadatos de ejecución
    estado = models.CharField(
        max_length=20, choices=ESTADOS, default="pendiente", db_index=True)
    # total de fotos a procesar (opcional)
    total = models.PositiveIntegerField(default=0)
    procesadas = models.PositiveIntegerField(
        default=0)   # cuántas van procesadas (opcional)

    # Logs/resultado
    log = models.TextField(blank=True)
    resultado_key = models.CharField(
        max_length=512, blank=True)  # key del XLSX en storage
    error = models.TextField(blank=True)

    # Timestamps
    creado_en = models.DateTimeField(auto_now_add=True)
    iniciado_en = models.DateTimeField(null=True, blank=True)
    terminado_en = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ("-creado_en",)
        indexes = [
            models.Index(fields=["sesion", "estado"]),
        ]

    def __str__(self):
        return f"ReporteJob #{self.id} • Sesión {self.sesion_id} • {self.estado}"

    def append_log(self, line: str):
        self.log = (self.log or "") + (line.rstrip() + "\n")
        self.save(update_fields=["log"])


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
    is_active = models.BooleanField(default=True, db_index=True)
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
    tecnico_comentario = models.TextField(blank=True, default="")
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
        max_length=1024,
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

"""
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
"""


class RequisitoFotoBilling(models.Model):

    tecnico_sesion = models.ForeignKey(
        SesionBillingTecnico, on_delete=models.CASCADE, related_name="requisitos"
    )

    titulo = models.CharField(max_length=150)

    descripcion = models.CharField(max_length=300, blank=True)

    obligatorio = models.BooleanField(default=True)

    orden = models.PositiveIntegerField(default=0)

    # ✅ NUEVO: flags de medición (Power Meter)

    needs_power_reading = models.BooleanField(
        default=False,
        db_index=True,
        help_text="If enabled, this requirement expects a Power Meter reading (dBm).",
    )

    power_port_no = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Optional port number (1..8) when title is POWER PORT X.",
    )

    class Meta:

        ordering = ("orden", "id")

        indexes = [
            models.Index(fields=["tecnico_sesion", "orden"]),
            models.Index(fields=["tecnico_sesion", "needs_power_reading"]),
        ]

    def __str__(self):

        return f"[{self.tecnico_sesion_id}] {self.orden}. {self.titulo}"

"""
class EvidenciaFotoBilling(models.Model):
    tecnico_sesion = models.ForeignKey(
        SesionBillingTecnico, on_delete=models.CASCADE, related_name="evidencias"
    )
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
        max_length=1024,
    )
    nota = models.CharField("Note", max_length=255, blank=True)
    tomada_en = models.DateTimeField(default=timezone.now)

    # Client metadata (optional)
    lat = models.DecimalField(
        "Latitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    lng = models.DecimalField(
        "Longitude", max_digits=9, decimal_places=6, null=True, blank=True
    )
    gps_accuracy_m = models.DecimalField(
        "GPS accuracy (m)", max_digits=7, decimal_places=2, null=True, blank=True
    )
    client_taken_at = models.DateTimeField(
        "Taken at (client)", null=True, blank=True)

    # NEW: For special projects (replacing default "Extra" behavior)
    titulo_manual = models.CharField(
        "Custom title", max_length=200, blank=True)
    direccion_manual = models.CharField(
        "Custom address", max_length=255, blank=True)

    class Meta:
        ordering = ("requisito__orden", "tomada_en", "id")
        indexes = [
            models.Index(fields=["tecnico_sesion"]),
            models.Index(fields=["requisito"]),
        ]

    def __str__(self):
        if self.requisito_id:
            return f"Evidence {self.requisito.titulo} (session {self.tecnico_sesion_id})"
        elif self.titulo_manual:
            return f"Evidence {self.titulo_manual} (session {self.tecnico_sesion_id})"
        return f"Evidence Extra (session {self.tecnico_sesion_id})"
"""


class EvidenciaFotoBilling(models.Model):

    tecnico_sesion = models.ForeignKey(
        SesionBillingTecnico, on_delete=models.CASCADE, related_name="evidencias"
    )

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
        max_length=1024,
    )

    nota = models.CharField("Note", max_length=255, blank=True)

    tomada_en = models.DateTimeField(default=timezone.now)

    # Client metadata (optional)

    lat = models.DecimalField(
        "Latitude", max_digits=9, decimal_places=6, null=True, blank=True
    )

    lng = models.DecimalField(
        "Longitude", max_digits=9, decimal_places=6, null=True, blank=True
    )

    gps_accuracy_m = models.DecimalField(
        "GPS accuracy (m)", max_digits=7, decimal_places=2, null=True, blank=True
    )

    client_taken_at = models.DateTimeField("Taken at (client)", null=True, blank=True)

    # Special projects

    titulo_manual = models.CharField("Custom title", max_length=200, blank=True)

    direccion_manual = models.CharField("Custom address", max_length=255, blank=True)

    # ✅ NUEVO: Power meter reading (extracted by admin)

    power_dbm = models.DecimalField(
        "Power (dBm)",
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        db_index=True,
        help_text="Extracted Power Meter reading in dBm (e.g., -21.05).",
    )

    power_extracted_at = models.DateTimeField(null=True, blank=True)

    power_extracted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="power_extractions",
    )

    power_extract_note = models.CharField(max_length=255, blank=True, default="")

    class Meta:

        ordering = ("requisito__orden", "tomada_en", "id")

        indexes = [
            models.Index(fields=["tecnico_sesion"]),
            models.Index(fields=["requisito"]),
            models.Index(fields=["power_dbm"]),
        ]

    def __str__(self):

        if self.requisito_id:

            return (
                f"Evidence {self.requisito.titulo} (session {self.tecnico_sesion_id})"
            )

        elif self.titulo_manual:

            return f"Evidence {self.titulo_manual} (session {self.tecnico_sesion_id})"

        return f"Evidence Extra (session {self.tecnico_sesion_id})"


# =========================== PAGOS SEMANALES ============================ #
# Guarda el comprobante en Wasabi (S3) y maneja el flujo del pago.


# Reutilizamos tu storage S3/Wasabi ya configurado en este archivo:
# WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
# wasabi_storage = WasabiStorageClass()


def _name_slug(user) -> str:
    base = (getattr(user, "get_full_name", lambda: "")()
            or getattr(user, "username", "")
            or "").strip()
    return slugify(base) or "user"


def upload_to_payment_receipt(instance, filename: str) -> str:
    """
    operaciones/pagos/<YYYY-Www>/<nombre-slug>/receipt_<uuid>.<ext>
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or ".pdf").lower()
    folder = _name_slug(getattr(instance, "technician", None))
    return f"operaciones/pagos/{instance.week}/{folder}/receipt_{uuid4().hex}{ext}"


class WeeklyPayment(models.Model):
    """
    1 registro por técnico y semana de pago.
    Monto total (sumado desde la producción), estado, motivo de rechazo,
    comprobante y semana efectiva en que se pagó.
    """
    STATUS = [
        ("pending_user", "Pending worker approval"),
        ("approved_user", "Approved by worker"),
        ("rejected_user", "Rejected by worker"),
        ("pending_payment", "Pending payment"),  # aprobado por el trabajador
        ("paid", "Paid"),
    ]

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="weekly_payments"
    )
    week = models.CharField(max_length=10, db_index=True)  # ISO: 2025-W34
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(
        max_length=20, choices=STATUS, default="pending_user", db_index=True
    )
    reject_reason = models.TextField(blank=True, default="")

    # Semana efectiva en que se marcó como pagado
    paid_week = models.CharField(max_length=10, blank=True, default="")

    # Comprobante en Wasabi
    receipt = models.FileField(
        upload_to=upload_to_payment_receipt,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
        blank=True,
        null=True,
        max_length=1024,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Un pago por técnico+semana
        unique_together = [("technician", "week")]
        ordering = ["-week", "technician_id"]
        indexes = [
            models.Index(fields=["week", "status"]),
            models.Index(fields=["technician", "week"]),
        ]

    def __str__(self):
        return f"{self.technician} • {self.week} • {self.amount}"

    @property
    def is_current_week(self) -> bool:
        y, w, _ = timezone.localdate().isocalendar()
        return self.week == f"{y}-W{int(w):02d}"

    def mark_paid(self, paid_week: str | None = None):
        """
        Marca como pagado y setea la semana de pago efectiva.
        """
        if not paid_week:
            y, w, _ = timezone.localdate().isocalendar()
            paid_week = f"{y}-W{int(w):02d}"
        self.status = "paid"
        self.paid_week = paid_week
        self.save(update_fields=["status", "paid_week", "updated_at"])


def upload_to_plan(instance, filename: str) -> str:
    """
    Guardamos el plano en una ruta estable por proyecto y nombre único con uuid para evitar colisiones.
    """
    proj_id = (getattr(instance.sesion, "proyecto_id", "")
               or "project").strip()
    proj_slug = slugify(proj_id) or "project"
    _, ext = os.path.splitext(filename or "")
    ext = (ext or ".pdf").lower()
    return f"operaciones/plans/{proj_slug}/plan_{uuid4().hex}{ext}"


class ProjectPlan(models.Model):
    sesion = models.ForeignKey(
        SesionBilling, on_delete=models.CASCADE, related_name="plans", db_index=True
    )
    plan_number = models.PositiveIntegerField(db_index=True)  # 1, 2, 3...
    file = models.FileField(
        upload_to=upload_to_plan,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["pdf", "dwg", "xlsx", "xls"])],
        max_length=1024,
    )
    original_name = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("plan_number", "id")
        unique_together = [("sesion", "plan_number")]  # evita reemplazos

    def __str__(self):
        return f"Plan {self.plan_number} — Sesión {self.sesion_id}"

    @property
    def label(self) -> str:
        return f"Plan {self.plan_number}"


# operations/models_adjustments.py  (o dentro de models.py si prefieres)


class AdjustmentEntry(models.Model):
    TYPES = [
        ("bonus", "Bonus"),
        ("advance", "Advance"),
        ("fixed_salary", "Fixed salary"),
    ]

    # A quién se aplica
    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="adjustments"
    )

    # Para ubicarlo en la vista y en pagos
    # ISO: YYYY-Www (ej: 2025-W34)
    week = models.CharField(max_length=10, db_index=True)

    # Datos descriptivos “ligeros” del proyecto (solo para visualizar en la tabla)
    client = models.CharField(max_length=120, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    project = models.CharField(max_length=120, blank=True, default="")
    office = models.CharField(max_length=120, blank=True, default="")
    project_id = models.CharField(
        max_length=64, blank=True, default="")  # opcional

    # Ajuste
    adjustment_type = models.CharField(max_length=20, choices=TYPES)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"))
    note = models.CharField(max_length=255, blank=True, default="")

    # Metadatos
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="adjustments_created"
    )
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-week", "-id")
        indexes = [
            models.Index(fields=["technician", "week"]),
            models.Index(fields=["adjustment_type"]),
        ]

    def __str__(self):
        return f"{self.get_adjustment_type_display()} • {self.technician} • {self.week}"

    @property
    def signed_amount(self):
        """
        Convención:
          - bonus: +amount
          - fixed_salary: +amount
          - advance: -amount
        """
        if self.adjustment_type == "advance":
            return -abs(self.amount or 0)
        return abs(self.amount or 0)
