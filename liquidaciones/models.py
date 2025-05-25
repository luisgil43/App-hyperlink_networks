from django.db import models
from tecnicos.models import Tecnico
from cloudinary_storage.storage import MediaCloudinaryStorage


def ruta_archivo_sin_firmar(instance, filename):
    return f"liquidaciones_sin_firmar/{instance.año}_{instance.mes}/{filename}"


def ruta_archivo_firmado(instance, filename):
    return f"liquidaciones_firmadas/{instance.año}_{instance.mes}/{filename}"


class Liquidacion(models.Model):
    tecnico = models.ForeignKey(Tecnico, on_delete=models.CASCADE)
    mes = models.PositiveIntegerField()
    año = models.PositiveIntegerField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    firmada = models.BooleanField(default=False)

    archivo_pdf_liquidacion = models.FileField(
        upload_to=ruta_archivo_sin_firmar,
        storage=MediaCloudinaryStorage(),
        blank=True,
        null=True,
        verbose_name="Liquidación de sueldo"
    )

    pdf_firmado = models.FileField(
        upload_to=ruta_archivo_firmado,
        storage=MediaCloudinaryStorage(),
        blank=True,
        null=True,
        verbose_name="Liquidación de sueldo firmada"
    )

    fecha_firma = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.tecnico} - {self.mes}/{self.año}"

    def save(self, *args, **kwargs):
        try:
            old = Liquidacion.objects.get(pk=self.pk)
        except Liquidacion.DoesNotExist:
            old = None

        if old and old.archivo_pdf_liquidacion != self.archivo_pdf_liquidacion:
            if old.pdf_firmado:
                old.pdf_firmado.delete(save=False)
            self.pdf_firmado = None
            self.firmada = False
            self.fecha_firma = None

        super().save(*args, **kwargs)

    class Meta:
        unique_together = ('tecnico', 'mes', 'año')
