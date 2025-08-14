# operaciones/views.py
from django.db.models import Prefetch
from .models import SesionBillingTecnico, EvidenciaFotoBilling, ItemBilling, ItemBillingTecnico
from .models import PrecioActividadTecnico  # tu modelo
from .models import (
    SesionBilling, SesionBillingTecnico,
    ItemBilling, ItemBillingTecnico,
    PrecioActividadTecnico,
)
from django.http import JsonResponse, HttpResponseBadRequest
from decimal import Decimal, ROUND_HALF_UP
from .forms import ImportarPreciosForm, PrecioActividadTecnicoForm  # <-- TUS FORMS
from .models import PrecioActividadTecnico           # <-- TU MODELO DE PRECIOS
from usuarios.models import CustomUser  # ajusta si tu user model es otro
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model
from django.db.models import Q
from decimal import Decimal, InvalidOperation
from django.db import transaction
from .forms import PrecioActividadTecnicoForm  # lo definimos abajo
from datetime import date
from .forms import ImportarPreciosForm
from .models import PrecioActividadTecnico
from django.utils.timezone import is_aware

import boto3
from django.db.models import Sum
from .forms import MovimientoUsuarioForm  # crearemos este form
from django.shortcuts import redirect
from facturacion.models import CartolaMovimiento
from django.db.models import Sum, Q
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from django.utils.html import escape
from django.utils.encoding import force_str
from django.core.paginator import Paginator
import calendar
from decimal import Decimal
import requests
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, Image
import io
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from django.db.models.functions import Coalesce
from django.db.models import Sum, F, Count, Value, FloatField
from django.db.models import Case, When, Value, IntegerField
from django.utils.timezone import now
from django.http import HttpResponseServerError
import logging
import xlwt
from django.http import HttpResponse
import csv
from usuarios.models import CustomUser
from django.urls import reverse
from usuarios.utils import crear_notificacion  # asegúrate de tener esta función
from datetime import datetime
import locale
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
import pandas as pd
from django.db import models
from django.contrib import messages
from django.shortcuts import render, redirect
from django.shortcuts import render

from django.contrib.auth.decorators import login_required
from usuarios.decoradores import rol_requerido
from botocore.exceptions import ClientError


def verificar_archivo_wasabi(ruta):
    """Verifica si un archivo existe en el bucket Wasabi."""
    s3 = boto3.client(
        's3',
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    try:
        s3.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=ruta)
        return True
    except ClientError:
        return False


@login_required
def mis_rendiciones(request):
    user = request.user

    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES)
        if form.is_valid():
            mov = form.save(commit=False)
            mov.usuario = user
            mov.fecha = now()
            mov.status = 'pendiente_abono_usuario' if mov.tipo and mov.tipo.categoria == "abono" else 'pendiente_supervisor'
            mov.comprobante = form.cleaned_data['comprobante']
            mov.save()  # Upload to Wasabi

            # Verify in Wasabi (with retry)
            ruta_archivo = mov.comprobante.name
            import time
            for _ in range(3):  # up to 3 attempts
                if verificar_archivo_wasabi(ruta_archivo):
                    break
                time.sleep(1)
            else:
                mov.delete()
                messages.error(
                    request, "Error uploading the receipt. Please try again.")
                return redirect('operaciones:mis_rendiciones')

            messages.success(
                request, "Expense report registered successfully.")
            return redirect('operaciones:mis_rendiciones')
    else:
        form = MovimientoUsuarioForm()

    # --- Filters & Pagination ---
    cantidad = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad == 'todos' else int(cantidad)

    movimientos = CartolaMovimiento.objects.filter(
        usuario=user).order_by('-fecha')
    paginator = Paginator(movimientos, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # --- Balance calculation ---
    saldo_disponible = (
        (movimientos.filter(tipo__categoria="abono", status="aprobado_abono_usuario")
         .aggregate(total=Sum('abonos'))['total'] or 0)
        -
        (movimientos.exclude(tipo__categoria="abono")
         .filter(status="aprobado_finanzas")
         .aggregate(total=Sum('cargos'))['total'] or 0)
    )

    saldo_pendiente = movimientos.filter(tipo__categoria="abono").exclude(
        status="aprobado_abono_usuario").aggregate(total=Sum('abonos'))['total'] or 0
    saldo_rendido = movimientos.exclude(tipo__categoria="abono").exclude(
        status="aprobado_finanzas").aggregate(total=Sum('cargos'))['total'] or 0

    return render(request, 'operaciones/mis_rendiciones.html', {
        'pagina': pagina,
        'cantidad': request.GET.get('cantidad', '10'),
        'saldo_disponible': saldo_disponible,
        'saldo_pendiente': saldo_pendiente,
        'saldo_rendido': saldo_rendido,
        'form': form,
    })


@login_required
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Deposit approved successfully.")
    return redirect('operaciones:mis_rendiciones')


@login_required
def rechazar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if request.method == "POST":
        motivo = request.POST.get("motivo", "")
        mov.status = "rechazado_abono_usuario"
        mov.motivo_rechazo = motivo
        mov.save()
        messages.error(
            request, "Deposit rejected and sent to Finance for review."
        )
    return redirect('operaciones:mis_rendiciones')


@login_required
def editar_rendicion(request, pk):
    rendicion = get_object_or_404(
        CartolaMovimiento, pk=pk, usuario=request.user
    )

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(
            request, "You cannot edit an already approved expense report.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        form = MovimientoUsuarioForm(
            request.POST, request.FILES, instance=rendicion)

        if form.is_valid():
            # --- Detectar cambios ---
            campos_editados = []
            for field in form.changed_data:
                # ignoramos campos automáticos como 'status'
                if field not in ['status', 'actualizado']:
                    campos_editados.append(field)

            if campos_editados:
                # Si cambió algo y estaba rechazado, restablecer estado
                if rendicion.status in [
                    'rechazado_abono_usuario',
                    'rechazado_supervisor',
                    'rechazado_pm',
                    'rechazado_finanzas'
                ]:
                    rendicion.status = 'pendiente_supervisor'  # estado reiniciado

            form.save()
            messages.success(request, "Expense report successfully updated.")
            return redirect('operaciones:mis_rendiciones')
    else:
        form = MovimientoUsuarioForm(instance=rendicion)

    return render(request, 'operaciones/editar_rendicion.html', {'form': form})


@login_required
def eliminar_rendicion(request, pk):
    rendicion = get_object_or_404(
        CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(
            request, "No puedes eliminar una rendición ya aprobada.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        rendicion.delete()
        messages.success(request, "Rendición eliminada correctamente.")
        return redirect('operaciones:mis_rendiciones')

    return render(request, 'operaciones/eliminar_rendicion.html', {'rendicion': rendicion})


@login_required
@rol_requerido('pm', 'admin', 'supervisor')
def vista_rendiciones(request):
    user = request.user

    if user.is_superuser:
        movimientos = CartolaMovimiento.objects.all()
    else:
        filtro = Q()

        # Supervisor: pendientes y rechazados por supervisor
        if getattr(user, 'es_supervisor', False):
            filtro |= Q(status='pendiente_supervisor') | Q(
                status='rechazado_supervisor')

        # PM: aprobados por supervisor (pendientes de PM), rechazados por PM, y ya aprobados por PM
        if getattr(user, 'es_pm', False):
            filtro |= Q(status='aprobado_supervisor') | Q(
                status='rechazado_pm') | Q(status='aprobado_pm')

        movimientos = CartolaMovimiento.objects.filter(
            filtro) if filtro else CartolaMovimiento.objects.none()

    # Orden personalizado
    movimientos = movimientos.annotate(
        orden_status=Case(
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado', then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by('orden_status', '-fecha')

    # Totales
    total = movimientos.aggregate(total=Sum('cargos'))['total'] or 0
    pendientes = movimientos.filter(status__startswith='pendiente').aggregate(
        total=Sum('cargos'))['total'] or 0
    rechazados = movimientos.filter(status__startswith='rechazado').aggregate(
        total=Sum('cargos'))['total'] or 0

    # Paginación
    cantidad = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad == 'todos' else int(cantidad)
    paginator = Paginator(movimientos, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/vista_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad,
        'total': total,
        'pendientes': pendientes,
        'rechazados': rechazados,
    })


@login_required
@rol_requerido('pm', 'admin', 'supervisor', 'facturacion')
def aprobar_rendicion(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    user = request.user

    if getattr(user, 'es_supervisor', False) and mov.status == 'pendiente_supervisor':
        mov.status = 'aprobado_supervisor'
        mov.aprobado_por_supervisor = user
    elif getattr(user, 'es_pm', False) and mov.status == 'aprobado_supervisor':
        mov.status = 'aprobado_pm'
        mov.aprobado_por_pm = user
    elif getattr(user, 'es_facturacion', False) and mov.status == 'aprobado_pm':
        mov.status = 'aprobado_finanzas'
        mov.aprobado_por_finanzas = user

    mov.motivo_rechazo = ''
    mov.save()
    messages.success(request, "Expense report approved successfully.")
    return redirect('operaciones:vista_rendiciones')


@login_required
@rol_requerido('pm', 'admin', 'supervisor', 'facturacion')
def rechazar_rendicion(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo')
        if motivo:
            movimiento.motivo_rechazo = motivo
            if request.user.es_supervisor and movimiento.status == 'pendiente_supervisor':
                movimiento.status = 'rechazado_supervisor'
                movimiento.aprobado_por_supervisor = request.user
            elif request.user.es_pm and movimiento.status == 'aprobado_supervisor':
                movimiento.status = 'rechazado_pm'
                movimiento.aprobado_por_pm = request.user
            elif request.user.es_facturacion and movimiento.status == 'aprobado_pm':
                movimiento.status = 'rechazado_finanzas'
                movimiento.aprobado_por_finanzas = request.user
            movimiento.save()
            messages.success(request, "Expense report rejected successfully.")
        else:
            messages.error(request, "Please enter the rejection reason.")
    return redirect('operaciones:vista_rendiciones')


@login_required
@rol_requerido('pm', 'admin')  # Solo PM
def exportar_rendiciones(request):
    # Filtro: solo lo que ve el PM
    movimientos = CartolaMovimiento.objects.filter(
        Q(status='aprobado_supervisor') | Q(
            status='rechazado_pm') | Q(status='aprobado_pm')
    ).order_by('status', '-fecha')

    # Crear respuesta Excel
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="expense_reports.xls"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Expense Reports')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = ["User", "Date", "Project",
               "Type", "Remarks", "Amount", "Status"]
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
        ws.write(row_num, 3, str(mov.tipo))
        ws.write(row_num, 4, mov.observaciones or "")
        ws.write(row_num, 5, float(mov.cargos or 0))
        ws.write(row_num, 6, mov.get_status_display())

    wb.save(response)
    return response


@login_required
def exportar_mis_rendiciones(request):
    user = request.user
    movimientos = CartolaMovimiento.objects.filter(
        usuario=user
    ).order_by('-fecha')

    # Crear archivo Excel
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="my_expense_reports.xls"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('My Expense Reports')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    # Columnas (igual que el PM)
    columns = [
        "User", "Date", "Project", "Type",
        "Expenses (USD)", "Credits (USD)", "Remarks", "Status"
    ]
    for col_num, column_title in enumerate(columns):
        ws.write(0, col_num, column_title, header_style)

    # Datos
    for row_num, mov in enumerate(movimientos, start=1):
        # Fecha: naive y solo date
        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()

        ws.write(row_num, 0, mov.usuario.get_full_name())
        ws.write(row_num, 1, fecha_excel, date_style)
        ws.write(row_num, 2, str(mov.proyecto))
        ws.write(row_num, 3, str(mov.tipo))
        ws.write(row_num, 4, float(mov.cargos or 0))
        ws.write(row_num, 5, float(mov.abonos or 0))
        ws.write(row_num, 6, mov.observaciones or "")
        ws.write(row_num, 7, mov.get_status_display())

    wb.save(response)
    return response


# operaciones/views.py


# ---------- LISTAR ----------
@login_required
@rol_requerido('admin', 'pm', 'facturacion')
def listar_precios_tecnico(request):
    cantidad_str = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad_str == 'todos' else int(cantidad_str)

    precios = PrecioActividadTecnico.objects.select_related(
        'tecnico').order_by('-fecha_creacion')
    paginator = Paginator(precios, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    return render(request, 'operaciones/listar_precios_tecnico.html', {
        'pagina': pagina,
        'cantidad': cantidad_str
    })


# ---------- IMPORTAR -> PREVIEW (con conflictos) ----------
@login_required
@rol_requerido('admin', 'pm')
def importar_precios(request):
    """
    Sube el Excel, arma preview_data, calcula conflictos por (Ciudad, Proyecto, Oficina, Cliente)
    y renderiza el preview. NO guarda aún.
    """
    if request.method == 'POST':
        form = ImportarPreciosForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Invalid form.")
            return redirect('operaciones:importar_precios')

        archivo = request.FILES['archivo']
        tecnicos = form.cleaned_data['tecnicos']

        try:
            # 1) Verificar extensión
            if not archivo.name.endswith('.xlsx'):
                messages.error(request, "The file must be in .xlsx format.")
                return redirect('operaciones:importar_precios')

            # 2) Leer Excel
            df = pd.read_excel(archivo, header=0)
            if df.empty:
                messages.error(request, "The uploaded Excel file is empty.")
                return redirect('operaciones:importar_precios')

            # 3) Normalizar columnas
            df.columns = df.columns.str.strip().str.lower().str.replace(r'\s+', '_', regex=True)

            colmap = {
                'city': ['city', 'ciudad'],
                'project': ['project', 'proyect', 'proyecto'],
                'office': ['office', 'oficina', 'oficce'],
                'client': ['client', 'cliente'],
                'work_type': ['work_type', 'tipo_trabajo', 'tipo_de_trabajo'],
                'code': ['code', 'job_code', 'codigo', 'codigo_trabajo'],
                'description': ['description', 'descripcion', 'descripción'],
                'uom': ['uom', 'unidad_medida', 'unidad', 'unit'],
                'technical_price': ['technical_price', 'tech_price', 'precio_tecnico', 'precio_técnico'],
                'company_price': ['company_price', 'precio_empresa', 'companyprice'],
            }

            def resolve(colkey, required=True):
                for cand in colmap[colkey]:
                    if cand in df.columns:
                        return cand
                if required:
                    raise KeyError(
                        f"Required column not found for '{colkey}'. Available columns: {list(df.columns)}"
                    )
                return None

            c_city = resolve('city')
            c_proj = resolve('project')
            c_code = resolve('code')
            c_desc = resolve('description')
            c_uom = resolve('uom')
            c_tp = resolve('technical_price')
            c_cp = resolve('company_price')

            c_office = resolve('office', required=False)
            c_client = resolve('client', required=False)
            c_wtype = resolve('work_type', required=False)

            # 4) Armar preview_data
            preview_data = []

            def _to2(val):
                try:
                    return float(Decimal(str(val)).quantize(Decimal("0.01")))
                except (InvalidOperation, ValueError, TypeError):
                    return None

            required = [c_city, c_proj, c_code, c_desc, c_uom, c_tp, c_cp]
            for _, row in df.iterrows():
                r = {
                    'ciudad': row.get(c_city),
                    'proyecto': row.get(c_proj),
                    'codigo_trabajo': row.get(c_code),
                    'descripcion': row.get(c_desc),
                    'uom': row.get(c_uom),
                    'precio_tecnico': _to2(row.get(c_tp)),
                    'precio_empresa': _to2(row.get(c_cp)),
                    'oficina': row.get(c_office) if c_office else "",
                    'cliente': row.get(c_client) if c_client else "",
                    'tipo_trabajo': row.get(c_wtype) if c_wtype else "",
                    'tecnico': [t.id for t in tecnicos],
                    'error': ''
                }

                # Validaciones básicas
                missing_keys = []
                if not r['ciudad']:
                    missing_keys.append('city')
                if not r['proyecto']:
                    missing_keys.append('project')
                if not r['codigo_trabajo']:
                    missing_keys.append('code')
                if not r['descripcion']:
                    missing_keys.append('description')
                if not r['uom']:
                    missing_keys.append('uom')
                if r['precio_tecnico'] is None:
                    r['error'] += (" | " if r['error'] else "") + \
                        "Invalid Technical Price"
                if r['precio_empresa'] is None:
                    r['error'] += (" | " if r['error'] else "") + \
                        "Invalid Company Price"
                if missing_keys:
                    r['error'] += (" | " if r['error'] else "") + \
                        f"Missing fields: {', '.join(missing_keys)}"

                preview_data.append(r)

            # 5) Guardar en sesión para el POST final
            request.session['preview_data'] = preview_data

            # 6) Calcular conflictos por (Ciudad, Proyecto, Oficina, Cliente)
            combos = {
                (row.get('ciudad'), row.get('proyecto'),
                 row.get('oficina'), row.get('cliente'))
                for row in preview_data
                if row.get('ciudad') and row.get('proyecto') and row.get('oficina') and row.get('cliente')
            }

            has_conflicts = False
            conflicts_by_tech = {}

            if combos:
                combo_q = Q()
                for c, p, o, cl in combos:
                    combo_q |= Q(ciudad=c, proyecto=p, oficina=o, cliente=cl)

                for t in tecnicos:
                    qs = (PrecioActividadTecnico.objects
                          .filter(tecnico=t)
                          .filter(combo_q)
                          .values('ciudad', 'proyecto', 'oficina', 'cliente')
                          .distinct())
                    conflicts = list(qs)
                    conflicts_by_tech[t.id] = conflicts
                    if conflicts:
                        has_conflicts = True
            else:
                for t in tecnicos:
                    conflicts_by_tech[t.id] = []

            # 7) Render del preview con flags
            return render(
                request,
                'operaciones/preview_import.html',
                {
                    'preview_data': preview_data,
                    'tecnicos': tecnicos,
                    'has_conflicts': has_conflicts,
                    'conflicts_by_tech': conflicts_by_tech,
                }
            )

        except KeyError as ke:
            messages.error(
                request, f"Column not found or incorrectly assigned: {ke}"
            )
            return redirect('operaciones:importar_precios')
        except Exception as e:
            messages.error(request, f"Error during import: {str(e)}")
            return redirect('operaciones:importar_precios')

    # GET
    form = ImportarPreciosForm()
    return render(request, 'operaciones/importar_precios.html', {'form': form})


# ---------- CONFIRMAR / GUARDAR ----------
@login_required
@rol_requerido('admin', 'pm')
def confirmar_importar_precios(request):
    """
    Saves the data from session['preview_data'].
    - If replace=yes: update_or_create using the key (tecnico, ciudad, proyecto, oficina, cliente, codigo_trabajo)
    - If replace=no: get_or_create to avoid duplicates
    """
    if request.method != 'POST':
        return redirect('operaciones:importar_precios')

    try:
        preview_data = request.session.get('preview_data', [])
        if not preview_data:
            messages.error(
                request, "No data to save. Please try again.")
            return redirect('operaciones:importar_precios')

        replace = request.POST.get('replace') == 'yes'
        created_total = 0
        updated_total = 0
        skipped_total = 0

        with transaction.atomic():
            for row in preview_data:
                # Skip if there's an error message
                if row.get('error'):
                    continue

                tecnico_ids = row.get('tecnico', [])
                tecnicos = CustomUser.objects.filter(id__in=tecnico_ids)

                for tecnico in tecnicos:
                    lookup = dict(
                        tecnico=tecnico,
                        ciudad=row.get('ciudad') or "",
                        proyecto=row.get('proyecto') or "",
                        oficina=row.get('oficina') or "",
                        cliente=row.get('cliente') or "",
                        codigo_trabajo=row.get('codigo_trabajo') or "",
                    )

                    defaults = dict(
                        tipo_trabajo=row.get('tipo_trabajo') or "",
                        descripcion=row.get('descripcion') or "",
                        unidad_medida=row.get('uom') or "",
                        precio_tecnico=row.get('precio_tecnico') or 0,
                        precio_empresa=row.get('precio_empresa') or 0,
                    )

                    if replace:
                        obj, created = PrecioActividadTecnico.objects.update_or_create(
                            **lookup, defaults=defaults
                        )
                        if created:
                            created_total += 1
                        else:
                            updated_total += 1
                    else:
                        obj, created = PrecioActividadTecnico.objects.get_or_create(
                            **lookup, defaults=defaults
                        )
                        if created:
                            created_total += 1
                        else:
                            skipped_total += 1

        msg = f"Import completed. Created: {created_total}, updated: {updated_total}"
        if skipped_total:
            msg += f", skipped (already existing): {skipped_total}"
        messages.success(request, msg)

        # Clear session
        request.session.pop('preview_data', None)
        return redirect('operaciones:listar_precios_tecnico')

    except Exception as e:
        messages.error(
            request, f"An error occurred during the import: {str(e)}")
        return redirect('operaciones:importar_precios')
# ---------- CRUD EDIT/DELETE ----------


@login_required
@rol_requerido('admin', 'pm')
def editar_precio(request, pk):
    precio = get_object_or_404(PrecioActividadTecnico, pk=pk)
    if request.method == 'POST':
        form = PrecioActividadTecnicoForm(request.POST, instance=precio)
        if form.is_valid():
            form.save()
            messages.success(request, "Price updated successfully.")
            return redirect('operaciones:listar_precios_tecnico')
    else:
        form = PrecioActividadTecnicoForm(instance=precio)
    return render(request, 'operaciones/editar_precio.html', {'form': form, 'precio': precio})


@login_required
@rol_requerido('admin', 'pm')
def eliminar_precio(request, pk):
    precio = get_object_or_404(PrecioActividadTecnico, pk=pk)
    precio.delete()
    messages.success(request, "Price deleted successfully.")
    return redirect('operaciones:listar_precios_tecnico')


# --- BILLING DE AQUI PARA ABAJO ---
#
# Ajusta si tu modelo de precios está en otra app


Usuario = get_user_model()


def money(x):  # redondeo
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def repartir_100(n):
    if n <= 0:
        return []
    base = (Decimal("100.00")/Decimal(n)).quantize(Decimal("0.01"))
    partes = [base]*n
    diff = Decimal("100.00") - sum(partes)
    if diff and partes:
        partes[-1] = (partes[-1]+diff).quantize(Decimal("0.01"))
    return partes

# ===== Listado =====


@login_required
def listar_billing(request):
    qs = (
        SesionBilling.objects
        .order_by("-creado_en")
        .prefetch_related(
            # Items + desglose
            Prefetch(
                "items",
                queryset=ItemBilling.objects.prefetch_related(
                    Prefetch(
                        "desglose_tecnico",
                        queryset=ItemBillingTecnico.objects.select_related(
                            "tecnico")
                    )
                ),
            ),
            # Asignaciones (técnico + evidencias para thumbnails)
            Prefetch(
                "tecnicos_sesion",
                queryset=SesionBillingTecnico.objects
                .select_related("tecnico")
                .prefetch_related(
                    Prefetch(
                        "evidencias",
                        queryset=EvidenciaFotoBilling.objects
                        .only("id", "imagen", "tecnico_sesion_id", "requisito_id")
                        .order_by("-id")
                    )
                )
            ),
        )
    )

    cantidad = request.GET.get("cantidad", "10")
    if cantidad == "todos":
        pagina = Paginator(qs, qs.count() or 1).get_page(1)
    else:
        try:
            per_page = int(cantidad)
        except (TypeError, ValueError):
            per_page = 10
        pagina = Paginator(qs, per_page).get_page(request.GET.get("page"))

    return render(
        request,
        "operaciones/billing_listar.html",
        {"pagina": pagina, "cantidad": cantidad},
    )

# ===== Crear / Editar =====


@login_required
def crear_billing(request):
    # POST -> guardar y redirigir (PRG)
    if request.method == "POST":
        # <-- _guardar_billing hace redirect al listar
        return _guardar_billing(request)

    # Combos
    clientes = (
        PrecioActividadTecnico.objects
        .values_list("cliente", flat=True)
        .distinct()
        .order_by("cliente")
    )

    # Técnicos con al menos una tarifa cargada
    tecnicos = (
        Usuario.objects
        .filter(precioactividadtecnico__isnull=False, is_active=True)
        .distinct()
        .order_by("first_name", "last_name", "username")
    )

    return render(request, "operaciones/billing_editar.html", {
        "sesion": None,
        "clientes": list(clientes),
        "tecnicos": tecnicos,
        "items": [],
        "ids_tecnicos": [],
    })


@login_required
def editar_billing(request, sesion_id: int):
    sesion = get_object_or_404(SesionBilling, pk=sesion_id)

    # POST -> guardar y redirigir (PRG)
    if request.method == "POST":
        # <-- redirect al listar
        return _guardar_billing(request, sesion=sesion)

    # Combos
    clientes = (
        PrecioActividadTecnico.objects
        .values_list("cliente", flat=True)
        .distinct()
        .order_by("cliente")
    )

    tecnicos = (
        Usuario.objects
        .filter(precioactividadtecnico__isnull=False, is_active=True)
        .distinct()
        .order_by("first_name", "last_name", "username")
    )

    items = (
        sesion.items
        .prefetch_related("desglose_tecnico__tecnico")
        .order_by("id")
    )
    ids_tecnicos = list(
        sesion.tecnicos_sesion.values_list("tecnico_id", flat=True)
    )

    return render(request, "operaciones/billing_editar.html", {
        "sesion": sesion,
        "clientes": list(clientes),
        "tecnicos": tecnicos,
        "items": items,
        "ids_tecnicos": ids_tecnicos,
    })


@login_required
@transaction.atomic
def eliminar_billing(request, sesion_id: int):
    get_object_or_404(SesionBilling, pk=sesion_id).delete()
    messages.success(request, "Billing deleted.")
    return redirect("operaciones:listar_billing")


@login_required
@transaction.atomic
def reasignar_tecnicos(request, sesion_id: int):
    sesion = get_object_or_404(SesionBilling, pk=sesion_id)
    if request.method != "POST":
        return HttpResponseBadRequest("POST requerido")

    ids = list(map(int, request.POST.getlist("tech_ids[]")))
    if not ids:
        return HttpResponseBadRequest("Seleccione al menos un técnico.")

    sesion.tecnicos_sesion.all().delete()
    partes = repartir_100(len(ids))

    for tid, pct in zip(ids, partes):
        SesionBillingTecnico.objects.create(
            sesion=sesion, tecnico_id=tid, porcentaje=pct)

    _recalcular_items_sesion(sesion)
    messages.success(
        request, "Technicians reassigned and totals recalculated.")
    return redirect("operaciones:editar_billing", sesion_id=sesion.id)

# ===== Persistencia =====


@transaction.atomic
def _guardar_billing(request, sesion: SesionBilling | None = None):
    proyecto_id = request.POST.get("project_id", "").strip()
    cliente = request.POST.get("client", "").strip()
    ciudad = request.POST.get("city", "").strip()
    proyecto = request.POST.get("project", "").strip()
    oficina = request.POST.get("office", "").strip()
    ids = list(map(int, request.POST.getlist("tech_ids[]")))

    if not (proyecto_id and cliente and ciudad and proyecto and oficina):
        messages.error(request, "Complete all header fields.")
        return redirect(request.path)
    if not ids:
        messages.error(request, "Select at least one technician.")
        return redirect(request.path)

    import json
    filas = []
    for raw in request.POST.getlist("items[]"):
        try:
            o = json.loads(raw)
        except Exception:
            return HttpResponseBadRequest("Items inválidos.")
        cod = (o.get("code") or "").strip()
        cant = o.get("amount")
        if not cod or cant in ("", None):
            return HttpResponseBadRequest("Cada fila requiere Job Code y Amount.")
        filas.append({"codigo": cod, "cantidad": Decimal(str(cant))})

    if sesion is None:
        sesion = SesionBilling.objects.create(
            proyecto_id=proyecto_id, cliente=cliente, ciudad=ciudad, proyecto=proyecto, oficina=oficina
        )
    else:
        sesion.proyecto_id = proyecto_id
        sesion.cliente = cliente
        sesion.ciudad = ciudad
        sesion.proyecto = proyecto
        sesion.oficina = oficina
        sesion.save()

    # técnicos con reparto 100/n
    sesion.tecnicos_sesion.all().delete()
    partes = repartir_100(len(ids))
    for tid, pct in zip(ids, partes):
        SesionBillingTecnico.objects.create(
            sesion=sesion, tecnico_id=tid, porcentaje=pct)

    # rehacer items
    sesion.items.all().delete()
    total_emp = Decimal("0.00")
    total_tec = Decimal("0.00")

    for fila in filas:
        meta = _meta_codigo(cliente, ciudad, proyecto, oficina, fila["codigo"])
        if not meta:
            return HttpResponseBadRequest(f"Código '{fila['codigo']}' no existe con los filtros.")
        precio_emp = _precio_empresa(
            cliente, ciudad, proyecto, oficina, fila["codigo"])
        sub_emp = money(precio_emp * fila["cantidad"])

        item = ItemBilling.objects.create(
            sesion=sesion,
            codigo_trabajo=fila["codigo"],
            tipo_trabajo=meta["tipo_trabajo"],
            descripcion=meta["descripcion"],
            unidad_medida=meta["unidad_medida"],
            cantidad=money(fila["cantidad"]),
            precio_empresa=precio_emp,
            subtotal_empresa=sub_emp,
            subtotal_tecnico=Decimal("0.00")
        )

        sub_tecs = Decimal("0.00")
        for tid, pct in zip(ids, partes):
            base = _tarifa_tecnico(tid, cliente, ciudad,
                                   proyecto, oficina, fila["codigo"])
            efectiva = money(base * (pct/Decimal("100")))
            subtotal = money(efectiva * item.cantidad)
            ItemBillingTecnico.objects.create(
                item=item, tecnico_id=tid, tarifa_base=base,
                porcentaje=pct, tarifa_efectiva=efectiva, subtotal=subtotal
            )
            sub_tecs += subtotal

        item.subtotal_tecnico = sub_tecs
        item.save(update_fields=["subtotal_tecnico"])

        total_emp += sub_emp
        total_tec += sub_tecs

    sesion.subtotal_empresa = money(total_emp)
    sesion.subtotal_tecnico = money(total_tec)
    sesion.save(update_fields=["subtotal_empresa", "subtotal_tecnico"])

    messages.success(request, "Billing saved successfully.")
    return redirect("operaciones:listar_billing")


def _recalcular_items_sesion(sesion: SesionBilling):
    ids = list(sesion.tecnicos_sesion.values_list("tecnico_id", flat=True))
    partes = list(sesion.tecnicos_sesion.values_list("porcentaje", flat=True))
    total_tec = Decimal("0.00")
    for it in sesion.items.all():
        it.desglose_tecnico.all().delete()
        sub = Decimal("0.00")
        for tid, pct in zip(ids, partes):
            base = _tarifa_tecnico(tid, sesion.cliente, sesion.ciudad,
                                   sesion.proyecto, sesion.oficina, it.codigo_trabajo)
            efectiva = money(base * (pct/Decimal("100")))
            subtotal = money(efectiva * it.cantidad)
            ItemBillingTecnico.objects.create(
                item=it, tecnico_id=tid, tarifa_base=base,
                porcentaje=pct, tarifa_efectiva=efectiva, subtotal=subtotal
            )
            sub += subtotal
        it.subtotal_tecnico = sub
        it.save(update_fields=["subtotal_tecnico"])
        total_tec += sub
    sesion.subtotal_tecnico = money(total_tec)
    sesion.save(update_fields=["subtotal_tecnico"])

# ===== Búsquedas / AJAX =====


def _precio_empresa(cliente, ciudad, proyecto, oficina, codigo):
    q = PrecioActividadTecnico.objects.filter(
        cliente__iexact=cliente, ciudad__iexact=ciudad,
        proyecto__iexact=proyecto, oficina__iexact=oficina or "-",
        codigo_trabajo__iexact=codigo
    ).first()
    return money(q.precio_empresa if q else 0)


def _tarifa_tecnico(tecnico_id, cliente, ciudad, proyecto, oficina, codigo):
    q = PrecioActividadTecnico.objects.filter(
        tecnico_id=tecnico_id, cliente__iexact=cliente, ciudad__iexact=ciudad,
        proyecto__iexact=proyecto, oficina__iexact=oficina or "-",
        codigo_trabajo__iexact=codigo
    ).first()
    return money(q.precio_tecnico if q else 0)


def _meta_codigo(cliente, ciudad, proyecto, oficina, codigo):
    p = PrecioActividadTecnico.objects.filter(
        cliente__iexact=cliente, ciudad__iexact=ciudad,
        proyecto__iexact=proyecto, oficina__iexact=oficina or "-",
        codigo_trabajo__iexact=codigo
    ).first()
    if not p:
        return None
    return {"tipo_trabajo": p.tipo_trabajo, "descripcion": p.descripcion, "unidad_medida": p.unidad_medida}


@login_required
def ajax_clientes(request):
    data = list(PrecioActividadTecnico.objects.values_list(
        "cliente", flat=True).distinct().order_by("cliente"))
    return JsonResponse({"results": data})


@login_required
def ajax_ciudades(request):
    cliente = request.GET.get("client", "")
    data = list(PrecioActividadTecnico.objects.filter(cliente__iexact=cliente)
                .values_list("ciudad", flat=True).distinct().order_by("ciudad")) if cliente else []
    return JsonResponse({"results": data})


@login_required
def ajax_proyectos(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    ok = cliente and ciudad
    data = list(PrecioActividadTecnico.objects.filter(cliente__iexact=cliente, ciudad__iexact=ciudad)
                .values_list("proyecto", flat=True).distinct().order_by("proyecto")) if ok else []
    return JsonResponse({"results": data})


@login_required
def ajax_oficinas(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto = request.GET.get("project", "")
    ok = cliente and ciudad and proyecto
    data = list(PrecioActividadTecnico.objects.filter(cliente__iexact=cliente, ciudad__iexact=ciudad, proyecto__iexact=proyecto)
                .values_list("oficina", flat=True).distinct().order_by("oficina")) if ok else []
    return JsonResponse({"results": data})


@login_required
def ajax_buscar_codigos(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto = request.GET.get("project", "")
    oficina = request.GET.get("office", "")
    q = (request.GET.get("q") or "").strip()
    if not (cliente and ciudad and proyecto and oficina):
        return JsonResponse({"error": "missing_filters"}, status=400)
    qs = PrecioActividadTecnico.objects.filter(
        cliente__iexact=cliente, ciudad__iexact=ciudad, proyecto__iexact=proyecto, oficina__iexact=oficina or "-")
    if q:
        qs = qs.filter(codigo_trabajo__istartswith=q)
    data = list(qs.values("codigo_trabajo", "tipo_trabajo", "descripcion",
                "unidad_medida").distinct().order_by("codigo_trabajo")[:20])
    return JsonResponse({"results": data})


@login_required
def ajax_detalle_codigo(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto = request.GET.get("project", "")
    oficina = request.GET.get("office", "")
    codigo = (request.GET.get("code") or "").strip()
    if not (cliente and ciudad and proyecto and oficina and codigo):
        return JsonResponse({"error": "missing_filters"}, status=400)
    meta = _meta_codigo(cliente, ciudad, proyecto, oficina, codigo)
    if not meta:
        return JsonResponse({"error": "not_found"}, status=404)
    precio_emp = _precio_empresa(cliente, ciudad, proyecto, oficina, codigo)
    tech_ids = list(map(int, request.GET.getlist("tech_ids[]")))
    partes = repartir_100(len(tech_ids)) if tech_ids else []
    desglose = []
    for tid, pct in zip(tech_ids, partes):
        base = _tarifa_tecnico(tid, cliente, ciudad, proyecto, oficina, codigo)
        desglose.append({
            "tecnico_id": tid,
            "tarifa_base": f"{base:.2f}",
            "porcentaje": f"{pct:.2f}",
            "tarifa_efectiva": f"{(base * (Decimal(pct)/100)):.2f}",
        })
    return JsonResponse({
        "tipo_trabajo": meta["tipo_trabajo"],
        "descripcion": meta["descripcion"],
        "unidad_medida": meta["unidad_medida"],
        "precio_empresa": f"{precio_emp:.2f}",
        "desglose_tecnico": desglose
    })
