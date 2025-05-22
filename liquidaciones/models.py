from django.db import models
from django.contrib.auth.models import User
from django.contrib import admin
from tecnicos.models import Tecnico


class Liquidacion(models.Model):
    tecnico = models.ForeignKey(Tecnico, on_delete=models.CASCADE)
    mes = models.PositiveIntegerField()
    año = models.PositiveIntegerField()
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    firmada = models.BooleanField(default=False)
    archivo_pdf_liquidacion = models.FileField(
        upload_to='pdf_originales/', blank=True, null=True, verbose_name="Liquidación de sueldo")
    fecha_firma = models.DateTimeField(blank=True, null=True)
    pdf_firmado = models.FileField(
        upload_to='pdf_firmados/', blank=True, null=True, verbose_name="Liquidación de sueldo firmada")

    def __str__(self):
        return f"{self.tecnico} - {self.mes}/{self.año}"
