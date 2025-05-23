from django.contrib import admin, messages
from django.utils.html import format_html
from django.db import IntegrityError
from .models import Liquidacion


@admin.register(Liquidacion)
class LiquidacionAdmin(admin.ModelAdmin):
    list_display = ('tecnico', 'mes', 'año', 'monto',
                    'firmada', 'fecha_firma', 'ver_pdf_firmado')
    readonly_fields = ('firmada', 'fecha_firma', 'pdf_firmado')

    def ver_pdf_firmado(self, obj):
        if obj.pdf_firmado:
            return format_html('<a href="{}" target="_blank">Descargar PDF Firmado</a>', obj.pdf_firmado.url)
        return "-"
    ver_pdf_firmado.short_description = "PDF Firmado"

    def save_model(self, request, obj, form, change):
        try:
            super().save_model(request, obj, form, change)
        except IntegrityError:
            self.message_user(
                request,
                f"⚠️ Estas intentando agregarle una liquidación a '{obj.tecnico}' en el mes '{obj.mes}' del año '{obj.año}', "
                f"pero ya existe una registrada. Si necesitas cambiarla, edita o elimina la que ya está.",
                level=messages.ERROR
            )
