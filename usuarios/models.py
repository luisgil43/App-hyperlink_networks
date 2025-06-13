from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string
from django.core.exceptions import ImproperlyConfigured

# ✅ Clase de carga diferida para usar Cloudinary dinámicamente


def ruta_firma_usuario(instance, filename):
    return f"media/firmas/usuario_{instance.id}_firma.png"


class LazyCloudinaryStorage(LazyObject):
    def _setup(self):
        storage_path = getattr(settings, 'DEFAULT_FILE_STORAGE', '')
        if not storage_path:
            raise ImproperlyConfigured(
                "DEFAULT_FILE_STORAGE no está definido en settings.")
        self._wrapped = import_string(storage_path)()


# ✅ Reutilizable para todos los FileField/ImageField
cloudinary_storage = LazyCloudinaryStorage()


class CustomUser(AbstractUser):
    identidad = models.CharField(max_length=20, blank=True, null=True)

    firma_digital = models.ImageField(
        upload_to=ruta_firma_usuario,
        storage=cloudinary_storage,
        blank=True,
        null=True
    )

    def __str__(self):
        return f"{self.identidad} - {self.first_name} {self.last_name}"
