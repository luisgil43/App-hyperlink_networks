from django.contrib import admin
# Verifica el nombre exacto del modelo, si es Produccion en tu models.py cambia acá también
from .models import ProduccionTecnico
from tecnicos.models import Curso


@admin.register(ProduccionTecnico)
class ProduccionTecnicoAdmin(admin.ModelAdmin):
    list_display = ('id', 'tecnico', 'status',
                    'fecha_aprobacion', 'monto')
    list_filter = ('status', 'fecha_aprobacion')
    search_fields = ('id', 'tecnico__user__username', 'descripcion')
    ordering = ('-fecha_aprobacion',)
    readonly_fields = ('id',)
