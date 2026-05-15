from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from usuarios.models import CustomUser
from utils.paths import upload_to

# ==========================================================
# Helpers / upload paths
# ==========================================================


def _safe_filename(texto: str) -> str:
    """
    Safe filename for storage paths.
    Allows letters, numbers, spaces, hyphens, underscores, dots and parentheses.
    """
    t = (texto or "").strip()
    return re.sub(r"[^\w\s\-\.\(\)]", "", t)


def _yyyy_mm(dt=None) -> str:
    if dt is None:
        return timezone.localdate().strftime("%Y-%m")
    try:
        return timezone.localtime(dt).date().strftime("%Y-%m")
    except Exception:
        return dt.strftime("%Y-%m")


def upload_to_herramienta_foto(instance, filename: str) -> str:
    """
    logistica/tools/photos/2026/2026-03/<serial>/photo_<uuid>.jpg
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".jpg"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    serial = _safe_filename(getattr(instance, "serial", "") or "no-serial")
    return f"logistica/tools/photos/{year}/{month_folder}/{serial}/photo_{uuid4().hex}{ext}"


def upload_to_herramienta_inventario(instance, filename: str) -> str:
    """
    logistica/tools/inventory/2026/2026-03/<serial>/inventory_<uuid>.jpg
    """
    _, ext = os.path.splitext(filename or "")
    ext = (ext or "").lower() or ".jpg"

    year = timezone.localdate().strftime("%Y")
    month_folder = _yyyy_mm()

    serial = _safe_filename(
        getattr(getattr(instance, "herramienta", None), "serial", "") or "no-serial"
    )
    return f"logistica/tools/inventory/{year}/{month_folder}/{serial}/inventory_{uuid4().hex}{ext}"


# ==========================================================
# EXISTING / LEGACY MODELS
# Kept to avoid breaking production.
# ==========================================================


class Bodega(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    # Added for the tools workflow. Nullable/blank to avoid breaking existing rows.
    ubicacion = models.CharField(max_length=200, blank=True, null=True)

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bodegas_creadas",
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = "Warehouse"
        verbose_name_plural = "Warehouses"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class Material(models.Model):
    codigo_interno = models.CharField(max_length=50)
    nombre = models.CharField(max_length=255)
    codigo_externo = models.CharField(max_length=50, blank=True, null=True)

    bodega = models.ForeignKey(
        Bodega,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    stock_actual = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    unidad_medida = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)

    valor_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="Unit value ($)",
    )

    def __str__(self):
        return f"{self.codigo_interno} - {self.nombre}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["codigo_interno", "bodega"],
                name="unique_codigo_interno_por_bodega",
            ),
            models.UniqueConstraint(
                fields=["codigo_externo", "bodega"],
                name="unique_codigo_externo_por_bodega",
            ),
        ]


class IngresoMaterial(models.Model):
    OPCIONES_TIPO_DOC = [
        ("guia", "Guía de Despacho"),
        ("factura", "Factura"),
    ]

    fecha_ingreso = models.DateField(auto_now_add=True)
    tipo_documento = models.CharField(max_length=10, choices=OPCIONES_TIPO_DOC)
    numero_documento = models.CharField(max_length=50)

    codigo_externo = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Código externo",
    )

    bodega = models.ForeignKey(
        Bodega,
        on_delete=models.PROTECT,
        related_name="ingresos",
    )

    archivo_documento = models.FileField(
        upload_to=upload_to,
        verbose_name="PDF de respaldo",
    )

    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"{self.numero_documento} - {self.get_tipo_documento_display()}"


class DetalleIngresoMaterial(models.Model):
    ingreso = models.ForeignKey(
        IngresoMaterial,
        on_delete=models.CASCADE,
        related_name="detalles",
    )
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.material.nombre} - {self.cantidad}"


class ArchivoCAF(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )
    nombre_archivo = models.CharField(max_length=255)

    archivo = models.FileField(
        upload_to=upload_to,
        verbose_name="Archivo CAF (.xml)",
    )

    tipo_dte = models.PositiveIntegerField()
    rango_inicio = models.PositiveIntegerField()
    rango_fin = models.PositiveIntegerField()
    fecha_subida = models.DateTimeField(auto_now_add=True)

    estado = models.CharField(
        max_length=20,
        choices=[
            ("activo", "Activo"),
            ("inactivo", "Inactivo"),
        ],
    )

    def __str__(self):
        return f"{self.nombre_archivo} (TD {self.tipo_dte})"


TIPO_DOCUMENTO_CHOICES = [
    ("guia", "Guía de Despacho"),
    ("factura", "Factura"),
    ("nota_credito", "Nota de Crédito"),
    ("nota_debito", "Nota de Débito"),
    ("otro", "Otro"),
]


class FolioDisponible(models.Model):
    caf = models.ForeignKey(ArchivoCAF, on_delete=models.CASCADE)
    folio = models.IntegerField()
    usado = models.BooleanField(default=False)

    class Meta:
        unique_together = ("folio", "caf")

    def __str__(self):
        return f"{self.caf.tipo_dte} - Folio {self.folio}"


class DetalleSalidaMaterial(models.Model):
    salida = models.ForeignKey(
        "SalidaMaterial",
        on_delete=models.CASCADE,
        related_name="detalles",
    )
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    descripcion = models.CharField(max_length=255, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)

    valor_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    descuento = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    def calcular_valor_total(self):
        bruto = self.cantidad * self.valor_unitario
        return max(bruto - self.descuento, 0)

    def __str__(self):
        return f"{self.material.nombre} - {self.cantidad}"


User = get_user_model()


class CertificadoDigital(models.Model):
    archivo = models.FileField(
        upload_to=upload_to,
        verbose_name="Archivo .pfx",
    )
    clave_certificado = models.CharField(max_length=255)
    rut_emisor = models.CharField(max_length=20)
    fecha_inicio = models.DateField(auto_now_add=True)
    activo = models.BooleanField(default=True)

    usuario = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Certificado de {self.rut_emisor}"


TIPO_DOCUMENTO_CHOICES = [
    ("guia_despacho", "Guía de Despacho"),
]


class SalidaMaterial(models.Model):
    fecha_salida = models.DateField(auto_now_add=True)
    bodega = models.ForeignKey(Bodega, on_delete=models.CASCADE)
    id_proyecto = models.CharField(max_length=100)

    tipo_documento = models.CharField(
        max_length=20,
        choices=TIPO_DOCUMENTO_CHOICES,
    )
    numero_documento = models.CharField(max_length=50)

    entregado_a = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entregado_salidas",
    )
    emitido_por = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emitido_salidas",
    )

    archivo_pdf = models.FileField(
        upload_to=upload_to,
        null=True,
        blank=True,
    )

    archivo_xml = models.FileField(
        upload_to=upload_to,
        null=True,
        blank=True,
        verbose_name="XML firmado",
    )

    # Datos del receptor
    rut_receptor = models.CharField(max_length=15)
    nombre_receptor = models.CharField(max_length=255)
    giro_receptor = models.CharField(max_length=255)
    direccion_receptor = models.CharField(max_length=255)
    comuna_receptor = models.CharField(max_length=100)
    ciudad_receptor = models.CharField(max_length=100)

    # Keep the existing field name to avoid migration conflicts.
    fecha_emisión = models.DateField(auto_now_add=True)

    obra = models.CharField(max_length=255)
    chofer = models.CharField(max_length=255)
    rut_transportista = models.CharField(max_length=20)
    patente = models.CharField(max_length=20)
    origen = models.CharField(max_length=255)
    destino = models.CharField(max_length=255)

    observaciones = models.TextField(blank=True)

    estado_envio_sii = models.CharField(
        max_length=20,
        choices=[
            ("pendiente", "Pendiente"),
            ("enviado", "Enviado"),
            ("aceptado", "Aceptado"),
            ("rechazado", "Rechazado"),
        ],
        default="pendiente",
    )
    mensaje_sii = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Guía #{self.numero_documento} - {self.fecha_salida}"


# ==========================================================
# TOOLS / ASSIGNMENTS / INVENTORY
# Internal names remain in Spanish to avoid risky table/model renames.
# UI labels can be translated in forms/templates/views.
# ==========================================================


class Herramienta(models.Model):
    STATUS_CHOICES = [
        ("operativa", "Operational"),
        ("asignada", "Assigned"),
        ("danada", "Damaged"),
        ("extraviada", "Lost"),
        ("robada", "Stolen"),
        ("bodega", "In warehouse"),
    ]

    nombre = models.CharField(max_length=160)
    descripcion = models.TextField(blank=True, null=True)

    serial = models.CharField(max_length=120, unique=True)

    cantidad = models.PositiveIntegerField(
        default=1,
        help_text="Available quantity for this tool/equipment.",
    )

    valor_comercial = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    foto = models.ImageField(
        upload_to=upload_to_herramienta_foto,
        max_length=1024,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
        blank=True,
        null=True,
    )

    bodega = models.ForeignKey(
        Bodega,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="operativa",
    )
    status_justificacion = models.TextField(blank=True, null=True)

    status_changed_at = models.DateTimeField(blank=True, null=True)
    status_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_status_cambiado",
    )

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_creadas",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    inventory_required = models.BooleanField(
        default=False,
        help_text="If enabled, the user must submit an inventory photo.",
    )
    next_inventory_due = models.DateField(blank=True, null=True)
    last_inventory_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Tool"
        verbose_name_plural = "Tools"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["serial"]),
        ]

    def __str__(self) -> str:
        return f"{self.nombre} ({self.serial})"

    def clean(self):
        if self.valor_comercial is not None and self.valor_comercial < 0:
            raise ValidationError(
                {"valor_comercial": "Commercial value cannot be negative."}
            )

        if self.cantidad is None:
            self.cantidad = 0

        if int(self.cantidad) < 0:
            raise ValidationError({"cantidad": "Quantity cannot be negative."})

        if self.status in ("danada", "extraviada", "robada"):
            if not (self.status_justificacion or "").strip():
                raise ValidationError(
                    {
                        "status_justificacion": (
                            "A justification is required for damaged, lost or stolen tools."
                        )
                    }
                )

    def mark_inventory_due_default(self):
        today = timezone.localdate()
        self.next_inventory_due = today + timedelta(days=60)

    def set_status(self, new_status: str, by_user=None, justification: str = ""):
        new_status = (new_status or "").strip()

        if new_status not in dict(self.STATUS_CHOICES):
            raise ValidationError("Invalid status.")

        self.status = new_status
        self.status_changed_at = timezone.now()
        self.status_changed_by = by_user

        if new_status in ("danada", "extraviada", "robada"):
            justification = (justification or "").strip()
            if not justification:
                raise ValidationError(
                    "Justification is required for damaged, lost or stolen tools."
                )
            self.status_justificacion = justification
        else:
            self.status_justificacion = (justification or "").strip() or None


class HerramientaAsignacion(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pending"),
        ("aceptada", "Accepted"),
        ("rechazada", "Rejected"),
        ("terminada", "Closed"),
    ]

    herramienta = models.ForeignKey(
        Herramienta,
        on_delete=models.CASCADE,
        related_name="asignaciones",
    )

    asignado_a = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="herramientas_asignadas",
    )

    asignado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_asignadas_por",
    )

    asignado_at = models.DateTimeField(default=timezone.now)

    cantidad_entregada = models.PositiveIntegerField(default=1)

    closed_at = models.DateTimeField(blank=True, null=True)

    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_asignaciones_cerradas_por",
    )

    cantidad_devuelta = models.PositiveIntegerField(blank=True, null=True)
    comentario_cierre = models.TextField(blank=True, null=True)
    justificacion_diferencia = models.TextField(blank=True, null=True)

    active = models.BooleanField(default=True)

    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default="pendiente",
    )

    comentario_rechazo = models.TextField(blank=True, null=True)

    aceptado_at = models.DateTimeField(blank=True, null=True)
    rechazado_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        verbose_name = "Tool assignment"
        verbose_name_plural = "Tool assignments"
        ordering = ["-asignado_at"]
        indexes = [
            models.Index(fields=["active", "estado"]),
            models.Index(fields=["asignado_a", "active"]),
            models.Index(fields=["herramienta", "active"]),
        ]

    def __str__(self) -> str:
        return f"{self.herramienta} -> {self.asignado_a} ({self.estado})"

    def clean(self):
        if self.estado == "rechazada":
            if not (self.comentario_rechazo or "").strip():
                raise ValidationError(
                    {"comentario_rechazo": "A rejection comment is required."}
                )

        if self.cantidad_entregada is None or int(self.cantidad_entregada) <= 0:
            raise ValidationError(
                {"cantidad_entregada": "Delivered quantity must be greater than 0."}
            )

        if self.estado == "terminada":
            if self.cantidad_devuelta is None:
                raise ValidationError(
                    {"cantidad_devuelta": "Returned quantity is required."}
                )

            if int(self.cantidad_devuelta) < 0:
                raise ValidationError(
                    {"cantidad_devuelta": "Returned quantity is invalid."}
                )

            if int(self.cantidad_devuelta) > int(self.cantidad_entregada):
                raise ValidationError(
                    {
                        "cantidad_devuelta": (
                            "Returned quantity cannot be greater than delivered quantity."
                        )
                    }
                )

            dev = int(self.cantidad_devuelta)
            ent = int(self.cantidad_entregada)

            if dev < ent and not (self.justificacion_diferencia or "").strip():
                raise ValidationError(
                    {
                        "justificacion_diferencia": (
                            "You must justify the missing/damaged/lost quantity."
                        )
                    }
                )

            if dev == 0 and not (self.comentario_cierre or "").strip():
                raise ValidationError(
                    {
                        "comentario_cierre": (
                            "If returned quantity is 0, a closing comment is required."
                        )
                    }
                )

    def close(self):
        self.active = False
        self.save(update_fields=["active"])


class HerramientaInventario(models.Model):
    ESTADO_CHOICES = [
        ("pendiente", "Pending"),
        ("aprobado", "Approved"),
        ("rechazado", "Rejected"),
    ]

    herramienta = models.ForeignKey(
        Herramienta,
        on_delete=models.CASCADE,
        related_name="inventarios",
    )

    asignacion = models.ForeignKey(
        HerramientaAsignacion,
        on_delete=models.CASCADE,
        related_name="inventarios",
    )

    foto = models.ImageField(
        upload_to=upload_to_herramienta_inventario,
        max_length=1024,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    estado = models.CharField(
        max_length=20,
        choices=ESTADO_CHOICES,
        default="pendiente",
    )

    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventarios_revisados",
    )

    revisado_at = models.DateTimeField(blank=True, null=True)
    motivo_rechazo = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Tool inventory"
        verbose_name_plural = "Tool inventories"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["estado"]),
        ]

    def __str__(self) -> str:
        return f"Inventory {self.herramienta.serial} ({self.estado})"

    def approve(self, by_user):
        self.estado = "aprobado"
        self.revisado_por = by_user
        self.revisado_at = timezone.now()
        self.motivo_rechazo = None

        self.save(
            update_fields=[
                "estado",
                "revisado_por",
                "revisado_at",
                "motivo_rechazo",
            ]
        )

        h = self.herramienta
        h.last_inventory_at = timezone.now()
        h.inventory_required = False
        h.mark_inventory_due_default()

        h.save(
            update_fields=[
                "last_inventory_at",
                "inventory_required",
                "next_inventory_due",
                "updated_at",
            ]
        )

    def reject(self, by_user, motivo: str):
        motivo = (motivo or "").strip()

        if not motivo:
            raise ValidationError("A rejection reason is required.")

        self.estado = "rechazado"
        self.revisado_por = by_user
        self.revisado_at = timezone.now()
        self.motivo_rechazo = motivo

        self.save(
            update_fields=[
                "estado",
                "revisado_por",
                "revisado_at",
                "motivo_rechazo",
            ]
        )

        h = self.herramienta
        h.inventory_required = True
        h.save(update_fields=["inventory_required", "updated_at"])


class HerramientaAsignacionLog(models.Model):
    """
    Audit trail for tool assignment changes.
    """

    ACCION_CHOICES = [
        ("create", "Created"),
        ("update", "Updated"),
        ("close", "Closed"),
        ("reset", "Reset"),
        ("delete", "Deleted"),
        ("inventario_solicitado", "Inventory requested"),
    ]

    asignacion = models.ForeignKey(
        HerramientaAsignacion,
        on_delete=models.CASCADE,
        related_name="logs",
    )

    accion = models.CharField(
        max_length=40,
        choices=ACCION_CHOICES,
    )

    by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="herramientas_asignaciones_logs",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    cambios = models.JSONField(default=dict, blank=True)

    nota = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["accion", "created_at"]),
            models.Index(fields=["asignacion", "created_at"]),
        ]

    def __str__(self):
        return f"{self.asignacion_id} {self.accion} {self.created_at}"
