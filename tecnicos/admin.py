from django.contrib import admin
from .models import Tecnico, Produccion, Curso


@admin.register(Tecnico)
class TecnicoAdmin(admin.ModelAdmin):
    list_display = ('id', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')


# @admin.register(Produccion)
# class ProduccionAdmin(admin.ModelAdmin):
    # list_display = ('id', 'tecnico', 'fecha_aprobacion',
    # 'supervisor', 'estado', 'total_pago')
    # list_filter = ('estado', 'fecha_aprobacion', 'supervisor')
    # search_fields = ('tecnico__user__username', 'supervisor__user__username')
    # ordering = ('-fecha_aprobacion',)


@admin.register(Curso)
class CursoAdmin(admin.ModelAdmin):
    list_display = ('id', 'tecnico', 'nombre_curso',
                    'fecha_vencimiento', 'activo')
    list_filter = ('activo',)
    search_fields = ('tecnico__user__username', 'nombre_curso')
