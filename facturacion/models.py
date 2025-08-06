from django.utils.module_loading import import_string
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from decimal import Decimal
from operaciones.models import ServicioCotizado
from utils.paths import upload_to  # 👈 Nuevo import


class OrdenCompraFacturacion(models.Model):
    du = models.ForeignKey(ServicioCotizado, on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='ordenes_compra')
    orden_compra = models.CharField(
        "Orden de Compra", max_length=30, blank=True, null=True)
    pos = models.CharField("POS", max_length=10, blank=True, null=True)
    cantidad = models.DecimalField(
        "Cantidad", max_digits=10, decimal_places=2, default=Decimal('0.00'))
    unidad_medida = models.CharField(
        "UM", max_length=10, blank=True, null=True)
    material_servicio = models.CharField(
        "Material/Servicio", max_length=100, blank=True, null=True)
    descripcion_sitio = models.TextField(
        "Descripción / Sitio", blank=True, null=True)
    fecha_entrega = models.DateField("Fecha de Entrega", blank=True, null=True)
    precio_unitario = models.DecimalField(
        "Precio Unitario", max_digits=10, decimal_places=2, default=Decimal('0.00'))
    monto = models.DecimalField(
        "Monto", max_digits=12, decimal_places=2, default=Decimal('0.00'))
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Orden de Compra Facturación"
        verbose_name_plural = "Órdenes de Compra Facturación"

    def __str__(self):
        return f"OC {self.orden_compra} - DU: {self.du.du if self.du else 'Sin DU'}"


class FacturaOC(models.Model):
    orden_compra = models.OneToOneField(
        OrdenCompraFacturacion,
        on_delete=models.CASCADE,
        related_name='factura',
        verbose_name="Orden de Compra"
    )
    hes = models.CharField("HES", max_length=50, blank=True, null=True)
    valor_en_clp = models.DecimalField(
        "Valor en CLP", max_digits=15, decimal_places=2, blank=True, null=True)
    conformidad = models.CharField(
        "Conformidad", max_length=50, blank=True, null=True)
    num_factura = models.CharField(
        "Número de Factura", max_length=50, blank=True, null=True)
    fecha_facturacion = models.DateField(
        "Fecha de Facturación", blank=True, null=True)
    mes_produccion = models.CharField(
        "Mes de Producción", max_length=20, blank=True, null=True)
    factorizado = models.BooleanField("¿Factorizado?", default=False)
    fecha_factoring = models.DateField(
        "Fecha de Factoring", blank=True, null=True)
    cobrado = models.BooleanField("¿Cobrado?", default=False)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Factura"
        verbose_name_plural = "Facturas"

    def __str__(self):
        return f"Factura {self.num_factura or 'Sin número'} - OC {self.orden_compra.orden_compra}"

    def get_status_factura(self):
        if not self.conformidad:
            return "Pendiente por Conformidad"
        if not self.num_factura:
            return "Pendiente por Facturación"
        status = "Facturado"
        if self.factorizado:
            status = "En proceso de Factoring"
        if self.cobrado:
            status = "Cobrado"
        return status


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
    tipo_doc = models.CharField(max_length=20, choices=TIPO_DOC_CHOICES,
                                blank=True, null=True, verbose_name="Tipo de Documento")
    numero_doc = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Número de Documento")
    observaciones = models.TextField(blank=True, null=True)
    numero_transferencia = models.CharField(
        max_length=100, blank=True, null=True)

    comprobante = models.FileField(
        upload_to='facturacion/cartolamovimiento/',
        storage=wasabi_storage,  # Fuerza Wasabi
        blank=True,
        null=True,
        verbose_name="Comprobante",
        validators=[FileExtensionValidator(['pdf', 'jpg', 'jpeg', 'png'])]
    )

    aprobado_por_supervisor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                                null=True, blank=True, related_name='rendiciones_aprobadas_supervisor')
    aprobado_por_pm = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                        null=True, blank=True, related_name='rendiciones_aprobadas_pm')
    aprobado_por_finanzas = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                              null=True, blank=True, related_name='rendiciones_aprobadas_finanzas')
    cargos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    abonos = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=50, choices=ESTADOS, default='pendiente_abono_usuario')
    motivo_rechazo = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.usuario} - {self.proyecto} - {self.tipo} - {self.fecha}"
