from datetime import date, datetime
from django.contrib.auth import get_user_model
from django.db import models
from django.conf import settings
from usuarios.models import CustomUser
from utils.paths import upload_to  # 👈 Importamos el upload dinámico
import os


class Bodega(models.Model):
    nombre = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.nombre


class Material(models.Model):
    codigo_interno = models.CharField(max_length=50)
    nombre = models.CharField(max_length=255)
    codigo_externo = models.CharField(
        max_length=50, blank=True, null=True)
    bodega = models.ForeignKey(
        Bodega, on_delete=models.SET_NULL, null=True, blank=True)
    stock_actual = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    unidad_medida = models.CharField(max_length=50)
    descripcion = models.TextField(blank=True)
    activo = models.BooleanField(default=True)
    valor_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        verbose_name="Valor unitario ($)"
    )

    def __str__(self):
        return f"{self.codigo_interno} - {self.nombre}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['codigo_interno', 'bodega'],
                name='unique_codigo_interno_por_bodega'
            ),
            models.UniqueConstraint(
                fields=['codigo_externo', 'bodega'],
                name='unique_codigo_externo_por_bodega'
            )
        ]


class IngresoMaterial(models.Model):
    OPCIONES_TIPO_DOC = [
        ('guia', 'Guía de Despacho'),
        ('factura', 'Factura'),
    ]

    fecha_ingreso = models.DateField(auto_now_add=True)
    tipo_documento = models.CharField(max_length=10, choices=OPCIONES_TIPO_DOC)
    numero_documento = models.CharField(max_length=50)
    codigo_externo = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Código externo")

    bodega = models.ForeignKey(
        Bodega, on_delete=models.PROTECT, related_name='ingresos')

    archivo_documento = models.FileField(
        upload_to=upload_to,
        verbose_name="PDF de respaldo"
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.numero_documento} - {self.get_tipo_documento_display()}"


class DetalleIngresoMaterial(models.Model):
    ingreso = models.ForeignKey(
        IngresoMaterial,
        on_delete=models.CASCADE,
        related_name='detalles'
    )
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    cantidad = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.material.nombre} - {self.cantidad}"


class ArchivoCAF(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    nombre_archivo = models.CharField(max_length=255)
    archivo = models.FileField(
        upload_to=upload_to,
        verbose_name="Archivo CAF (.xml)"
    )
    tipo_dte = models.PositiveIntegerField()
    rango_inicio = models.PositiveIntegerField()
    rango_fin = models.PositiveIntegerField()
    fecha_subida = models.DateTimeField(auto_now_add=True)
    estado = models.CharField(max_length=20, choices=[
        ('activo', 'Activo'), ('inactivo', 'Inactivo')])

    def __str__(self):
        return f"{self.nombre_archivo} (TD {self.tipo_dte})"


TIPO_DOCUMENTO_CHOICES = [
    ('guia', 'Guía de Despacho'),
    ('factura', 'Factura'),
    ('nota_credito', 'Nota de Crédito'),
    ('nota_debito', 'Nota de Débito'),
    ('otro', 'Otro'),
]


class FolioDisponible(models.Model):
    caf = models.ForeignKey(ArchivoCAF, on_delete=models.CASCADE)
    folio = models.IntegerField()
    usado = models.BooleanField(default=False)

    class Meta:
        unique_together = ('folio', 'caf')

    def __str__(self):
        return f"{self.caf.tipo_dte} - Folio {self.folio}"


class DetalleSalidaMaterial(models.Model):
    salida = models.ForeignKey(
        'SalidaMaterial', on_delete=models.CASCADE, related_name='detalles'
    )
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    descripcion = models.CharField(max_length=255, blank=True)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)
    valor_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)
    descuento = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)

    def calcular_valor_total(self):
        bruto = self.cantidad * self.valor_unitario
        return max(bruto - self.descuento, 0)

    def __str__(self):
        return f"{self.material.nombre} - {self.cantidad}"


User = get_user_model()


class CertificadoDigital(models.Model):
    archivo = models.FileField(
        upload_to=upload_to,
        verbose_name="Archivo .pfx"
    )
    clave_certificado = models.CharField(max_length=255)
    rut_emisor = models.CharField(max_length=20)
    fecha_inicio = models.DateField(auto_now_add=True)
    activo = models.BooleanField(default=True)
    usuario = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Certificado de {self.rut_emisor}"


TIPO_DOCUMENTO_CHOICES = [
    ('guia_despacho', 'Guía de Despacho'),
]


class SalidaMaterial(models.Model):
    fecha_salida = models.DateField(auto_now_add=True)
    bodega = models.ForeignKey(Bodega, on_delete=models.CASCADE)
    id_proyecto = models.CharField(max_length=100)
    tipo_documento = models.CharField(
        max_length=20, choices=TIPO_DOCUMENTO_CHOICES)
    numero_documento = models.CharField(max_length=50)

    entregado_a = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='entregado_salidas'
    )
    emitido_por = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='emitido_salidas'
    )

    archivo_pdf = models.FileField(
        upload_to=upload_to,
        null=True,
        blank=True
    )

    archivo_xml = models.FileField(
        upload_to=upload_to,
        null=True,
        blank=True,
        verbose_name="XML firmado"
    )

    # Datos del receptor
    rut_receptor = models.CharField(max_length=15)
    nombre_receptor = models.CharField(max_length=255)
    giro_receptor = models.CharField(max_length=255)
    direccion_receptor = models.CharField(max_length=255)
    comuna_receptor = models.CharField(max_length=100)
    ciudad_receptor = models.CharField(max_length=100)
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
            ('pendiente', 'Pendiente'),
            ('enviado', 'Enviado'),
            ('aceptado', 'Aceptado'),
            ('rechazado', 'Rechazado'),
        ],
        default='pendiente'
    )
    mensaje_sii = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Guía #{self.numero_documento} - {self.fecha_salida}"
