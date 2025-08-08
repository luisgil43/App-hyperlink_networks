from django.db.models import Sum, F
from django.utils.timezone import is_aware
import xlwt
from io import BytesIO
from django.utils.module_loading import import_string
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import Q
from operaciones.forms import MovimientoUsuarioForm
from django.db.models import Sum, Q
from django.contrib.auth import get_user_model
from facturacion.models import CartolaMovimiento
from django.shortcuts import render
from django.db.models import Sum, F, Value
from .forms import CartolaMovimientoCompletoForm
from .forms import ProyectoForm
from .models import Proyecto
from django.template.loader import render_to_string
from .forms import TipoGastoForm
from .models import TipoGasto
from .forms import CartolaAbonoForm
from .forms import CartolaGastoForm
from .models import CartolaMovimiento
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from dateutil import parser
from decimal import Decimal, InvalidOperation
from django.http import JsonResponse
from openpyxl.styles import Font, Alignment, PatternFill
from django.http import HttpResponse
from openpyxl.utils import get_column_letter
import openpyxl
import traceback
from usuarios.decoradores import rol_requerido
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from datetime import datetime
from decimal import Decimal
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.contrib import messages
import re
import pdfplumber

from django.core.paginator import Paginator


from django.db.models import Subquery


User = get_user_model()


@login_required
@rol_requerido('facturacion', 'admin')
def listar_cartola(request):
    cantidad = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad == 'todos' else int(cantidad)

    # Capturar filtros
    du = request.GET.get('du', '').strip()
    fecha = request.GET.get('fecha', '').strip()
    proyecto = request.GET.get('proyecto', '').strip()
    categoria = request.GET.get('categoria', '').strip()
    tipo = request.GET.get('tipo', '').strip()
    rut_factura = request.GET.get('rut_factura', '').strip()
    estado = request.GET.get('estado', '').strip()

    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    # Filtrar por usuario (busca en rut, nombre y apellido)
    if du:
        movimientos = movimientos.filter(
            Q(usuario__username__icontains=du) |
            Q(usuario__first_name__icontains=du) |
            Q(usuario__last_name__icontains=du)
        )

    # Filtrar por fecha con validación segura (dd-mm-yyyy → yyyy-mm-dd)
    if fecha:
        try:
            fecha_valida = datetime.strptime(fecha, "%d-%m-%Y").date()
            movimientos = movimientos.filter(fecha__date=fecha_valida)
        except ValueError:
            messages.warning(
                request, "Invalid date format. Please use DD-MM-YYYY.")

    if proyecto:
        movimientos = movimientos.filter(proyecto__nombre__icontains=proyecto)
    if categoria:
        movimientos = movimientos.filter(tipo__categoria__icontains=categoria)
    if tipo:
        movimientos = movimientos.filter(tipo__nombre__icontains=tipo)
    if rut_factura:
        movimientos = movimientos.filter(rut_factura__icontains=rut_factura)
    if estado:
        movimientos = movimientos.filter(status=estado)

    # Paginación
    paginator = Paginator(movimientos, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    estado_choices = CartolaMovimiento.ESTADOS
    filtros = {
        'du': du,
        'fecha': fecha,
        'proyecto': proyecto,
        'categoria': categoria,
        'tipo': tipo,
        'rut_factura': rut_factura,
        'estado': estado,
    }

    return render(request, 'facturacion/listar_cartola.html', {
        'pagina': pagina,
        'cantidad': request.GET.get('cantidad', '10'),
        'estado_choices': estado_choices,
        'filtros': filtros
    })


@login_required
@rol_requerido('facturacion', 'admin')
def registrar_abono(request):
    if request.method == 'POST':
        form = CartolaAbonoForm(request.POST, request.FILES)
        if form.is_valid():
            movimiento = form.save(commit=False)
            from .models import TipoGasto
            tipo_abono = TipoGasto.objects.filter(categoria='abono').first()
            movimiento.tipo = tipo_abono
            movimiento.cargos = 0

            # Solo asignamos el archivo, Django lo subirá a Wasabi
            if 'comprobante' in request.FILES:
                movimiento.comprobante = request.FILES['comprobante']

            movimiento.save()
            messages.success(request, "Transaction registered successfully.")
            return redirect('facturacion:listar_cartola')
        else:
            messages.error(
                request, "Please correct the errors before proceeding.")
    else:
        form = CartolaAbonoForm()
    return render(request, 'facturacion/registrar_abono.html', {'form': form})


@login_required
@rol_requerido('facturacion', 'admin')
def crear_tipo(request):
    if request.method == 'POST':
        form = TipoGastoForm(request.POST)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                tipos = TipoGasto.objects.all().order_by('-id')
                html = render_to_string(
                    'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
                return JsonResponse({'success': True, 'html': html})
            messages.success(request, "Expense type created successfully.")
            return redirect('facturacion:crear_tipo')
    else:
        form = TipoGastoForm()
    tipos = TipoGasto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_tipo.html', {'form': form, 'tipos': tipos})


@login_required
@rol_requerido('admin')
def editar_tipo(request, pk):
    tipo = get_object_or_404(TipoGasto, pk=pk)
    if request.method == 'POST':
        form = TipoGastoForm(request.POST, instance=tipo)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                tipos = TipoGasto.objects.all().order_by('-id')
                html = render_to_string(
                    'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
                return JsonResponse({'success': True, 'html': html})
            messages.success(request, "Expense type updated successfully.")
            return redirect('facturacion:crear_tipo')
    else:
        form = TipoGastoForm(instance=tipo)
    tipos = TipoGasto.objects.all().order_by('-id')
    # Usamos el mismo template que crear
    return render(request, 'facturacion/crear_tipo.html', {'form': form, 'tipos': tipos, 'editando': True})


@login_required
@rol_requerido('admin')
def eliminar_tipo(request, pk):
    tipo = get_object_or_404(TipoGasto, pk=pk)
    tipo.delete()
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        tipos = TipoGasto.objects.all().order_by('-id')
        html = render_to_string(
            'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
        return JsonResponse({'success': True, 'html': html})
    messages.success(request, "Expense type deleted successfully.")
    return redirect('facturacion:crear_tipo')


# Listar y crear
@login_required
@rol_requerido('facturacion', 'admin')
def crear_proyecto(request):
    if request.method == 'POST':
        form = ProyectoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Project created successfully.")
            return redirect('facturacion:crear_proyecto')
    else:
        form = ProyectoForm()
    proyectos = Proyecto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_proyecto.html', {
        'form': form,
        'proyectos': proyectos
    })

# Editar


@login_required
@rol_requerido('admin')
def editar_proyecto(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        form = ProyectoForm(request.POST, instance=proyecto)
        if form.is_valid():
            form.save()
            messages.success(request, "Project updated successfully.")
            return redirect('facturacion:crear_proyecto')
    else:
        form = ProyectoForm(instance=proyecto)
    proyectos = Proyecto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_proyecto.html', {
        'form': form,
        'proyectos': proyectos
    })

# Eliminar


@login_required
@rol_requerido('admin')
def eliminar_proyecto(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        proyecto.delete()
        messages.success(request, "Project deleted successfully.")
        return redirect('facturacion:crear_proyecto')
    return redirect('facturacion:crear_proyecto')


@login_required
@rol_requerido('facturacion', 'supervisor', 'pm', 'admin')
def aprobar_movimiento(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    if mov.tipo and mov.tipo.categoria != "abono":
        # Asignar aprobador según el rol
        if request.user.es_supervisor and mov.status == 'pendiente_supervisor':
            mov.status = 'aprobado_supervisor'
            mov.aprobado_por_supervisor = request.user
        elif request.user.es_pm and mov.status == 'aprobado_supervisor':
            mov.status = 'aprobado_pm'
            mov.aprobado_por_pm = request.user
        elif request.user.es_facturacion and mov.status == 'aprobado_pm':
            mov.status = 'aprobado_finanzas'
            mov.aprobado_por_finanzas = request.user  # Usuario de finanzas

        mov.motivo_rechazo = ''  # Limpiar cualquier rechazo previo
        mov.save()
        messages.success(request, "Expense approved successfully.")
    return redirect('facturacion:listar_cartola')


@login_required
@rol_requerido('facturacion', 'supervisor', 'pm', 'admin')
def rechazar_movimiento(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo', '').strip()
        if mov.tipo and mov.tipo.categoria != "abono":
            if request.user.es_supervisor and mov.status == 'pendiente_supervisor':
                mov.status = 'rechazado_supervisor'
                mov.aprobado_por_supervisor = request.user
            elif request.user.es_pm and mov.status == 'aprobado_supervisor':
                mov.status = 'rechazado_pm'
                mov.aprobado_por_pm = request.user
            elif request.user.es_facturacion and mov.status == 'aprobado_pm':
                mov.status = 'rechazado_finanzas'
                mov.aprobado_por_finanzas = request.user  # Usuario de finanzas

            mov.motivo_rechazo = motivo
            mov.save()
            messages.success(request, "Expense rejected successfully.")
    return redirect('facturacion:listar_cartola')


@login_required
@rol_requerido('facturacion', 'admin')
def editar_movimiento(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)

    FormClass = CartolaAbonoForm if (
        movimiento.tipo and movimiento.tipo.categoria == "abono") else MovimientoUsuarioForm
    estado_restaurado = 'pendiente_abono_usuario' if FormClass == CartolaAbonoForm else 'pendiente_supervisor'

    if request.method == 'POST':
        form = FormClass(request.POST, request.FILES, instance=movimiento)
        if form.is_valid():
            movimiento = form.save(commit=False)
            if 'comprobante' in request.FILES:
                # Replace in Wasabi
                movimiento.comprobante = request.FILES['comprobante']

            if form.changed_data:
                movimiento.status = estado_restaurado
                movimiento.motivo_rechazo = ""
            movimiento.save()
            messages.success(request, "Expense updated successfully.")
            return redirect('facturacion:listar_cartola')
    else:
        form = FormClass(instance=movimiento)

    return render(request, 'facturacion/editar_movimiento.html', {
        'form': form,
        'movimiento': movimiento
    })


@login_required
@rol_requerido('admin')
def eliminar_movimiento(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        movimiento.delete()
        messages.success(request, "Expense deleted successfully.")
        return redirect('facturacion:listar_cartola')
    return render(request, 'facturacion/eliminar_movimiento.html', {'movimiento': movimiento})


@login_required
@rol_requerido('facturacion', 'admin')
def listar_saldos_usuarios(request):
    cantidad = request.GET.get('cantidad', '5')

    # Agrupar por usuario y calcular rendido y disponible
    saldos = (CartolaMovimiento.objects
              .values('usuario__id', 'usuario__first_name', 'usuario__last_name', 'usuario__email')
              .annotate(
                  monto_rendido=Sum('cargos'),
                  monto_asignado=Sum('abonos'),
              )
              .order_by('usuario__first_name'))

    # Calcular monto disponible
    for s in saldos:
        s['monto_disponible'] = (
            s['monto_asignado'] or 0) - (s['monto_rendido'] or 0)

    # Paginación como facturación
    if cantidad == 'todos':
        paginator = Paginator(saldos, saldos.count() or 1)  # Todo en 1 página
    else:
        paginator = Paginator(saldos, int(cantidad))

    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    return render(request, 'facturacion/listar_saldos_usuarios.html', {
        'saldos': pagina,
        'pagina': pagina,
        'cantidad': cantidad,
    })


@login_required
@rol_requerido('facturacion', 'admin')
def exportar_cartola(request):
    movimientos = CartolaMovimiento.objects.all()

    if usuario := request.GET.get("du"):
        movimientos = movimientos.filter(usuario__username__icontains=usuario)
    if fecha := request.GET.get("fecha"):
        movimientos = movimientos.filter(fecha=fecha)
    if proyecto := request.GET.get("proyecto"):
        movimientos = movimientos.filter(proyecto__nombre__icontains=proyecto)
    if categoria := request.GET.get("categoria"):
        movimientos = movimientos.filter(tipo__categoria__icontains=categoria)
    if tipo := request.GET.get("tipo"):
        movimientos = movimientos.filter(tipo__nombre__icontains=tipo)
    if rut := request.GET.get("rut_factura"):
        movimientos = movimientos.filter(rut_factura__icontains=rut)
    if estado := request.GET.get("estado"):
        movimientos = movimientos.filter(status=estado)

    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="transactions_ledger.xls"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Transactions')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = [
        "User", "Date", "Project", "Category", "Type", "Remarks",
        "Transfer Number", "Debits", "Credits", "Status"
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    for row_num, mov in enumerate(movimientos, start=1):
        ws.write(row_num, 0, str(mov.usuario))

        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()
        ws.write(row_num, 1, fecha_excel, date_style)

        ws.write(row_num, 2, str(mov.proyecto))
        ws.write(row_num, 3, mov.tipo.categoria.title())
        ws.write(row_num, 4, str(mov.tipo))
        ws.write(row_num, 5, mov.observaciones or "")
        ws.write(row_num, 6, mov.numero_transferencia or "")
        ws.write(row_num, 7, float(mov.cargos or 0))
        ws.write(row_num, 8, float(mov.abonos or 0))
        ws.write(row_num, 9, mov.get_status_display())

    wb.save(response)
    return response


@login_required
def exportar_saldos(request):
    """
    Exporta todos los saldos disponibles en un archivo Excel.
    Los títulos visibles estarán en inglés, pero el código comentado queda en español.
    """
    from facturacion.models import CartolaMovimiento

    # Agrupamos por usuario para obtener montos rendidos y disponibles
    balances = (CartolaMovimiento.objects
                .values('usuario__first_name', 'usuario__last_name')
                .annotate(
                    rendered_amount=Sum('cargos', default=0),
                    available_amount=Sum(F('abonos') - F('cargos'), default=0)
                )
                .order_by('usuario__first_name', 'usuario__last_name'))

    # Configuramos respuesta HTTP para descarga directa
    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="available_balances.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    # Creamos el archivo Excel
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Available Balances')

    # Estilos
    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    currency_style = xlwt.easyxf(num_format_str='$#,##0.00')

    # Cabeceras en inglés
    columns = ["User", "Rendered Amount", "Available Amount"]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    # Escribir los datos
    for row_num, b in enumerate(balances, start=1):
        user_name = f"{b['usuario__first_name']} {b['usuario__last_name']}"
        ws.write(row_num, 0, user_name)
        ws.write(row_num, 1, float(b['rendered_amount'] or 0), currency_style)
        ws.write(row_num, 2, float(b['available_amount'] or 0), currency_style)

    # Guardar archivo
    wb.save(response)
    return response
