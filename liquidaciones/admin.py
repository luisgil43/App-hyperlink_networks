# liquidaciones/admin.py
from mv_construcciones.custom_admin import custom_admin_site
from django.contrib import admin
from django.contrib.admin import ModelAdmin

from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.urls import path
from django.shortcuts import render, redirect
from django.utils.html import format_html
from django.core.files.base import ContentFile
from decimal import Decimal

import tempfile
import os

from .models import Liquidacion
from .forms import LiquidacionForm


class LiquidacionAdmin(admin.ModelAdmin):
    form = LiquidacionForm
    list_display = ('tecnico', 'mes', 'año', 'monto',
                    'firmada', 'fecha_firma', 'ver_pdf_firmado')
    readonly_fields = ('firmada', 'fecha_firma', 'pdf_firmado')

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                'confirmar-reemplazo/',
                self.admin_site.admin_view(self.confirmar_reemplazo_view),
                name='liquidaciones_liquidacion_confirmar_reemplazo'
            ),
        ]
        return custom + urls

    def ver_pdf_firmado(self, obj):
        if obj.pdf_firmado:
            return format_html(
                '<a href="{}" target="_blank">Descargar PDF Firmado</a>',
                obj.pdf_firmado.url
            )
        return "-"
    ver_pdf_firmado.short_description = "PDF Firmado"

    def add_view(self, request, form_url='', extra_context=None):
        # Detecta intento de duplicado y redirige a confirmación
        if request.method == "POST" and not request.POST.get("_reemplazar") and not request.POST.get("_cancelar"):
            form = self.get_form(request)(request.POST, request.FILES)
            if form.is_valid():
                t = form.cleaned_data['tecnico']
                m = form.cleaned_data['mes']
                a = form.cleaned_data['año']
                existe = Liquidacion.objects.filter(
                    tecnico=t, mes=m, año=a).first()
                if existe:
                    # Guarda archivo temporal
                    if request.FILES.get('archivo_pdf_liquidacion'):
                        archivo = request.FILES['archivo_pdf_liquidacion']
                        tmpdir = tempfile.gettempdir()
                        tmp_path = os.path.join(tmpdir, archivo.name)
                        with open(tmp_path, 'wb+') as dst:
                            for chunk in archivo.chunks():
                                dst.write(chunk)
                        request.session['archivo_temporal'] = {
                            'path': tmp_path, 'name': archivo.name}
                    # Guarda datos de duplicado
                    request.session['duplicado_data'] = {
                        'tecnico': t.id, 'mes': m, 'año': a, 'monto': str(form.cleaned_data['monto'])
                    }
                    return redirect('admin:liquidaciones_liquidacion_confirmar_reemplazo')
        return super().add_view(request, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        # Guardado normal (no se usa para reemplazo)
        super().save_model(request, obj, form, change)

    def confirmar_reemplazo_view(self, request):
        # Procesa POST de Reemplazar / Cancelar
        if request.method == 'POST':
            # Cancelar primero para limpiar y salir
            if '_cancelar' in request.POST:
                arch = request.session.pop('archivo_temporal', None)
                request.session.pop('duplicado_data', None)
                if arch and os.path.exists(arch.get('path', '')):
                    os.remove(arch['path'])
                messages.info(
                    request, "❌ Se canceló el reemplazo de la liquidación.")
                return redirect('admin:liquidaciones_liquidacion_changelist')

            # Reemplazar
            if '_reemplazar' in request.POST:
                data = request.session.pop('duplicado_data', None)
                arch = request.session.pop('archivo_temporal', None)
                if data and arch:
                    # Eliminar anterior
                    anterior = Liquidacion.objects.filter(
                        tecnico_id=data['tecnico'], mes=data['mes'], año=data['año']
                    ).first()
                    if anterior:
                        if anterior.firmada and anterior.pdf_firmado and anterior.pdf_firmado.storage.exists(anterior.pdf_firmado.name):
                            anterior.pdf_firmado.delete(save=False)
                        anterior.delete()
                    # Crear nuevo
                    with open(arch['path'], 'rb') as f:
                        content = f.read()
                    nueva = Liquidacion(
                        tecnico_id=data['tecnico'],
                        mes=data['mes'],
                        año=data['año'],
                        monto=Decimal(data['monto']),
                        firmada=False
                    )
                    nueva.archivo_pdf_liquidacion.save(
                        arch['name'], ContentFile(content), save=True)
                    os.remove(arch['path'])
                    messages.success(
                        request, "✅ Liquidación reemplazada correctamente.")
                return redirect('admin:liquidaciones_liquidacion_changelist')

        # GET → mostrar formulario
        data = request.session.get('duplicado_data', {})
        arch = request.session.get('archivo_temporal', {})
        tecnico_obj = None
        if data.get('tecnico'):
            from tecnicos.models import Tecnico
            tecnico_obj = Tecnico.objects.get(pk=data['tecnico'])
        return render(request, 'liquidaciones/confirmar_reemplazo.html', {
            'tecnico_id': data.get('tecnico'),
            'tecnico': tecnico_obj,
            'mes': data.get('mes'),
            'año': data.get('año'),
            'monto': data.get('monto'),
            'pdf_name': arch.get('name'),
        })


# Registra tus modelos aquí en lugar de usar admin.site.register
admin.site.register(Liquidacion, LiquidacionAdmin)
