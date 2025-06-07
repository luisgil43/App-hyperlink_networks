from django.contrib.auth.models import AbstractUser
from django.db import models
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
class CustomUser(AbstractUser):
    identidad = models.CharField(max_length=20, blank=True, null=True)
    firma_digital = models.ImageField(
        upload_to='firmas/', blank=True, null=True)

    def __str__(self):
        
        Muestra el usuario de forma legible en listas y selectores.
Ej: 255637991 - David Suarez
        
        return f"{self.identidad} - {self.first_name} {self.last_name}"
"""
