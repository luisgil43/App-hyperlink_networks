from django.db import models
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django_select2.views import AutoResponseView
from django.contrib.auth import get_user_model
from django_select2.forms import ModelSelect2Widget


def ruta_archivo_sin_firmar(instance, filename):
    return f"liquidaciones_sin_firmar/{instance.año}_{instance.mes}/{filename}"


def ruta_archivo_firmado(instance, filename):
    return f"liquidaciones_firmadas/{instance.año}_{instance.mes}/{filename}"


storage_backend = (
    FileSystemStorage() if settings.DEFAULT_FILE_STORAGE == 'django.core.files.storage.FileSystemStorage'
    else None
)


class Liquidacion(models.Model):
    # clave fornaea de usuario
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    mes = models.PositiveIntegerField()
    año = models.PositiveIntegerField()
    monto = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)

    archivo_pdf_liquidacion = models.FileField(
        upload_to=ruta_archivo_sin_firmar,
        storage=storage_backend,
        blank=True,
        null=True,
        verbose_name="Liquidación de Sueldo"
    )

    pdf_firmado = models.FileField(
        upload_to=ruta_archivo_firmado,
        storage=storage_backend,
        blank=True,
        null=True,
        verbose_name="Liquidación de sueldo firmada"
    )

    fecha_firma = models.DateTimeField(blank=True, null=True)
    firmada = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.tecnico} - {self.mes}/{self.año}"

    def save(self, *args, **kwargs):
        try:
            old = Liquidacion.objects.get(pk=self.pk)
        except Liquidacion.DoesNotExist:
            old = None

        # Si se actualiza el archivo PDF original, eliminar firma anterior y limpiar datos
        if (
            old and
            old.archivo_pdf_liquidacion and self.archivo_pdf_liquidacion and
            old.archivo_pdf_liquidacion.name != self.archivo_pdf_liquidacion.name
        ):
            if old.pdf_firmado and old.pdf_firmado.storage.exists(old.pdf_firmado.name):
                old.pdf_firmado.delete(save=False)
            self.pdf_firmado = None
            self.fecha_firma = None

        # Actualizar campo booleano firmada
        self.firmada = bool(self.pdf_firmado)
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Liquidación"
        verbose_name_plural = "Liquidaciones"
        unique_together = ('tecnico', 'mes', 'año')  # evitar duplicados


User = get_user_model()


class UsuarioSelectWidget(ModelSelect2Widget):
    model = User
    search_fields = [
        "identidad__icontains",
        "first_name__icontains",
        "last_name__icontains",
    ]
    url = '/liquidaciones/usuario-autocomplete/'

    def label_from_instance(self, obj):
        return f"{obj.identidad} - {obj.first_name} {obj.last_name}"

    def get_result_value(self, obj):
        return str(obj.pk)  # 👈 ESTA LÍNEA ES LA CLAVE
