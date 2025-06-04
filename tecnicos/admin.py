from django.contrib import admin
from .models import Tecnico, Produccion, Curso
from django.utils.html import format_html


class TecnicoAdmin(admin.ModelAdmin):
    list_display = ('id', 'user')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')


class CursoAdmin(admin.ModelAdmin):
    list_display = ('nombre_curso', 'tecnico',
                    'fecha_vencimiento', 'mostrar_estado')

    def mostrar_estado(self, obj):
        if obj.esta_activo:
            return format_html('<span style="color: green;">✔️ Sí</span>')
        return format_html('<span style="color: red;">❌ No</span>')

    mostrar_estado.short_description = 'Activo'


admin.site.register(Tecnico, TecnicoAdmin)
admin.site.register(Curso, CursoAdmin)
# Si querés que Producción también esté en el admin:
admin.site.register(Produccion)
