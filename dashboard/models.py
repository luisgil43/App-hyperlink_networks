from django.db import models
from django.contrib.auth.models import User


class ProduccionTecnico(models.Model):
    STATUS_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('aprobado', 'Aprobado'),
        ('rechazado', 'Rechazado'),
    ]

    id = models.IntegerField(primary_key=True, verbose_name="ID Manual")
    tecnico = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='producciones')
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='pendiente')
    fecha_aprobacion = models.DateField(blank=True, null=True)
    descripcion = models.TextField()
    monto = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"{self.id} - {self.tecnico.username}"
