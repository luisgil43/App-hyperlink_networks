from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
import uuid
import os
from .models import ContratoTrabajo
from .forms import ContratoTrabajoForm
from django.shortcuts import get_object_or_404
import logging
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import FileResponse, Http404

logger = logging.getLogger(__name__)


@staff_member_required
def listar_contratos_admin(request):
    contratos = ContratoTrabajo.objects.all()

    nombre = request.GET.get("nombre", "")
    fecha_inicio = request.GET.get("fecha_inicio")
    fecha_termino = request.GET.get("fecha_termino")

    if nombre:
        contratos = contratos.filter(
            tecnico__first_name__icontains=nombre
        ) | contratos.filter(tecnico__last_name__icontains=nombre)

    if fecha_inicio:
        contratos = contratos.filter(fecha_inicio__gte=fecha_inicio)

    if fecha_termino:
        contratos = contratos.filter(fecha_termino__lte=fecha_termino)

    return render(request, 'rrhh/listar_contratos_admin.html', {
        'contratos': contratos
    })


@login_required
def listar_contratos_usuario(request):
    usuario = request.user
    contratos = ContratoTrabajo.objects.filter(tecnico=usuario)

    return render(request, 'rrhh/contratos_trabajo.html', {
        'contratos': contratos
    })


@staff_member_required
def crear_contrato(request):
    if request.method == 'POST':
        form = ContratoTrabajoForm(request.POST, request.FILES)

        if form.is_valid():
            contrato = form.save(commit=False)

            if request.POST.get('indefinido-check'):
                contrato.fecha_termino = None

            archivo = request.FILES.get('archivo')
            if not archivo:
                messages.error(request, '❌ Debes subir un archivo PDF.')
                return render(request, 'rrhh/crear_contrato.html', {'form': form})

            if archivo.content_type != 'application/pdf':
                messages.error(request, '❌ El archivo debe ser un PDF válido.')
                return render(request, 'rrhh/crear_contrato.html', {'form': form})

            # ✅ Asignar directamente, Django usará upload_to automáticamente
            contrato.archivo = archivo

            contrato.save()
            messages.success(request, '✅ Contrato creado correctamente.')
            return redirect('rrhh:contratos_trabajo')
        else:
            messages.error(
                request, '❌ Error al crear el contrato. Revisa los campos.')
    else:
        form = ContratoTrabajoForm()

    return render(request, 'rrhh/crear_contrato.html', {'form': form})


@staff_member_required
def editar_contrato(request, contrato_id):
    contrato = get_object_or_404(ContratoTrabajo, id=contrato_id)

    form = ContratoTrabajoForm(
        request.POST or None, request.FILES or None, instance=contrato)

    if request.method == 'POST' and form.is_valid():
        contrato = form.save(commit=False)

        if request.POST.get('indefinido-check'):
            contrato.fecha_termino = None

        reemplazar = form.cleaned_data.get('reemplazar_archivo')
        archivo_nuevo = request.FILES.get('archivo')

        if reemplazar and archivo_nuevo:
            if archivo_nuevo.content_type != 'application/pdf':
                messages.error(request, '❌ El archivo debe ser un PDF válido.')
                return render(request, 'rrhh/editar_contrato.html', {'form': form, 'contrato': contrato})

            try:
                if contrato.archivo and contrato.archivo.name:
                    nombre_original = contrato.archivo.name.split(
                        '/')[-1]  # 🔁 Guardamos antes
                    # 🗑️ Eliminar archivo existente
                    contrato.archivo.delete(save=False)

                    archivo_nuevo.seek(0)
                    contenido = archivo_nuevo.read()
                    contrato.archivo.save(
                        nombre_original, ContentFile(contenido), save=False)
            except Exception as e:
                messages.error(
                    request, f"❌ Error al subir el nuevo archivo: {e}")
                return render(request, 'rrhh/editar_contrato.html', {'form': form, 'contrato': contrato})

        contrato.save()
        messages.success(request, '✅ Contrato actualizado correctamente.')
        return redirect('rrhh:contratos_trabajo')

    return render(request, 'rrhh/editar_contrato.html', {'form': form, 'contrato': contrato})


@staff_member_required
def eliminar_contrato(request, contrato_id):
    try:
        contrato = get_object_or_404(ContratoTrabajo, id=contrato_id)
    except Exception:
        messages.error(request, "❌ Este contrato ya fue eliminado.")
        return redirect('rrhh:contratos_trabajo')

    if request.method == 'POST':
        if contrato.archivo and contrato.archivo.name:
            try:
                contrato.archivo.delete(save=False)
            except Exception:
                messages.warning(
                    request, "⚠️ El archivo ya no existe en Cloudinary.")

        contrato.delete()
        messages.success(request, "✅ Contrato eliminado correctamente.")
        return redirect('rrhh:contratos_trabajo')

    return render(request, 'rrhh/eliminar_contrato.html', {'contrato': contrato})


@staff_member_required
def ver_contrato(request, contrato_id):
    try:
        contrato = get_object_or_404(ContratoTrabajo, id=contrato_id)

        try:
            archivo = contrato.archivo.open()
        except Exception as e:
            messages.error(request, f"❌ No se pudo acceder al archivo: {e}")
            return redirect('rrhh:contratos_trabajo')

        # ✅ Mostramos el PDF directamente en navegador
        return FileResponse(contrato.archivo.open(), content_type='application/pdf')

    except Exception as e:
        messages.error(request, f"❌ Error al mostrar el contrato: {e}")
        return redirect('rrhh:contratos_trabajo')
