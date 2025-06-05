from urllib.parse import urljoin
import os
import uuid
import base64
import requests
import fitz  # PyMuPDF
from io import BytesIO
from PIL import Image
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import HttpResponse, FileResponse, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from .models import Liquidacion
from .forms import LiquidacionForm
from django_select2.views import AutoResponseView
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from .models import Liquidacion
from tecnicos.models import Tecnico
from django.views.decorators.csrf import csrf_exempt
from dal import autocomplete


"""
Esta se activa si es sin filtro. 
@staff_member_required
def admin_lista_liquidaciones(request):
    liquidaciones = Liquidacion.objects.select_related('tecnico').all()
    return render(request, 'liquidaciones/admin_lista.html', {
        'liquidaciones': liquidaciones
    })"""


@staff_member_required
def admin_lista_liquidaciones(request):
    liquidaciones = Liquidacion.objects.select_related('tecnico').all()

    # Extraer valores únicos para filtros
    nombres = sorted(set(l.tecnico.get_full_name() for l in liquidaciones))
    meses = sorted(set(l.mes for l in liquidaciones))
    años = sorted(set(l.año for l in liquidaciones))
    montos = sorted(set(l.monto for l in liquidaciones))

    return render(request, 'liquidaciones/admin_lista.html', {
        'liquidaciones': liquidaciones,
        'nombres': nombres,
        'meses': meses,
        'años': años,
        'montos': montos,
    })


@login_required
def listar_liquidaciones(request):
    usuario = request.user
    liquidaciones = Liquidacion.objects.filter(tecnico=usuario)
    return render(request, 'liquidaciones/listar.html', {
        'liquidaciones': liquidaciones
    })


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
def firmar_liquidacion(request, pk):
    usuario = request.user
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=usuario)

    # Verifica que el usuario tenga firma digital
    if not usuario.firma_digital:
        messages.warning(
            request, "Debes registrar tu firma digital primero para poder firmar.")
        return redirect('liquidaciones:registrar_firma')

    if request.method == 'POST':
        try:
            # Cargar PDF
            if liquidacion.archivo_pdf_liquidacion:
                pdf_path = liquidacion.archivo_pdf_liquidacion.path
                with open(pdf_path, 'rb') as f:
                    original_pdf = BytesIO(f.read())
            else:
                return HttpResponseBadRequest("No se encontró el archivo PDF.")

            # Cargar firma del usuario
            firma_path = usuario.firma_digital.path
            with open(firma_path, 'rb') as f:
                firma_data = BytesIO(f.read())

            # Verificar formato de imagen
            img = Image.open(firma_data)
            if img.format not in ['PNG', 'JPEG']:
                raise ValueError("Formato de imagen no compatible")

            # Convertir a PNG si es necesario
            firma_img_io = BytesIO()
            img.save(firma_img_io, format='PNG')
            firma_img_io.seek(0)

            # Insertar firma en el PDF
            doc = fitz.open(stream=original_pdf, filetype='pdf')
            page = doc[-1]
            rect = fitz.Rect(400, 700, 550, 750)
            page.insert_image(rect, stream=firma_img_io)

            pdf_firmado_io = BytesIO()
            doc.save(pdf_firmado_io)
            doc.close()
            pdf_firmado_io.seek(0)

            # Guardar el PDF firmado
            file_name = f"liq_{liquidacion.pk}_firmada.pdf"
            liquidacion.pdf_firmado.save(
                file_name, ContentFile(pdf_firmado_io.read()), save=False)
            liquidacion.firmada = True
            liquidacion.fecha_firma = now()
            liquidacion.save()

            messages.success(
                request, "La liquidación fue firmada correctamente. Puedes descargarla ahora.")
            return redirect('liquidaciones:listar')

        except Exception as e:
            return HttpResponseBadRequest(f"Error al firmar el PDF: {e}")

    return render(request, 'liquidaciones/firmar.html', {
        'liquidacion': liquidacion,
        'tecnico': usuario
    })


@login_required
def registrar_firma(request):
    usuario = request.user  # <- cambio clave

    if request.method == 'POST':
        data_url = request.POST.get('firma_digital')
        if data_url:
            try:
                if not data_url.startswith('data:image/png;base64,'):
                    raise ValueError("Formato de firma inválido.")

                format, imgstr = data_url.split(';base64,')
                ext = format.split('/')[-1]
                file_name = f"{uuid.uuid4()}.{ext}"
                data = ContentFile(base64.b64decode(imgstr), name=file_name)

                usuario.firma_digital = data
                usuario.save()

                messages.success(request, "Tu firma digital ha sido guardada.")
                return redirect('liquidaciones:listar')

            except Exception:
                messages.error(
                    request, "Error al procesar la firma digital. Asegúrate de que esté en formato PNG.")
        else:
            messages.error(request, "No se recibió ninguna firma.")

    # <- sigue siendo valido si tu template usa {{ tecnico }}
    return render(request, 'liquidaciones/registrar_firma.html', {'tecnico': usuario})


@login_required
def liquidaciones_pdf(request):
    usuario = request.user
    liquidaciones = Liquidacion.objects.filter(tecnico=usuario)

    html_string = render_to_string('liquidaciones/liquidaciones_pdf.html', {
        'liquidaciones': liquidaciones,
        # si tu template usa {{ tecnico }}, esto se mantiene
        'tecnico': usuario,
    })

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="liquidaciones.pdf"'

    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response


@login_required
def descargar_pdf(request):
    filepath = os.path.join(settings.MEDIA_ROOT,
                            'liquidaciones', 'liquidaciones_completas.pdf')
    if os.path.exists(filepath):
        return FileResponse(open(filepath, 'rb'), content_type='application/pdf')
    else:
        messages.error(request, "El archivo no existe.")
        return redirect('liquidaciones:listar')


@login_required
def confirmar_firma(request, pk):
    user = request.user

    # Verificación de rol técnico
    if user.is_staff:
        messages.error(
            request, "Solo los técnicos pueden confirmar una firma.")
        return redirect('liquidaciones:listar')

    # Buscar la liquidación asociada al técnico (usuario actual)
    liquidacion = get_object_or_404(Liquidacion, pk=pk, tecnico=user)

    if not liquidacion.firmada:
        liquidacion.firmada = True
        liquidacion.fecha_firma = timezone.now()
        liquidacion.save()
        messages.success(request, "Firma confirmada correctamente.")
    else:
        messages.info(request, "La liquidación ya estaba firmada.")

    return redirect('liquidaciones:listar')


@staff_member_required
@csrf_protect
def confirmar_reemplazo(request):
    if request.method == 'POST':
        if '_reemplazar' in request.POST:
            data = request.session.get('duplicado_data')
            archivo_info = request.session.get('archivo_temporal')

            if data and archivo_info:
                tecnico_id = data.get('tecnico')
                mes = data.get('mes')
                año = data.get('año')

                anterior = Liquidacion.objects.filter(
                    tecnico_id=tecnico_id, mes=mes, año=año).first()

                if anterior:
                    if anterior.firmada and anterior.pdf_firmado and anterior.pdf_firmado.storage.exists(anterior.pdf_firmado.name):
                        anterior.pdf_firmado.delete(save=False)
                    anterior.delete()

                temp_file_path = archivo_info['path']
                with open(temp_file_path, 'rb') as f:
                    contenido = f.read()

                archivo = ContentFile(contenido, name=archivo_info['name'])

                nueva = Liquidacion(
                    tecnico_id=tecnico_id,
                    mes=mes,
                    año=año,
                    monto=data.get('monto'),
                    firmada=False
                )
                nueva.archivo_pdf_liquidacion.save(
                    archivo_info['name'], archivo, save=True)

                messages.success(
                    request, "✅ Liquidación reemplazada correctamente.")
                request.session.pop('duplicado_data', None)
                request.session.pop('archivo_temporal', None)

                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)

                return redirect('admin:liquidaciones_liquidacion_changelist')

        elif '_cancelar' in request.POST:
            archivo_info = request.session.pop('archivo_temporal', None)
            request.session.pop('duplicado_data', None)

            if archivo_info:
                temp_path = archivo_info.get('path')
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)

            messages.info(
                request, "❌ Se canceló el reemplazo de la liquidación.")
            return redirect('admin:liquidaciones_liquidacion_changelist')

    data = request.session.get('duplicado_data', {})
    archivo_info = request.session.get('archivo_temporal', {})

    tecnico_nombre = ''
    if data.get('tecnico'):
        obj = Liquidacion.objects.filter(tecnico_id=data['tecnico']).first()
        tecnico_nombre = obj.tecnico if obj else ''

    return render(request, 'liquidaciones/confirmar_reemplazo.html', {
        'tecnico_id': data.get('tecnico'),
        'tecnico': tecnico_nombre,
        'mes': data.get('mes'),
        'año': data.get('año'),
        'monto': data.get('monto'),
        'pdf_name': archivo_info.get('name'),
    })


@staff_member_required
def carga_masiva_view(request):
    if request.method == 'POST':
        mes = int(request.POST.get('mes'))
        año = int(request.POST.get('año'))

        archivos = request.FILES.getlist('archivos[]')
        if not archivos:
            messages.error(request, "No se han subido archivos.")
            return render(request, 'liquidaciones/carga_masiva.html')

        errores = []
        exitos = 0

        for archivo in archivos:
            nombre_archivo = os.path.splitext(archivo.name)[0]
            if not nombre_archivo.isdigit():
                errores.append(f"Nombre de archivo inválido: {archivo.name}")
                continue

            tecnico_id = int(nombre_archivo)

            tecnico = Tecnico.objects.filter(pk=tecnico_id).first()
            if not tecnico:
                errores.append(f"Técnico con ID {tecnico_id} no existe.")
                continue

            # Verificar si liquidación para ese técnico, mes y año existe
            existe = Liquidacion.objects.filter(
                tecnico=tecnico, mes=mes, año=año).exists()

            if existe:
                # Aquí podrías guardar en sesión para confirmar reemplazo, o
                # simplemente ignorar o actualizar
                errores.append(
                    f"Liquidación para Técnico ID {tecnico_id}, mes {mes}, año {año} ya existe.")
                continue

            nueva = Liquidacion(
                tecnico=tecnico,
                mes=mes,
                año=año,
                monto=None,
                firmada=False
            )
            nueva.archivo_pdf_liquidacion.save(
                archivo.name, archivo, save=True)
            exitos += 1

        if exitos:
            messages.success(
                request, f"Se cargaron correctamente {exitos} liquidaciones.")
        if errores:
            for error in errores:
                messages.error(request, error)

    return render(request, 'liquidaciones/carga_masiva.html')


@staff_member_required
def crear_liquidacion(request):
    if request.method == 'POST':
        form = LiquidacionForm(request.POST, request.FILES)
        if form.is_valid():
            # fields=(tecnico;mes;año;monto;archivo_pdf_liquidacion;pdf_firmado;fecha_firma;firmada)
            form.save()
            messages.success(request, "Liquidación creada con éxito.")
            # return redirect('liquidaciones:lista_liquidaciones')
            return redirect('liquidaciones:admin_lista')
        else:
            print("🔴 Errores en el formulario:", form.errors)
    else:
        form = LiquidacionForm()

    return render(request, 'liquidaciones/crear_liquidacion.html', {'form': form})


@method_decorator(login_required, name='dispatch')
class UsuarioAutocomplete(AutoResponseView):
    def get_queryset(self):
        qs = User.objects.filter(is_active=True)
        if self.q:
            qs = qs.filter(
                Q(identidad__icontains=self.q) |
                Q(first_name__icontains=self.q) |
                Q(last_name__icontains=self.q)
            )
        return qs

    def get_result_label(self, item):
        return f"{item.identidad} - {item.first_name} {item.last_name}"

    def get_result_value(self, item):
        return str(item.pk)


@staff_member_required
def editar_liquidacion(request, pk):
    liquidacion = get_object_or_404(Liquidacion, pk=pk)
    if request.method == 'POST':
        form = LiquidacionForm(
            request.POST, request.FILES, instance=liquidacion)
        if form.is_valid():
            form.save()
            messages.success(request, 'Liquidación actualizada con éxito.')
            return redirect('liquidaciones:admin_lista')
    else:
        form = LiquidacionForm(instance=liquidacion)

    return render(request, 'liquidaciones/editar_liquidacion.html', {'form': form, 'liquidacion': liquidacion})


@staff_member_required
def eliminar_liquidacion(request, pk):
    liquidacion = get_object_or_404(Liquidacion, pk=pk)

    if request.method == "POST":
        liquidacion.delete()
        messages.success(request, "Liquidación eliminada correctamente.")
        return redirect("liquidaciones:admin_lista")

    return render(request, "liquidaciones/eliminar_confirmacion.html", {"liquidacion": liquidacion})


def verificar_storage(request):
    return JsonResponse({
        "USE_CLOUDINARY": getattr(settings, 'USE_CLOUDINARY', False),
        "STORAGE_BACKEND": getattr(settings, 'DEFAULT_FILE_STORAGE', 'No definido')
    })
