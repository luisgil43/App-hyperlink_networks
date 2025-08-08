# operaciones/views.py
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
@rol_requerido('usuarios')
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
@rol_requerido('usuarios')
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Deposit approved successfully.")
    return redirect('operaciones:mis_rendiciones')


@login_required
@rol_requerido('usuarios')
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
@rol_requerido('usuarios', 'admin')
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
@rol_requerido('usuarios', 'admin')
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
@rol_requerido('usuarios')
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
