from django.contrib import admin
from .models import ProduccionTecnico


@admin.register(ProduccionTecnico)
class ProduccionTecnicoAdmin(admin.ModelAdmin):
    list_display = ('tecnico', 'id', 'status',
                    'fecha_aprobacion', 'descripcion', 'monto')
    fields = ('tecnico', 'id', 'status',
              'fecha_aprobacion', 'descripcion', 'monto')
