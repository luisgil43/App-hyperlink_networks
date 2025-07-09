import json

from django.utils import timezone
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string
from django.core.exceptions import ImproperlyConfigured
from datetime import timedelta, date
from django.db.models import Sum
from django.core.files.storage import default_storage
from cloudinary_storage.storage import MediaCloudinaryStorage

# ✅ Firma en Cloudinary


def ruta_firma_usuario(instance, filename):
    # Asegúrate de que `identidad` existe en tu modelo de usuario
    identidad = instance.identidad
    return f"media/firmas/{identidad}/{identidad}_firma.png"


class LazyCloudinaryStorage(LazyObject):
    def _setup(self):
        storage_path = getattr(settings, 'DEFAULT_FILE_STORAGE', '')
        if not storage_path:
            raise ImproperlyConfigured(
                "DEFAULT_FILE_STORAGE no está definido en settings.")
        self._wrapped = import_string(storage_path)()


cloudinary_storage = LazyCloudinaryStorage()


class Rol(models.Model):
    nombre = models.CharField(max_length=30, unique=True)

    def __str__(self):
        return self.nombre


class CustomUser(AbstractUser):
    identidad = models.CharField(max_length=20, blank=True, null=True)
    roles = models.ManyToManyField("usuarios.Rol", blank=True)

    # Firma digital
    firma_digital = models.ImageField(
        upload_to=ruta_firma_usuario,
        storage=cloudinary_storage,
        blank=True,
        null=True
    )

    # Responsables jerárquicos
    supervisor = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supervisados')
    pm = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='pms')
    rrhh_encargado = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='rrhhs')
    prevencionista = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='prevencionistas')
    logistica_encargado = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='logisticas')
    es_bodeguero_encargado = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='bodeguero')
    encargado_flota = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='flotas')
    encargado_subcontrato = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='subcontratos')
    encargado_facturacion = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='facturaciones')

    # Días consumidos externamente
    dias_vacaciones_consumidos = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text="Días de vacaciones que ya ha consumido fuera del sistema"
    )

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def tiene_rol(self, nombre_rol):
        return self.roles.filter(nombre=nombre_rol).exists()

    @property
    def es_usuario(self): return self.tiene_rol('usuario') or self.is_superuser

    @property
    def es_supervisor(self): return self.tiene_rol(
        'supervisor') or self.is_superuser

    @property
    def es_pm(self): return self.tiene_rol('pm') or self.is_superuser
    @property
    def es_rrhh(self): return self.tiene_rol('rrhh') or self.is_superuser

    @property
    def es_prevencion(self): return self.tiene_rol(
        'prevencion') or self.is_superuser

    @property
    def es_logistica(self): return self.tiene_rol(
        'logistica') or self.is_superuser

    @property
    def es_bodeguero(self): return self.tiene_rol(
        'bodeguero') or self.is_superuser

    @property
    def es_flota(self): return self.tiene_rol('flota') or self.is_superuser

    @property
    def es_subcontrato(self): return self.tiene_rol(
        'subcontrato') or self.is_superuser

    @property
    def es_facturacion(self): return self.tiene_rol(
        'facturacion') or self.is_superuser

    @property
    def es_admin_general(self): return self.tiene_rol(
        'admin') or self.is_superuser

    @property
    def rol(self):
        primer_rol = self.roles.first()
        return primer_rol.nombre if primer_rol else None

    def obtener_dias_vacaciones_disponibles(self):
        from rrhh.models import SolicitudVacaciones, ContratoTrabajo

        contrato = ContratoTrabajo.objects.filter(
            tecnico=self).order_by('fecha_inicio').first()

        if not contrato or not contrato.fecha_inicio:
            return 0

        dias_trabajados = (date.today() - contrato.fecha_inicio).days
        dias_generados = dias_trabajados * 0.04166

        dias_consumidos_manualmente = float(
            self.dias_vacaciones_consumidos or 0)

        dias_aprobados = SolicitudVacaciones.objects.filter(
            usuario=self, estatus='aprobada'
        ).aggregate(total=Sum('dias_solicitados'))['total'] or 0

        total_disponible = dias_generados - \
            dias_consumidos_manualmente - float(dias_aprobados)
        return round(total_disponible, 2)

    def __str__(self):
        nombre = self.get_full_name() or self.username
        return f"{self.identidad or 'Sin RUT'} - {nombre}"


def ruta_firma_representante(instance, filename):
    return f"firmas_representante_legal/{filename}"


class FirmaRepresentanteLegal(models.Model):
    archivo = models.FileField(
        upload_to=ruta_firma_representante,
        storage=MediaCloudinaryStorage(),
        verbose_name="Firma del Representante Legal"
    )
    fecha_subida = models.DateTimeField(null=False, blank=False)

    def __str__(self):
        return f"Firma representante legal (ID: {self.pk})"


class Notificacion(models.Model):
    usuario = models.ForeignKey(
        'usuarios.CustomUser', on_delete=models.CASCADE)
    mensaje = models.TextField()
    url = models.URLField(null=True, blank=True)
    tipo = models.CharField(max_length=20, default='info')
    leido = models.BooleanField(default=False)
    fecha = models.DateTimeField(auto_now_add=True)

    # Cambia esto
    para_roles = models.TextField(
        null=True, blank=True)  # ← Antes era JSONField

    def roles_lista(self):
        try:
            return json.loads(self.para_roles or '[]')
        except json.JSONDecodeError:
            return []

    class Meta:
        ordering = ['-fecha']
