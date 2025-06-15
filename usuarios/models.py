from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string
from django.core.exceptions import ImproperlyConfigured

# ✅ Firma en Cloudinary


def ruta_firma_usuario(instance, filename):
    return f"media/firmas/usuario_{instance.id}_firma.png"


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
    roles = models.ManyToManyField(Rol, blank=True)

    firma_digital = models.ImageField(
        upload_to=ruta_firma_usuario,
        storage=cloudinary_storage,
        blank=True,
        null=True
    )

    def tiene_rol(self, nombre_rol):
        return self.roles.filter(nombre=nombre_rol).exists()

    @property
    def es_usuario(self):
        return self.tiene_rol('usuario')

    @property
    def es_supervisor(self):
        return self.tiene_rol('supervisor')

    @property
    def es_pm(self):
        return self.tiene_rol('pm')

    @property
    def es_rrhh(self):
        return self.tiene_rol('rrhh')

    @property
    def es_prevencion(self):
        return self.tiene_rol('prevencion')

    @property
    def es_logistica(self):
        return self.tiene_rol('logistica')

    @property
    def es_flota(self):
        return self.tiene_rol('flota')

    @property
    def es_subcontrato(self):
        return self.tiene_rol('subcontrato')

    @property
    def es_facturacion(self):
        return self.tiene_rol('facturacion')

    @property
    def es_admin_general(self):
        return self.tiene_rol('admin')

    def __str__(self):
        return f"{self.identidad or self.username} - {self.first_name} {self.last_name}"
