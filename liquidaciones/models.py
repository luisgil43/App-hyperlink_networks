from django.db import models
from tecnicos.models import Tecnico
from cloudinary_storage.storage import MediaCloudinaryStorage


class Liquidacion(models.Model):
    tecnico = models.ForeignKey(Tecnico, on_delete=models.CASCADE)
    mes = models.PositiveIntegerField()
    año = models.PositiveIntegerField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    firmada = models.BooleanField(default=False)

    archivo_pdf_liquidacion = models.FileField(
        upload_to='pdf_originales/',
        storage=MediaCloudinaryStorage(),
        blank=True,
        null=True,
        verbose_name="Liquidación de sueldo"
    )

    pdf_firmado = models.FileField(
        upload_to='pdf_firmados/',
        storage=MediaCloudinaryStorage(),
        blank=True,
        null=True,
        verbose_name="Liquidación de sueldo firmada"
    )

    fecha_firma = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.tecnico} - {self.mes}/{self.año}"

    def save(self, *args, **kwargs):
        # ✅ Verifica si el archivo original fue reemplazado
        try:
            old = Liquidacion.objects.get(pk=self.pk)
        except Liquidacion.DoesNotExist:
            old = None

        if old and old.archivo_pdf_liquidacion != self.archivo_pdf_liquidacion:
            # ✅ Si se cambió el PDF original, borrar firma anterior
            if old.pdf_firmado:
                old.pdf_firmado.delete(save=False)
            self.pdf_firmado = None
            self.firmada = False
            self.fecha_firma = None

        super().save(*args, **kwargs)

        # 🔁 Código eliminado (comentado para referencia futura)
        # No se aplicaron otras eliminaciones directas
