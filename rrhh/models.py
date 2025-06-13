from django.db import models
from django.contrib.auth import get_user_model
from liquidaciones.models import cloudinary_storage

User = get_user_model()


def ruta_contrato_trabajo(instance, filename):
    rut = instance.tecnico.identidad or f"usuario_{instance.tecnico.id}"
    rut_limpio = rut.replace('.', '').replace('-', '')
    return f"media/Contratos_trabajo/{rut_limpio}/{filename}"


class ContratoTrabajo(models.Model):
    tecnico = models.ForeignKey(User, on_delete=models.CASCADE)
    fecha_inicio = models.DateField()
    fecha_termino = models.DateField(
        null=True, blank=True)  # 🟢 Permite "Indefinido"
    archivo = models.FileField(
        upload_to=ruta_contrato_trabajo,
        storage=cloudinary_storage,
        verbose_name="Archivo del contrato"
    )

    def __str__(self):
        return f"Contrato de {self.tecnico.get_full_name()}"
