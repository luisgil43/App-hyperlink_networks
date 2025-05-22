from django.db import models
from django.contrib.auth.models import User


class ProduccionTecnico(models.Model):
    ESTADOS = [
        ('pendiente', 'Pendiente'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    tecnico = models.ForeignKey(User, on_delete=models.CASCADE)
    id = models.CharField(max_length=100, unique=True, primary_key=True)
    status = models.CharField(max_length=20, choices=ESTADOS)
    fecha_aprobacion = models.DateField(null=True, blank=True)
    descripcion = models.TextField(blank=True)
    monto = models.DecimalField(
        max_digits=10, decimal_places=2)  # Monto a cobrar

    def __str__(self):
        return f"{self.tecnico} - {self.id}"
