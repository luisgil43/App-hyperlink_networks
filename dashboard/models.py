from django.db import models
from django.contrib.auth.models import User


class ProduccionTecnico(models.Model):
    # Lista desplegable para el campo status
    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    tecnico = models.ForeignKey(User, on_delete=models.CASCADE)

    # ✅ id definido como editable (ya es correcto)
    id = models.CharField(
        max_length=100,
        unique=True,
        primary_key=True,
        verbose_name="ID de Producción"
    )

    status = models.CharField(
        max_length=20,
        choices=ESTADOS,
        verbose_name="Estado"
    )

    fecha_aprobacion = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha de Aprobación"
    )

    descripcion = models.TextField(
        blank=True,
        verbose_name="Descripción"
    )

    monto = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Monto"
    )

    def __str__(self):
        return f"{self.tecnico} - {self.id}"
