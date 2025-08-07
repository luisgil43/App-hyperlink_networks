from django.db.models import Max
from django.db import models
from django.conf import settings
from decimal import Decimal
from usuarios.models import CustomUser
from utils.paths import upload_to  # 👈 Importamos upload dinámico


from django.db import models
from django.conf import settings


class PrecioActividadTecnico(models.Model):
    tecnico = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ciudad = models.CharField(max_length=100)
    proyecto = models.CharField(max_length=200)
    codigo = models.CharField(max_length=50)
    descripcion = models.TextField()
    unidad_medida = models.CharField(max_length=20)
    precio_tecnico = models.DecimalField(max_digits=10, decimal_places=2)
    precio_empresa = models.DecimalField(max_digits=10, decimal_places=2)
    fecha_creacion = models.DateField(auto_now_add=True)

    class Meta:
        unique_together = ('tecnico', 'ciudad', 'proyecto', 'codigo')
        verbose_name = 'Precio por Actividad'
        verbose_name_plural = 'Precios por Actividad'

    def __str__(self):
        return f'{self.codigo} - {self.tecnico} - {self.ciudad}'
