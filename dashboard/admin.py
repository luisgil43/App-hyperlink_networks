from django.contrib import admin
from .models import ProduccionTecnico

# Si no tienes choices definidos en el modelo, los definimos aquí:
STATUS_CHOICES = [
    ('Pendiente', 'Pendiente'),
    ('Aprobado', 'Aprobado'),
    ('Rechazado', 'Rechazado'),
]


@admin.register(ProduccionTecnico)
class ProduccionTecnicoAdmin(admin.ModelAdmin):
    list_display = ('tecnico', 'id', 'status',
                    'fecha_aprobacion', 'descripcion', 'monto')

    fields = ('tecnico', 'id', 'status',
              'fecha_aprobacion', 'descripcion', 'monto')

    readonly_fields = ()

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "status":
            kwargs['choices'] = STATUS_CHOICES
        return super().formfield_for_choice_field(db_field, request, **kwargs)
