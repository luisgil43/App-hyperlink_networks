from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from django.utils.module_loading import import_string
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from decimal import Decimal
from utils.paths import upload_to  # 👈 Nuevo import


class Proyecto(models.Model):
    nombre = models.CharField(max_length=255)
    mandante = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.nombre} ({self.mandante})"


class TipoGasto(models.Model):
    nombre = models.CharField(max_length=255)
    categoria = models.CharField(max_length=50, choices=[
        ('costo', 'Cost'),
        ('inversion', 'Investment'),
        ('gasto', 'Expense'),
        ('abono', 'Deposit'),
    ])

    def __str__(self):
        return self.nombre


WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()


class CartolaMovimiento(models.Model):
    ESTADOS = [
        ('pendiente_abono_usuario', 'Pending User Approval'),
        ('aprobado_abono_usuario', 'Credit Approved by User'),
        ('rechazado_abono_usuario', 'Credit Rejected by User'),
        ('pendiente_supervisor', 'Pending Supervisor Approval'),
        ('aprobado_supervisor', 'Approved by Supervisor'),
        ('rechazado_supervisor', 'Rejected by Supervisor'),
        ('aprobado_pm', 'Approved by PM'),
        ('rechazado_pm', 'Rejected by PM'),
        ('aprobado_finanzas', 'Approved by Finance'),
        ('rechazado_finanzas', 'Rejected by Finance'),
    ]

    TIPO_DOC_CHOICES = [
        ('boleta', 'Boleta'),
        ('factura', 'Factura'),
        ('otros', 'Otros'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    fecha = models.DateTimeField(auto_now_add=True, editable=False)
    proyecto = models.ForeignKey(
        'Proyecto', on_delete=models.SET_NULL, null=True, blank=True)
    tipo = models.ForeignKey(
        'TipoGasto', on_delete=models.SET_NULL, null=True, blank=True)

    rut_factura = models.CharField(max_length=12, blank=True, null=True)
    tipo_doc = models.CharField(
        max_length=20, choices=TIPO_DOC_CHOICES,
        blank=True, null=True, verbose_name="Document Type"
    )
    numero_doc = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Document Number"
    )
    observaciones = models.TextField(blank=True, null=True)
    numero_transferencia = models.CharField(
        max_length=100, blank=True, null=True)

    comprobante = models.FileField(
        upload_to=upload_to,
        storage=wasabi_storage,   # Wasabi storage
        blank=True, null=True,
        verbose_name="Receipt",
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )

    kilometraje = models.PositiveIntegerField(
        blank=True, null=True, help_text="Vehicle odometer (km)"
    )
    foto_tablero = models.FileField(
        upload_to=upload_to,
        storage=wasabi_storage,
        blank=True, null=True,
        verbose_name="Odometer photo (dashboard)",
        validators=[FileExtensionValidator(['jpg', 'jpeg', 'png'])]
    )

    aprobado_por_supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rendiciones_aprobadas_supervisor'
    )
    aprobado_por_pm = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rendiciones_aprobadas_pm'
    )
    aprobado_por_finanzas = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='rendiciones_aprobadas_finanzas'
    )

    cargos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    abonos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=50, choices=ESTADOS, default='pendiente_abono_usuario')
    motivo_rechazo = models.TextField(blank=True, null=True)

    def clean(self):
        super().clean()
        # Requirements when the expense type is "Fuel"
        tipo_nombre = (self.tipo.nombre if self.tipo else "") or ""
        if tipo_nombre.strip().lower() == "fuel":
            if not self.kilometraje:
                raise ValidationError({
                    "kilometraje": _("Odometer (km) is required for Fuel expenses.")
                })

            # flags set by the ModelForm when presigned keys exist
            skip_receipt = getattr(self, "_skip_receipt_required", False)
            skip_odo = getattr(self, "_skip_odo_required", False)

            if not self.comprobante and not skip_receipt:
                raise ValidationError({
                    "comprobante": _("A receipt (photo or file) is required for Fuel expenses.")
                })
            if not self.foto_tablero and not skip_odo:
                raise ValidationError({
                    "foto_tablero": _("A dashboard (odometer) photo is required for Fuel expenses.")
                })

    def __str__(self):
        return f"{self.usuario} - {self.proyecto} - {self.tipo} - {self.fecha}"
