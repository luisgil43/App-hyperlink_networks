import os
import shutil
import uuid
import base64
import tempfile
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, FileResponse, Http404
from django.contrib import messages
from django.utils import timezone
from django.template.loader import render_to_string
import tempfile
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

    if not tecnico.firma_digital:
        messages.warning(request, "Debes registrar tu firma digital primero.")
        return redirect('liquidaciones:registrar_firma')

    original_path = liquidacion.archivo_pdf_liquidacion.path
    firma_path = tecnico.firma_digital.path

    # Ruta donde guardar el PDF firmado
    output_rel_path = f'liquidaciones_firmadas/liquidacion_{liquidacion.pk}_firmada.pdf'
    output_abs_path = os.path.join(settings.MEDIA_ROOT, output_rel_path)

    # Crear carpeta si no existe
    os.makedirs(os.path.dirname(output_abs_path), exist_ok=True)

    # Abrir y firmar el PDF
    doc = fitz.open(original_path)
    page = doc[-1]
    rect = fitz.Rect(400, 700, 550, 750)
    page.insert_image(rect, filename=firma_path)
    doc.save(output_abs_path)
    doc.close()

    # Guardar en el modelo
    liquidacion.pdf_firmado.name = output_rel_path
    liquidacion.firmada = True
    liquidacion.fecha_firma = timezone.now()
    liquidacion.save()

    messages.success(
        request, "La liquidación fue firmada correctamente. Puedes descargarla ahora.")
    return redirect('liquidaciones:listar')


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
            format, imgstr = data_url.split(';base64,')
            ext = format.split('/')[-1]
            file_name = f"{uuid.uuid4()}.{ext}"
            data = ContentFile(base64.b64decode(imgstr), name=file_name)
            tecnico.firma_digital = data
            tecnico.save()
            messages.success(request, "Tu firma digital ha sido guardada.")
            return redirect('liquidaciones:listar')
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
    tecnico = request.user.tecnico
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=tecnico)

    preview_path = request.session.get('preview_path')
    output_rel_path = request.session.get('output_rel_path')
    output_abs_path = os.path.join(settings.MEDIA_ROOT, output_rel_path)

    if preview_path and os.path.exists(preview_path):
        os.makedirs(os.path.dirname(output_abs_path), exist_ok=True)
        shutil.copy(preview_path, output_abs_path)

        liquidacion.pdf_firmado.name = output_rel_path
        liquidacion.firmada = True
        liquidacion.fecha_firma = timezone.now()
        liquidacion.save()

        messages.success(request, "La liquidación fue firmada correctamente.")
    else:
        messages.error(request, "No se pudo confirmar la firma.")

    return redirect('liquidaciones:listar')
