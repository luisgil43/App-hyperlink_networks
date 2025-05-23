import os
import shutil
import uuid
import base64
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, FileResponse
from django.contrib import messages
from django.utils import timezone
from django.template.loader import render_to_string
from weasyprint import HTML
import fitz

from .models import Liquidacion, Tecnico
from django.core.files.base import ContentFile


@login_required
def ver_pdf_liquidacion(request, pk):
    tecnico = request.user.tecnico
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=tecnico)

    if liquidacion.archivo_pdf_liquidacion:
        return FileResponse(liquidacion.archivo_pdf_liquidacion.open('rb'), content_type='application/pdf')
    else:
        messages.error(
            request, "No hay PDF original disponible para esta liquidación.")
        return redirect('liquidaciones:listar')


@login_required
def listar_liquidaciones(request):
    tecnico = request.user.tecnico
    liquidaciones = Liquidacion.objects.filter(tecnico=tecnico)
    return render(request, 'liquidaciones/listar.html', {'liquidaciones': liquidaciones})


@login_required
def firmar_liquidacion(request, pk):
    tecnico = request.user.tecnico
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=tecnico)

    # Validación: no permitir firmar si no hay firma digital registrada
    if not tecnico.firma_digital:
        messages.warning(
            request, "Debes registrar tu firma digital primero para poder firmar.")
        return redirect('liquidaciones:registrar_firma')

    if request.method == 'POST':
        # Revalidar firma digital antes de firmar
        if not tecnico.firma_digital:
            messages.error(
                request, "No puedes firmar sin una firma digital registrada.")
            return redirect('liquidaciones:registrar_firma')

        original_path = liquidacion.archivo_pdf_liquidacion.path
        firma_path = tecnico.firma_digital.path

        output_rel_path = f'liquidaciones_firmadas/liquidacion_{liquidacion.pk}_firmada.pdf'
        output_abs_path = os.path.join(settings.MEDIA_ROOT, output_rel_path)

        os.makedirs(os.path.dirname(output_abs_path), exist_ok=True)

        doc = fitz.open(original_path)
        page = doc[-1]
        rect = fitz.Rect(400, 700, 550, 750)
        page.insert_image(rect, filename=firma_path)
        doc.save(output_abs_path)
        doc.close()

        liquidacion.pdf_firmado.name = output_rel_path
        liquidacion.firmada = True
        liquidacion.fecha_firma = timezone.now()
        liquidacion.save()

        messages.success(
            request, "La liquidación fue firmada correctamente. Puedes descargarla ahora.")
        return redirect('liquidaciones:listar')

    # Método GET, mostrar la página de firma
    return render(request, 'liquidaciones/firmar.html', {'liquidacion': liquidacion, 'tecnico': tecnico})


@login_required
def liquidaciones_pdf(request):
    tecnico = request.user.tecnico
    liquidaciones = Liquidacion.objects.filter(tecnico=tecnico)

    html_string = render_to_string('liquidaciones/liquidaciones_pdf.html', {
        'liquidaciones': liquidaciones,
        'tecnico': tecnico,
    })

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="liquidaciones.pdf"'

    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response


@login_required
def registrar_firma(request):
    tecnico = request.user.tecnico

    if request.method == 'POST':
        data_url = request.POST.get('firma_digital')
        if data_url:
            try:
                format, imgstr = data_url.split(';base64,')
                ext = format.split('/')[-1]
                file_name = f"{uuid.uuid4()}.{ext}"
                data = ContentFile(base64.b64decode(imgstr), name=file_name)
                tecnico.firma_digital = data
                tecnico.save()
                messages.success(request, "Tu firma digital ha sido guardada.")
                return redirect('liquidaciones:listar')
            except Exception as e:
                messages.error(request, "Error al procesar la firma digital.")
        else:
            messages.error(request, "No se recibió ninguna firma.")

    return render(request, 'liquidaciones/registrar_firma.html', {'tecnico': tecnico})


@login_required
def descargar_pdf(request):
    filepath = os.path.join(settings.MEDIA_ROOT,
                            'liquidaciones', 'liquidaciones_completas.pdf')
    return FileResponse(open(filepath, 'rb'), content_type='application/pdf')


@login_required
def confirmar_firma(request, pk):
    t
