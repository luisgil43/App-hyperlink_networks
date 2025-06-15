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
from .forms import FichaIngresoForm
from .models import FichaIngreso
from .models import SolicitudVacaciones
from .forms import SolicitudVacacionesForm
from datetime import date
from django.http import HttpResponseForbidden
from usuarios.decoradores import rol_requerido


logger = logging.getLogger(__name__)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def listar_contratos_admin(request):
    contratos = ContratoTrabajo.objects.select_related('tecnico')

    identidades = contratos.values_list(
        'tecnico__identidad', flat=True).distinct()
    nombres = contratos.values_list(
        'tecnico__first_name', 'tecnico__last_name').distinct()
    fechas_inicio = contratos.values_list('fecha_inicio', flat=True).distinct()

    fechas_termino_raw = contratos.values_list('fecha_termino', flat=True)
    fechas_termino = []
    for fecha in fechas_termino_raw:
        if fecha:
            fechas_termino.append(str(fecha))
        else:
            fechas_termino.append("Indefinido")
    fechas_termino = sorted(set(fechas_termino))

    nombres_completos = sorted(set(
        f"{n[0]} {n[1]}" for n in nombres if n[0] and n[1]
    ))

    return render(request, 'rrhh/listar_contratos_admin.html', {
        'contratos': contratos,
        'identidades': identidades,
        'nombres': nombres_completos,
        'fechas_inicio': fechas_inicio,
        'fechas_termino': fechas_termino,
    })


@login_required
def listar_contratos_usuario(request):
    try:
        usuario = request.user
        logger.info(f"🧪 Usuario: {usuario} - ID: {usuario.id}")

        contratos = ContratoTrabajo.objects.filter(tecnico=usuario)
        logger.info(f"🧪 Total contratos: {contratos.count()}")

        return render(request, 'rrhh/contratos_trabajo.html', {
            'contratos': contratos
        })
    except Exception as e:
        logger.error(f"❌ Error al cargar contratos usuario: {e}")
        raise e  # Deja que falle para ver en los logs de Render


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
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
@rol_requerido('admin', 'pm', 'rrhh')
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
@rol_requerido('admin', 'pm', 'rrhh')
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


@login_required
def listar_fichas_ingreso_usuario(request):
    fichas = FichaIngreso.objects.filter(tecnico=request.user)
    return render(request, 'rrhh/listar_fichas_ingreso_usuario.html', {
        'fichas': fichas
    })

# Vista admin para listar fichas de ingreso (reutiliza ContratoTrabajo)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def listar_fichas_ingreso_admin(request):
    fichas = FichaIngreso.objects.all().select_related(
        'tecnico')  # Esto mejora rendimiento
    return render(request, 'rrhh/listar_fichas_ingreso_admin.html', {'fichas': fichas})

# Crear ficha (reutilizando formulario)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def crear_ficha_ingreso(request):
    if request.method == 'POST':
        form = FichaIngresoForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(
                request, "Ficha de ingreso guardada exitosamente.")
            return redirect('rrhh:listar_fichas_ingreso_admin')
        else:
            messages.error(request, "Corrige los errores del formulario.")
    else:
        form = FichaIngresoForm()

    return render(request, 'rrhh/crear_ficha_ingreso.html', {'form': form})

# Ver ficha


@login_required
def ver_ficha_ingreso(request, pk):
    ficha = get_object_or_404(FichaIngreso, pk=pk)
    return redirect(ficha.archivo.url)


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def editar_ficha_ingreso(request, pk):
    ficha = get_object_or_404(FichaIngreso, pk=pk)
    if request.method == 'POST':
        form = FichaIngresoForm(request.POST, request.FILES, instance=ficha)
        if form.is_valid():
            form.save()
            return redirect('rrhh:listar_fichas_ingreso_admin')
    else:
        form = FichaIngresoForm(instance=ficha)
    return render(request, 'rrhh/editar_ficha_ingreso.html', {'form': form})


@staff_member_required
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_ficha_ingreso(request, pk):
    ficha = get_object_or_404(FichaIngreso, pk=pk)
    if request.method == 'POST':
        ficha.delete()
        return redirect('rrhh:listar_fichas_ingreso_admin')
    return render(request, 'rrhh/eliminar_ficha_ingreso.html', {'ficha': ficha})


@login_required
def mis_vacaciones(request):
    usuario = request.user

    # Calcular días disponibles desde la fecha de inicio del contrato
    primer_contrato = usuario.contratotrabajo_set.order_by(
        'fecha_inicio').first()
    dias_disponibles = 0
    if primer_contrato and primer_contrato.fecha_inicio:
        dias_trabajados = (date.today() - primer_contrato.fecha_inicio).days
        dias_disponibles = round(dias_trabajados * 0.04166, 2)

    # Crear solicitud
    if request.method == 'POST':
        form = SolicitudVacacionesForm(request.POST)
        if form.is_valid():
            solicitud = form.save(commit=False)
            solicitud.usuario = usuario
            solicitud.dias_solicitados = form.cleaned_data['dias_solicitados']
            solicitud.save()
            messages.success(request, "Solicitud enviada correctamente.")
            return redirect('rrhh:mis_vacaciones')
    else:
        form = SolicitudVacacionesForm()

    solicitudes = SolicitudVacaciones.objects.filter(
        usuario=usuario).order_by('-fecha_solicitud')
    context = {
        'dias_disponibles': dias_disponibles,
        'form': form,
        'solicitudes': solicitudes,
    }
    return render(request, 'rrhh/solicitud_vacaciones.html', context)


@login_required
def editar_solicitud(request, pk):
    solicitud = get_object_or_404(
        SolicitudVacaciones, pk=pk, usuario=request.user)

    if solicitud.estatus != 'pendiente_supervisor':
        messages.warning(
            request, "Solo puedes editar solicitudes que aún no han sido revisadas.")
        return redirect('rrhh:mis_vacaciones')

    if request.method == 'POST':
        form = SolicitudVacacionesForm(request.POST, instance=solicitud)
        if form.is_valid():
            solicitud = form.save(commit=False)
            solicitud.dias_solicitados = form.cleaned_data['dias_solicitados']
            solicitud.save()
            messages.success(request, "Solicitud actualizada correctamente.")
            return redirect('rrhh:mis_vacaciones')
    else:
        form = SolicitudVacacionesForm(instance=solicitud)

    return render(request, 'rrhh/editar_solicitud_vacaciones.html', {
        'form': form,
        'solicitud': solicitud
    })


@login_required
def eliminar_solicitud(request, pk):
    solicitud = get_object_or_404(
        SolicitudVacaciones, pk=pk, usuario=request.user)

    if solicitud.estatus != 'pendiente_supervisor':
        messages.warning(
            request, "Solo puedes eliminar solicitudes que aún no han sido revisadas.")
        return redirect('rrhh:mis_vacaciones')

    if request.method == 'POST':
        solicitud.delete()
        messages.success(request, "Solicitud eliminada correctamente.")
        return redirect('rrhh:mis_vacaciones')

    return render(request, 'rrhh/eliminar_solicitud_vacaciones.html', {
        'solicitud': solicitud
    })
