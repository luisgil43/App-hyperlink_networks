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
    fecha_termino = models.DateField(null=True, blank=True)
    archivo = models.FileField(
        upload_to=ruta_contrato_trabajo,
        storage=cloudinary_storage,
        verbose_name="Archivo del contrato"
    )

    def __str__(self):
        return f"Contrato de {self.tecnico.get_full_name()}"

    def save(self, *args, **kwargs):
        try:
            old = ContratoTrabajo.objects.get(pk=self.pk)
        except ContratoTrabajo.DoesNotExist:
            old = None

        if (
            old and
            old.archivo and self.archivo and
            old.archivo.name != self.archivo.name
        ):
            if old.archivo.storage.exists(old.archivo.name):
                old.archivo.delete(save=False)

        super().save(*args, **kwargs)


def ruta_ficha_ingreso(instance, filename):
    identidad = instance.tecnico.identidad or f"usuario_{instance.tecnico.id}"
    identidad_limpia = identidad.replace('.', '').replace('-', '')
    return f"media/fichas_de_ingreso/{identidad_limpia}/{filename}"


class FichaIngreso(models.Model):
    tecnico = models.ForeignKey(User, on_delete=models.CASCADE)
    archivo = models.FileField(
        upload_to=ruta_ficha_ingreso,
        storage=cloudinary_storage,
        verbose_name="Ficha de Ingreso (PDF)"
    )

    def __str__(self):
        return f"Ficha de ingreso de {self.tecnico.get_full_name()}"

    def save(self, *args, **kwargs):
        try:
            old = FichaIngreso.objects.get(pk=self.pk)
        except FichaIngreso.DoesNotExist:
            old = None

        if (
            old and old.archivo and self.archivo and
            old.archivo.name != self.archivo.name
        ):
            if old.archivo.storage.exists(old.archivo.name):
                old.archivo.delete(save=False)

        super().save(*args, **kwargs)
