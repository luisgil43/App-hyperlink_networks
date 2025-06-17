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
from .forms import RevisionVacacionesForm
from rrhh.models import Feriado
import json
from rrhh.models import DiasVacacionesTomadosManualmente
from django.urls import reverse
from django.db.models import Q
from rrhh.utils import contar_dias_habiles


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
@rol_requerido('usuario')
def mis_vacaciones(request):
    usuario = request.user

    # Llamar al método del modelo para obtener días disponibles
    dias_disponibles = usuario.obtener_dias_vacaciones_disponibles()

    # Obtener los días cargados manualmente si existen
    try:
        dias_manuales = usuario.vacaciones_manuales.cantidad_dias
    except DiasVacacionesTomadosManualmente.DoesNotExist:
        dias_manuales = 0

    if request.method == 'POST':
        form = SolicitudVacacionesForm(request.POST, usuario=usuario)
        if form.is_valid():
            dias_solicitados = usuario.calcular_dias_habiles(
                form.cleaned_data['fecha_inicio'],
                form.cleaned_data['fecha_fin']
            )
            if dias_solicitados > dias_disponibles:
                form.add_error(
                    None, "Los días solicitados superan los días disponibles.")
            else:
                solicitud = form.save(commit=False)
                solicitud.usuario = usuario
                solicitud.dias_solicitados = dias_solicitados
                solicitud.save()
                messages.success(request, "Solicitud enviada correctamente.")
                return redirect('rrhh:mis_vacaciones')
    else:
        form = SolicitudVacacionesForm(usuario=usuario)

    solicitudes = SolicitudVacaciones.objects.filter(
        usuario=usuario).order_by('-fecha_solicitud')

    feriados = Feriado.objects.values_list('fecha', flat=True)
    feriados_json = json.dumps([f.strftime("%Y-%m-%d") for f in feriados])

    context = {
        'dias_disponibles': dias_disponibles,
        'form': form,
        'solicitudes': solicitudes,
        'feriados_json': feriados_json,
        'dias_manuales': dias_manuales
    }

    return render(request, 'rrhh/solicitud_vacaciones.html', context)


@login_required
def editar_solicitud_vacaciones(request, pk):
    solicitud = get_object_or_404(
        SolicitudVacaciones, pk=pk, usuario=request.user)

    if solicitud.estatus != 'pendiente_supervisor':
        mensajes_estado = {
            'rechazada_supervisor': "No puedes editar una solicitud que fue rechazada por el supervisor.",
            'pendiente_pm': "No puedes editar una solicitud que ya fue enviada al PM.",
            'rechazada_pm': "No puedes editar una solicitud que fue rechazada por el PM.",
            'pendiente_rrhh': "No puedes editar una solicitud que ya fue enviada a RRHH.",
            'rechazada_rrhh': "No puedes editar una solicitud que fue rechazada por RRHH.",
            'rechazada_admin': "No puedes editar una solicitud que fue rechazada por administración.",
            'aprobada': "No puedes editar una solicitud que ya fue aprobada.",
        }
        mensaje = mensajes_estado.get(
            solicitud.estatus, "No puedes editar esta solicitud.")
        messages.warning(request, mensaje)
        return redirect('rrhh:mis_vacaciones')

    if request.method == 'POST':
        form = SolicitudVacacionesForm(request.POST, instance=solicitud)
        if form.is_valid():
            solicitud = form.save(commit=False)

            # Recalcular los días hábiles
            fecha_inicio = form.cleaned_data['fecha_inicio']
            fecha_fin = form.cleaned_data['fecha_fin']
            dias = contar_dias_habiles(fecha_inicio, fecha_fin)

            solicitud.dias_solicitados = dias
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
def eliminar_solicitud_vacaciones(request, pk):
    solicitud = get_object_or_404(
        SolicitudVacaciones, pk=pk, usuario=request.user)

    if solicitud.estatus != 'pendiente_supervisor':
        mensajes_estado = {
            'rechazada_supervisor': "No puedes eliminar una solicitud rechazada por el supervisor.",
            'pendiente_pm': "No puedes eliminar una solicitud que ya fue enviada al PM.",
            'rechazada_pm': "No puedes eliminar una solicitud rechazada por el PM.",
            'pendiente_rrhh': "No puedes eliminar una solicitud que ya fue enviada a RRHH.",
            'rechazada_rrhh': "No puedes eliminar una solicitud rechazada por RRHH.",
            'rechazada_admin': "No puedes eliminar una solicitud rechazada por administración.",
            'aprobada': "No puedes eliminar una solicitud que ya fue aprobada.",
        }
        mensaje = mensajes_estado.get(
            solicitud.estatus, "No puedes eliminar esta solicitud.")
        messages.warning(request, mensaje)
        return redirect('rrhh:mis_vacaciones')

    if request.method == 'POST':
        solicitud.delete()
        messages.success(request, "Solicitud eliminada correctamente.")
        return redirect('rrhh:mis_vacaciones')

    return render(request, 'rrhh/confirmar_eliminacion.html', {
        'solicitud': solicitud
    })

# --- Vista para Supervisor ---


@staff_member_required
@rol_requerido('supervisor', 'admin')
def revisar_solicitudes_supervisor(request):
    if not request.user.es_supervisor and not request.user.es_admin_general:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    solicitudes = SolicitudVacaciones.objects.filter(
        estatus='pendiente_supervisor').order_by('fecha_solicitud')

    return render(request, 'rrhh/revisar_vacaciones_supervisor.html', {
        'solicitudes': solicitudes,
        'titulo': "Solicitudes Pendientes - Supervisor",
        'rol': 'supervisor',
    })

# --- Vista para PM ---


@staff_member_required
@rol_requerido('pm', 'admin')
def revisar_solicitudes_pm(request):
    if not request.user.es_pm and not request.user.es_admin_general:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    solicitudes = SolicitudVacaciones.objects.filter(
        estatus='pendiente_pm').order_by('fecha_solicitud')

    return render(request, 'rrhh/revisar_vacaciones_pm.html', {
        'solicitudes': solicitudes,
        'titulo': "Solicitudes Pendientes - PM",
        'rol': 'pm',
    })

# --- Vista para RRHH ---


@staff_member_required
@rol_requerido('rrhh', 'admin')
def revisar_solicitudes_rrhh(request):
    if not request.user.es_rrhh and not request.user.es_admin_general:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    # Recoger filtros desde GET
    identidad = request.GET.get('identidad', '').strip()
    nombre = request.GET.get('nombre', '').strip()
    estatus = request.GET.get('estatus', '').strip()

    # Filtros base
    solicitudes = SolicitudVacaciones.objects.all()

    if identidad:
        solicitudes = solicitudes.filter(
            usuario__identidad__icontains=identidad)

    if nombre:
        solicitudes = solicitudes.filter(
            Q(usuario__first_name__icontains=nombre) |
            Q(usuario__last_name__icontains=nombre)
        )

    if estatus:
        solicitudes = solicitudes.filter(estatus=estatus)

    solicitudes = solicitudes.order_by('-fecha_solicitud')

    return render(request, 'rrhh/revisar_vacaciones_rrhh.html', {
        'solicitudes': solicitudes,
        'filtros': {
            'identidad': identidad,
            'nombre': nombre,
            'estatus': estatus,
        }
    })


@staff_member_required
@rol_requerido('supervisor', 'pm', 'rrhh', 'admin')
def revisar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)

    # Detectar rol activo
    if request.user.es_supervisor:
        rol = 'supervisor'
    elif request.user.es_pm:
        rol = 'pm'
    elif request.user.es_rrhh or request.user.es_admin_general:
        rol = 'rrhh'
    else:
        return HttpResponseForbidden("No tienes permiso para revisar esta solicitud.")

    transiciones_validas = {
        'supervisor': 'pendiente_supervisor',
        'pm': 'pendiente_pm',
        'rrhh': 'pendiente_rrhh',
    }

    if transiciones_validas.get(rol) != solicitud.estatus:
        return HttpResponseForbidden("No puedes revisar esta solicitud.")

    if request.method == 'POST':
        form = RevisionVacacionesForm(request.POST)
        if form.is_valid():
            accion = request.POST.get('accion')
            observacion = form.cleaned_data['observacion']
            solicitud.observacion = observacion

            if accion == 'aprobar':
                if rol == 'supervisor':
                    solicitud.estatus = 'pendiente_pm'
                elif rol == 'pm':
                    solicitud.estatus = 'pendiente_rrhh'
                elif rol == 'rrhh':
                    solicitud.estatus = 'aprobada'
            elif accion == 'rechazar':
                solicitud.estatus = f'rechazada_{rol}'

            solicitud.save()
            messages.success(
                request, f"Solicitud {'aprobada' if accion == 'aprobar' else 'rechazada'} exitosamente.")
            return redirect('rrhh:revisar_' + rol)
    else:
        form = RevisionVacacionesForm()

    return render(request, 'rrhh/revisar_solicitud_vacaciones.html', {
        'solicitud': solicitud,
        'form': form
    })


@staff_member_required
@rol_requerido('admin', 'rrhh')  # ✅ RRHH añadido
def revisar_todas_vacaciones(request):
    if not request.user.es_admin_general and not request.user.es_rrhh:
        return HttpResponseForbidden("No tienes permiso para ver esta vista.")

    solicitudes = SolicitudVacaciones.objects.all().order_by('-fecha_solicitud')

    return render(request, 'rrhh/revisar_todas_vacaciones.html', {
        'solicitudes': solicitudes,
        'titulo': "Todas las Solicitudes de Vacaciones",
        'rol': 'rrhh' if request.user.es_rrhh else 'admin',
    })


@staff_member_required
@rol_requerido('supervisor', 'pm', 'rrhh', 'admin')
def rechazar_solicitud_vacaciones(request):
    if request.method == 'POST':
        solicitud_id = request.POST.get('solicitud_id')
        observacion = request.POST.get('observacion')
        solicitud = get_object_or_404(SolicitudVacaciones, pk=solicitud_id)

        # Detectar el rol de mayor privilegio permitido
        if request.user.es_supervisor:
            rol_usuario = 'supervisor'
        elif request.user.es_pm:
            rol_usuario = 'pm'
        elif request.user.es_rrhh:
            rol_usuario = 'rrhh'
        elif request.user.es_admin_general:
            rol_usuario = 'admin'
        else:
            return HttpResponseForbidden("Rol no válido para esta acción.")

        estatus_rechazo = {
            'supervisor': 'rechazada_supervisor',
            'pm': 'rechazada_pm',
            'rrhh': 'rechazada_rrhh',
            'admin': 'rechazada_admin'
        }[rol_usuario]

        solicitud.estatus = estatus_rechazo
        solicitud.observacion = observacion

        # ✅ Asignar quién rechazó la solicitud según el rol
        if rol_usuario == 'supervisor':
            solicitud.aprobado_por_supervisor = request.user
        elif rol_usuario == 'pm':
            solicitud.aprobado_por_pm = request.user
        elif rol_usuario == 'rrhh':
            solicitud.aprobado_por_rrhh = request.user

        solicitud.save()

        messages.success(
            request, f"Solicitud rechazada por {rol_usuario.upper()}.")

        redirecciones = {
            'supervisor': 'rrhh:revisar_supervisor',
            'pm': 'rrhh:revisar_pm',
            'rrhh': 'rrhh:revisar_rrhh',
            'admin': 'rrhh:revisar_todas_vacaciones'
        }

        return redirect(reverse(redirecciones[rol_usuario]))

    return HttpResponseForbidden("Acceso denegado.")


@rol_requerido('supervisor')
def aprobar_vacacion_supervisor(request, pk):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)
    if solicitud.estatus == 'pendiente_supervisor':
        solicitud.estatus = 'pendiente_pm'
        solicitud.aprobado_por_supervisor = request.user  # Guarda quién aprobó
        solicitud.save()
        messages.success(request, "Solicitud aprobada y enviada al PM.")
    else:
        messages.warning(
            request, "La solicitud no está pendiente para Supervisor.")
    return redirect('rrhh:revisar_supervisor')


@rol_requerido('pm')
def aprobar_vacacion_pm(request, pk):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)
    if solicitud.estatus == 'pendiente_pm':
        solicitud.estatus = 'pendiente_rrhh'
        solicitud.aprobado_por_pm = request.user  # Guarda quién aprobó
        solicitud.save()
        messages.success(request, "Solicitud aprobada y enviada a RRHH.")
    else:
        messages.warning(request, "La solicitud no está pendiente para PM.")
    return redirect('rrhh:revisar_pm')


@rol_requerido('rrhh')
def aprobar_vacacion_rrhh(request, pk):
    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)
    if solicitud.estatus == 'pendiente_rrhh':
        solicitud.estatus = 'aprobada'
        solicitud.aprobado_por_rrhh = request.user  # Guarda quién aprobó
        solicitud.save()
        messages.success(request, "Solicitud aprobada por RRHH.")
    else:
        messages.warning(request, "La solicitud no está pendiente para RRHH.")
    return redirect('rrhh:revisar_rrhh')


@staff_member_required
@rol_requerido('admin', 'rrhh')
def eliminar_solicitud_vacaciones_admin(request, pk):
    if not request.user.es_rrhh and not request.user.es_admin_general:
        messages.error(
            request, "No tienes permisos para eliminar esta solicitud.")
        return redirect('dashboard_admin:vacaciones_admin')

    solicitud = get_object_or_404(SolicitudVacaciones, pk=pk)

    if request.method == 'POST':
        solicitud.delete()
        messages.success(request, "Solicitud eliminada correctamente.")
        return redirect('dashboard_admin:vacaciones_admin')

    messages.warning(request, "La solicitud no se eliminó.")
    return redirect('dashboard_admin:vacaciones_admin')
