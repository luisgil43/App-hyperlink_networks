from django.db import models
from django.conf import settings
from django_select2.forms import ModelSelect2Widget
from django.contrib.auth import get_user_model
from utils.paths import upload_to  # 👈 Nuevo import


class Liquidacion(models.Model):
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    mes = models.PositiveIntegerField()
    año = models.PositiveIntegerField()

    archivo_pdf_liquidacion = models.FileField(
        upload_to=upload_to,
        blank=True,
        null=True,
        verbose_name="Liquidación de Sueldo"
    )

    pdf_firmado = models.FileField(
        upload_to=upload_to,
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

        if (
            old and
            old.archivo_pdf_liquidacion and self.archivo_pdf_liquidacion and
            old.archivo_pdf_liquidacion.name != self.archivo_pdf_liquidacion.name
        ):
            if old.archivo_pdf_liquidacion.storage.exists(old.archivo_pdf_liquidacion.name):
                old.archivo_pdf_liquidacion.delete(save=False)

            if old.pdf_firmado and old.pdf_firmado.storage.exists(old.pdf_firmado.name):
                old.pdf_firmado.delete(save=False)

            self.pdf_firmado = None
            self.fecha_firma = None

        self.firmada = bool(self.pdf_firmado)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.archivo_pdf_liquidacion and self.archivo_pdf_liquidacion.storage.exists(self.archivo_pdf_liquidacion.name):
            self.archivo_pdf_liquidacion.delete(save=False)
        if self.pdf_firmado and self.pdf_firmado.storage.exists(self.pdf_firmado.name):
            self.pdf_firmado.delete(save=False)
        super().delete(*args, **kwargs)

    class Meta:
        verbose_name = "Liquidación"
        verbose_name_plural = "Liquidaciones"
        constraints = [
            models.UniqueConstraint(
                fields=['tecnico', 'mes', 'año'],
                name='unique_liquidacion_por_tecnico_mes_anio',
                violation_error_message='Ya existe una liquidación para este técnico en ese mes y año.'
            )
        ]


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
        return str(obj.pk)
