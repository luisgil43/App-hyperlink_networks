from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


class ProduccionTecnico(models.Model):
    # Lista desplegable para el campo status
    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    tecnico = models.ForeignKey(User, on_delete=models.CASCADE)

    # id editable y único
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

    def clean(self):
        # Normaliza el campo status para que coincida con las claves válidas
        mapa = {
            'pendiente': 'pendiente',
            'Pendiente': 'pendiente',
            'PENDIENTE': 'pendiente',
            'aprobado': 'aprobado',
            'Aprobado': 'aprobado',
            'APROBADO': 'aprobado',
            'rechazado': 'rechazado',
            'Rechazado': 'rechazado',
            'RECHAZADO': 'rechazado',
        }
        if self.status not in mapa.values():
            if self.status in mapa:
                self.status = mapa[self.status]
            else:
                raise ValidationError({'status': 'Estado inválido.'})

    def save(self, *args, **kwargs):
        self.clean()  # Asegura que status esté correcto antes de guardar
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tecnico} - {self.id}"
