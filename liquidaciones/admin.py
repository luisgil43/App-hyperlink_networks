from django.contrib import admin
from django.utils.html import format_html
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
