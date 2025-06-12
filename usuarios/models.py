from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string
from django.core.exceptions import ImproperlyConfigured

# ✅ Clase de carga diferida para usar Cloudinary dinámicamente


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
        upload_to='firmas/',  # Subcarpeta lógica en Cloudinary
        storage=cloudinary_storage,  # ✅ Usa Cloudinary si está activo
        blank=True,
        null=True
    )

    def __str__(self):
        return f"{self.identidad} - {self.first_name} {self.last_name}"


"""
from django.contrib.auth.models import AbstractUser
from django.db import models
# reutiliza el mismo objeto que usas en Liquidacion
from django.core.files.storage import default_storage  # <-- agrega esta línea


class CustomUser(AbstractUser):
    identidad = models.CharField(max_length=20, blank=True, null=True)

    firma_digital = models.ImageField(
        upload_to='firmas/',
        storage=default_storage,  # <-- usa el backend configurado dinámicamente
        blank=True,
        null=True
    )

    def __str__(self):
        return f"{self.identidad} - {self.first_name} {self.last_name}"
"""
