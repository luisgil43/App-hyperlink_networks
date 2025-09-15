from django.urls import reverse
import datetime as dt
from operaciones.models import SesionBilling, ItemBillingTecnico
from django.db.models import Prefetch
from openpyxl import Workbook
import datetime
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.contrib.auth.decorators import login_required, user_passes_test
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponseForbidden
from django.http import (
    HttpResponseBadRequest,
    JsonResponse,
    HttpResponseNotAllowed,)
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from operaciones.models import SesionBilling
from django.db import transaction
from django.db.models import Sum, Case, When, Q, Value, DecimalField, F, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.db.models import Sum, F, Q, Case, When, Value, DecimalField
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
    from datetime import datetime, time, timedelta
    from django.contrib import messages
    from django.core.paginator import Paginator
    from django.db import models
    from django.db.models import Q
    from django.utils import timezone

    def parse_date_any(s: str):
        """Devuelve date para varios formatos comunes o None."""
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    params = request.GET.copy()

    cantidad = params.get('cantidad', '10')
    page_number = params.get('page', '1')
    cantidad_int = 1000000 if cantidad == 'todos' else int(cantidad)

    du = params.get('du', '').strip()
    fecha_str = params.get('fecha', '').strip()
    proyecto = params.get('proyecto', '').strip()
    categoria = params.get('categoria', '').strip()
    tipo = params.get('tipo', '').strip()
    estado = params.get('estado', '').strip()

    movimientos = CartolaMovimiento.objects.all().order_by('-fecha')

    # Usuario (username, nombre, apellido)
    if du:
        movimientos = movimientos.filter(
            Q(usuario__username__icontains=du) |
            Q(usuario__first_name__icontains=du) |
            Q(usuario__last_name__icontains=du)
        )

    # ===== Filtro de FECHA =====
    if fecha_str:
        solo_digitos = fecha_str.isdigit()
        if solo_digitos and 1 <= int(fecha_str) <= 31:
            # Buscar por día del mes (cualquier mes/año)
            dia = int(fecha_str)
            movimientos = movimientos.filter(fecha__day=dia)
        else:
            fecha_valida = parse_date_any(fecha_str)
            if not fecha_valida:
                messages.warning(
                    request, "Invalid date. Use DD-MM-YYYY or only the day (e.g. 20).")
            else:
                campo_fecha = CartolaMovimiento._meta.get_field('fecha')
                if isinstance(campo_fecha, models.DateTimeField):
                    # Rango del día en la zona horaria activa
                    tz = timezone.get_current_timezone()
                    start = timezone.make_aware(
                        datetime.combine(fecha_valida, time.min), tz)
                    end = start + timedelta(days=1)
                    movimientos = movimientos.filter(
                        fecha__gte=start, fecha__lt=end)
                else:
                    # Si es DateField: igualdad directa
                    movimientos = movimientos.filter(fecha=fecha_valida)
    # ===========================

    if proyecto:
        movimientos = movimientos.filter(proyecto__nombre__icontains=proyecto)
    if categoria:
        movimientos = movimientos.filter(tipo__categoria__icontains=categoria)
    if tipo:
        movimientos = movimientos.filter(tipo__nombre__icontains=tipo)
    if estado:
        movimientos = movimientos.filter(status=estado)

    # Paginación
    paginator = Paginator(movimientos, cantidad_int)
    pagina = paginator.get_page(page_number)

    # QS para paginación manteniendo filtros
    params_no_page = params.copy()
    params_no_page.pop('page', None)
    base_qs = params_no_page.urlencode()
    full_qs = params.urlencode()

    estado_choices = CartolaMovimiento.ESTADOS
    filtros = {
        'du': du,
        'fecha': fecha_str,
        'proyecto': proyecto,
        'categoria': categoria,
        'tipo': tipo,
        'estado': estado,
    }

    ctx = {
        'pagina': pagina,
        'cantidad': cantidad,
        'estado_choices': estado_choices,
        'filtros': filtros,
        'base_qs': base_qs,
        'full_qs': full_qs,
    }
    return render(request, 'facturacion/listar_cartola.html', ctx)


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

    # Estados según tu modelo (pendientes por etapa)
    USER_PENDING = ['pendiente_abono_usuario']
    SUP_PENDING = ['pendiente_supervisor']
    PM_PENDING = ['aprobado_supervisor']   # esperando PM
    FIN_PENDING = ['aprobado_pm']           # esperando Finanzas

    # Constante decimal tipada (¡clave para evitar el FieldError!)
    DEC = DecimalField(max_digits=12, decimal_places=2)
    V0 = Value(Decimal('0.00'), output_field=DEC)

    # Sumas condicionadas (usar V0 en default)
    pend_user_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=USER_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_sup_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=SUP_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_sup_cargos = Sum(
        Case(
            When(Q(cargos__gt=0) & Q(status__in=SUP_PENDING), then=F('cargos')),
            default=V0, output_field=DEC,
        )
    )
    pend_pm_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=PM_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_pm_cargos = Sum(
        Case(
            When(Q(cargos__gt=0) & Q(status__in=PM_PENDING), then=F('cargos')),
            default=V0, output_field=DEC,
        )
    )
    pend_fin_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=FIN_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_fin_cargos = Sum(
        Case(
            When(Q(cargos__gt=0) & Q(status__in=FIN_PENDING), then=F('cargos')),
            default=V0, output_field=DEC,
        )
    )

    qs = (
        CartolaMovimiento.objects
        .values('usuario__id', 'usuario__first_name', 'usuario__last_name', 'usuario__email')
        .annotate(
            # Totales base (Coalesce con V0 decimal)
            monto_rendido=Coalesce(Sum('cargos'), V0, output_field=DEC),
            monto_asignado=Coalesce(Sum('abonos'), V0, output_field=DEC),

            # Pendiente por usuario (solo abonos)
            pend_user=pend_user_abonos,

            # Parciales por etapa
            _pend_sup_abonos=pend_sup_abonos,
            _pend_sup_cargos=pend_sup_cargos,
            _pend_pm_abonos=pend_pm_abonos,
            _pend_pm_cargos=pend_pm_cargos,
            _pend_fin_abonos=pend_fin_abonos,
            _pend_fin_cargos=pend_fin_cargos,
        )
        .annotate(
            # Combinar abonos+cargos en SQL (usa Coalesce(..., V0))
            pend_sup=ExpressionWrapper(
                Coalesce(F('_pend_sup_abonos'), V0, output_field=DEC) +
                Coalesce(F('_pend_sup_cargos'), V0, output_field=DEC),
                output_field=DEC,
            ),
            pend_pm=ExpressionWrapper(
                Coalesce(F('_pend_pm_abonos'), V0, output_field=DEC) +
                Coalesce(F('_pend_pm_cargos'), V0, output_field=DEC),
                output_field=DEC,
            ),
            pend_fin=ExpressionWrapper(
                Coalesce(F('_pend_fin_abonos'), V0, output_field=DEC) +
                Coalesce(F('_pend_fin_cargos'), V0, output_field=DEC),
                output_field=DEC,
            ),
            # Disponible: asignado - rendido (todo decimal)
            monto_disponible=ExpressionWrapper(
                Coalesce(F('monto_asignado'), V0, output_field=DEC) -
                Coalesce(F('monto_rendido'), V0, output_field=DEC),
                output_field=DEC,
            ),
        )
        .order_by('usuario__first_name', 'usuario__last_name')
    )

    paginator = Paginator(
        qs, qs.count() or 1) if cantidad == 'todos' else Paginator(qs, int(cantidad))
    pagina = paginator.get_page(request.GET.get('page'))

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
@rol_requerido('facturacion', 'admin')
def exportar_saldos(request):
    from django.db.models import Sum, Case, When, Q, Value, DecimalField, F
    import xlwt
    from django.http import HttpResponse

    USER_PENDING = ['pendiente_usuario',
                    'pendiente_aprobacion_usuario', 'pendiente_abono_usuario']
    SUP_PENDING = ['pendiente_supervisor']
    PM_PENDING = ['aprobado_supervisor', 'pendiente_pm']
    FIN_PENDING = ['aprobado_pm', 'pendiente_finanzas']

    def _sum_pending_abonos(status_list):
        return Sum(
            Case(
                When(Q(abonos__gt=0) & Q(status__in=status_list), then=F('abonos')),
                default=Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )

    def _sum_pending_cargos(status_list):
        return Sum(
            Case(
                When(Q(cargos__gt=0) & Q(status__in=status_list), then=F('cargos')),
                default=Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )

    balances = (
        CartolaMovimiento.objects
        .values('usuario__first_name', 'usuario__last_name')
        .annotate(
            rendered_amount=Sum('cargos', default=0),
            assigned_amount=Sum('abonos', default=0),
            available_amount=Sum(F('abonos') - F('cargos'), default=0),

            pending_user=_sum_pending_abonos(USER_PENDING),

            sup_abonos=_sum_pending_abonos(SUP_PENDING),
            sup_cargos=_sum_pending_cargos(SUP_PENDING),

            pm_abonos=_sum_pending_abonos(PM_PENDING),
            pm_cargos=_sum_pending_cargos(PM_PENDING),

            fin_abonos=_sum_pending_abonos(FIN_PENDING),
            fin_cargos=_sum_pending_cargos(FIN_PENDING),
        )
        .order_by('usuario__first_name', 'usuario__last_name')
    )

    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="available_balances.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Available Balances')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    currency_style = xlwt.easyxf(num_format_str='$#,##0.00')

    columns = [
        "User", "Amount Rendered", "Assigned Amount", "Available Amount",
        "Pending (User)", "Pending (Supervisor)", "Pending (PM)", "Pending (Finance)"
    ]
    for col, title in enumerate(columns):
        ws.write(0, col, title, header_style)

    for r, b in enumerate(balances, start=1):
        pend_sup = float((b['sup_abonos'] or 0) + (b['sup_cargos'] or 0))
        pend_pm = float((b['pm_abonos'] or 0) + (b['pm_cargos'] or 0))
        pend_fin = float((b['fin_abonos'] or 0) + (b['fin_cargos'] or 0))

        ws.write(r, 0, f"{b['usuario__first_name']} {b['usuario__last_name']}")
        ws.write(r, 1, float(b['rendered_amount'] or 0), currency_style)
        ws.write(r, 2, float(b['assigned_amount'] or 0), currency_style)
        ws.write(r, 3, float(b['available_amount'] or 0), currency_style)
        ws.write(r, 4, float(b['pending_user'] or 0), currency_style)
        ws.write(r, 5, pend_sup, currency_style)
        ws.write(r, 6, pend_pm,  currency_style)
        ws.write(r, 7, pend_fin, currency_style)

    wb.save(response)
    return response


def _parse_decimal(val: str | None) -> Decimal | None:
    if val is None:
        return None
    s = (str(val) or "").strip().replace(",", "")
    if s == "":
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _can_edit_real_week(user) -> bool:
    """
    PM / Finance / Admin can edit the real pay week.
    Ajusta esto a tu lógica de roles si usas permisos/grupos.
    """
    try:
        from usuarios.utils import user_has_any_role  # opcional
        return user_has_any_role(user, ["pm", "facturacion", "admin"])
    except Exception:
        # Fallback: usa el decorador que ya aplicamos a la vista
        return True


@login_required
@rol_requerido("facturacion", "admin")
def invoices_list(request):
    """
    Finance invoices list with the same columns and layout conventions
    you have in Operations (Billing List).
    """
    scope = request.GET.get("scope", "open")  # open | all | paid

    qs = (
        SesionBilling.objects.all()
        .select_related()
        .prefetch_related(
            "tecnicos_sesion__tecnico",
            "tecnicos_sesion__evidencias",
            "items",
            "items__desglose_tecnico__tecnico",
        )
        .order_by("-creado_en")
    )

    # 👇 Consideramos "open" también los flujos de descuento directo
    FINANCE_OPEN = (
        "review_discount",     # primer estado para descuentos directos
        "discount_applied",    # descuento confirmado, aún no cobrado
        "sent",                # flujo normal (aprobado por supervisor)
        "in_review",
        "pending",
        "rejected",
    )

    if scope == "paid":
        qs = qs.filter(finance_status="paid")
    elif scope == "all":
        # Todo lo que compete a Finanzas (open + paid), excluyendo los que aún no se han enviado
        qs = qs.exclude(
            Q(finance_status='none') |
            Q(finance_status='') |
            Q(finance_status__isnull=True)
        )
    else:  # "open"
        qs = qs.filter(finance_status__in=FINANCE_OPEN)

    cantidad = request.GET.get("cantidad", "10")
    try:
        per_page = 1000000 if cantidad == "todos" else int(cantidad)
    except Exception:
        per_page = 10
        cantidad = "10"

    from django.core.paginator import Paginator
    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page")
    pagina = paginator.get_page(page_number)

    ctx = {
        "pagina": pagina,
        "cantidad": cantidad,
        "scope": scope,
        "can_edit_real_week": _can_edit_real_week(request.user),
    }
    return render(request, "facturacion/invoices_list.html", ctx)


@require_POST
def invoice_update_real(request, pk):
    # Solo AJAX
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return HttpResponseForbidden('AJAX only')

    s = get_object_or_404(SesionBilling, pk=pk)

    real_raw = request.POST.get('real', None)
    week_raw = request.POST.get('week', None)

    with transaction.atomic():
        updated_fields = []

        # ----- Real Company Billing -----
        # la clave llegó (aunque sea vacía)
        if real_raw is not None:
            raw = (real_raw or '').strip()

            # Vacío o guiones => NULL en DB
            if raw in ('', '-', '—', 'null', 'None'):
                s.real_company_billing = None
                updated_fields.append('real_company_billing')
            else:
                # normaliza $ , espacios y miles
                txt = raw.replace('$', '').replace(',', '').replace(' ', '')
                try:
                    s.real_company_billing = Decimal(txt)
                    updated_fields.append('real_company_billing')
                    # si estaba “sent/in_review” y ahora hay número => pending
                    if s.finance_status in ('sent', 'in_review'):
                        s.finance_status = 'pending'
                        updated_fields.append('finance_status')
                except (InvalidOperation, ValueError):
                    return JsonResponse({'error': 'Invalid amount.'}, status=400)

        # ----- Real pay week (permite vacío) -----
        if week_raw is not None:
            s.semana_pago_real = (week_raw or '').strip()
            updated_fields.append('semana_pago_real')

        if updated_fields:
            updated_fields.append('finance_updated_at')  # tu campo auto_now
            s.save(update_fields=updated_fields)

    # difference solo si hay real
    diff = None
    if s.real_company_billing is not None:
        diff = (s.subtotal_empresa or Decimal('0')) - s.real_company_billing

    return JsonResponse({
        'ok': True,
        'real': (None if s.real_company_billing is None else f'{s.real_company_billing:.2f}'),
        'week': s.semana_pago_real or '',
        'difference': ('' if diff is None else f'{diff:.2f}'),
        'finance_status': s.finance_status,
    })


@login_required
@rol_requerido("facturacion", "admin")
def invoice_mark_paid(request, pk: int):
    """
    Mark invoice as Paid.
    - Requires real_company_billing not null
    - If difference > 0 (we receive less than company billing), require client-side confirmation (force=1)
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    s = get_object_or_404(SesionBilling, pk=pk)
    if s.real_company_billing is None:
        return HttpResponseBadRequest("Real Company Billing is required before marking as paid.")

    difference = (s.subtotal_empresa or Decimal("0")) - s.real_company_billing
    force = (request.POST.get("force") or "") == "1"

    if difference > 0 and not force:
        return JsonResponse({
            "ok": False,
            "confirm": True,
            "message": "You are collecting less than expected. Do you still want to mark it as paid?"
        }, status=409)

    with transaction.atomic():
        s.finance_status = "paid"
        if not s.semana_pago_real:
            y, w, _ = timezone.localdate().isocalendar()
            s.semana_pago_real = f"{y}-W{int(w):02d}"
        s.save(update_fields=["finance_status", "semana_pago_real"])

    return JsonResponse({"ok": True})


@login_required
@rol_requerido("facturacion", "admin")
def invoice_reject(request, pk: int):
    """
    Reject invoice back to Operations with a reason.
    Sets finance_status='rejected' and stores the note.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        return HttpResponseBadRequest("A rejection reason is required.")

    s = get_object_or_404(SesionBilling, pk=pk)
    with transaction.atomic():
        s.finance_status = "rejected"
        s.finance_note = reason
        s.save(update_fields=["finance_status", "finance_note"])

    return JsonResponse({"ok": True})


@login_required
@rol_requerido("facturacion", "admin")
@require_POST
@transaction.atomic
def invoice_remove(request, pk: int):
    """
    Saca la sesión de la cola de Finanzas (NO borra el billing).
    """
    # 🔒 Bloqueamos por id y actualizamos sin invocar save()
    updated = (
        SesionBilling.objects
        .filter(pk=pk)
        .update(
            finance_status="none",
            finance_note="",
            finance_sent_at=None,
            finance_updated_at=timezone.now(),
        )
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        # Si no existía, devolvemos error
        if not updated:
            return JsonResponse({"ok": False, "error": "Not found"}, status=404)
        return JsonResponse({"ok": True, "id": pk, "finance_status": "none"})

    messages.success(request, f"Billing #{pk} removed from Finance queue.")
    return redirect(request.META.get("HTTP_REFERER") or reverse("facturacion:invoices"))


@rol_requerido('facturacion', 'admin', 'pm')
@require_POST
def invoice_discount_verified(request, pk):
    s = get_object_or_404(SesionBilling, pk=pk)

    if not s.is_direct_discount:
        return JsonResponse({"ok": False, "error": "NOT_DIRECT_DISCOUNT"}, status=400)

    note = (request.POST.get("note") or "").strip()

    # Primer estado de descuentos → discount_applied
    s.finance_status = "discount_applied"
    if note:
        # apendea (igual que en otros flujos)
        prefix = f"{timezone.now():%Y-%m-%d %H:%M} Finance: "
        s.finance_note = ((s.finance_note + "\n")
                          if s.finance_note else "") + prefix + note

    s.save(update_fields=[
        "finance_status",
        *(["finance_note"] if note else []),
        "finance_updated_at",  # auto_now se actualiza
    ])

    return JsonResponse({"ok": True, "finance_status": s.finance_status})


def _to_excel_dt(value):
    """Excel no admite tz-aware datetimes."""
    if value is None:
        return ""
    return timezone.make_naive(value) if timezone.is_aware(value) else value


@rol_requerido('facturacion', 'admin', 'pm')
def invoices_export(request):
    scope = request.GET.get("scope", "open")

    qs = (
        SesionBilling.objects
        .select_related()
        .prefetch_related(
            "items",
            Prefetch(
                "items__desglose_tecnico",
                queryset=ItemBillingTecnico.objects.select_related("tecnico")
            ),
            "tecnicos_sesion__tecnico",
        )
    )

    # Solo los que corresponden al export: descuentos directos, aprobados supervisor/PM, o pagados.
    qs = qs.filter(
        Q(is_direct_discount=True) |
        Q(estado__in=["aprobado_supervisor", "aprobado_pm"]) |
        Q(finance_status__in=["paid"])
    )

    # Respeta el scope
    if scope == "open":
        qs = qs.exclude(finance_status="paid")
    elif scope == "paid":
        qs = qs.filter(finance_status="paid")
    # scope == "all": sin filtro adicional

    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"

    headers = [
        "ID", "Date", "Project ID", "Project address", "Projected week",
        "Status",
        "Technicians", "Client", "City", "Project", "Office",
        "Technical Billing", "Company Billing", "Real Company Billing",
        "Difference", "Finance status", "Finance note",
        "Pay week / Discount week",
    ]
    ws.append(headers)

    status_map = {
        "aprobado_pm": "Approved by PM",
        "rechazado_pm": "Rejected by PM",
        "aprobado_supervisor": "Approved by supervisor",
        "rechazado_supervisor": "Rejected by supervisor",
        "en_revision_supervisor": "In supervisor review",
        "finalizado": "Finished (pending review)",
        "en_proceso": "In progress",
        "asignado": "Assigned",
    }
    finance_map = {
        "none": "—",
        "review_discount": "Review discount",
        "discount_applied": "Discount applied",
        "sent": "Sent to client",
        "pending": "Pending payment",
        "in_review": "In review",
        "rejected": "Rejected",
        "paid": "Paid",
    }

    def _to_excel_dt(value):
        """Excel no acepta tz-aware; devolver naive o vacío."""
        if not value:
            return ""
        try:
            return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value
        except Exception:
            return value

    def techs_string(s):
        parts = []
        for st in s.tecnicos_sesion.all():
            tech = st.tecnico
            name = (tech.get_full_name() or tech.username) if tech else "—"
            parts.append(f"{name} ({st.porcentaje:.2f}%)")
        return ", ".join(parts)

    for s in qs:
        status_label = "Direct discount" if s.is_direct_discount else status_map.get(
            s.estado, "Assigned")
        finance_label = finance_map.get(s.finance_status, "—")
        diff = s.diferencia if s.diferencia is not None else Decimal("0.00")

        # Fusionar semanas
        real_week = s.semana_pago_real or ""
        disc_week = getattr(s, "discount_week", "") or getattr(
            s, "semana_descuento", "") or ""
        if real_week and disc_week:
            week_cell = f"{real_week} / {disc_week}"
        else:
            week_cell = real_week or disc_week  # el que exista

        ws.append([
            s.id,
            _to_excel_dt(s.creado_en),
            s.proyecto_id,
            s.direccion_proyecto,
            s.semana_pago_proyectada,
            status_label,
            techs_string(s),
            s.cliente, s.ciudad, s.proyecto, s.oficina,
            float(s.subtotal_tecnico or 0),
            float(s.subtotal_empresa or 0),
            float(s.real_company_billing or 0),
            float(diff or 0),
            finance_label,
            (s.finance_note or ""),
            week_cell,
        ])

    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="invoices_{now_str}.xlsx"'
    wb.save(resp)
    return resp
