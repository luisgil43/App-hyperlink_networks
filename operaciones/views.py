# operaciones/views.py
from datetime import date as _date
import xml.etree.ElementTree as ET
import shutil
from django.core.files.storage import default_storage as storage
from django.http import FileResponse, JsonResponse, HttpResponseBadRequest
import zipfile
from .models import SesionBilling
from django.core.files.storage import default_storage
from copy import copy as _copy
from tempfile import NamedTemporaryFile
from django.http import FileResponse, HttpResponseBadRequest, JsonResponse
from django.utils.text import slugify
from django.http import HttpResponse, JsonResponse
import xlsxwriter
from io import BytesIO
from openpyxl.drawing.image import Image as XLImage
from openpyxl import load_workbook, Workbook
from openpyxl.drawing.image import Image as XLImage  # para copiar imágenes
from openpyxl import Workbook, load_workbook
from django.db.models import Case, When, IntegerField
from django.http import JsonResponse, FileResponse, HttpResponseNotAllowed
from django.db.models import Q, Prefetch
from urllib.parse import urlencode
from django.db.models import Q, F, Sum
from django.db.models import Sum, Q, F
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.db.models import Count
from .models import SesionBilling  # <-- AJUSTA este import al modelo correcto
from django.http import HttpResponseRedirect
from django.utils.http import urlencode
from .services.weekly import (
    sync_weekly_totals_no_create,   # no crea; actualiza y borra huérfanos
    materialize_week_for_payments,  # crea/actualiza solo la semana indicada
)
from django.db.models import Exists, OuterRef, Sum
from .services.weekly import sync_weekly_totals_no_create  # versión que NO crea
from django.views.decorators.cache import never_cache

import json
from botocore.client import Config
from .forms import PaymentApproveForm, PaymentRejectForm, PaymentMarkPaidForm
from uuid import uuid4
import os
from .models import WeeklyPayment
from datetime import timedelta
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import SesionBilling  # ajusta a tu ruta real
from openpyxl import Workbook
import re
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


# --- Direct upload (receipts/rendiciones) ---
RECEIPT_ALLOWED_MIME = {"application/pdf",
                        "image/jpeg", "image/jpg", "image/png", "image/webp"}

RECEIPT_MAX_MB = int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25))
RECEIPTS_SAFE_PREFIX = getattr(
    settings, "DIRECT_UPLOADS_RECEIPTS_PREFIX", "operaciones/rendiciones/"
)


def _build_receipt_key(user_id: int, filename: str) -> str:
    base = RECEIPTS_SAFE_PREFIX.rstrip("/")  # ej: operaciones/rendiciones
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "pdf").lower()
    today = timezone.now()
    # carpeta por usuario y fecha para que quede ordenado
    return f"{base}/{user_id}/{today:%Y/%m/%d}/rcpt_{uuid4().hex}.{ext}"


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@require_POST
def presign_rendicion(request, pk: int):
    """
    Pre-firma para subir DIRECTO el comprobante de rendición a Wasabi via POST.
    Request JSON: { filename, contentType, sizeBytes }
    Devuelve: {"post": {...}, "key": "<s3_key>"}  (url path-style)
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    filename = (data.get("filename") or "").strip()
    ctype = (data.get("contentType") or "").strip()
    size_b = int(data.get("sizeBytes") or 0)

    if not filename or ctype not in RECEIPT_ALLOWED_MIME:
        return HttpResponseBadRequest("Invalid file type.")
    if size_b <= 0 or size_b > RECEIPT_MAX_MB * 1024 * 1024:
        return HttpResponseBadRequest("File too large.")

    key = _build_receipt_key(request.user.id, filename)

    s3 = _s3_client()
    fields = {
        "acl": "private",
        "success_action_status": "201",
        # TIP: si quieres forzar Content-Type, puedes incluirlo aquí y en Conditions.
        # "Content-Type": ctype,
    }
    conditions = [
        {"acl": "private"},
        {"success_action_status": "201"},
        ["starts-with", "$key", key.rsplit("/", 1)[0] + "/"],
        ["content-length-range", 1, RECEIPT_MAX_MB * 1024 * 1024],
        # Si decides forzar Content-Type:
        # {"Content-Type": ctype},
    ]

    post = s3.generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=600,
    )

    # Forzar URL path-style (coincide con lo que ya usas)
    endpoint = settings.AWS_S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    post["url"] = f"{endpoint}/{bucket}"

    return JsonResponse({"post": post, "key": key})


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
@rol_requerido('usuario')
def mis_rendiciones(request):
    user = request.user

    # --- Query base + paginación (se calcula SIEMPRE) ---
    cantidad_str = request.GET.get('cantidad', '10')
    try:
        per_page = 1000000 if cantidad_str == 'todos' else int(cantidad_str)
    except (TypeError, ValueError):
        per_page = 10
        cantidad_str = '10'

    movimientos_qs = CartolaMovimiento.objects.filter(
        usuario=user
    ).order_by('-fecha')

    paginator = Paginator(movimientos_qs, per_page)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # --- Saldos (usa el mismo QS para consistencia) ---
    saldo_disponible = (
        (movimientos_qs.filter(tipo__categoria="abono", status="aprobado_abono_usuario")
         .aggregate(total=Sum('abonos'))['total'] or 0)
        -
        (movimientos_qs.exclude(tipo__categoria="abono")
         .filter(status="aprobado_finanzas")
         .aggregate(total=Sum('cargos'))['total'] or 0)
    )
    saldo_pendiente = movimientos_qs.filter(tipo__categoria="abono") \
        .exclude(status="aprobado_abono_usuario") \
        .aggregate(total=Sum('abonos'))['total'] or 0
    saldo_rendido = movimientos_qs.exclude(tipo__categoria="abono") \
        .exclude(status="aprobado_finanzas") \
        .aggregate(total=Sum('cargos'))['total'] or 0

    # --- POST: crea la rendición (direct upload o multipart clásico) ---
    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES)
        if form.is_valid():
            mov = form.save(commit=False)
            mov.usuario = user
            mov.fecha = now()
            mov.status = 'pendiente_abono_usuario' if (
                mov.tipo and mov.tipo.categoria == "abono") else 'pendiente_supervisor'

            # Soporte de subida directa (si tu JS envía wasabi_key)
            wasabi_key = (request.POST.get('wasabi_key') or '').strip()
            if wasabi_key:
                mov.comprobante.name = wasabi_key
            else:
                mov.comprobante = form.cleaned_data['comprobante']

            mov.save()

            # Verificación opcional en Wasabi (igual que tenías)
            ruta_archivo = mov.comprobante.name
            import time
            for _ in range(3):
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

    return render(request, 'operaciones/mis_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad_str,
        'saldo_disponible': saldo_disponible,
        'saldo_pendiente': saldo_pendiente,
        'saldo_rendido': saldo_rendido,
        'form': form,

        # Si usas el JS de subida directa para rendiciones:
        'direct_uploads_receipts_enabled': True,
        'receipt_max_mb': int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25)),
    })


@login_required
@rol_requerido('usuario')
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Deposit approved successfully.")
    return redirect('operaciones:mis_rendiciones')


@login_required
@rol_requerido('usuario')
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
@rol_requerido('usuario')
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
        CartolaMovimiento, pk=pk, usuario=request.user
    )

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(
            request, "You cannot delete an already approved expense report."
        )
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        rendicion.delete()
        messages.success(request, "Expense report deleted successfully.")
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
@rol_requerido('usuario')
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
@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'facturacion')
def listar_precios_tecnico(request):
    # Cantidad por página
    cantidad_str = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad_str == 'todos' else int(cantidad_str)

    # Filtros (GET)
    f_tecnico = (request.GET.get('f_tecnico') or '').strip()
    f_ciudad = (request.GET.get('f_ciudad') or '').strip()
    f_proy = (request.GET.get('f_proyecto') or '').strip()
    f_codigo = (request.GET.get('f_codigo') or '').strip()

    qs = PrecioActividadTecnico.objects.select_related(
        'tecnico').order_by('-fecha_creacion')

    if f_tecnico:
        qs = qs.filter(
            Q(tecnico__first_name__icontains=f_tecnico) |
            Q(tecnico__last_name__icontains=f_tecnico) |
            Q(tecnico__username__icontains=f_tecnico)
        )
    if f_ciudad:
        qs = qs.filter(ciudad__icontains=f_ciudad)
    if f_proy:
        qs = qs.filter(proyecto__icontains=f_proy)
    if f_codigo:
        qs = qs.filter(codigo_trabajo__icontains=f_codigo)

    paginator = Paginator(qs, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    ctx = {
        'pagina': pagina,
        'cantidad': cantidad_str,
        'f_tecnico': f_tecnico,
        'f_ciudad': f_ciudad,
        'f_proyecto': f_proy,
        'f_codigo': f_codigo,
    }
    return render(request, 'operaciones/listar_precios_tecnico.html', ctx)


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


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm')
def bulk_delete_precios(request):
    if request.method != "POST":
        messages.error(request, "Invalid request.")
        return redirect('operaciones:listar_precios_tecnico')

    ids = request.POST.getlist("ids")
    return_page = request.POST.get("return_page") or ""
    return_cantidad = request.POST.get("return_cantidad") or ""

    if not ids:
        messages.info(request, "No prices selected.")
        return redirect('operaciones:listar_precios_tecnico')

    qs = PrecioActividadTecnico.objects.filter(id__in=ids)
    deleted_count = qs.count()
    qs.delete()

    messages.success(
        request, f"{deleted_count} price(s) deleted successfully.")

    # Reconstruir URL de retorno preservando filtros y paginación
    base = reverse('operaciones:listar_precios_tecnico')
    params = []
    if return_cantidad:
        params.append(f"cantidad={return_cantidad}")
    if return_page and return_cantidad != "todos":
        params.append(f"page={return_page}")

    for key in ("f_tecnico", "f_ciudad", "f_proyecto", "f_codigo"):
        val = (request.POST.get(key) or '').strip()
        if val:
            params.append(f"{key}={val}")

    url = f"{base}?{'&'.join(params)}" if params else base
    return redirect(url)


# ===== Listado =====


def repartir_100(n):
    if n <= 0:
        return []
    base = (Decimal("100.00")/Decimal(n)).quantize(Decimal("0.01"))
    partes = [base]*n
    diff = Decimal("100.00") - sum(partes)
    if diff and partes:
        partes[-1] = (partes[-1]+diff).quantize(Decimal("0.01"))
    return partes


# ===== Descuento directo =====

def recomputar_estado_desde_asignaciones(self, save: bool = True) -> str:
    # NUEVO: si es descuento directo, no tocar el estado
    if self.is_direct_discount:
        return self.estado

    estados = list(self.tecnicos_sesion.values_list("estado", flat=True))
    nuevo = "asignado"
    if estados:
        if any(e == "en_revision_supervisor" for e in estados):
            nuevo = "en_revision_supervisor"
        elif any(e == "en_proceso" for e in estados):
            nuevo = "en_proceso"
        elif all(e == "aprobado_pm" for e in estados):
            nuevo = "aprobado_pm"
        elif any(e == "rechazado_pm" for e in estados):
            nuevo = "rechazado_pm"
        elif all(e == "aprobado_supervisor" for e in estados):
            nuevo = "aprobado_supervisor"
        elif any(e == "rechazado_supervisor" for e in estados):
            nuevo = "rechazado_supervisor"

    if self.estado != nuevo:
        self.estado = nuevo
        if save:
            self.save(update_fields=["estado"])
    return self.estado


@login_required
@require_POST
def exportar_billing_excel(request):
    """
    Exporta a XLSX con columnas:
    Project ID, Date, Week, Project Address, City, Work Type, Job Code,
    Description, Qty, Subtotal Company.
    - Encabezado con color y filtros
    - Bordes finos en toda la tabla
    - Bandas alternadas (gris/ blanco) en filas de datos
    - Fila Total: 'Total' en Qty (col I) y monto en Subtotal Company (col J)
    - Líneas de cuadricula DESACTIVADAS
    """

    # ========= Estilos locales =========
    HDR_FILL = PatternFill("solid", fgColor="374151")   # gris oscuro
    HDR_FONT = Font(bold=True, color="FFFFFF")
    HDR_ALIGN = Alignment(horizontal="center", vertical="center")
    CELL_ALIGN_LEFT = Alignment(
        horizontal="left", vertical="center", wrap_text=False)
    CELL_ALIGN_LEFT_WRAP = Alignment(
        horizontal="left", vertical="center", wrap_text=True)
    CELL_ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
    THIN = Side(style="thin", color="D1D5DB")
    BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    ZEBRA_GRAY = "E5E7EB"   # gris clarito
    ZEBRA_WHITE = "FFFFFF"  # blanco

    # ========= Helpers locales =========
    def _export_headers():
        return [
            "Project ID", "Date", "Week", "Project Address", "City",
            "Work Type", "Job Code", "Description", "Qty", "Subtotal Company",
        ]

    def _get_address_from_session_only(s):
        return (
            getattr(s, "direccion_proyecto", None)
            or getattr(s, "direccion", None)
            or getattr(s, "project_address", None)
            or getattr(s, "direccion_obra", None)
            or ""
        )

    def _get_address_from_item_or_session(it, s):
        return (
            getattr(it, "direccion", None)
            or getattr(it, "project_address", None)
            or getattr(it, "direccion_obra", None)
            or getattr(s, "direccion_proyecto", None)
            or getattr(s, "direccion", None)
            or getattr(s, "project_address", None)
            or getattr(s, "direccion_obra", None)
            or ""
        )

    def _xlsx_response(workbook):
        from io import BytesIO
        bio = BytesIO()
        workbook.save(bio)
        bio.seek(0)
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        resp = HttpResponse(
            bio.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="billing_export_{ts}.xlsx"'
        return resp

    def _format_money(ws, cols):
        money_fmt = '$#,##0.00'
        for col in cols:
            for col_cells in ws.iter_cols(min_col=col, max_col=col, min_row=2, values_only=False):
                for c in col_cells:
                    c.number_format = money_fmt

    def _format_number(ws, cols):
        num_fmt = '#,##0.00'
        for col in cols:
            for col_cells in ws.iter_cols(min_col=col, max_col=col, min_row=2, values_only=False):
                for c in col_cells:
                    c.number_format = num_fmt

    def _set_widths(ws, mapping):
        for idx, width in mapping.items():
            ws.column_dimensions[get_column_letter(idx)].width = width

    def _apply_table_borders(ws):
        """Bordes + alineaciones en todo el rango con datos (incluye encabezado)."""
        max_r, max_c = ws.max_row, ws.max_column
        for r in range(1, max_r + 1):
            for c in range(1, max_c + 1):
                cell = ws.cell(row=r, column=c)
                cell.border = BORDER_ALL
                if c in (9, 10):                    # Qty / Subtotal
                    cell.alignment = CELL_ALIGN_RIGHT
                elif c in (4, 8):                   # Address / Description -> wrap
                    cell.alignment = CELL_ALIGN_LEFT_WRAP
                else:
                    cell.alignment = CELL_ALIGN_LEFT

    def _apply_zebra(ws, start_row: int, end_row: int, gray_hex: str, white_hex: str):
        """Relleno alternado (gris/blanco) desde start_row hasta end_row."""
        if end_row < start_row:
            return
        fill_gray = PatternFill("solid", fgColor=gray_hex)
        fill_white = PatternFill("solid", fgColor=white_hex)
        max_c = ws.max_column
        for r in range(start_row, end_row + 1):
            fill = fill_gray if (r - start_row) % 2 == 0 else fill_white
            for c in range(1, max_c + 1):
                ws.cell(row=r, column=c).fill = fill

    def _style_after_fill(ws):
        """Header gris + filtros + congelar panes."""
        for col, _ in enumerate(_export_headers(), start=1):
            cell = ws.cell(row=1, column=col)
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.alignment = HDR_ALIGN
            cell.border = BORDER_ALL
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
        ws.freeze_panes = "A2"

    # ========= 1) Parseo de IDs =========
    raw = (request.POST.get("ids") or "").strip()
    ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]

    # ========= 2) Workbook / hoja =========
    wb = Workbook()
    ws = wb.active
    ws.title = "Billing"

    # 👉 Desactivar líneas de cuadricula (en pantalla y también en impresión)
    ws.sheet_view.showGridLines = False
    ws.print_options.gridLines = False

    # Encabezados
    headers = _export_headers()
    ws.append(headers)

    if not ids:
        _style_after_fill(ws)
        return _xlsx_response(wb)

    # ========= 3) Prefetch =========
    sesiones = (
        SesionBilling.objects.filter(id__in=ids)
        .prefetch_related(
            Prefetch("items", queryset=ItemBilling.objects.order_by("id")),
            Prefetch("tecnicos_sesion",
                     queryset=SesionBillingTecnico.objects.select_related("tecnico")),
        )
        .order_by("id")
    )

    # ========= 4) Filas =========
    total_subtotal_company = 0.0
    tz = timezone.get_current_timezone()

    for s in sesiones:
        dt = s.creado_en
        date_str = timezone.localtime(dt, tz).strftime(
            "%d-%b").lower() if dt else ""
        week_str = getattr(s, "semana_pago_proyectada",
                           "") or getattr(s, "week", "")
        city = getattr(s, "ciudad", "") or getattr(s, "city", "")
        project_id = getattr(s, "proyecto_id", "") or getattr(
            s, "project_id", "")

        if not s.items.all():
            addr_session = _get_address_from_session_only(s)
            ws.append([project_id, date_str, week_str,
                      addr_session, city, "", "", "", 0.0, 0.0])
            continue

        for it in s.items.all():
            project_address = _get_address_from_item_or_session(it, s)
            qty = float(getattr(it, "cantidad", 0) or 0)
            sub_company = float(getattr(it, "subtotal_empresa", 0) or 0)
            ws.append([
                project_id,                  # A
                date_str,                    # B
                week_str,                    # C
                project_address,             # D
                city,                        # E
                getattr(it, "tipo_trabajo", "") or getattr(
                    it, "work_type", ""),   # F
                getattr(it, "codigo_trabajo", "") or getattr(
                    it, "job_code", ""),  # G
                getattr(it, "descripcion", "") or getattr(
                    it, "description", ""),  # H
                qty,                         # I
                sub_company                  # J
            ])
            total_subtotal_company += sub_company

    # ========= 5) Formatos / estilos =========
    _format_money(ws, cols=[10])   # J: Subtotal Company
    _format_number(ws, cols=[9])   # I: Qty

    _set_widths(ws, {
        1: 12, 2: 10, 3: 12, 4: 36, 5: 14, 6: 14, 7: 12, 8: 34, 9: 6, 10: 16
    })

    _apply_table_borders(ws)

    # Zebra desde la fila 2 (datos) hasta la última fila de datos
    data_end = ws.max_row
    _apply_zebra(ws, start_row=2, end_row=data_end,
                 gray_hex=ZEBRA_GRAY, white_hex=ZEBRA_WHITE)

    _style_after_fill(ws)

    # ========= 6) Fila Total =========
    ws.append([""] * 10)  # separador opcional
    total_row = ws.max_row
    ws.cell(row=total_row, column=9, value="Total").font = Font(
        bold=True)                   # I
    ws.cell(row=total_row, column=10,
            value=total_subtotal_company).font = Font(bold=True)   # J
    ws.cell(row=total_row, column=10).number_format = '$#,##0.00'
    for col in range(1, 11):
        c = ws.cell(row=total_row, column=col)
        c.border = BORDER_ALL
        c.alignment = CELL_ALIGN_RIGHT if col in (9, 10) else (
            CELL_ALIGN_LEFT_WRAP if col in (4, 8) else CELL_ALIGN_LEFT)

    return _xlsx_response(wb)


# views.py


def _norm(txt: str) -> str:
    """minúsculas + sin espacios/guiones/underscores (para comparar estados)."""
    if not txt:
        return ""
    t = txt.strip().lower()
    return "".join(ch for ch in t if ch.isalnum())


def _norm(s: str) -> str:
    """normaliza: minúsculas + solo letras/dígitos, sin espacios."""
    s = (s or "").lower()
    return "".join(c for c in s if c.isalnum())


@require_POST
@login_required
@rol_requerido("admin", "pm")
@transaction.atomic
def billing_send_finance(request):
    """
    Enviar a Finanzas SOLO si:
      - is_direct_discount == True             -> finance_status = 'review_discount'
      - estado == 'aprobado_supervisor' (normalizado) -> finance_status = 'sent'

    Nunca 400 por mezcla: procesa lo permitido y devuelve 'skipped' con motivo.
    Responde SIEMPRE JSON (nada de HTML).

    FIXES:
      - Sellar finance_sent_at también cuando new_status='review_discount'.
      - Permitir re-sellar si ya está en 'review_discount' PERO sin finance_sent_at (intentos previos).
    """
    # ---- parseo ids + nota ----
    ids, note = [], ""
    ctype = (request.content_type or "").lower()

    if "application/json" in ctype:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "INVALID_JSON"}, status=400)
        ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
        note = (payload.get("note") or "").strip()
    else:
        raw = (request.POST.get("ids") or "").strip()
        ids = [int(x) for x in raw.split(",") if x.isdigit()]
        note = (request.POST.get("note") or "").strip()

    if not ids:
        return JsonResponse({"ok": False, "error": "NO_IDS"}, status=400)

    # ---- reglas permitidas ----
    allowed_supervisor_norms = {
        "aprobadosupervisor",
        "approvedsupervisor",
        "approvedbysupervisor",
        "aprobadoporsupervisor",
    }

    # Estados de finanzas que BLOQUEAN reenvío.
    # OJO: dejaremos pasar 'review_discount' SI NO TIENE finance_sent_at (para reestampar).
    blocked_fin = {
        "sent", "senttofinance",
        "reviewdiscount", "discountapplied",
        "inreview", "pending", "readyforpayment",
        "paid", "rejected", "cancelled", "canceled",
        "enviado", "enrevision", "pendiente", "listoparapago",
        "pagado", "rechazado", "cancelado",
    }

    rows = list(SesionBilling.objects.filter(id__in=ids))
    now = timezone.now()

    updated = 0
    updated_rows = []
    skipped = []
    plan = []  # (id, new_finance_status)

    def _norm(s: str) -> str:
        return (s or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")

    # Primera pasada: decidir plan
    for s in rows:
        estado_norm = _norm(getattr(s, "estado", ""))
        fin_norm = _norm(getattr(s, "finance_status", ""))
        fin_sent = getattr(s, "finance_sent_at", None)

        # Si está bloqueado en finanzas...
        if fin_norm in blocked_fin:
            # ...EXCEPCIÓN: permitir reestampar si está en review_discount PERO sin finance_sent_at
            if fin_norm == "reviewdiscount" and not fin_sent:
                plan.append((s.id, "review_discount"))
                continue

            skipped.append({
                "id": s.id, "estado": s.estado,
                "is_direct_discount": bool(s.is_direct_discount),
                "finance_status": s.finance_status,
                "skip_reason": "FINANCE_STATUS_BLOCKED",
            })
            continue

        # Flujo normal
        if getattr(s, "is_direct_discount", False) is True:
            plan.append((s.id, "review_discount"))
        elif estado_norm in allowed_supervisor_norms:
            plan.append((s.id, "sent"))
        else:
            skipped.append({
                "id": s.id, "estado": s.estado,
                "is_direct_discount": bool(s.is_direct_discount),
                "finance_status": s.finance_status,
                "skip_reason": "NOT_ALLOWED_STATUS",
            })

    # aplicar updates con lock
    by_id_new = {i: st for (i, st) in plan}
    if by_id_new:
        for s in SesionBilling.objects.select_for_update().filter(id__in=by_id_new.keys()):
            new_status = by_id_new[s.id]
            s.finance_status = new_status

            touched_fields = ["finance_status"]

            # Siempre sellamos updated_at si existe
            if hasattr(s, "finance_updated_at"):
                s.finance_updated_at = now
                touched_fields.append("finance_updated_at")

            # ✅ SELLAR finance_sent_at para 'sent' y también para 'review_discount'
            if hasattr(s, "finance_sent_at") and new_status in ("sent", "review_discount"):
                s.finance_sent_at = now
                touched_fields.append("finance_sent_at")

            if note:
                prefix = f"{now:%Y-%m-%d %H:%M} Ops: "
                s.finance_note = ((s.finance_note + "\n")
                                  if s.finance_note else "") + prefix + note
                touched_fields.append("finance_note")

            s.save(update_fields=touched_fields)
            updated += 1
            updated_rows.append(
                {"id": s.id, "finance_status": s.finance_status})

    return JsonResponse({"ok": True, "count": updated, "updated": updated_rows, "skipped": skipped})


@login_required
@rol_requerido('admin', 'pm')
@require_POST
@transaction.atomic
def billing_mark_in_review(request, pk: int):
    s = get_object_or_404(SesionBilling, pk=pk)
    if s.finance_status != "rejected":
        messages.info(request, "Only applies when Finance has rejected it.")
        return redirect("operaciones:listar_billing")

    note = (request.POST.get("reason")
            or request.POST.get("note") or "").strip()
    now = timezone.now()

    # Lo dejamos como "in_review" (aparece en Finanzas con scope=open)
    s.finance_status = "in_review"
    s.finance_updated_at = now

    if note:
        prefix = f"{now:%Y-%m-%d %H:%M} Ops: "
        s.finance_note = (
            s.finance_note + "\n" if s.finance_note else "") + prefix + note

    s.save(update_fields=["finance_status",
           "finance_updated_at", "finance_note"])
    messages.success(request, "Marked as 'In review' for Finance.")
    return redirect("operaciones:listar_billing")


@login_required
@require_POST
def billing_reopen_asignado(request, pk):
    obj = get_object_or_404(SesionBilling, pk=pk)

    if obj.estado in ("aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"):
        with transaction.atomic():
            obj.estado = "asignado"
            obj.save(update_fields=["estado"])
            obj.tecnicos_sesion.all().update(
                estado="asignado",
                aceptado_en=None,
                finalizado_en=None,
                supervisor_revisado_en=None,
                supervisor_comentario="",
                pm_revisado_en=None,
                pm_comentario="",
                reintento_habilitado=True,
            )
        messages.success(
            request, f"Billing #{obj.pk} has been reopened to 'Assigned' and all assignments were reactivated.")
    else:
        messages.info(request, "This record is not in an approved state.")
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/operaciones/billing/listar/"))


@login_required
def listar_billing(request):
    """
    Visibilidad en Operaciones:
      - Descuento directo (is_direct_discount=True):
          mostrar SOLO si AÚN NO se ha enviado -> finance_sent_at IS NULL.
          (si ya fue enviado, ya no debe verse aquí)
      - Resto:
          ocultar si finance_status ∈ {'sent','pending','paid','in_review'}.
    """
    visible_filter = (
        # ⬇️ descuento directo todavía sin enviar
        (Q(is_direct_discount=True) & Q(
            finance_sent_at__isnull=True) & ~Q(finance_status='paid'))
        |
        # ⬇️ flujo normal: ocultar enviados / en proceso de cobro
        (Q(is_direct_discount=False) & ~Q(
            finance_status__in=['sent', 'pending', 'paid', 'in_review']))
    )

    qs = (
        SesionBilling.objects
        .filter(visible_filter)
        .order_by("-creado_en")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=ItemBilling.objects.prefetch_related(
                    Prefetch(
                        "desglose_tecnico",
                        queryset=ItemBillingTecnico.objects.select_related(
                            "tecnico"),
                    )
                ),
            ),
            Prefetch(
                "tecnicos_sesion",
                queryset=SesionBillingTecnico.objects
                .select_related("tecnico")
                .prefetch_related(
                    Prefetch(
                        "evidencias",
                        queryset=EvidenciaFotoBilling.objects.only(
                            "id", "imagen", "tecnico_sesion_id", "requisito_id"
                        ).order_by("-id")
                    )
                )
            ),
        )
    )

    # ---------- Filtros de servidor ----------
    f = {
        "date":   (request.GET.get("date") or "").strip(),
        "projid": (request.GET.get("projid") or "").strip(),
        "week":   (request.GET.get("week") or "").strip(),
        "tech":   (request.GET.get("tech") or "").strip(),
        "client": (request.GET.get("client") or "").strip(),
        "status": (request.GET.get("status") or "").strip(),
    }

    qs_filtered = qs

    # Date: YYYY-MM-DD
    if f["date"]:
        try:
            d = _date.fromisoformat(f["date"])
            qs_filtered = qs_filtered.filter(creado_en__date=d)
        except ValueError:
            pass

    # Project ID
    if f["projid"]:
        qs_filtered = qs_filtered.filter(proyecto_id__icontains=f["projid"])

    # Week: proyectada o real
    if f["week"]:
        qs_filtered = qs_filtered.filter(
            Q(semana_pago_proyectada__icontains=f["week"]) |
            Q(semana_pago_real__icontains=f["week"])
        )

    # Technicians: nombre, apellido o username
    if f["tech"]:
        qs_filtered = qs_filtered.filter(
            Q(tecnicos_sesion__tecnico__first_name__icontains=f["tech"]) |
            Q(tecnicos_sesion__tecnico__last_name__icontains=f["tech"]) |
            Q(tecnicos_sesion__tecnico__username__icontains=f["tech"])
        )

    # Client
    if f["client"]:
        qs_filtered = qs_filtered.filter(cliente__icontains=f["client"])

    # Status (palabras clave → estado/banderas)
    if f["status"]:
        s = f["status"].lower().strip()

        if any(k in s for k in ("direct", "descuento", "discount")):
            qs_filtered = qs_filtered.filter(is_direct_discount=True)
        else:
            mapping = [
                (("aprobado supervisor", "approved by supervisor"),
                 Q(estado="aprobado_supervisor")),
                (("rechazado supervisor", "rejected by supervisor"),
                 Q(estado="rechazado_supervisor")),
                (("en revision", "supervisor review", "in supervisor review"),
                 Q(estado="en_revision_supervisor")),
                (("finalizado", "finished"), Q(estado="finalizado")),
                (("en proceso", "in progress"), Q(estado="en_proceso")),
                (("asignado", "assigned"), Q(estado="asignado")),
                (("aprobado pm", "approved by pm"), Q(estado="aprobado_pm")),
                (("rechazado pm", "rejected by pm"), Q(estado="rechazado_pm")),
            ]
            applied = False
            for keys, cond in mapping:
                if any(k in s for k in keys):
                    qs_filtered = qs_filtered.filter(cond)
                    applied = True
                    break

            if not applied:
                if "aprobado" in s or "approved" in s:
                    qs_filtered = qs_filtered.filter(
                        estado__in=["aprobado_supervisor", "aprobado_pm"])
                elif "rechazado" in s or "rejected" in s:
                    qs_filtered = qs_filtered.filter(
                        estado__in=["rechazado_supervisor", "rechazado_pm"])

    # Evita duplicados por joins con tecnicos_sesion
    qs = qs_filtered.distinct()
    # ---------- /Filtros de servidor ----------

    # Paginación
    cantidad = request.GET.get("cantidad", "10")
    if cantidad == "todos":
        pagina = Paginator(qs, qs.count() or 1).get_page(1)
    else:
        try:
            per_page = int(cantidad)
        except (TypeError, ValueError):
            per_page = 10
        pagina = Paginator(qs, per_page).get_page(request.GET.get("page"))

    can_edit_real_week = (
        getattr(request.user, "es_pm", False)
        or getattr(request.user, "es_facturacion", False)
        or getattr(request.user, "es_admin_general", False)
        or request.user.is_superuser
    )
    can_edit_items = bool(
        getattr(request.user, "es_admin_general",
                False) or request.user.is_superuser
    )

    # Mantener QS en paginación
    from urllib.parse import urlencode
    keep_params = {**f}
    if cantidad and cantidad != "":
        keep_params["cantidad"] = cantidad
    qs_keep = urlencode({k: v for k, v in keep_params.items() if v})

    return render(
        request,
        "operaciones/billing_listar.html",
        {
            "pagina": pagina,
            "cantidad": cantidad,
            "can_edit_real_week": can_edit_real_week,
            "can_edit_items": can_edit_items,
            "f": f,
            "qs_keep": qs_keep,
        },
    )


@login_required
@require_POST
def billing_item_update_qty(request, item_id: int):
    # Solo admin
    is_admin = bool(getattr(request.user, "es_admin_general",
                    False) or request.user.is_superuser)
    if not is_admin:
        return HttpResponseForbidden("Solo admin puede editar cantidades en línea.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
        cantidad = payload.get("cantidad", None)
        if cantidad is None:
            return HttpResponseBadRequest("Falta 'cantidad'.")
        cantidad = Decimal(str(cantidad))
        if cantidad < 0:
            return HttpResponseBadRequest("Cantidad inválida.")
    except (json.JSONDecodeError, InvalidOperation):
        return HttpResponseBadRequest("Payload inválido.")

    try:
        item = ItemBilling.objects.select_related(
            "sesion").prefetch_related("desglose_tecnico").get(pk=item_id)
    except ItemBilling.DoesNotExist:
        return HttpResponseBadRequest("Item no existe.")

    sesion = item.sesion  # SesionBilling

    # Si NO quieres permitir edición cuando la sesión está "paid", descomenta:
    # if sesion.finance_status == "paid":
    #     return HttpResponseForbidden("No se puede editar un billing pagado.")

    with transaction.atomic():
        # Recalcular subtotales del item
        # subtotal_empresa = precio_empresa * cantidad
        subtotal_empresa = (item.precio_empresa or Decimal("0")) * cantidad

        # subtotal_tecnico: si hay desglose_tecnico -> sum(tarifa_efectiva * cantidad)
        # si tu modelo ya lo calcula con una propiedad/método, úsalo en su lugar.
        subtotal_tecnico = Decimal("0")
        for bd in item.desglose_tecnico.all():
            # tarifa_efectiva usualmente es tarifa_base * (porcentaje/100)
            tarifa_efectiva = getattr(bd, "tarifa_efectiva", None)
            if tarifa_efectiva is None:
                base = Decimal(bd.tarifa_base or 0)
                pct = Decimal(bd.porcentaje or 0) / Decimal("100")
                tarifa_efectiva = base * pct
            subtotal_tecnico += (tarifa_efectiva or Decimal("0")) * cantidad

        # ⚠️ Evitar save() si tienes señales que tocan 'estado':
        ItemBilling.objects.filter(pk=item.pk).update(
            cantidad=cantidad,
            subtotal_empresa=subtotal_empresa,
            subtotal_tecnico=subtotal_tecnico,
        )

        # Recalcular totales de la sesión (sin tocar estado)
        # Vuelve a leer items de la sesión con lock opcional
        items_qs = ItemBilling.objects.select_related(None).filter(sesion=sesion).only(
            "subtotal_tecnico", "subtotal_empresa"
        )
        total_tecnico = items_qs.aggregate(s=Sum("subtotal_tecnico"))[
            "s"] or Decimal("0")
        total_empresa = items_qs.aggregate(s=Sum("subtotal_empresa"))[
            "s"] or Decimal("0")

        # No modificar 'estado' NI 'finance_status'
        SesionBilling.objects.filter(pk=sesion.pk).update(
            subtotal_tecnico=total_tecnico,
            subtotal_empresa=total_empresa,
            # real_company_billing: no lo tocamos
        )

        # Preparar diferencia para la respuesta
        sesion_refrescada = SesionBilling.objects.only(
            "id", "subtotal_tecnico", "subtotal_empresa", "real_company_billing"
        ).get(pk=sesion.pk)
        diff_text = "—"
        if sesion_refrescada.real_company_billing is not None:
            diff = sesion_refrescada.real_company_billing - sesion_refrescada.subtotal_empresa
            if diff < 0:
                diff_text = f"<span class='font-semibold text-red-600'>- ${abs(diff):.2f}</span>"
            elif diff > 0:
                diff_text = f"<span class='font-semibold text-green-600'>+ ${diff:.2f}</span>"
            else:
                diff_text = "<span class='text-gray-700'>$0.00</span>"

    return JsonResponse({
        "ok": True,
        "item_id": item.pk,
        "cantidad": float(cantidad),
        "subtotal_tecnico": float(subtotal_tecnico),
        "subtotal_empresa": float(subtotal_empresa),
        "parent": {
            "id": sesion_refrescada.pk,
            "subtotal_tecnico": float(sesion_refrescada.subtotal_tecnico or 0),
            "subtotal_empresa": float(sesion_refrescada.subtotal_empresa or 0),
            "real_company_billing": (
                float(sesion_refrescada.real_company_billing)
                if sesion_refrescada.real_company_billing is not None
                else None
            ),
            "diferencia_text": diff_text,
        }
    })


# ===== Crear / Editar =====


@login_required
def crear_billing(request):
    # POST -> guardar y redirigir (PRG)
    if request.method == "POST":
        # <-- ahora maneja "descuento directo"
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


WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


@login_required
def editar_billing(request, sesion_id: int):
    sesion = get_object_or_404(SesionBilling, pk=sesion_id)

    # POST -> guardar y redirigir (PRG)
    if request.method == "POST":
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
            sesion=sesion, tecnico_id=tid, porcentaje=pct
        )

    _recalcular_items_sesion(sesion)
    messages.success(
        request, "Technicians reassigned and totals recalculated.")
    return redirect("operaciones:editar_billing", sesion_id=sesion.id)


# ===== Persistencia =====

WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")  # formato de <input type="week">


# operaciones/views.py


# --- Direct upload (receipts/rendiciones) ---
RECEIPT_ALLOWED_MIME = {"application/pdf",
                        "image/jpeg", "image/jpg", "image/png", "image/webp"}

RECEIPT_MAX_MB = int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25))
RECEIPTS_SAFE_PREFIX = getattr(
    settings, "DIRECT_UPLOADS_RECEIPTS_PREFIX", "operaciones/rendiciones/"
)


def _build_receipt_key(user_id: int, filename: str) -> str:
    base = RECEIPTS_SAFE_PREFIX.rstrip("/")  # ej: operaciones/rendiciones
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "pdf").lower()
    today = timezone.now()
    # carpeta por usuario y fecha para que quede ordenado
    return f"{base}/{user_id}/{today:%Y/%m/%d}/rcpt_{uuid4().hex}.{ext}"


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@require_POST
def presign_rendicion(request, pk: int):
    """
    Pre-firma para subir DIRECTO el comprobante de rendición a Wasabi via POST.
    Request JSON: { filename, contentType, sizeBytes }
    Devuelve: {"post": {...}, "key": "<s3_key>"}  (url path-style)
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    filename = (data.get("filename") or "").strip()
    ctype = (data.get("contentType") or "").strip()
    size_b = int(data.get("sizeBytes") or 0)

    if not filename or ctype not in RECEIPT_ALLOWED_MIME:
        return HttpResponseBadRequest("Invalid file type.")
    if size_b <= 0 or size_b > RECEIPT_MAX_MB * 1024 * 1024:
        return HttpResponseBadRequest("File too large.")

    key = _build_receipt_key(request.user.id, filename)

    s3 = _s3_client()
    fields = {
        "acl": "private",
        "success_action_status": "201",
        # TIP: si quieres forzar Content-Type, puedes incluirlo aquí y en Conditions.
        # "Content-Type": ctype,
    }
    conditions = [
        {"acl": "private"},
        {"success_action_status": "201"},
        ["starts-with", "$key", key.rsplit("/", 1)[0] + "/"],
        ["content-length-range", 1, RECEIPT_MAX_MB * 1024 * 1024],
        # Si decides forzar Content-Type:
        # {"Content-Type": ctype},
    ]

    post = s3.generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=600,
    )

    # Forzar URL path-style (coincide con lo que ya usas)
    endpoint = settings.AWS_S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    post["url"] = f"{endpoint}/{bucket}"

    return JsonResponse({"post": post, "key": key})


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
@rol_requerido('usuario')
def mis_rendiciones(request):
    user = request.user

    # --- Query base + paginación (se calcula SIEMPRE) ---
    cantidad_str = request.GET.get('cantidad', '10')
    try:
        per_page = 1000000 if cantidad_str == 'todos' else int(cantidad_str)
    except (TypeError, ValueError):
        per_page = 10
        cantidad_str = '10'

    movimientos_qs = CartolaMovimiento.objects.filter(
        usuario=user
    ).order_by('-fecha')

    paginator = Paginator(movimientos_qs, per_page)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # --- Saldos (usa el mismo QS para consistencia) ---
    saldo_disponible = (
        (movimientos_qs.filter(tipo__categoria="abono", status="aprobado_abono_usuario")
         .aggregate(total=Sum('abonos'))['total'] or 0)
        -
        (movimientos_qs.exclude(tipo__categoria="abono")
         .filter(status="aprobado_finanzas")
         .aggregate(total=Sum('cargos'))['total'] or 0)
    )
    saldo_pendiente = movimientos_qs.filter(tipo__categoria="abono") \
        .exclude(status="aprobado_abono_usuario") \
        .aggregate(total=Sum('abonos'))['total'] or 0
    saldo_rendido = movimientos_qs.exclude(tipo__categoria="abono") \
        .exclude(status="aprobado_finanzas") \
        .aggregate(total=Sum('cargos'))['total'] or 0

    # --- POST: crea la rendición (direct upload o multipart clásico) ---
    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES)
        if form.is_valid():
            mov = form.save(commit=False)
            mov.usuario = user
            mov.fecha = now()
            mov.status = 'pendiente_abono_usuario' if (
                mov.tipo and mov.tipo.categoria == "abono") else 'pendiente_supervisor'

            # Soporte de subida directa (si tu JS envía wasabi_key)
            wasabi_key = (request.POST.get('wasabi_key') or '').strip()
            if wasabi_key:
                mov.comprobante.name = wasabi_key
            else:
                mov.comprobante = form.cleaned_data['comprobante']

            mov.save()

            # Verificación opcional en Wasabi (igual que tenías)
            ruta_archivo = mov.comprobante.name
            import time
            for _ in range(3):
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

    return render(request, 'operaciones/mis_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad_str,
        'saldo_disponible': saldo_disponible,
        'saldo_pendiente': saldo_pendiente,
        'saldo_rendido': saldo_rendido,
        'form': form,

        # Si usas el JS de subida directa para rendiciones:
        'direct_uploads_receipts_enabled': True,
        'receipt_max_mb': int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25)),
    })


@login_required
@rol_requerido('usuario')
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Deposit approved successfully.")
    return redirect('operaciones:mis_rendiciones')


@login_required
@rol_requerido('usuario')
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
@rol_requerido('usuario')
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
        CartolaMovimiento, pk=pk, usuario=request.user
    )

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(
            request, "You cannot delete an already approved expense report."
        )
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        rendicion.delete()
        messages.success(request, "Expense report deleted successfully.")
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
@rol_requerido('usuario')
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
@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'facturacion')
def listar_precios_tecnico(request):
    # Cantidad por página
    cantidad_str = request.GET.get('cantidad', '10')
    cantidad = 1000000 if cantidad_str == 'todos' else int(cantidad_str)

    # Filtros (GET)
    f_tecnico = (request.GET.get('f_tecnico') or '').strip()
    f_ciudad = (request.GET.get('f_ciudad') or '').strip()
    f_proy = (request.GET.get('f_proyecto') or '').strip()
    f_codigo = (request.GET.get('f_codigo') or '').strip()

    qs = PrecioActividadTecnico.objects.select_related(
        'tecnico').order_by('-fecha_creacion')

    if f_tecnico:
        qs = qs.filter(
            Q(tecnico__first_name__icontains=f_tecnico) |
            Q(tecnico__last_name__icontains=f_tecnico) |
            Q(tecnico__username__icontains=f_tecnico)
        )
    if f_ciudad:
        qs = qs.filter(ciudad__icontains=f_ciudad)
    if f_proy:
        qs = qs.filter(proyecto__icontains=f_proy)
    if f_codigo:
        qs = qs.filter(codigo_trabajo__icontains=f_codigo)

    paginator = Paginator(qs, cantidad)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    ctx = {
        'pagina': pagina,
        'cantidad': cantidad_str,
        'f_tecnico': f_tecnico,
        'f_ciudad': f_ciudad,
        'f_proyecto': f_proy,
        'f_codigo': f_codigo,
    }
    return render(request, 'operaciones/listar_precios_tecnico.html', ctx)


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


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm')
def bulk_delete_precios(request):
    if request.method != "POST":
        messages.error(request, "Invalid request.")
        return redirect('operaciones:listar_precios_tecnico')

    ids = request.POST.getlist("ids")
    return_page = request.POST.get("return_page") or ""
    return_cantidad = request.POST.get("return_cantidad") or ""

    if not ids:
        messages.info(request, "No prices selected.")
        return redirect('operaciones:listar_precios_tecnico')

    qs = PrecioActividadTecnico.objects.filter(id__in=ids)
    deleted_count = qs.count()
    qs.delete()

    messages.success(
        request, f"{deleted_count} price(s) deleted successfully.")

    # Reconstruir URL de retorno preservando filtros y paginación
    base = reverse('operaciones:listar_precios_tecnico')
    params = []
    if return_cantidad:
        params.append(f"cantidad={return_cantidad}")
    if return_page and return_cantidad != "todos":
        params.append(f"page={return_page}")

    for key in ("f_tecnico", "f_ciudad", "f_proyecto", "f_codigo"):
        val = (request.POST.get(key) or '').strip()
        if val:
            params.append(f"{key}={val}")

    url = f"{base}?{'&'.join(params)}" if params else base
    return redirect(url)


# ===== Listado =====


def repartir_100(n):
    if n <= 0:
        return []
    base = (Decimal("100.00")/Decimal(n)).quantize(Decimal("0.01"))
    partes = [base]*n
    diff = Decimal("100.00") - sum(partes)
    if diff and partes:
        partes[-1] = (partes[-1]+diff).quantize(Decimal("0.01"))
    return partes


# ===== Descuento directo =====

def recomputar_estado_desde_asignaciones(self, save: bool = True) -> str:
    # NUEVO: si es descuento directo, no tocar el estado
    if self.is_direct_discount:
        return self.estado

    estados = list(self.tecnicos_sesion.values_list("estado", flat=True))
    nuevo = "asignado"
    if estados:
        if any(e == "en_revision_supervisor" for e in estados):
            nuevo = "en_revision_supervisor"
        elif any(e == "en_proceso" for e in estados):
            nuevo = "en_proceso"
        elif all(e == "aprobado_pm" for e in estados):
            nuevo = "aprobado_pm"
        elif any(e == "rechazado_pm" for e in estados):
            nuevo = "rechazado_pm"
        elif all(e == "aprobado_supervisor" for e in estados):
            nuevo = "aprobado_supervisor"
        elif any(e == "rechazado_supervisor" for e in estados):
            nuevo = "rechazado_supervisor"

    if self.estado != nuevo:
        self.estado = nuevo
        if save:
            self.save(update_fields=["estado"])
    return self.estado


@login_required
@require_POST
def exportar_billing_excel(request):
    """
    Exporta a XLSX con columnas:
    Project ID, Date, Week, Project Address, City, Work Type, Job Code,
    Description, Qty, Subtotal Company.
    - Encabezado con color y filtros
    - Bordes finos en toda la tabla
    - Bandas alternadas (gris/ blanco) en filas de datos
    - Fila Total: 'Total' en Qty (col I) y monto en Subtotal Company (col J)
    - Líneas de cuadricula DESACTIVADAS
    """

    # ========= Estilos locales =========
    HDR_FILL = PatternFill("solid", fgColor="374151")   # gris oscuro
    HDR_FONT = Font(bold=True, color="FFFFFF")
    HDR_ALIGN = Alignment(horizontal="center", vertical="center")
    CELL_ALIGN_LEFT = Alignment(
        horizontal="left", vertical="center", wrap_text=False)
    CELL_ALIGN_LEFT_WRAP = Alignment(
        horizontal="left", vertical="center", wrap_text=True)
    CELL_ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")
    THIN = Side(style="thin", color="D1D5DB")
    BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    ZEBRA_GRAY = "E5E7EB"   # gris clarito
    ZEBRA_WHITE = "FFFFFF"  # blanco

    # ========= Helpers locales =========
    def _export_headers():
        return [
            "Project ID", "Date", "Week", "Project Address", "City",
            "Work Type", "Job Code", "Description", "Qty", "Subtotal Company",
        ]

    def _get_address_from_session_only(s):
        return (
            getattr(s, "direccion_proyecto", None)
            or getattr(s, "direccion", None)
            or getattr(s, "project_address", None)
            or getattr(s, "direccion_obra", None)
            or ""
        )

    def _get_address_from_item_or_session(it, s):
        return (
            getattr(it, "direccion", None)
            or getattr(it, "project_address", None)
            or getattr(it, "direccion_obra", None)
            or getattr(s, "direccion_proyecto", None)
            or getattr(s, "direccion", None)
            or getattr(s, "project_address", None)
            or getattr(s, "direccion_obra", None)
            or ""
        )

    def _xlsx_response(workbook):
        from io import BytesIO
        bio = BytesIO()
        workbook.save(bio)
        bio.seek(0)
        ts = timezone.now().strftime("%Y%m%d_%H%M%S")
        resp = HttpResponse(
            bio.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="billing_export_{ts}.xlsx"'
        return resp

    def _format_money(ws, cols):
        money_fmt = '$#,##0.00'
        for col in cols:
            for col_cells in ws.iter_cols(min_col=col, max_col=col, min_row=2, values_only=False):
                for c in col_cells:
                    c.number_format = money_fmt

    def _format_number(ws, cols):
        num_fmt = '#,##0.00'
        for col in cols:
            for col_cells in ws.iter_cols(min_col=col, max_col=col, min_row=2, values_only=False):
                for c in col_cells:
                    c.number_format = num_fmt

    def _set_widths(ws, mapping):
        for idx, width in mapping.items():
            ws.column_dimensions[get_column_letter(idx)].width = width

    def _apply_table_borders(ws):
        """Bordes + alineaciones en todo el rango con datos (incluye encabezado)."""
        max_r, max_c = ws.max_row, ws.max_column
        for r in range(1, max_r + 1):
            for c in range(1, max_c + 1):
                cell = ws.cell(row=r, column=c)
                cell.border = BORDER_ALL
                if c in (9, 10):                    # Qty / Subtotal
                    cell.alignment = CELL_ALIGN_RIGHT
                elif c in (4, 8):                   # Address / Description -> wrap
                    cell.alignment = CELL_ALIGN_LEFT_WRAP
                else:
                    cell.alignment = CELL_ALIGN_LEFT

    def _apply_zebra(ws, start_row: int, end_row: int, gray_hex: str, white_hex: str):
        """Relleno alternado (gris/blanco) desde start_row hasta end_row."""
        if end_row < start_row:
            return
        fill_gray = PatternFill("solid", fgColor=gray_hex)
        fill_white = PatternFill("solid", fgColor=white_hex)
        max_c = ws.max_column
        for r in range(start_row, end_row + 1):
            fill = fill_gray if (r - start_row) % 2 == 0 else fill_white
            for c in range(1, max_c + 1):
                ws.cell(row=r, column=c).fill = fill

    def _style_after_fill(ws):
        """Header gris + filtros + congelar panes."""
        for col, _ in enumerate(_export_headers(), start=1):
            cell = ws.cell(row=1, column=col)
            cell.fill = HDR_FILL
            cell.font = HDR_FONT
            cell.alignment = HDR_ALIGN
            cell.border = BORDER_ALL
        ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
        ws.freeze_panes = "A2"

    # ========= 1) Parseo de IDs =========
    raw = (request.POST.get("ids") or "").strip()
    ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]

    # ========= 2) Workbook / hoja =========
    wb = Workbook()
    ws = wb.active
    ws.title = "Billing"

    # 👉 Desactivar líneas de cuadricula (en pantalla y también en impresión)
    ws.sheet_view.showGridLines = False
    ws.print_options.gridLines = False

    # Encabezados
    headers = _export_headers()
    ws.append(headers)

    if not ids:
        _style_after_fill(ws)
        return _xlsx_response(wb)

    # ========= 3) Prefetch =========
    sesiones = (
        SesionBilling.objects.filter(id__in=ids)
        .prefetch_related(
            Prefetch("items", queryset=ItemBilling.objects.order_by("id")),
            Prefetch("tecnicos_sesion",
                     queryset=SesionBillingTecnico.objects.select_related("tecnico")),
        )
        .order_by("id")
    )

    # ========= 4) Filas =========
    total_subtotal_company = 0.0
    tz = timezone.get_current_timezone()

    for s in sesiones:
        dt = s.creado_en
        date_str = timezone.localtime(dt, tz).strftime(
            "%d-%b").lower() if dt else ""
        week_str = getattr(s, "semana_pago_proyectada",
                           "") or getattr(s, "week", "")
        city = getattr(s, "ciudad", "") or getattr(s, "city", "")
        project_id = getattr(s, "proyecto_id", "") or getattr(
            s, "project_id", "")

        if not s.items.all():
            addr_session = _get_address_from_session_only(s)
            ws.append([project_id, date_str, week_str,
                      addr_session, city, "", "", "", 0.0, 0.0])
            continue

        for it in s.items.all():
            project_address = _get_address_from_item_or_session(it, s)
            qty = float(getattr(it, "cantidad", 0) or 0)
            sub_company = float(getattr(it, "subtotal_empresa", 0) or 0)
            ws.append([
                project_id,                  # A
                date_str,                    # B
                week_str,                    # C
                project_address,             # D
                city,                        # E
                getattr(it, "tipo_trabajo", "") or getattr(
                    it, "work_type", ""),   # F
                getattr(it, "codigo_trabajo", "") or getattr(
                    it, "job_code", ""),  # G
                getattr(it, "descripcion", "") or getattr(
                    it, "description", ""),  # H
                qty,                         # I
                sub_company                  # J
            ])
            total_subtotal_company += sub_company

    # ========= 5) Formatos / estilos =========
    _format_money(ws, cols=[10])   # J: Subtotal Company
    _format_number(ws, cols=[9])   # I: Qty

    _set_widths(ws, {
        1: 12, 2: 10, 3: 12, 4: 36, 5: 14, 6: 14, 7: 12, 8: 34, 9: 6, 10: 16
    })

    _apply_table_borders(ws)

    # Zebra desde la fila 2 (datos) hasta la última fila de datos
    data_end = ws.max_row
    _apply_zebra(ws, start_row=2, end_row=data_end,
                 gray_hex=ZEBRA_GRAY, white_hex=ZEBRA_WHITE)

    _style_after_fill(ws)

    # ========= 6) Fila Total =========
    ws.append([""] * 10)  # separador opcional
    total_row = ws.max_row
    ws.cell(row=total_row, column=9, value="Total").font = Font(
        bold=True)                   # I
    ws.cell(row=total_row, column=10,
            value=total_subtotal_company).font = Font(bold=True)   # J
    ws.cell(row=total_row, column=10).number_format = '$#,##0.00'
    for col in range(1, 11):
        c = ws.cell(row=total_row, column=col)
        c.border = BORDER_ALL
        c.alignment = CELL_ALIGN_RIGHT if col in (9, 10) else (
            CELL_ALIGN_LEFT_WRAP if col in (4, 8) else CELL_ALIGN_LEFT)

    return _xlsx_response(wb)


@login_required
@rol_requerido('admin', 'pm')
@require_POST
@transaction.atomic
def billing_send_to_finance(request):
    """
    Envía billings a Finanzas.

    Reglas:
      - Descuentos directos (is_direct_discount=True):
          -> finance_status='review_discount', finance_sent_at=now (si no está pagado).
      - No descuentos:
          -> requieren estado en {'aprobado_supervisor','aprobado_pm','aprobado_finanzas'}
             y se marcan como finance_status='sent', finance_sent_at=now.

    Procesa id por id (no aborta el batch completo).
    """
    # --- parse ids + note ---
    ids, note = [], ""
    if request.content_type and "application/json" in request.content_type:
        import json
        try:
            payload = json.loads(request.body.decode("utf-8"))
            ids = [int(x)
                   for x in (payload.get("ids") or []) if str(x).isdigit()]
            note = (payload.get("note") or "").strip()
        except Exception:
            return JsonResponse({"ok": False, "error": "INVALID_JSON"}, status=400)
    else:
        raw = (request.POST.get("ids") or "").strip()
        ids = [int(x) for x in raw.split(",") if x.isdigit()]
        note = (request.POST.get("note") or "").strip()

    if not ids:
        return JsonResponse({"ok": False, "error": "NO_IDS"}, status=400)

    allowed_ops = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    now = timezone.now()

    qs = (
        SesionBilling.objects
        .select_for_update()
        .filter(id__in=ids)
        .only("id", "is_direct_discount", "estado",
              "finance_status", "finance_sent_at", "finance_note")
    )

    updated_ids = []
    skipped = {}  # id -> reason

    for s in qs:
        # No se puede enviar si ya está pagado
        if s.finance_status == "paid":
            skipped[s.id] = "paid"
            continue

        if s.is_direct_discount:
            # Descuento directo: siempre enviable -> review_discount + sent_at
            s.finance_status = "review_discount"
            s.finance_sent_at = now
            s.finance_updated_at = now
            if note:
                prefix = f"{now:%Y-%m-%d %H:%M} Ops: "
                s.finance_note = (
                    s.finance_note + "\n" if s.finance_note else "") + prefix + note
            s.save(update_fields=[
                   "finance_status", "finance_sent_at", "finance_updated_at", "finance_note"])
            updated_ids.append(s.id)
        else:
            # Normal: validar estado operativo
            if s.estado not in allowed_ops:
                skipped[s.id] = "invalid_status"
                continue
            s.finance_status = "sent"
            s.finance_sent_at = now
            s.finance_updated_at = now
            if note:
                prefix = f"{now:%Y-%m-%d %H:%M} Ops: "
                s.finance_note = (
                    s.finance_note + "\n" if s.finance_note else "") + prefix + note
            s.save(update_fields=[
                   "finance_status", "finance_sent_at", "finance_updated_at", "finance_note"])
            updated_ids.append(s.id)

    # Respuesta
    payload = {"ok": True, "count": len(
        updated_ids), "updated_ids": updated_ids}
    if skipped:
        payload["skipped"] = skipped

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(payload)

    if updated_ids:
        messages.success(request, f"Sent to Finance: {len(updated_ids)}.")
    if skipped:
        # Mensaje compactado de omitidos
        msg = ", ".join([f"#{i}: {r}" for i, r in skipped.items()])
        messages.warning(request, f"Skipped: {msg}")
    return redirect("operaciones:listar_billing")


@login_required
@require_POST
def billing_reopen_asignado(request, pk):
    obj = get_object_or_404(SesionBilling, pk=pk)

    if obj.estado in ("aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"):
        with transaction.atomic():
            obj.estado = "asignado"
            obj.save(update_fields=["estado"])
            obj.tecnicos_sesion.all().update(
                estado="asignado",
                aceptado_en=None,
                finalizado_en=None,
                supervisor_revisado_en=None,
                supervisor_comentario="",
                pm_revisado_en=None,
                pm_comentario="",
                reintento_habilitado=True,
            )
        messages.success(
            request, f"Billing #{obj.pk} has been reopened to 'Assigned' and all assignments were reactivated.")
    else:
        messages.info(request, "This record is not in an approved state.")
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/operaciones/billing/listar/"))


WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


@login_required
@require_POST
def billing_update_item_qty(request, item_id: int):
    """
    Actualiza la cantidad de un ItemBilling y recalcula subtotales
    SIN cambiar el estado de la SesionBilling.
    Solo Admin general o superuser.
    """
    item = get_object_or_404(
        ItemBilling.objects.select_related("sesion"), pk=item_id)
    user = request.user

    is_admin = user.is_superuser or getattr(user, "es_admin_general", False)
    if not is_admin:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    # (Opcional) Bloquear si ya está pagado salvo superuser
    if item.sesion.finance_status == "paid" and not user.is_superuser:
        return JsonResponse({"ok": False, "error": "paid-locked"}, status=403)

    qty_raw = (request.POST.get("cantidad") or "").strip()
    try:
        qty = Decimal(qty_raw)
    except (InvalidOperation, TypeError):
        return JsonResponse({"ok": False, "error": "invalid-quantity"}, status=400)

    if qty < 0:
        return JsonResponse({"ok": False, "error": "negative-quantity"}, status=400)

    old_estado = item.sesion.estado  # ← preservamos
    sesion = item.sesion

    with transaction.atomic():
        # 1) Actualizar ítem
        item.cantidad = qty
        item.subtotal_empresa = (item.precio_empresa or Decimal("0")) * qty

        # Recalcular desglose técnico del ítem
        total_tech = Decimal("0")
        for d in ItemBillingTecnico.objects.filter(item=item).select_related("item"):
            d.subtotal = (d.tarifa_efectiva or Decimal("0")) * qty
            d.save(update_fields=["subtotal"])
            total_tech += d.subtotal

        item.subtotal_tecnico = total_tech
        item.save(update_fields=["cantidad",
                  "subtotal_empresa", "subtotal_tecnico"])

        # 2) Recalcular totales de la sesión
        aggr = sesion.items.aggregate(
            total_tecnico=Sum("subtotal_tecnico"),
            total_empresa=Sum("subtotal_empresa"),
        )
        sesion.subtotal_tecnico = aggr["total_tecnico"] or Decimal("0")
        sesion.subtotal_empresa = aggr["total_empresa"] or Decimal("0")
        # ¡NO cambiamos el estado!
        sesion.save(update_fields=["subtotal_tecnico", "subtotal_empresa"])

        # Por seguridad, si algo externo tocó el estado, lo forzamos al anterior
        if sesion.estado != old_estado:
            SesionBilling.objects.filter(
                pk=sesion.pk).update(estado=old_estado)

    return JsonResponse({
        "ok": True,
        "cantidad": f"{item.cantidad:.2f}",
        "itemSubtotalEmpresa": f"{item.subtotal_empresa:.2f}",
        "itemSubtotalTecnico": f"{item.subtotal_tecnico:.2f}",
        "sesionSubtotalEmpresa": f"{sesion.subtotal_empresa:.2f}",
        "sesionSubtotalTecnico": f"{sesion.subtotal_tecnico:.2f}",
    })


def _actualizar_tecnicos_preservando_fotos(sesion: SesionBilling, nuevos_ids: list[int]) -> None:
    """
    - NO elimina en masa.
    - Mantiene asignaciones que ya tengan evidencias.
    - Elimina sólo asignaciones sin evidencias y que ya no estén en la lista.
    - Actualiza/crea porcentajes según repartir_100 de los ids solicitados.
      Si tuvimos que conservar un técnico “viejo” por tener fotos, ese conserva su % original.
    """
    existentes = {
        ts.tecnico_id: ts for ts in sesion.tecnicos_sesion.select_related("tecnico")}
    nuevos_ids = [int(x) for x in nuevos_ids]

    # 1) Crear/actualizar los solicitados
    partes_nuevas = repartir_100(len(nuevos_ids)) if nuevos_ids else []
    for tid, pct in zip(nuevos_ids, partes_nuevas):
        if tid in existentes:
            ts = existentes[tid]
            if ts.porcentaje != pct:
                ts.porcentaje = pct
                ts.save(update_fields=["porcentaje"])
        else:
            SesionBillingTecnico.objects.create(
                sesion=sesion, tecnico_id=tid, porcentaje=pct
            )

    # 2) Eliminar sólo los que NO están en la lista y NO tienen fotos
    for tid, ts in list(existentes.items()):
        if tid in nuevos_ids:
            continue
        tiene_fotos = EvidenciaFotoBilling.objects.filter(
            tecnico_sesion=ts).exists()
        if tiene_fotos:
            # Lo conservamos y avisamos (para que el usuario sepa por qué “no se fue”)
            messages.warning(
                # tolerante en tareas
                None if hasattr(messages, "_queued_messages") else sesion,
                f"No se eliminó a {getattr(ts.tecnico, 'get_full_name', lambda: ts.tecnico.username)()} "
                "porque ya tiene fotos registradas en esta sesión."
            )
            continue
        ts.delete()


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
            sesion=sesion, tecnico_id=tid, porcentaje=pct
        )

    _recalcular_items_sesion(sesion)
    messages.success(
        request, "Technicians reassigned and totals recalculated.")
    return redirect("operaciones:editar_billing", sesion_id=sesion.id)


# ===== Persistencia =====

WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")  # formato de <input type="week">


@transaction.atomic
def _guardar_billing(request, sesion: SesionBilling | None = None):
    # ======================== Helpers locales ========================= #
    import re
    import json

    WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")

    def money(v) -> Decimal:
        return Decimal(str(v or "0")).quantize(Decimal("0.01"))

    def meta_codigo(cliente, ciudad, proyecto, oficina, codigo):
        return (PrecioActividadTecnico.objects
                .filter(cliente=cliente, ciudad=ciudad, proyecto=proyecto,
                        oficina=oficina, codigo_trabajo=codigo)
                .values("tipo_trabajo", "descripcion", "unidad_medida")
                .first())

    def precio_empresa(cliente, ciudad, proyecto, oficina, codigo) -> Decimal:
        val = (PrecioActividadTecnico.objects
               .filter(cliente=cliente, ciudad=ciudad, proyecto=proyecto,
                       oficina=oficina, codigo_trabajo=codigo)
               .values_list("precio_empresa", flat=True)
               .first())
        return money(val or 0)

    def tarifa_tecnico(tid, cliente, ciudad, proyecto, oficina, codigo) -> Decimal:
        val = (PrecioActividadTecnico.objects
               .filter(tecnico_id=tid, cliente=cliente, ciudad=ciudad,
                       proyecto=proyecto, oficina=oficina, codigo_trabajo=codigo)
               .values_list("precio_tecnico", flat=True)
               .first())
        if val is None:
            val = (PrecioActividadTecnico.objects
                   .filter(cliente=cliente, ciudad=ciudad, proyecto=proyecto,
                           oficina=oficina, codigo_trabajo=codigo)
                   .values_list("precio_tecnico", flat=True)
                   .first())
        return money(val or 0)

    def actualizar_tecnicos_preservando_fotos(sesion: SesionBilling, ids: list[int]):
        """
        Crea/actualiza asignaciones preservando evidencias.
        Mantiene porcentajes existentes; crea faltantes con 100/n.
        Elimina ausentes solo si no tienen evidencias.
        """
        existentes = {x.tecnico_id: x for x in sesion.tecnicos_sesion.all()}
        pct_default = money(Decimal("100") / Decimal(len(ids))
                            ) if ids else Decimal("0.00")

        # crear
        for tid in ids:
            if tid not in existentes:
                SesionBillingTecnico.objects.create(
                    sesion=sesion, tecnico_id=tid, porcentaje=pct_default
                )
        # eliminar sin evidencias
        for tid, obj in list(existentes.items()):
            if tid not in ids and not obj.evidencias.exists():
                obj.delete()
    # =================================================================== #

    # ----------------------------- Header ------------------------------ #
    proyecto_id = (request.POST.get("project_id") or "").strip()
    cliente = (request.POST.get("client") or "").strip()
    ciudad = (request.POST.get("city") or "").strip()
    proyecto = (request.POST.get("project") or "").strip()
    oficina = (request.POST.get("office") or "").strip()
    ids = list(map(int, request.POST.getlist("tech_ids[]")))

    direccion_proyecto = (request.POST.get("direccion_proyecto") or "").strip()
    semana_pago_proyectada = (request.POST.get(
        "semana_pago_proyectada") or "").strip()
    if semana_pago_proyectada and not WEEK_RE.match(semana_pago_proyectada):
        semana_pago_proyectada = ""

    is_direct_discount = request.POST.get("direct_discount") == "1"

    # ------------------------- Validaciones UX ------------------------- #
    def render_with_data(error_msg: str | None = None):
        # Para no "perder" lo escrito: re-render con datos posteados.
        if error_msg:
            messages.error(request, error_msg)

        clientes = (PrecioActividadTecnico.objects
                    .values_list("cliente", flat=True).distinct().order_by("cliente"))
        tecnicos = (Usuario.objects
                    .filter(precioactividadtecnico__isnull=False, is_active=True)
                    .distinct().order_by("first_name", "last_name", "username"))

        # reconstruir items para el template
        items_ctx = []
        for raw in request.POST.getlist("items[]"):
            try:
                o = json.loads(raw)
            except Exception:
                continue
            items_ctx.append({
                "codigo_trabajo": (o.get("code") or "").strip(),
                "tipo_trabajo": "",
                "descripcion": "",
                "unidad_medida": "",
                "cantidad": o.get("amount"),
                "precio_empresa": "",
                "subtotal_empresa": "",
                "subtotal_tecnico": "",
                "desglose_tecnico": [],  # el front los rehidrata al seleccionar código
            })

        sesion_ctx = {
            "proyecto_id": proyecto_id,
            "cliente": cliente,
            "ciudad": ciudad,
            "proyecto": proyecto,
            "oficina": oficina,
            "direccion_proyecto": direccion_proyecto,
            "semana_pago_proyectada": semana_pago_proyectada,
            "is_direct_discount": is_direct_discount,
        }

        return render(request, "operaciones/billing_editar.html", {
            "sesion": sesion_ctx if not sesion else sesion,
            "clientes": list(clientes),
            "tecnicos": tecnicos,
            "items": items_ctx,
            "ids_tecnicos": ids,
        })

    if not (proyecto_id and cliente and ciudad and proyecto and oficina):
        return render_with_data("Complete all header fields.")
    if not ids:
        return render_with_data("Select at least one technician.")

    # ------------------------------ Items ------------------------------ #
    filas = []
    raw_items = request.POST.getlist("items[]")
    if not raw_items:
        return render_with_data("Please add at least one item.")

    for raw in raw_items:
        try:
            o = json.loads(raw)
        except Exception:
            return HttpResponseBadRequest("Items inválidos.")
        cod = (o.get("code") or "").strip()
        amt = o.get("amount")
        if not cod or amt in ("", None):
            return HttpResponseBadRequest("Cada fila requiere Job Code y Amount.")
        qty = Decimal(str(amt))
        if is_direct_discount and qty > 0:
            # normalizamos a negativo SIN recargar página
            qty = -qty
        filas.append({"codigo": cod, "cantidad": qty})

    # --------------- Crear / actualizar la sesión base ----------------- #
    if sesion is None:
        sesion = SesionBilling.objects.create(
            proyecto_id=proyecto_id,
            cliente=cliente, ciudad=ciudad, proyecto=proyecto, oficina=oficina,
            direccion_proyecto=direccion_proyecto,
            semana_pago_proyectada=semana_pago_proyectada,
            semana_pago_real=semana_pago_proyectada if is_direct_discount else "",
            is_direct_discount=is_direct_discount,
        )
    else:
        sesion.proyecto_id = proyecto_id
        sesion.cliente = cliente
        sesion.ciudad = ciudad
        sesion.proyecto = proyecto
        sesion.oficina = oficina
        sesion.direccion_proyecto = direccion_proyecto
        sesion.semana_pago_proyectada = semana_pago_proyectada
        if is_direct_discount:
            sesion.semana_pago_real = semana_pago_proyectada
        sesion.is_direct_discount = is_direct_discount
        sesion.save()

    # ---- Técnicos (SIEMPRE se guardan para asociación/visualización) --- #
    actualizar_tecnicos_preservando_fotos(sesion, ids)
    actuales = list(
        sesion.tecnicos_sesion.values_list(
            "tecnico_id", "porcentaje").order_by("id")
    )
    # si no hay porcentajes (sesión recién creada), igualar 100/n
    if not actuales:
        pct = money(Decimal("100") / Decimal(len(ids)))
        for tid in ids:
            SesionBillingTecnico.objects.create(
                sesion=sesion, tecnico_id=tid, porcentaje=pct)
        actuales = [(tid, pct) for tid in ids]

    ids_def = [tid for (tid, _) in actuales]
    partes_def = [pct for (_, pct) in actuales]

    # -------------------- Rehacer items y totales ---------------------- #
    sesion.items.all().delete()
    total_emp = Decimal("0.00")
    total_tec = Decimal("0.00")

    for fila in filas:
        meta = meta_codigo(cliente, ciudad, proyecto, oficina, fila["codigo"])
        if not meta:
            return HttpResponseBadRequest(f"Código '{fila['codigo']}' no existe con los filtros.")

        p_emp = precio_empresa(cliente, ciudad, proyecto,
                               oficina, fila["codigo"])
        sub_emp = money(p_emp * fila["cantidad"])

        item = ItemBilling.objects.create(
            sesion=sesion,
            codigo_trabajo=fila["codigo"],
            tipo_trabajo=meta["tipo_trabajo"],
            descripcion=meta["descripcion"],
            unidad_medida=meta["unidad_medida"],
            cantidad=money(fila["cantidad"]),
            precio_empresa=p_emp,
            subtotal_empresa=sub_emp,
            subtotal_tecnico=Decimal("0.00"),
        )

        sub_tecs = Decimal("0.00")
        for tid, pct in zip(ids_def, partes_def):
            base = tarifa_tecnico(tid, cliente, ciudad,
                                  proyecto, oficina, fila["codigo"])
            efectiva = money(base * (pct / Decimal("100")))
            subtotal = money(efectiva * item.cantidad)
            ItemBillingTecnico.objects.create(
                item=item, tecnico_id=tid,
                tarifa_base=base, porcentaje=pct,
                tarifa_efectiva=efectiva, subtotal=subtotal,
            )
            sub_tecs += subtotal

        item.subtotal_tecnico = sub_tecs
        item.save(update_fields=["subtotal_tecnico"])

        total_emp += sub_emp
        total_tec += sub_tecs

    sesion.subtotal_empresa = money(total_emp)
    sesion.subtotal_tecnico = money(total_tec)
    sesion.save(update_fields=[
                "subtotal_empresa", "subtotal_tecnico", "semana_pago_real", "is_direct_discount"])

    # Nota: para que NO salga en aprobación del técnico:
    # en la vista/listado de aprobaciones, excluye sesiones con is_direct_discount=True

    messages.success(
        request,
        "Direct discount saved and linked to the selected technician(s)." if is_direct_discount
        else "Billing saved successfully (photos preserved)."
    )
    return redirect("operaciones:listar_billing")


def _recalcular_items_sesion(sesion: SesionBilling):
    ids = list(sesion.tecnicos_sesion.values_list("tecnico_id", flat=True))
    partes = list(sesion.tecnicos_sesion.values_list("porcentaje", flat=True))
    total_tec = Decimal("0.00")
    for it in sesion.items.all():
        it.desglose_tecnico.all().delete()
        sub = Decimal("0.00")
        for tid, pct in zip(ids, partes):
            base = _tarifa_tecnico(
                tid, sesion.cliente, sesion.ciudad, sesion.proyecto, sesion.oficina, it.codigo_trabajo
            )
            efectiva = money(base * (pct / Decimal("100")))
            subtotal = money(efectiva * it.cantidad)
            ItemBillingTecnico.objects.create(
                item=it,
                tecnico_id=tid,
                tarifa_base=base,
                porcentaje=pct,
                tarifa_efectiva=efectiva,
                subtotal=subtotal,
            )
            sub += subtotal
        it.subtotal_tecnico = sub
        it.save(update_fields=["subtotal_tecnico"])
        total_tec += sub
    sesion.subtotal_tecnico = money(total_tec)
    sesion.save(update_fields=["subtotal_tecnico"])

# ===== Búsquedas / AJAX =====


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
    return {
        "tipo_trabajo": p.tipo_trabajo,
        "descripcion": p.descripcion,
        "unidad_medida": p.unidad_medida
    }


@login_required
def ajax_clientes(request):
    data = list(
        PrecioActividadTecnico.objects
        .values_list("cliente", flat=True)
        .distinct()
        .order_by("cliente")
    )
    return JsonResponse({"results": data})


@login_required
def ajax_ciudades(request):
    cliente = request.GET.get("client", "")
    data = list(
        PrecioActividadTecnico.objects.filter(cliente__iexact=cliente)
        .values_list("ciudad", flat=True)
        .distinct()
        .order_by("ciudad")
    ) if cliente else []
    return JsonResponse({"results": data})


@login_required
def ajax_proyectos(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    ok = cliente and ciudad
    data = list(
        PrecioActividadTecnico.objects.filter(
            cliente__iexact=cliente, ciudad__iexact=ciudad)
        .values_list("proyecto", flat=True)
        .distinct()
        .order_by("proyecto")
    ) if ok else []
    return JsonResponse({"results": data})


@login_required
def ajax_oficinas(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto = request.GET.get("project", "")
    ok = cliente and ciudad and proyecto
    data = list(
        PrecioActividadTecnico.objects.filter(
            cliente__iexact=cliente, ciudad__iexact=ciudad, proyecto__iexact=proyecto
        )
        .values_list("oficina", flat=True)
        .distinct()
        .order_by("oficina")
    ) if ok else []
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
        cliente__iexact=cliente, ciudad__iexact=ciudad, proyecto__iexact=proyecto, oficina__iexact=oficina or "-"
    )
    if q:
        qs = qs.filter(codigo_trabajo__istartswith=q)
    data = list(
        qs.values("codigo_trabajo", "tipo_trabajo",
                  "descripcion", "unidad_medida")
        .distinct()
        .order_by("codigo_trabajo")[:20]
    )
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


@login_required
@rol_requerido('admin', 'supervisor', 'pm', 'facturacion')
def produccion_admin(request):
    """
    Producción por técnico (vista Admin) con filtros + paginación (UX como Weekly Payments).
    Filtros: proyecto (por Project ID parcial o nombre), REAL pay week (34 / W34 / 2025-W34),
             técnico, cliente.
    Solo filtra por semana REAL: 'semana_pago_real'.
    Incluye también sesiones de DESCUENTO DIRECTO (is_direct_discount=True), aunque su estado
    operativo no esté en aprobadas.
    """
    # --- imports locales para que la función sea autocontenida ---
    import re
    from decimal import Decimal
    from urllib.parse import urlencode
    from django.db.models import Q, CharField
    from django.db.models.functions import Cast
    from django.core.paginator import Paginator
    from django.utils import timezone

    # --- helpers locales ---
    def _iso_week_str(dt):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{int(w):02d}"

    def parse_week_query(q: str):
        """
        Acepta: '34', 'w34', 'W34', '2025-W34', '2025W34'
        Retorna (exact_iso, week_token)
          - exact_iso: 'YYYY-W##' cuando viene año
          - week_token: 'W##' cuando solo viene el número
        """
        if not q:
            return (None, None)
        s = q.strip().upper().replace("WEEK", "W").replace(" ", "")
        m = re.fullmatch(r'(\d{4})-?W(\d{1,2})', s)   # 2025-W34 ó 2025W34
        if m:
            year, ww = int(m.group(1)), int(m.group(2))
            return (f"{year}-W{ww:02d}", None)
        m = re.fullmatch(r'(?:W)?(\d{1,2})', s)       # W34 ó 34
        if m:
            ww = int(m.group(1))
            return (None, f"W{ww:02d}")
        return (None, None)

    def _normalize_week_str(s: str) -> str:
        """Normaliza guiones y espacios; devuelve MAYÚSCULAS."""
        if not s:
            return ""
        s = s.replace("\u2013", "-").replace("\u2014", "-")  # – — -> -
        s = re.sub(r"\s+", "", s)
        return s.upper()

    # ---------------- configuración ----------------
    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    current_week = _iso_week_str(timezone.now())

    # ---------------- Filtros GET ----------------
    f_project = (request.GET.get("f_project") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_client = (request.GET.get("f_client") or "").strip()

    exact_week, week_token = parse_week_query(f_week_input)

    # ---------------- Query base ----------------
    # 🔴 Cambio clave: incluir descuentos directos aunque no estén en estados_ok
    qs = (
        SesionBilling.objects
        .filter(Q(estado__in=estados_ok) | Q(is_direct_discount=True))
        .prefetch_related("tecnicos_sesion__tecnico", "items__desglose_tecnico")
        .order_by("-creado_en")
        .distinct()
    )

    # ---------------- Semana REAL (único criterio de semana) ----------------
    if exact_week:
        token = exact_week.split("-", 1)[-1].upper()  # 'W##'
        qs = qs.filter(
            Q(semana_pago_real__iexact=exact_week) |
            Q(semana_pago_real__icontains=token)   # tolera 2025W34, etc.
        )
    elif week_token:
        qs = qs.filter(semana_pago_real__icontains=week_token)

    # ---------------- Otros filtros ----------------
    if f_project:
        qs = qs.annotate(proyecto_id_str=Cast('proyecto_id', CharField()))
        qs = qs.filter(
            Q(proyecto_id_str__icontains=f_project) |
            Q(proyecto__icontains=f_project)
        )

    if f_client:
        qs = qs.filter(cliente__icontains=f_client)

    # ---------------- Construcción de filas (una por técnico) ----------------
    filas = []
    for s in qs:
        for asig in s.tecnicos_sesion.all():
            tecnico = asig.tecnico

            # Filtro por técnico (por fila)
            if f_tech:
                target = f_tech.lower()
                full_name = ((tecnico.first_name or "") + " " +
                             (tecnico.last_name or "")).strip().lower()
                username = (tecnico.username or "").lower()
                if target not in full_name and target not in username:
                    continue

            detalle = []
            total_tecnico = Decimal('0')

            for it in s.items.all():
                bd = next(
                    (d for d in it.desglose_tecnico.all()
                     if getattr(d, "tecnico_id", None) == getattr(tecnico, "id", None)),
                    None
                )
                if not bd:
                    continue

                rate = bd.tarifa_efectiva if isinstance(
                    bd.tarifa_efectiva, Decimal) else Decimal(str(bd.tarifa_efectiva or 0))
                qty = it.cantidad if isinstance(
                    it.cantidad, Decimal) else Decimal(str(it.cantidad or 0))

                # ⚠️ si qty < 0 (descuento), subtotal será negativo → resta
                sub_tec = rate * qty
                total_tecnico += sub_tec

                detalle.append({
                    "codigo": it.codigo_trabajo,
                    "tipo": it.tipo_trabajo,
                    "desc": it.descripcion,
                    "uom": it.unidad_medida,
                    "qty": it.cantidad,
                    "rate_tec": rate,
                    "subtotal_tec": sub_tec,
                })

            filas.append({
                "sesion": s,
                "tecnico": tecnico,
                "project_id": s.proyecto_id,
                # Columna principal = semana REAL
                "week": s.semana_pago_real or "—",
                "status": s.estado,
                # ← NUEVO para la vista
                "is_discount": bool(getattr(s, "is_direct_discount", False)),
                "client": s.cliente,
                "city": s.ciudad,
                "project": s.proyecto,
                "office": s.oficina,
                "real_week": s.semana_pago_real or "—",
                "proj_week": s.semana_pago_proyectada or "—",
                "total_tecnico": total_tecnico,
                "detalle": detalle,
            })

    # ---------------- Filtro defensivo por semana REAL en memoria ----------------
    if exact_week or week_token:
        token = (week_token or exact_week.split("-", 1)[-1]).upper()  # 'W##'
        exact_norm = _normalize_week_str(exact_week) if exact_week else None

        def _match_row_real(r):
            rw = _normalize_week_str(r["real_week"])
            if exact_norm:
                return (rw == exact_norm) or (token in rw)
            return token in rw

        filas = [r for r in filas if _match_row_real(r)]

    # ---------------- Orden por semana real ----------------
    def bucket_key(row):
        rw = row["real_week"]
        if rw == "—":
            return (2, "ZZZ")
        if rw == current_week:
            return (0, "000")
        if rw > current_week:
            return (0, rw)
        return (1, rw)

    filas.sort(key=bucket_key)

    # ---------------- Paginación ----------------
    cantidad = request.GET.get("cantidad", "10")
    if cantidad != "todos":
        try:
            per_page = max(5, min(int(cantidad), 100))
        except ValueError:
            per_page = 10
        paginator = Paginator(filas, per_page)
        page_number = request.GET.get("page") or 1
        pagina = paginator.get_page(page_number)
    else:
        class _OnePage:
            number = 1

            @property
            def paginator(self):
                class P:
                    num_pages = 1
                return P()
            has_previous = False
            has_next = False
            object_list = filas
        pagina = _OnePage()

    # QS de filtros
    filters_dict = {
        "f_project": f_project,
        "f_week": f_week_input,
        "f_tech": f_tech,
        "f_client": f_client,
        "cantidad": cantidad,
    }
    filters_qs = urlencode({k: v for k, v in filters_dict.items() if v})

    return render(request, "operaciones/produccion_admin.html", {
        "current_week": current_week,
        "pagina": pagina,
        "cantidad": cantidad,
        "f_project": f_project,
        "f_week_input": f_week_input,
        "f_tech": f_tech,
        "f_client": f_client,
        "filters_qs": filters_qs,
    })


@login_required
@rol_requerido('usuario')
def produccion_usuario(request):
    """
    Producción del técnico logueado.
    Incluye:
      - sesiones aprobadas (Supervisor/PM/Finanzas)
      - descuentos directos (is_direct_discount=True)
      - cualquier sesión donde el técnico tenga líneas negativas (subtotal<0)
    No requiere que exista TecnicoSesion: alcanza con estar en items.desglose_tecnico.
    """
    tecnico = request.user

    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    qs = (
        SesionBilling.objects
        # el técnico debe estar en el desglose (esto cubre descuentos sin TecnicoSesion)
        .filter(items__desglose_tecnico__tecnico=tecnico)
        # incluir aprobadas O descuentos directos O líneas negativas
        .filter(
            Q(estado__in=estados_ok)
            | Q(is_direct_discount=True)
            | Q(items__desglose_tecnico__subtotal__lt=0)
        )
        .prefetch_related("items__desglose_tecnico")
        .order_by("-creado_en")
        .distinct()
    )

    # Semana ISO YYYY-W##
    def _iso_week_str(dt):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{int(w):02d}"

    current_week = _iso_week_str(timezone.now())

    # filtro por semana REAL: "all" o "YYYY-W##"
    week_filter = (request.GET.get("week") or "all").strip()
    weeks_wanted = None if week_filter.lower() == "all" else {
        week_filter.upper()}

    filas = []
    total_semana_actual = Decimal("0")

    for s in qs:
        rw = (s.semana_pago_real or "").upper()
        if weeks_wanted is not None and rw not in weeks_wanted:
            continue

        detalle = []
        total_tecnico = Decimal("0")

        for it in s.items.all():
            bd = next(
                (d for d in it.desglose_tecnico.all()
                 if getattr(d, "tecnico_id", None) == getattr(tecnico, "id", None)),
                None
            )
            if not bd:
                continue

            rate = bd.tarifa_efectiva if isinstance(
                bd.tarifa_efectiva, Decimal) else Decimal(str(bd.tarifa_efectiva or 0))
            qty = it.cantidad if isinstance(
                it.cantidad,        Decimal) else Decimal(str(it.cantidad or 0))

            sub_tec = rate * qty  # si qty<0 => descuenta
            total_tecnico += sub_tec

            detalle.append({
                "codigo": it.codigo_trabajo,
                "tipo": it.tipo_trabajo,
                "desc": it.descripcion,
                "uom": it.unidad_medida,
                "qty": it.cantidad,
                "rate_tec": rate,
                "subtotal_tec": sub_tec,
            })

        if detalle:
            if rw == current_week:
                total_semana_actual += total_tecnico  # incluye negativos

            filas.append({
                "sesion": s,
                "project_id": s.proyecto_id,
                "week": s.semana_pago_proyectada or "—",
                "status": s.estado,
                "client": s.cliente,
                "city": s.ciudad,
                "project": s.proyecto,
                "office": s.oficina,
                "real_week": s.semana_pago_real or "—",
                "total_tecnico": total_tecnico,   # puede ser negativo
                "detalle": detalle,
            })

    # Orden: actual, futuras, pasadas, sin semana
    def bucket_key(row):
        rw = row["real_week"]
        if rw == "—":
            return (2, "ZZZ")
        if rw == current_week:
            return (0, "000")
        if rw > current_week:
            return (0, rw)
        return (1, rw)

    filas.sort(key=bucket_key)

    return render(request, "operaciones/produccion_usuario.html", {
        "filas": filas,
        "current_week": current_week,
        "total_semana_actual": total_semana_actual,
        "week_filter": week_filter,
    })


def _s3_client():
    """
    Wasabi S3 en path-style para evitar problemas de CORS/SSL.
    Usa el endpoint REGIONAL del bucket (p.ej. us-east-1).
    """
    return boto3.client(
        "s3",
        endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL",
                             "https://s3.us-east-1.wasabisys.com"),
        region_name=getattr(settings, "AWS_S3_REGION_NAME", "us-east-1"),
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", s3={
                      "addressing_style": "path"}),
        verify=getattr(settings, "AWS_S3_VERIFY", True),
    )


ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}


@transaction.atomic
def _sync_weekly_totals(week: str | None = None, create_missing: bool = False) -> dict:
    """
    Sincroniza WeeklyPayment con la producción (incluye descuentos):
    - Actualiza amount si cambió (approved_user -> pending_payment).
    - Elimina registros sin producción (== 0) si status != 'paid'.
    - [opcional] Crea los que faltan cuando create_missing=True (pueden ser negativos).
    """
    # ✅ Incluir DESCUENTOS: OR subtotal__lt=0
    base = (
        ItemBillingTecnico.objects
        .filter(item__sesion__semana_pago_real__gt="")
        .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
    )
    if week:
        base = base.filter(item__sesion__semana_pago_real=week)

    agg = (
        base.values("tecnico_id", "item__sesion__semana_pago_real")
        .annotate(total=Sum("subtotal"))
    )

    # 👉 No descartamos negativos. Sólo excluimos exactamente 0 para que
    #     se elimine el WP existente o no se cree uno vacío.
    prod_totals = {
        (row["tecnico_id"], row["item__sesion__semana_pago_real"]): (row["total"] or Decimal("0"))
        for row in agg
        if (row["total"] or Decimal("0")) != 0
    }

    updated = deleted = created = 0

    for wp in WeeklyPayment.objects.select_for_update():
        if week and wp.week != week:
            continue

        key = (wp.technician_id, wp.week)
        if key not in prod_totals:
            # no hay producción neta (== 0) → eliminar si no está pagado
            if wp.status != "paid":
                wp.delete()
                deleted += 1
            continue

        total = prod_totals[key]
        if wp.amount != total:
            wp.amount = total
            save_fields = ["amount", "updated_at"]
            if wp.status == "approved_user":
                wp.status = "pending_payment"
                save_fields.append("status")
            wp.save(update_fields=save_fields)
            updated += 1

        prod_totals.pop(key, None)  # ya atendido

    # Crea los que faltan (incluye negativos)
    if create_missing and prod_totals:
        to_create = [
            WeeklyPayment(
                technician_id=tech_id,
                week=w,
                amount=total,
                status="pending_user",
            )
            for (tech_id, w), total in prod_totals.items()
            if (not week) or (w == week)
        ]
        WeeklyPayment.objects.bulk_create(to_create, ignore_conflicts=True)
        created = len(to_create)

    return {"updated": updated, "deleted": deleted, "created": created}


# ================================ ADMIN / PM ================================ #


# imports (arriba de views.py)


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@never_cache
def admin_weekly_payments(request):
    """
    Pagos semanales:
    - TOP: semana actual (no pagados). Se sincroniza creando faltantes.
    - Bottom (Paid): historial con filtros, paginación y desglose por Project ID/Subtotal.
    Incluye DESCUENTOS (subtotales negativos) en los desgloses por proyecto.
    """
    # Semana ISO actual
    y, w, _ = timezone.localdate().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    # 🔧 Sincroniza SOLO la semana actual y CREA faltantes con producción (puede ser negativa)
    _sync_weekly_totals(week=current_week, create_missing=True)

    # ------------------ TOP (This week) ------------------
    top_qs = (
        WeeklyPayment.objects
        .filter(week=current_week)
        .exclude(status="paid")
        .select_related("technician")
        .order_by("status", "technician__first_name", "technician__last_name")
    )
    top = list(top_qs)  # para adjuntar atributos

    # Desglose por proyecto para TOP  ✅ incluye descuentos
    tech_ids_top = {wp.technician_id for wp in top}
    details_map_top = {}
    if tech_ids_top:
        det = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id__in=tech_ids_top,
                item__sesion__semana_pago_real=current_week,
            )
            # ← incluye descuentos
            .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
            .values(
                "tecnico_id",
                "item__sesion__semana_pago_real",
                project_id=F("item__sesion__proyecto_id"),
            )
            .annotate(subtotal=Sum("subtotal"))
            .order_by("project_id")
        )
        for r in det:
            key = (r["tecnico_id"], r["item__sesion__semana_pago_real"])
            details_map_top.setdefault(key, []).append(
                {"project_id": r["project_id"],
                    "subtotal": r["subtotal"] or Decimal("0")}
            )
    for wp in top:
        wp.details = details_map_top.get((wp.technician_id, wp.week), [])

    # ------------------ Helpers para normalizar semana (historial) ------------------
    def _norm_week_input(raw: str) -> str:
        s = (raw or "").strip().upper()
        if not s:
            return ""
        # Acepta: 34 / W34 / 2025-W34 / 2025w34
        m_year = re.match(r"^(\d{4})[- ]?W?(\d{1,2})$", s)
        if m_year:
            yy = int(m_year.group(1))
            ww = int(m_year.group(2))
            return f"{yy}-W{ww:02d}"
        m_now = re.match(r"^W?(\d{1,2})$", s)
        if m_now:
            ww = int(m_now.group(1))
            return f"{y}-W{ww:02d}"
        return s

    # ------------------ Filtros GET (historial pagado) ------------------
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_paid_week_input = (request.GET.get("f_paid_week") or "").strip()
    f_receipt = (request.GET.get("f_receipt")
                 or "").strip()   # "", "with", "without"

    f_week = _norm_week_input(f_week_input)
    f_paid_week = _norm_week_input(f_paid_week_input)

    bottom_qs = WeeklyPayment.objects.filter(
        status="paid").select_related("technician")

    if f_tech:
        bottom_qs = bottom_qs.filter(
            Q(technician__first_name__icontains=f_tech) |
            Q(technician__last_name__icontains=f_tech) |
            Q(technician__username__icontains=f_tech)
        )
    if f_week:
        bottom_qs = bottom_qs.filter(week=f_week)
    if f_paid_week:
        bottom_qs = bottom_qs.filter(paid_week=f_paid_week)
    if f_receipt == "with":
        bottom_qs = bottom_qs.exclude(Q(receipt__isnull=True) | Q(receipt=""))
    elif f_receipt == "without":
        bottom_qs = bottom_qs.filter(Q(receipt__isnull=True) | Q(receipt=""))

    bottom_qs = bottom_qs.order_by(
        "-paid_week", "-week",
        "technician__first_name", "technician__last_name"
    )

    # ------------------ Paginación (historial pagado) ------------------
    cantidad = (request.GET.get("cantidad") or "10").strip().lower()
    page_number = request.GET.get("page") or "1"

    if cantidad == "todos":
        pagina = list(bottom_qs)  # renderizará como lista
    else:
        try:
            per_page = max(1, min(100, int(cantidad)))
        except ValueError:
            per_page = 10
            cantidad = "10"
        paginator = Paginator(bottom_qs, per_page)
        pagina = paginator.get_page(page_number)

    # ===== Desglose para el HISTORIAL (Paid)  ✅ incluye descuentos =====
    wp_list = list(pagina) if not isinstance(pagina, list) else pagina
    tech_ids_bottom = {wp.technician_id for wp in wp_list}
    weeks_bottom = {wp.week for wp in wp_list}

    details_map_bottom = {}
    if tech_ids_bottom and weeks_bottom:
        det_b = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id__in=tech_ids_bottom,
                item__sesion__semana_pago_real__in=weeks_bottom,
            )
            # ← incluye descuentos
            .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
            .values(
                "tecnico_id",
                "item__sesion__semana_pago_real",
                project_id=F("item__sesion__proyecto_id"),
            )
            .annotate(subtotal=Sum("subtotal"))
            .order_by("item__sesion__semana_pago_real", "project_id")
        )
        for r in det_b:
            key = (r["tecnico_id"], r["item__sesion__semana_pago_real"])
            details_map_bottom.setdefault(key, []).append(
                {"project_id": r["project_id"],
                    "subtotal": r["subtotal"] or Decimal("0")}
            )
    for wp in wp_list:
        wp.details = details_map_bottom.get((wp.technician_id, wp.week), [])

    # Querystring para mantener filtros en la paginación
    keep = {
        "f_tech": f_tech,
        "f_week": f_week_input,
        "f_paid_week": f_paid_week_input,
        "f_receipt": f_receipt,
        "cantidad": cantidad,
    }
    filters_qs = urlencode({k: v for k, v in keep.items() if v})

    return render(request, "operaciones/pagos_admin_list.html", {
        "current_week": current_week,

        # TOP (pendientes de esta semana) con details adjuntos
        "top": top,

        # Historial pagado (cada objeto ya trae .details)
        "pagina": pagina,
        "cantidad": cantidad,
        "filters_qs": filters_qs,

        # valores de filtros para inputs
        "f_tech": f_tech,
        "f_week_input": f_week_input,
        "f_paid_week_input": f_paid_week_input,
        "f_receipt": f_receipt,
    })


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@require_POST
@transaction.atomic
def admin_unpay(request, pk: int):
    """
    Quita el comprobante y mueve el registro a 'pending_payment'.
    Mantiene el historial (no borra el registro).
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)

    if wp.status != "paid":
        messages.info(request, "Only PAID items can be reverted.")
        return redirect("operaciones:admin_weekly_payments")

    # borra archivo del storage (si existe) sin guardar el modelo todavía
    try:
        if wp.receipt:
            wp.receipt.delete(save=False)
    except Exception:
        # no interrumpir si no se pudo borrar físicamente
        pass

    wp.receipt = None
    wp.paid_week = ""
    wp.status = "pending_payment"
    wp.save(update_fields=["receipt", "paid_week", "status", "updated_at"])

    messages.success(request, "Payment reverted. It is now pending again.")
    return redirect("operaciones:admin_weekly_payments")


def _is_admin(user) -> bool:
    # Adecuado a tu modelo de usuario
    return getattr(user, "rol", "") == "admin" or getattr(user, "is_superuser", False)


def _session_is_paid_locked(sesion) -> bool:
    """
    Queda bloqueada si existe al menos un WeeklyPayment en estado 'paid'
    para (técnico de la sesión, semana real de la sesión).
    """
    week = (sesion.semana_pago_real or "").upper()
    if not week:
        return False
    tech_ids = list(sesion.tecnicos_sesion.values_list(
        "tecnico_id", flat=True))
    if not tech_ids:
        return False
    return WeeklyPayment.objects.filter(
        week=week, technician_id__in=tech_ids, status="paid"
    ).exists()


@login_required
@require_POST
def billing_set_real_week(request, pk: int):
    """
    Actualiza 'semana_pago_real' de una SesionBilling.
    - Si hay pagos PAID relacionados, SOLO admin puede modificar.
    - Re-sincroniza totales semanales alrededor del cambio.
    """
    sesion = get_object_or_404(SesionBilling, pk=pk)
    new_week = (request.POST.get("week") or "").strip().upper()
    if not new_week:
        return JsonResponse({"ok": False, "error": "MISSING_WEEK"}, status=400)

    is_admin = _is_admin(request.user)

    # ¿Bloqueada por pagos 'PAID'?
    if _session_is_paid_locked(sesion) and not is_admin:
        return JsonResponse({
            "ok": False,
            "error": "LOCKED_PAID",
            "message": "This session has PAID weekly payments. Only admins can change the real pay week."
        }, status=403)

    old_week = (sesion.semana_pago_real or "").upper()
    sesion.semana_pago_real = new_week
    sesion.save(update_fields=["semana_pago_real", "updated_at"])

    # Re-sincroniza los totales semanales de ambas semanas
    try:
        if old_week:
            _sync_weekly_totals(week=old_week)
        _sync_weekly_totals(week=new_week)
    except Exception:
        pass

    return JsonResponse({"ok": True, "week": new_week})


@require_POST
def presign_receipt(request, pk: int):
    """
    Presigned POST directo a Wasabi (path-style):
    - Sin Content-Type en condiciones (evita mismatches).
    - success_action_status=201.
    - Fuerza URL path-style: https://s3.<region>.wasabisys.com/<bucket>
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)

    filename = request.POST.get("filename") or "receipt"
    _base, ext = os.path.splitext(filename)
    ext = (ext or ".pdf").lower()

    key = f"operaciones/pagos/{wp.week}/{wp.technician_id}/receipt_{uuid4().hex}{ext}"

    s3 = _s3_client()
    fields = {
        "acl": "private",
        "success_action_status": "201",
    }
    conditions = [
        {"acl": "private"},
        {"success_action_status": "201"},
        ["content-length-range", 0, 25 * 1024 * 1024],
        # NOTA: no metemos Content-Type en conditions para evitar CORS/preflight raros
    ]

    post = s3.generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=600,
    )

    # 👇 Forzar URL path-style (algunos entornos devuelven virtual-hosted)
    endpoint = settings.AWS_S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    post["url"] = f"{endpoint}/{bucket}"

    return JsonResponse({"post": post, "key": key})


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@transaction.atomic
def confirm_receipt(request, pk: int):
    """
    Confirma la subida directa: guarda key en FileField y marca 'paid'.
    No re-sube el archivo; solo enlaza el objeto S3 ya subido.
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)
    key = request.POST.get("key")
    if not key:
        return HttpResponseBadRequest("Missing key")

    if wp.status not in ("approved_user", "pending_payment"):
        messages.error(request, "This item is not approved by the worker yet.")
        return redirect("operaciones:admin_weekly_payments")

    # Enlaza el objeto subido en S3 (Wasabi)
    wp.receipt.name = key
    y, w, _ = timezone.localdate().isocalendar()
    wp.paid_week = f"{y}-W{int(w):02d}"
    wp.status = "paid"
    wp.save(update_fields=["receipt", "paid_week", "status", "updated_at"])

    messages.success(request, "Payment marked as PAID.")
    return redirect("operaciones:admin_weekly_payments")


# (Opcional) Respaldo de flujo clásico con multipart a Django
@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@transaction.atomic
def admin_mark_paid(request, pk: int):
    """
    Alternativa si no quieres presigned: sube via Django, guarda y marca 'paid'.
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)
    if wp.status not in ("approved_user", "pending_payment"):
        messages.error(request, "This item is not approved by the worker yet.")
        return redirect("operaciones:admin_weekly_payments")

    form = PaymentMarkPaidForm(request.POST, request.FILES, instance=wp)
    if not form.is_valid():
        messages.error(request, "Receipt is required.")
        return redirect("operaciones:admin_weekly_payments")

    form.save()  # guarda receipt en Wasabi via DEFAULT_FILE_STORAGE
    y, w, _ = timezone.localdate().isocalendar()
    wp.paid_week = f"{y}-W{int(w):02d}"
    wp.status = "paid"
    wp.save(update_fields=["paid_week", "status", "updated_at"])

    messages.success(request, "Payment marked as PAID.")
    return redirect("operaciones:admin_weekly_payments")


# ================================= USUARIO ================================= #


@login_required
@never_cache
def user_weekly_payments(request):
    """
    Vista del trabajador:
    - Sincroniza sus registros (sin crear nuevos).
    - Lista sus WeeklyPayment.
    - Adjunta 'details' = [(project_id, subtotal), ...] por cada (week).
    """
    from django.db.models import Sum, F

    # sincroniza SOLO este técnico, sin crear weeklies
    sync_weekly_totals_no_create(technician_id=request.user.id)

    y, w, _ = timezone.localdate().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    # ¿Existe producción aprobada (>0) para (tecnico, week)?
    prod_exists = (
        ItemBillingTecnico.objects
        .filter(tecnico_id=request.user.id,
                item__sesion__semana_pago_real=OuterRef("week"),
                item__sesion__estado__in=ESTADOS_OK)
        .values("tecnico_id")
        .annotate(total=Sum("subtotal"))
        .filter(total__gt=0)
    )

    # Borra huérfanos (no paid) que no tengan producción vigente
    (WeeklyPayment.objects
        .filter(technician=request.user)
        .annotate(has_prod=Exists(prod_exists))
        .filter(has_prod=False)
        .exclude(status="paid")
        .delete())

    # Lista solo los que sí tienen producción vigente
    mine_qs = (
        WeeklyPayment.objects
        .filter(technician=request.user)
        .annotate(has_prod=Exists(prod_exists))
        .filter(has_prod=True)
        .select_related("technician")
        .order_by("-week")
    )
    mine = list(mine_qs)

    # Desglose por proyecto para las semanas visibles del usuario
    weeks = {wp.week for wp in mine}
    details_map = {}
    if weeks:
        det = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id=request.user.id,
                item__sesion__semana_pago_real__in=weeks,
                item__sesion__estado__in=ESTADOS_OK,
            )
            .values(
                "item__sesion__semana_pago_real",
                project_id=F("item__sesion__proyecto_id"),
            )
            .annotate(subtotal=Sum("subtotal"))
            .order_by("item__sesion__semana_pago_real", "project_id")
        )
        for r in det:
            key = r["item__sesion__semana_pago_real"]
            details_map.setdefault(key, []).append(
                {"project_id": r["project_id"], "subtotal": r["subtotal"] or 0}
            )

    # Adjunta details a cada fila
    for wp in mine:
        wp.details = details_map.get(wp.week, [])

    return render(request, "operaciones/pagos_user_list.html", {
        "current_week": current_week,
        "mine": mine,
        "approve_form": PaymentApproveForm(),
        "reject_form": PaymentRejectForm(),
    })


@login_required
@transaction.atomic
def user_approve_payment(request, pk: int):
    wp = get_object_or_404(WeeklyPayment, pk=pk, technician=request.user)

    if wp.status != "pending_user":
        messages.info(
            request, "You can only approve when status is 'Pending my approval'.")
        return redirect("operaciones:user_weekly_payments")

    wp.reject_reason = ""
    wp.status = "pending_payment"  # aprobado -> queda esperando pago
    wp.save(update_fields=["status", "reject_reason", "updated_at"])

    messages.success(request, "Amount approved. Waiting for payment.")
    return redirect("operaciones:user_weekly_payments")


@login_required
@transaction.atomic
def user_reject_payment(request, pk: int):
    wp = get_object_or_404(WeeklyPayment, pk=pk, technician=request.user)

    if wp.status != "pending_user":
        messages.info(
            request, "You can only reject when status is 'Pending my approval'.")
        return redirect("operaciones:user_weekly_payments")

    form = PaymentRejectForm(request.POST, instance=wp)
    if not form.is_valid():
        messages.error(request, "Please provide a reason.")
        return redirect("operaciones:user_weekly_payments")

    wp = form.save(commit=False)
    wp.status = "rejected_user"
    wp.save(update_fields=["status", "reject_reason", "updated_at"])

    messages.success(request, "Amount rejected. Your reason is visible now.")
    return redirect("operaciones:user_weekly_payments")


def admin_reset_payment_status(request, pk: int):
    """
    Vuelve un registro RECHAZADO a 'pending_user' para que el técnico lo vuelva a aprobar.
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)

    if wp.status != "rejected_user":
        messages.info(
            request, "Only items rejected by the worker can be reset.")
        return redirect("operaciones:admin_weekly_payments")

    wp.status = "pending_user"
    wp.reject_reason = ""  # si prefieres conservar el motivo, comenta esta línea
    wp.save(update_fields=["status", "reject_reason", "updated_at"])

    messages.success(request, "Status reset to 'Pending worker approval'.")
    return redirect("operaciones:admin_weekly_payments")


def _recalcular_items_sesion(sesion: SesionBilling):
    ids = list(sesion.tecnicos_sesion.values_list("tecnico_id", flat=True))
    partes = list(sesion.tecnicos_sesion.values_list("porcentaje", flat=True))
    total_tec = Decimal("0.00")
    for it in sesion.items.all():
        it.desglose_tecnico.all().delete()
        sub = Decimal("0.00")
        for tid, pct in zip(ids, partes):
            base = _tarifa_tecnico(
                tid, sesion.cliente, sesion.ciudad, sesion.proyecto, sesion.oficina, it.codigo_trabajo
            )
            efectiva = money(base * (pct / Decimal("100")))
            subtotal = money(efectiva * it.cantidad)
            ItemBillingTecnico.objects.create(
                item=it,
                tecnico_id=tid,
                tarifa_base=base,
                porcentaje=pct,
                tarifa_efectiva=efectiva,
                subtotal=subtotal,
            )
            sub += subtotal
        it.subtotal_tecnico = sub
        it.save(update_fields=["subtotal_tecnico"])
        total_tec += sub
    sesion.subtotal_tecnico = money(total_tec)
    sesion.save(update_fields=["subtotal_tecnico"])

# ===== Búsquedas / AJAX =====


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
    return {
        "tipo_trabajo": p.tipo_trabajo,
        "descripcion": p.descripcion,
        "unidad_medida": p.unidad_medida
    }


@login_required
def ajax_clientes(request):
    data = list(
        PrecioActividadTecnico.objects
        .values_list("cliente", flat=True)
        .distinct()
        .order_by("cliente")
    )
    return JsonResponse({"results": data})


@login_required
def ajax_ciudades(request):
    cliente = request.GET.get("client", "")
    data = list(
        PrecioActividadTecnico.objects.filter(cliente__iexact=cliente)
        .values_list("ciudad", flat=True)
        .distinct()
        .order_by("ciudad")
    ) if cliente else []
    return JsonResponse({"results": data})


@login_required
def ajax_proyectos(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    ok = cliente and ciudad
    data = list(
        PrecioActividadTecnico.objects.filter(
            cliente__iexact=cliente, ciudad__iexact=ciudad)
        .values_list("proyecto", flat=True)
        .distinct()
        .order_by("proyecto")
    ) if ok else []
    return JsonResponse({"results": data})


@login_required
def ajax_oficinas(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto = request.GET.get("project", "")
    ok = cliente and ciudad and proyecto
    data = list(
        PrecioActividadTecnico.objects.filter(
            cliente__iexact=cliente, ciudad__iexact=ciudad, proyecto__iexact=proyecto
        )
        .values_list("oficina", flat=True)
        .distinct()
        .order_by("oficina")
    ) if ok else []
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
        cliente__iexact=cliente, ciudad__iexact=ciudad, proyecto__iexact=proyecto, oficina__iexact=oficina or "-"
    )
    if q:
        qs = qs.filter(codigo_trabajo__istartswith=q)
    data = list(
        qs.values("codigo_trabajo", "tipo_trabajo",
                  "descripcion", "unidad_medida")
        .distinct()
        .order_by("codigo_trabajo")[:20]
    )
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


@login_required
@rol_requerido('admin', 'supervisor', 'pm', 'facturacion')
def produccion_admin(request):
    """
    Producción por técnico (vista Admin) con filtros + paginación (UX como Weekly Payments).
    Filtros: proyecto (por Project ID parcial o nombre), REAL pay week (34 / W34 / 2025-W34),
             técnico, cliente.
    Solo filtra por semana REAL: 'semana_pago_real'.
    Incluye también sesiones de DESCUENTO DIRECTO (is_direct_discount=True), aunque su estado
    operativo no esté en aprobadas.
    """
    # --- imports locales para que la función sea autocontenida ---
    import re
    from decimal import Decimal
    from urllib.parse import urlencode
    from django.db.models import Q, CharField
    from django.db.models.functions import Cast
    from django.core.paginator import Paginator
    from django.utils import timezone

    # --- helpers locales ---
    def _iso_week_str(dt):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{int(w):02d}"

    def parse_week_query(q: str):
        """
        Acepta: '34', 'w34', 'W34', '2025-W34', '2025W34'
        Retorna (exact_iso, week_token)
          - exact_iso: 'YYYY-W##' cuando viene año
          - week_token: 'W##' cuando solo viene el número
        """
        if not q:
            return (None, None)
        s = q.strip().upper().replace("WEEK", "W").replace(" ", "")
        m = re.fullmatch(r'(\d{4})-?W(\d{1,2})', s)   # 2025-W34 ó 2025W34
        if m:
            year, ww = int(m.group(1)), int(m.group(2))
            return (f"{year}-W{ww:02d}", None)
        m = re.fullmatch(r'(?:W)?(\d{1,2})', s)       # W34 ó 34
        if m:
            ww = int(m.group(1))
            return (None, f"W{ww:02d}")
        return (None, None)

    def _normalize_week_str(s: str) -> str:
        """Normaliza guiones y espacios; devuelve MAYÚSCULAS."""
        if not s:
            return ""
        s = s.replace("\u2013", "-").replace("\u2014", "-")  # – — -> -
        s = re.sub(r"\s+", "", s)
        return s.upper()

    # ---------------- configuración ----------------
    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    current_week = _iso_week_str(timezone.now())

    # ---------------- Filtros GET ----------------
    f_project = (request.GET.get("f_project") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_client = (request.GET.get("f_client") or "").strip()

    exact_week, week_token = parse_week_query(f_week_input)

    # ---------------- Query base ----------------
    # 🔴 Cambio clave: incluir descuentos directos aunque no estén en estados_ok
    qs = (
        SesionBilling.objects
        .filter(Q(estado__in=estados_ok) | Q(is_direct_discount=True))
        .prefetch_related("tecnicos_sesion__tecnico", "items__desglose_tecnico")
        .order_by("-creado_en")
        .distinct()
    )

    # ---------------- Semana REAL (único criterio de semana) ----------------
    if exact_week:
        token = exact_week.split("-", 1)[-1].upper()  # 'W##'
        qs = qs.filter(
            Q(semana_pago_real__iexact=exact_week) |
            Q(semana_pago_real__icontains=token)   # tolera 2025W34, etc.
        )
    elif week_token:
        qs = qs.filter(semana_pago_real__icontains=week_token)

    # ---------------- Otros filtros ----------------
    if f_project:
        qs = qs.annotate(proyecto_id_str=Cast('proyecto_id', CharField()))
        qs = qs.filter(
            Q(proyecto_id_str__icontains=f_project) |
            Q(proyecto__icontains=f_project)
        )

    if f_client:
        qs = qs.filter(cliente__icontains=f_client)

    # ---------------- Construcción de filas (una por técnico) ----------------
    filas = []
    for s in qs:
        for asig in s.tecnicos_sesion.all():
            tecnico = asig.tecnico

            # Filtro por técnico (por fila)
            if f_tech:
                target = f_tech.lower()
                full_name = ((tecnico.first_name or "") + " " +
                             (tecnico.last_name or "")).strip().lower()
                username = (tecnico.username or "").lower()
                if target not in full_name and target not in username:
                    continue

            detalle = []
            total_tecnico = Decimal('0')

            for it in s.items.all():
                bd = next(
                    (d for d in it.desglose_tecnico.all()
                     if getattr(d, "tecnico_id", None) == getattr(tecnico, "id", None)),
                    None
                )
                if not bd:
                    continue

                rate = bd.tarifa_efectiva if isinstance(
                    bd.tarifa_efectiva, Decimal) else Decimal(str(bd.tarifa_efectiva or 0))
                qty = it.cantidad if isinstance(
                    it.cantidad, Decimal) else Decimal(str(it.cantidad or 0))

                # ⚠️ si qty < 0 (descuento), subtotal será negativo → resta
                sub_tec = rate * qty
                total_tecnico += sub_tec

                detalle.append({
                    "codigo": it.codigo_trabajo,
                    "tipo": it.tipo_trabajo,
                    "desc": it.descripcion,
                    "uom": it.unidad_medida,
                    "qty": it.cantidad,
                    "rate_tec": rate,
                    "subtotal_tec": sub_tec,
                })

            filas.append({
                "sesion": s,
                "tecnico": tecnico,
                "project_id": s.proyecto_id,
                # Columna principal = semana REAL
                "week": s.semana_pago_real or "—",
                "status": s.estado,
                # ← NUEVO para la vista
                "is_discount": bool(getattr(s, "is_direct_discount", False)),
                "client": s.cliente,
                "city": s.ciudad,
                "project": s.proyecto,
                "office": s.oficina,
                "real_week": s.semana_pago_real or "—",
                "proj_week": s.semana_pago_proyectada or "—",
                "total_tecnico": total_tecnico,
                "detalle": detalle,
            })

    # ---------------- Filtro defensivo por semana REAL en memoria ----------------
    if exact_week or week_token:
        token = (week_token or exact_week.split("-", 1)[-1]).upper()  # 'W##'
        exact_norm = _normalize_week_str(exact_week) if exact_week else None

        def _match_row_real(r):
            rw = _normalize_week_str(r["real_week"])
            if exact_norm:
                return (rw == exact_norm) or (token in rw)
            return token in rw

        filas = [r for r in filas if _match_row_real(r)]

    # ---------------- Orden por semana real ----------------
    def bucket_key(row):
        rw = row["real_week"]
        if rw == "—":
            return (2, "ZZZ")
        if rw == current_week:
            return (0, "000")
        if rw > current_week:
            return (0, rw)
        return (1, rw)

    filas.sort(key=bucket_key)

    # ---------------- Paginación ----------------
    cantidad = request.GET.get("cantidad", "10")
    if cantidad != "todos":
        try:
            per_page = max(5, min(int(cantidad), 100))
        except ValueError:
            per_page = 10
        paginator = Paginator(filas, per_page)
        page_number = request.GET.get("page") or 1
        pagina = paginator.get_page(page_number)
    else:
        class _OnePage:
            number = 1

            @property
            def paginator(self):
                class P:
                    num_pages = 1
                return P()
            has_previous = False
            has_next = False
            object_list = filas
        pagina = _OnePage()

    # QS de filtros
    filters_dict = {
        "f_project": f_project,
        "f_week": f_week_input,
        "f_tech": f_tech,
        "f_client": f_client,
        "cantidad": cantidad,
    }
    filters_qs = urlencode({k: v for k, v in filters_dict.items() if v})

    return render(request, "operaciones/produccion_admin.html", {
        "current_week": current_week,
        "pagina": pagina,
        "cantidad": cantidad,
        "f_project": f_project,
        "f_week_input": f_week_input,
        "f_tech": f_tech,
        "f_client": f_client,
        "filters_qs": filters_qs,
    })


@login_required
@rol_requerido('admin', 'supervisor', 'pm', 'facturacion')
def Exportar_produccion_admin(request):
    """
    Exporta a Excel las columnas:
    Project ID | Real pay week | Status | Technician | Client | City | Project | Office | Technical Billing
    Respeta los filtros GET: f_project, f_week, f_tech, f_client.
    No aplica paginación: exporta todo lo filtrado.
    """
    from operaciones.models import SesionBilling  # ajusta si tu modelo está en otro módulo

    # -------- Helpers locales (idénticos al criterio de la vista) --------
    def _iso_week_str(dt):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{int(w):02d}"

    def parse_week_query(q: str):
        """
        Acepta: '34', 'w34', 'W34', '2025-W34', '2025W34'
        Retorna (exact_iso, week_token)
        """
        if not q:
            return (None, None)
        s = q.strip().upper().replace("WEEK", "W").replace(" ", "")
        m = re.fullmatch(r'(\d{4})-?W(\d{1,2})', s)   # 2025-W34 ó 2025W34
        if m:
            year, ww = int(m.group(1)), int(m.group(2))
            return (f"{year}-W{ww:02d}", None)
        m = re.fullmatch(r'(?:W)?(\d{1,2})', s)       # W34 ó 34
        if m:
            ww = int(m.group(1))
            return (None, f"W{ww:02d}")
        return (None, None)

    def _normalize_week_str(s: str) -> str:
        if not s:
            return ""
        s = s.replace("\u2013", "-").replace("\u2014", "-")  # – — -> -
        s = re.sub(r"\s+", "", s)
        return s.upper()

    # -------- Filtros GET --------
    f_project = (request.GET.get("f_project") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_client = (request.GET.get("f_client") or "").strip()

    exact_week, week_token = parse_week_query(f_week_input)
    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    current_week = _iso_week_str(timezone.now())

    # -------- Query base (incluye descuentos directos) --------
    qs = (
        SesionBilling.objects
        .filter(Q(estado__in=estados_ok) | Q(is_direct_discount=True))
        .prefetch_related("tecnicos_sesion__tecnico", "items__desglose_tecnico")
        .order_by("-creado_en")
        .distinct()
    )

    # Semana REAL
    if exact_week:
        token = exact_week.split("-", 1)[-1].upper()  # 'W##'
        qs = qs.filter(
            Q(semana_pago_real__iexact=exact_week) |
            Q(semana_pago_real__icontains=token)
        )
    elif week_token:
        qs = qs.filter(semana_pago_real__icontains=week_token)

    # Project
    if f_project:
        qs = qs.annotate(proyecto_id_str=Cast('proyecto_id', CharField()))
        qs = qs.filter(
            Q(proyecto_id_str__icontains=f_project) |
            Q(proyecto__icontains=f_project)
        )

    # Client
    if f_client:
        qs = qs.filter(cliente__icontains=f_client)

    # -------- Construcción de filas (una por técnico) --------
    filas = []
    for s in qs:
        for asig in s.tecnicos_sesion.all():
            tecnico = asig.tecnico

            # Filtrar por técnico
            if f_tech:
                target = f_tech.lower()
                full_name = ((tecnico.first_name or "") + " " +
                             (tecnico.last_name or "")).strip().lower()
                username = (tecnico.username or "").lower()
                if target not in full_name and target not in username:
                    continue

            total_tecnico = Decimal('0')
            for it in s.items.all():
                bd = next((d for d in it.desglose_tecnico.all()
                           if getattr(d, "tecnico_id", None) == getattr(tecnico, "id", None)), None)
                if not bd:
                    continue
                rate = bd.tarifa_efectiva if isinstance(
                    bd.tarifa_efectiva, Decimal) else Decimal(str(bd.tarifa_efectiva or 0))
                qty = it.cantidad if isinstance(
                    it.cantidad, Decimal) else Decimal(str(it.cantidad or 0))
                total_tecnico += (rate * qty)

            filas.append({
                "project_id": s.proyecto_id,
                "week": s.semana_pago_real or "—",
                "status": s.estado,
                "tecnico": tecnico,
                "client": s.cliente,
                "city": s.ciudad,
                "project": s.proyecto,
                "office": s.oficina,
                "real_week": s.semana_pago_real or "—",
                "total_tecnico": total_tecnico,
            })

    # Filtro defensivo por semana en memoria
    if exact_week or week_token:
        token = (week_token or exact_week.split("-", 1)[-1]).upper()
        exact_norm = _normalize_week_str(exact_week) if exact_week else None

        def _match_row_real(r):
            rw = _normalize_week_str(r["real_week"])
            if exact_norm:
                return (rw == exact_norm) or (token in rw)
            return token in rw

        filas = [r for r in filas if _match_row_real(r)]

    # Orden igual que en la vista
    def bucket_key(row):
        rw = row["real_week"]
        if rw == "—":
            return (2, "ZZZ")
        if rw == current_week:
            return (0, "000")
        if rw > current_week:
            return (0, rw)
        return (1, rw)
    filas.sort(key=bucket_key)

    # -------- Generar Excel --------
    wb = Workbook()
    ws = wb.active
    ws.title = "Production"

    headers = [
        "Project ID", "Real pay week", "Status", "Technician",
        "Client", "City", "Project", "Office", "Technical Billing"
    ]
    ws.append(headers)

    for r in filas:
        tech = r["tecnico"]
        try:
            tech_name = tech.get_full_name() or tech.username
        except Exception:
            tech_name = getattr(tech, "username", "") or ""

        ws.append([
            r.get("project_id", ""),
            r.get("week", "") or r.get("real_week", ""),
            r.get("status", ""),
            tech_name,
            r.get("client", ""),
            r.get("city", ""),
            r.get("project", ""),
            r.get("office", ""),
            float(r.get("total_tecnico") or 0.0),
        ])

    # Auto ancho + formato numérico en la última columna
    for col in ws.columns:
        max_len = 0
        letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                max_len = max(max_len, len(str(cell.value or "")))
            except Exception:
                pass
        ws.column_dimensions[letter].width = min(max(10, max_len + 2), 50)

    last_col = len(headers)
    for col_cells in ws.iter_cols(min_col=last_col, max_col=last_col, min_row=2, values_only=False):
        for c in col_cells:
            c.number_format = '#,##0.00'

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = 'attachment; filename=\"production_export.xlsx\"'
    wb.save(resp)
    return resp


@login_required
@rol_requerido('usuario')
def produccion_usuario(request):
    """
    Producción del técnico logueado.
    Incluye:
      - sesiones aprobadas (Supervisor/PM/Finanzas)
      - descuentos directos (is_direct_discount=True)
      - sesiones con alguna línea negativa para el técnico
    """
    from decimal import Decimal
    tecnico = request.user

    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    qs = (
        SesionBilling.objects
        .filter(items__desglose_tecnico__tecnico=tecnico)
        .filter(
            Q(estado__in=estados_ok)
            | Q(is_direct_discount=True)
            | Q(items__desglose_tecnico__subtotal__lt=0)
        )
        .prefetch_related("items__desglose_tecnico")
        .order_by("-creado_en")
        .distinct()
    )

    # Semana ISO YYYY-W##
    def _iso_week_str(dt):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{int(w):02d}"

    current_week = _iso_week_str(timezone.now())

    # Filtro por semana REAL: "all" o "YYYY-W##"
    week_filter = (request.GET.get("week") or "all").strip()
    weeks_wanted = None if week_filter.lower() == "all" else {
        week_filter.upper()}

    filas = []
    total_semana_actual = Decimal("0")

    for s in qs:
        rw = (s.semana_pago_real or "").upper()
        if weeks_wanted is not None and rw not in weeks_wanted:
            continue

        detalle = []
        total_tecnico = Decimal("0")
        tiene_linea_negativa = False

        for it in s.items.all():
            # desglose del ítem para ESTE técnico
            bd = next(
                (d for d in it.desglose_tecnico.all()
                 if getattr(d, "tecnico_id", None) == getattr(tecnico, "id", None)),
                None
            )
            if not bd:
                continue

            rate = bd.tarifa_efectiva if isinstance(
                bd.tarifa_efectiva, Decimal) else Decimal(str(bd.tarifa_efectiva or 0))
            qty = it.cantidad if isinstance(
                it.cantidad,        Decimal) else Decimal(str(it.cantidad or 0))

            sub_tec = rate * qty
            total_tecnico += sub_tec
            if sub_tec < 0:
                tiene_linea_negativa = True

            detalle.append({
                "codigo": it.codigo_trabajo,
                "tipo": it.tipo_trabajo,
                "desc": it.descripcion,
                "uom": it.unidad_medida,
                "qty": it.cantidad,
                "rate_tec": rate,
                "subtotal_tec": sub_tec,
            })

        if not detalle:
            continue

        # Marca de descuento para la UI
        is_discount_row = bool(
            getattr(s, "is_direct_discount", False) or tiene_linea_negativa)

        if rw == current_week:
            total_semana_actual += total_tecnico  # incluye negativos

        filas.append({
            "sesion": s,
            "project_id": s.proyecto_id,
            "week": s.semana_pago_proyectada or "—",
            # si es descuento mostramos "descuento" en lugar del estado real
            "status": ("descuento" if is_discount_row else s.estado),
            "is_discount": is_discount_row,
            "client": s.cliente,
            "city": s.ciudad,
            "project": s.proyecto,
            "office": s.oficina,
            "real_week": s.semana_pago_real or "—",
            "total_tecnico": total_tecnico,   # puede ser negativo
            "detalle": detalle,
        })

    # Orden: actual, futuras, pasadas, sin semana
    def bucket_key(row):
        rw = row["real_week"]
        if rw == "—":
            return (2, "ZZZ")
        if rw == current_week:
            return (0, "000")
        if rw > current_week:
            return (0, rw)
        return (1, rw)

    filas.sort(key=bucket_key)

    return render(request, "operaciones/produccion_usuario.html", {
        "filas": filas,
        "current_week": current_week,
        "total_semana_actual": total_semana_actual,
        "week_filter": week_filter,
    })


def _s3_client():
    """
    Wasabi S3 en path-style para evitar problemas de CORS/SSL.
    Usa el endpoint REGIONAL del bucket (p.ej. us-east-1).
    """
    return boto3.client(
        "s3",
        endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL",
                             "https://s3.us-east-1.wasabisys.com"),
        region_name=getattr(settings, "AWS_S3_REGION_NAME", "us-east-1"),
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", s3={
                      "addressing_style": "path"}),
        verify=getattr(settings, "AWS_S3_VERIFY", True),
    )


ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}


@transaction.atomic
def _sync_weekly_totals(week: str | None = None, create_missing: bool = False) -> dict:
    """
    Sincroniza WeeklyPayment con la producción (incluye descuentos):
    - Actualiza amount si cambió (approved_user -> pending_payment).
    - Elimina registros sin producción (== 0) si status != 'paid'.
    - [opcional] Crea los que faltan cuando create_missing=True (pueden ser negativos).
    """
    # ✅ Incluir DESCUENTOS: OR subtotal__lt=0
    base = (
        ItemBillingTecnico.objects
        .filter(item__sesion__semana_pago_real__gt="")
        .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
    )
    if week:
        base = base.filter(item__sesion__semana_pago_real=week)

    agg = (
        base.values("tecnico_id", "item__sesion__semana_pago_real")
        .annotate(total=Sum("subtotal"))
    )

    # 👉 No descartamos negativos. Sólo excluimos exactamente 0 para que
    #     se elimine el WP existente o no se cree uno vacío.
    prod_totals = {
        (row["tecnico_id"], row["item__sesion__semana_pago_real"]): (row["total"] or Decimal("0"))
        for row in agg
        if (row["total"] or Decimal("0")) != 0
    }

    updated = deleted = created = 0

    for wp in WeeklyPayment.objects.select_for_update():
        if week and wp.week != week:
            continue

        key = (wp.technician_id, wp.week)
        if key not in prod_totals:
            # no hay producción neta (== 0) → eliminar si no está pagado
            if wp.status != "paid":
                wp.delete()
                deleted += 1
            continue

        total = prod_totals[key]
        if wp.amount != total:
            wp.amount = total
            save_fields = ["amount", "updated_at"]
            if wp.status == "approved_user":
                wp.status = "pending_payment"
                save_fields.append("status")
            wp.save(update_fields=save_fields)
            updated += 1

        prod_totals.pop(key, None)  # ya atendido

    # Crea los que faltan (incluye negativos)
    if create_missing and prod_totals:
        to_create = [
            WeeklyPayment(
                technician_id=tech_id,
                week=w,
                amount=total,
                status="pending_user",
            )
            for (tech_id, w), total in prod_totals.items()
            if (not week) or (w == week)
        ]
        WeeklyPayment.objects.bulk_create(to_create, ignore_conflicts=True)
        created = len(to_create)

    return {"updated": updated, "deleted": deleted, "created": created}


# ================================ ADMIN / PM ================================ #


# imports (arriba de views.py)


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@never_cache
def admin_weekly_payments(request):
    """
    Pagos semanales:
    - TOP: semana actual (no pagados). Se sincroniza creando faltantes.
    - Bottom (Paid): historial con filtros, paginación y desglose por Project ID/Subtotal.
    Incluye DESCUENTOS (subtotales negativos) en los desgloses por proyecto.
    """
    import re
    from decimal import Decimal
    from urllib.parse import urlencode
    from django.db.models import Q, F, Sum

    # Semana ISO actual
    y, w, _ = timezone.localdate().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    # 🔧 Sincroniza SOLO la semana actual y CREA faltantes (pueden ser negativos)
    _sync_weekly_totals(week=current_week, create_missing=True)

    # ------------------ TOP (This week) ------------------
    # ✅ Solo mostrar registros con amount > 0 (si es 0 o negativo NO se paga)
    top_qs = (
        WeeklyPayment.objects
        .filter(week=current_week, amount__gt=0)     # ← CAMBIO CLAVE
        .exclude(status="paid")
        .select_related("technician")
        .order_by("status", "technician__first_name", "technician__last_name")
    )
    top = list(top_qs)

    # Desglose por proyecto para TOP  ✅ incluye descuentos (para referencia)
    tech_ids_top = {wp.technician_id for wp in top}
    details_map_top = {}
    if tech_ids_top:
        det = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id__in=tech_ids_top,
                item__sesion__semana_pago_real=current_week,
            )
            .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
            .values(
                "tecnico_id",
                "item__sesion__semana_pago_real",
                project_id=F("item__sesion__proyecto_id"),
            )
            .annotate(subtotal=Sum("subtotal"))
            .order_by("project_id")
        )
        for r in det:
            key = (r["tecnico_id"], r["item__sesion__semana_pago_real"])
            details_map_top.setdefault(key, []).append(
                {"project_id": r["project_id"],
                    "subtotal": r["subtotal"] or Decimal("0")}
            )
    for wp in top:
        wp.details = details_map_top.get((wp.technician_id, wp.week), [])

    # ------------------ Helpers para normalizar semana (historial) ------------------
    def _norm_week_input(raw: str) -> str:
        s = (raw or "").strip().upper()
        if not s:
            return ""
        m_year = re.match(r"^(\d{4})[- ]?W?(\d{1,2})$", s)
        if m_year:
            yy = int(m_year.group(1))
            ww = int(m_year.group(2))
            return f"{yy}-W{ww:02d}"
        m_now = re.match(r"^W?(\d{1,2})$", s)
        if m_now:
            ww = int(m_now.group(1))
            return f"{y}-W{ww:02d}"
        return s

    # ------------------ Filtros GET (historial pagado) ------------------
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_paid_week_input = (request.GET.get("f_paid_week") or "").strip()
    f_receipt = (request.GET.get("f_receipt")
                 or "").strip()   # "", "with", "without"

    f_week = _norm_week_input(f_week_input)
    f_paid_week = _norm_week_input(f_paid_week_input)

    bottom_qs = WeeklyPayment.objects.filter(
        status="paid").select_related("technician")

    if f_tech:
        bottom_qs = bottom_qs.filter(
            Q(technician__first_name__icontains=f_tech) |
            Q(technician__last_name__icontains=f_tech) |
            Q(technician__username__icontains=f_tech)
        )
    if f_week:
        bottom_qs = bottom_qs.filter(week=f_week)
    if f_paid_week:
        bottom_qs = bottom_qs.filter(paid_week=f_paid_week)
    if f_receipt == "with":
        bottom_qs = bottom_qs.exclude(Q(receipt__isnull=True) | Q(receipt=""))
    elif f_receipt == "without":
        bottom_qs = bottom_qs.filter(Q(receipt__isnull=True) | Q(receipt=""))

    bottom_qs = bottom_qs.order_by(
        "-paid_week", "-week",
        "technician__first_name", "technician__last_name"
    )

    # ------------------ Paginación (historial pagado) ------------------
    cantidad = (request.GET.get("cantidad") or "10").strip().lower()
    page_number = request.GET.get("page") or "1"

    if cantidad == "todos":
        pagina = list(bottom_qs)
    else:
        try:
            per_page = max(1, min(100, int(cantidad)))
        except ValueError:
            per_page = 10
            cantidad = "10"
        paginator = Paginator(bottom_qs, per_page)
        pagina = paginator.get_page(page_number)

    # ===== Desglose para el HISTORIAL (Paid)  ✅ incluye descuentos =====
    wp_list = list(pagina) if not isinstance(pagina, list) else pagina
    tech_ids_bottom = {wp.technician_id for wp in wp_list}
    weeks_bottom = {wp.week for wp in wp_list}

    details_map_bottom = {}
    if tech_ids_bottom and weeks_bottom:
        det_b = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id__in=tech_ids_bottom,
                item__sesion__semana_pago_real__in=weeks_bottom,
            )
            .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
            .values(
                "tecnico_id",
                "item__sesion__semana_pago_real",
                project_id=F("item__sesion__proyecto_id"),
            )
            .annotate(subtotal=Sum("subtotal"))
            .order_by("item__sesion__semana_pago_real", "project_id")
        )
        for r in det_b:
            key = (r["tecnico_id"], r["item__sesion__semana_pago_real"])
            details_map_bottom.setdefault(key, []).append(
                {"project_id": r["project_id"],
                    "subtotal": r["subtotal"] or Decimal("0")}
            )
    for wp in wp_list:
        wp.details = details_map_bottom.get((wp.technician_id, wp.week), [])

    # Querystring para mantener filtros en la paginación
    keep = {
        "f_tech": f_tech,
        "f_week": f_week_input,
        "f_paid_week": f_paid_week_input,
        "f_receipt": f_receipt,
        "cantidad": cantidad,
    }
    filters_qs = urlencode({k: v for k, v in keep.items() if v})

    return render(request, "operaciones/pagos_admin_list.html", {
        "current_week": current_week,
        "top": top,                     # ← ya sin montos <= 0
        "pagina": pagina,
        "cantidad": cantidad,
        "filters_qs": filters_qs,
        "f_tech": f_tech,
        "f_week_input": f_week_input,
        "f_paid_week_input": f_paid_week_input,
        "f_receipt": f_receipt,
    })


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@require_POST
@transaction.atomic
def admin_unpay(request, pk: int):
    """
    Quita el comprobante y mueve el registro a 'pending_payment'.
    Mantiene el historial (no borra el registro).
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)

    if wp.status != "paid":
        messages.info(request, "Only PAID items can be reverted.")
        return redirect("operaciones:admin_weekly_payments")

    # borra archivo del storage (si existe) sin guardar el modelo todavía
    try:
        if wp.receipt:
            wp.receipt.delete(save=False)
    except Exception:
        # no interrumpir si no se pudo borrar físicamente
        pass

    wp.receipt = None
    wp.paid_week = ""
    wp.status = "pending_payment"
    wp.save(update_fields=["receipt", "paid_week", "status", "updated_at"])

    messages.success(request, "Payment reverted. It is now pending again.")
    return redirect("operaciones:admin_weekly_payments")


def _is_admin(user) -> bool:
    # Adecuado a tu modelo de usuario
    return getattr(user, "rol", "") == "admin" or getattr(user, "is_superuser", False)


def _session_is_paid_locked(sesion) -> bool:
    """
    Queda bloqueada si existe al menos un WeeklyPayment en estado 'paid'
    para (técnico de la sesión, semana real de la sesión).
    """
    week = (sesion.semana_pago_real or "").upper()
    if not week:
        return False
    tech_ids = list(sesion.tecnicos_sesion.values_list(
        "tecnico_id", flat=True))
    if not tech_ids:
        return False
    return WeeklyPayment.objects.filter(
        week=week, technician_id__in=tech_ids, status="paid"
    ).exists()


@login_required
@require_POST
def billing_set_real_week(request, pk: int):
    """
    Actualiza 'semana_pago_real' de una SesionBilling.
    - Si hay pagos PAID relacionados, SOLO admin puede modificar.
    - Re-sincroniza totales semanales alrededor del cambio.
    """
    sesion = get_object_or_404(SesionBilling, pk=pk)
    new_week = (request.POST.get("week") or "").strip().upper()
    if not new_week:
        return JsonResponse({"ok": False, "error": "MISSING_WEEK"}, status=400)

    is_admin = _is_admin(request.user)

    # ¿Bloqueada por pagos 'PAID'?
    if _session_is_paid_locked(sesion) and not is_admin:
        return JsonResponse({
            "ok": False,
            "error": "LOCKED_PAID",
            "message": "This session has PAID weekly payments. Only admins can change the real pay week."
        }, status=403)

    old_week = (sesion.semana_pago_real or "").upper()
    sesion.semana_pago_real = new_week
    sesion.save(update_fields=["semana_pago_real", "updated_at"])

    # Re-sincroniza los totales semanales de ambas semanas
    try:
        if old_week:
            _sync_weekly_totals(week=old_week)
        _sync_weekly_totals(week=new_week)
    except Exception:
        pass

    return JsonResponse({"ok": True, "week": new_week})


@require_POST
def presign_receipt(request, pk: int):
    """
    Presigned POST directo a Wasabi (path-style):
    - Sin Content-Type en condiciones (evita mismatches).
    - success_action_status=201.
    - Fuerza URL path-style: https://s3.<region>.wasabisys.com/<bucket>
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)

    filename = request.POST.get("filename") or "receipt"
    _base, ext = os.path.splitext(filename)
    ext = (ext or ".pdf").lower()

    key = f"operaciones/pagos/{wp.week}/{wp.technician_id}/receipt_{uuid4().hex}{ext}"

    s3 = _s3_client()
    fields = {
        "acl": "private",
        "success_action_status": "201",
    }
    conditions = [
        {"acl": "private"},
        {"success_action_status": "201"},
        ["content-length-range", 0, 25 * 1024 * 1024],
        # NOTA: no metemos Content-Type en conditions para evitar CORS/preflight raros
    ]

    post = s3.generate_presigned_post(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=600,
    )

    # 👇 Forzar URL path-style (algunos entornos devuelven virtual-hosted)
    endpoint = settings.AWS_S3_ENDPOINT_URL.rstrip("/")
    bucket = settings.AWS_STORAGE_BUCKET_NAME
    post["url"] = f"{endpoint}/{bucket}"

    return JsonResponse({"post": post, "key": key})


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@transaction.atomic
def confirm_receipt(request, pk: int):
    """
    Confirma la subida directa: guarda key en FileField y marca 'paid'.
    No re-sube el archivo; solo enlaza el objeto S3 ya subido.
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)
    key = request.POST.get("key")
    if not key:
        return HttpResponseBadRequest("Missing key")

    if wp.status not in ("approved_user", "pending_payment"):
        messages.error(request, "This item is not approved by the worker yet.")
        return redirect("operaciones:admin_weekly_payments")

    # Enlaza el objeto subido en S3 (Wasabi)
    wp.receipt.name = key
    y, w, _ = timezone.localdate().isocalendar()
    wp.paid_week = f"{y}-W{int(w):02d}"
    wp.status = "paid"
    wp.save(update_fields=["receipt", "paid_week", "status", "updated_at"])

    messages.success(request, "Payment marked as PAID.")
    return redirect("operaciones:admin_weekly_payments")


# (Opcional) Respaldo de flujo clásico con multipart a Django
@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@transaction.atomic
def admin_mark_paid(request, pk: int):
    """
    Alternativa si no quieres presigned: sube via Django, guarda y marca 'paid'.
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)
    if wp.status not in ("approved_user", "pending_payment"):
        messages.error(request, "This item is not approved by the worker yet.")
        return redirect("operaciones:admin_weekly_payments")

    form = PaymentMarkPaidForm(request.POST, request.FILES, instance=wp)
    if not form.is_valid():
        messages.error(request, "Receipt is required.")
        return redirect("operaciones:admin_weekly_payments")

    form.save()  # guarda receipt en Wasabi via DEFAULT_FILE_STORAGE
    y, w, _ = timezone.localdate().isocalendar()
    wp.paid_week = f"{y}-W{int(w):02d}"
    wp.status = "paid"
    wp.save(update_fields=["paid_week", "status", "updated_at"])

    messages.success(request, "Payment marked as PAID.")
    return redirect("operaciones:admin_weekly_payments")


# ================================= USUARIO ================================= #


@login_required
@never_cache
def user_weekly_payments(request):
    """
    Vista del trabajador:
    - Sincroniza sus registros (sin crear nuevos).
    - Lista sus WeeklyPayment.
    - Adjunta 'details' por proyecto y un display_amount por semana
      calculados desde ItemBillingTecnico (incluye descuentos).
    """
    from decimal import Decimal
    from django.db.models import Q, Exists, OuterRef, Sum, F

    # sincroniza SOLO este técnico, sin crear weeklies
    sync_weekly_totals_no_create(technician_id=request.user.id)

    y, w, _ = timezone.localdate().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    # ¿Existe producción NO CERO (positiva o negativa) para (tecnico, week)?
    prod_exists = (
        ItemBillingTecnico.objects
        .filter(
            tecnico_id=request.user.id,
            item__sesion__semana_pago_real=OuterRef("week"),
        )
        # Aprobadas O descuentos (subtotal<0)
        .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
        .values("tecnico_id")
        .annotate(total=Sum("subtotal"))
        .exclude(total=0)
    )

    # Borra huérfanos (no paid) que no tengan producción vigente
    (WeeklyPayment.objects
        .filter(technician=request.user)
        .annotate(has_prod=Exists(prod_exists))
        .filter(has_prod=False)
        .exclude(status="paid")
        .delete())

    # Lista sólo los que sí tienen producción vigente (positiva o negativa)
    mine_qs = (
        WeeklyPayment.objects
        .filter(technician=request.user)
        .annotate(has_prod=Exists(prod_exists))
        .filter(has_prod=True)
        .select_related("technician")
        .order_by("-week")
    )
    mine = list(mine_qs)

    # Desglose y total recalculado por semana (incluye descuentos)
    weeks = {wp.week for wp in mine}
    details_map: dict[str, list] = {}
    totals_map: dict[str, Decimal] = {}

    if weeks:
        det = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id=request.user.id,
                item__sesion__semana_pago_real__in=weeks,
            )
            .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
            .values(
                "item__sesion__semana_pago_real",
                project_id=F("item__sesion__proyecto_id"),
            )
            .annotate(subtotal=Sum("subtotal"))
            .order_by("item__sesion__semana_pago_real", "project_id")
        )

        for r in det:
            week = r["item__sesion__semana_pago_real"]
            sub = r["subtotal"] or Decimal("0")
            details_map.setdefault(week, []).append(
                {"project_id": r["project_id"], "subtotal": sub}
            )
            totals_map[week] = (totals_map.get(week, Decimal("0")) + sub)

    # Adjunta details y el monto a mostrar (display_amount)
    for wp in mine:
        wp.details = details_map.get(wp.week, [])
        wp.display_amount = totals_map.get(wp.week, Decimal("0"))

    return render(request, "operaciones/pagos_user_list.html", {
        "current_week": current_week,
        "mine": mine,
        "approve_form": PaymentApproveForm(),
        "reject_form": PaymentRejectForm(),
    })


@login_required
@transaction.atomic
def user_approve_payment(request, pk: int):
    wp = get_object_or_404(WeeklyPayment, pk=pk, technician=request.user)

    if wp.status != "pending_user":
        messages.info(
            request, "You can only approve when status is 'Pending my approval'.")
        return redirect("operaciones:user_weekly_payments")

    wp.reject_reason = ""
    wp.status = "pending_payment"  # aprobado -> queda esperando pago
    wp.save(update_fields=["status", "reject_reason", "updated_at"])

    messages.success(request, "Amount approved. Waiting for payment.")
    return redirect("operaciones:user_weekly_payments")


@login_required
@transaction.atomic
def user_reject_payment(request, pk: int):
    wp = get_object_or_404(WeeklyPayment, pk=pk, technician=request.user)

    if wp.status != "pending_user":
        messages.info(
            request, "You can only reject when status is 'Pending my approval'.")
        return redirect("operaciones:user_weekly_payments")

    form = PaymentRejectForm(request.POST, instance=wp)
    if not form.is_valid():
        messages.error(request, "Please provide a reason.")
        return redirect("operaciones:user_weekly_payments")

    wp = form.save(commit=False)
    wp.status = "rejected_user"
    wp.save(update_fields=["status", "reject_reason", "updated_at"])

    messages.success(request, "Amount rejected. Your reason is visible now.")
    return redirect("operaciones:user_weekly_payments")


def admin_reset_payment_status(request, pk: int):
    """
    Vuelve un registro RECHAZADO a 'pending_user' para que el técnico lo vuelva a aprobar.
    """
    wp = get_object_or_404(WeeklyPayment, pk=pk)

    if wp.status != "rejected_user":
        messages.info(
            request, "Only items rejected by the worker can be reset.")
        return redirect("operaciones:admin_weekly_payments")

    wp.status = "pending_user"
    wp.reject_reason = ""  # si prefieres conservar el motivo, comenta esta línea
    wp.save(update_fields=["status", "reject_reason", "updated_at"])

    messages.success(request, "Status reset to 'Pending worker approval'.")
    return redirect("operaciones:admin_weekly_payments")


# -------------------------------------------------------------------
# LOGGIN
# -------------------------------------------------------------------

logger = logging.getLogger("merge_xlsx")

# ===== Namespaces =====
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"        # *.rels
_NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"  # r:id en XML
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_NS_APP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
_NS_VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_XML_NS = "http://www.w3.org/XML/1998/namespace"

# ¡IMPORTANTE! 'r' = namespace DOC (para r:id en XPaths)
NS = {"m": _NS_MAIN, "r": _NS_REL_DOC}
CT = {"ct": _NS_CT}

# ===== XML helpers =====


def _read_xml(zf: zipfile.ZipFile, path: str) -> ET.Element:
    return ET.fromstring(zf.read(path))


def _write_xml(zf: zipfile.ZipFile, path: str, root: ET.Element):
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    zf.writestr(path, data)


def _fetch_to_temp(django_filefield) -> str:
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp.close()
    with django_filefield.open("rb") as fsrc, open(tmp.name, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, length=1024*1024)
    return tmp.name


def _max_index_from_paths(paths, prefix, suffix):
    mx = 0
    for p in paths:
        if p.startswith(prefix) and p.endswith(suffix):
            m = re.search(r"(\d+)", p[len(prefix):-len(suffix)])
            if m:
                mx = max(mx, int(m.group(1)))
    return mx


def _next_rid(wb_rels_root: ET.Element) -> str:
    ids = [
        int(e.attrib["Id"][3:])
        for e in wb_rels_root.findall(f".//{{{_NS_REL_PKG}}}Relationship")
        if e.attrib.get("Id", "").startswith("rId") and e.attrib["Id"][3:].isdigit()
    ]
    return f"rId{(max(ids)+1) if ids else 1}"

# ===== Content_Types helpers =====


def _read_ct(zf: zipfile.ZipFile): return ET.fromstring(
    zf.read("[Content_Types].xml"))


def _write_ct(zf: zipfile.ZipFile, root: ET.Element):
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    zf.writestr("[Content_Types].xml", data)


def _ensure_default(ct_root: ET.Element, ext: str, ctype: str):
    if ct_root.find(f".//ct:Default[@Extension='{ext}']", CT) is None:
        el = ET.SubElement(ct_root, "{%s}Default" % _NS_CT)
        el.set("Extension", ext)
        el.set("ContentType", ctype)


def _ensure_override(ct_root: ET.Element, partname: str, ctype: str):
    if ct_root.find(f".//ct:Override[@PartName='/{partname}']", CT) is None:
        el = ET.SubElement(ct_root, "{%s}Override" % _NS_CT)
        el.set("PartName", "/" + partname)
        el.set("ContentType", ctype)

# ===== .rels target normalizer =====


def _rels_target_to_zip_path(target: str) -> str:
    p = (target or "").replace("\\", "/")
    while p.startswith("../"):
        p = p[3:]
    if p.startswith("/"):
        p = p[1:]
    if not p.startswith("xl/"):
        p = "xl/" + p
    return p

# ===== sharedStrings → inlineStr =====


def _get_shared_strings(src_zip: zipfile.ZipFile):
    p = "xl/sharedStrings.xml"
    if p not in src_zip.namelist():
        return None, []
    root = _read_xml(src_zip, p)
    out = []
    for si in root.findall("{%s}si" % _NS_MAIN):
        out.append("".join((t.text or "")
                   for t in si.findall(".//{%s}t" % _NS_MAIN)))
    return root, out


def _inline_shared_strings(sheet_root: ET.Element, sst_list):
    if not sst_list:
        return
    for c in sheet_root.findall(".//m:c", NS):
        if c.get("t") != "s":
            continue
        v_el = c.find("m:v", NS)
        if v_el is None or v_el.text is None:
            c.set("t", "inlineStr")
            for ch in list(c):
                if ch.tag.endswith("v") or ch.tag.endswith("is"):
                    c.remove(ch)
            ET.SubElement(c, "{%s}is" % _NS_MAIN)
            continue
        try:
            idx = int(v_el.text)
        except:
            idx = -1
        text = sst_list[idx] if 0 <= idx < len(sst_list) else ""
        for ch in list(c):
            if ch.tag.endswith("v") or ch.tag.endswith("is"):
                c.remove(ch)
        c.set("t", "inlineStr")
        is_el = ET.SubElement(c, "{%s}is" % _NS_MAIN)
        t_el = ET.SubElement(is_el, "{%s}t" % _NS_MAIN)
        if text and (text.startswith(" ") or text.endswith(" ")):
            t_el.set("{%s}space" % _XML_NS, "preserve")
        t_el.text = text
        # NO tocamos el atributo 's' (estilo)

# ===== SOLO quitamos <extLst> (mantenemos estilos) =====


def _strip_extlst_only(sheet_root: ET.Element):
    for ch in list(sheet_root):
        if ch.tag.endswith("extLst"):
            sheet_root.remove(ch)

# ===== nombres de hoja seguros =====


def _safe_sheet_name(name: str) -> str:
    n = re.sub(r'[\\/:*?\[\]]', ' ', (name or '').strip())
    if n.startswith("'"):
        n = n[1:]
    if n.endswith("'"):
        n = n[:-1]
    n = re.sub(r'\s+', ' ', n)[:31]
    return n or 'Sheet'

# ===== app.xml =====


def _read_app_xml(zf: zipfile.ZipFile):
    p = "docProps/app.xml"
    return _read_xml(zf, p) if p in zf.namelist() else None


def _rewrite_app_xml(app_root: ET.Element, sheet_titles: list[str]):
    if app_root is None:
        return None
    app_root.set("xmlns", _NS_APP)
    app_root.set("xmlns:vt", _NS_VT)
    for tag in ("HeadingPairs", "TitlesOfParts"):
        n = app_root.find(f"{{{_NS_APP}}}{tag}")
        if n is not None:
            app_root.remove(n)
    hp = ET.SubElement(app_root, f"{{{_NS_APP}}}HeadingPairs")
    v = ET.SubElement(hp, f"{{{_NS_VT}}}vector", size="2", baseType="variant")
    var1 = ET.SubElement(v, f"{{{_NS_VT}}}variant")
    ET.SubElement(var1, f"{{{_NS_VT}}}lpstr").text = "Worksheets"
    var2 = ET.SubElement(v, f"{{{_NS_VT}}}variant")
    ET.SubElement(var2, f"{{{_NS_VT}}}i4").text = str(len(sheet_titles))
    top = ET.SubElement(app_root, f"{{{_NS_APP}}}TitlesOfParts")
    v2 = ET.SubElement(top, f"{{{_NS_VT}}}vector", size=str(
        len(sheet_titles)), baseType="lpstr")
    for nm in sheet_titles:
        ET.SubElement(v2, f"{{{_NS_VT}}}lpstr").text = nm
    return app_root

# ===== limpieza si falta .rels =====


def _strip_relationship_bound_elements(sheet_root: ET.Element):
    for xp in [".//m:drawing", ".//m:legacyDrawing", ".//m:legacyDrawingHF",
               ".//m:hyperlinks", ".//m:tableParts", ".//m:controls"]:
        for el in sheet_root.findall(xp, NS):
            try:
                sheet_root.remove(el)
            except:
                pass
    for el in sheet_root.findall(".//*[@r:id]", NS):
        try:
            el.attrib.pop("{%s}id" % _NS_REL_DOC, None)
        except:
            pass

# ===================================================================
# MERGE (con FIX de r:id y conservando estilos)
# ===================================================================


def merge_xlsx_files_preserving_images(src_paths, out_path, sheet_names=None):
    if not src_paths:
        raise ValueError("No hay archivos de entrada")
    if len(src_paths) == 1:
        shutil.copyfile(src_paths[0], out_path)
        return

    with zipfile.ZipFile(src_paths[0], "r") as base:
        existing = set(base.namelist())
        wb_xml_path = "xl/workbook.xml"
        wb_rels_path = "xl/_rels/workbook.xml.rels"
        wb_root = _read_xml(base, wb_xml_path)
        wb_rels_root = _read_xml(base, wb_rels_path)
        ct_root = _read_ct(base)
        app_root = _read_app_xml(base)

        # mínimos
        _ensure_default(
            ct_root, "rels", "application/vnd.openxmlformats-package.relationships+xml")
        _ensure_default(ct_root, "xml", "application/xml")
        _ensure_default(ct_root, "png", "image/png")
        _ensure_default(ct_root, "jpg", "image/jpeg")
        _ensure_default(ct_root, "jpeg", "image/jpeg")
        _ensure_default(
            ct_root, "vml", "application/vnd.openxmlformats-officedocument.vmlDrawing")
        _ensure_default(
            ct_root, "bin", "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings")

        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            skip = {wb_xml_path, wb_rels_path,
                    "[Content_Types].xml", "xl/calcChain.xml", "docProps/app.xml"}
            for name in existing:
                if name not in skip:
                    dst.writestr(name, base.read(name))

            used_names = set()
            sheets_node = wb_root.find("m:sheets", NS) or ET.SubElement(
                wb_root, "{%s}sheets" % _NS_MAIN)
            base_sheets = sheets_node.findall("m:sheet", NS)
            for s in base_sheets:
                nm = (s.get("name") or "").strip()
                if nm:
                    used_names.add(nm.lower())

            def unique_name(raw: str) -> str:
                base_nm = _safe_sheet_name(raw)
                name = base_nm or "Sheet"
                k = 2
                while name.lower() in used_names:
                    cut = 31 - (len(str(k))+3)
                    cut = 1 if cut < 1 else cut
                    name = f"{(base_nm or 'Sheet')[:cut]} ({k})"
                    k += 1
                used_names.add(name.lower())
                return name

            if sheet_names and len(sheet_names) >= 1 and base_sheets:
                base_sheets[0].set("name", unique_name(sheet_names[0]))

            def _idx(pref, suf): return _max_index_from_paths(
                existing, pref, suf) + 1
            next_sheet_idx = _idx("xl/worksheets/sheet", ".xml")
            next_drawing_idx = _idx("xl/drawings/drawing", ".xml")
            next_vml_idx = _idx("xl/drawings/vmlDrawing", ".vml")
            next_comments_idx = _idx("xl/comments", ".xml")
            next_table_idx = _idx("xl/tables/table", ".xml")
            next_ps_idx = _idx("xl/printerSettings/printerSettings", ".bin")

            img_nums = [int(m.group(1)) for p in existing for m in [
                re.search(r"xl/media/image(\d+)\.(?:png|jpe?g)$", p)] if m]
            chart_nums = [int(m.group(1)) for p in existing for m in [
                re.search(r"xl/charts/chart(\d+)\.xml$", p)] if m]
            next_image_idx = (max(img_nums)+1) if img_nums else 1
            next_chart_idx = (max(chart_nums)+1) if chart_nums else 1
            next_cstyle_idx = 1
            next_ccolor_idx = 1

            sheet_ids = []
            for s in wb_root.findall("m:sheets/m:sheet", NS):
                try:
                    sheet_ids.append(int(s.get("sheetId", "0")))
                except:
                    pass
            next_sheet_id = (max(sheet_ids)+1) if sheet_ids else 1

            def _add_sheet_from_src(src_path, i):
                nonlocal next_sheet_idx, next_sheet_id, next_drawing_idx, next_vml_idx
                nonlocal next_comments_idx, next_table_idx, next_ps_idx
                nonlocal next_image_idx, next_chart_idx, next_cstyle_idx, next_ccolor_idx

                with zipfile.ZipFile(src_path, "r") as src:
                    src_names = set(src.namelist())
                    if "xl/workbook.xml" not in src_names or "xl/_rels/workbook.xml.rels" not in src_names:
                        return

                    swb = _read_xml(src, "xl/workbook.xml")
                    swb_rels = _read_xml(
                        src, "xl/_rels/workbook.xml.rels")  # PACKAGE

                    src_sheets = swb.findall("m:sheets/m:sheet", NS)
                    if not src_sheets:
                        return

                    first_sheet = src_sheets[0]
                    src_rid = first_sheet.get("{%s}id" % _NS_REL_DOC) or first_sheet.get(
                        "r:id") or first_sheet.get("id")
                    src_sheet_path = None
                    if src_rid:
                        rel = swb_rels.find(
                            f".//{{{_NS_REL_PKG}}}Relationship[@Id='{src_rid}']")
                        if rel is not None:
                            t = rel.get("Target") or ""
                            p1 = "xl/"+t
                            src_sheet_path = p1 if p1 in src_names else (_rels_target_to_zip_path(
                                t) if _rels_target_to_zip_path(t) in src_names else None)
                    if not src_sheet_path:
                        cands = []
                        for p in src_names:
                            if p.startswith("xl/worksheets/sheet") and p.endswith(".xml"):
                                m = re.search(r"sheet(\d+)\.xml$", p)
                                idx = int(m.group(1)) if m else 9999
                                cands.append((idx, p))
                        if not cands:
                            return
                        cands.sort()
                        src_sheet_path = cands[0][1]

                    sheet_root = _read_xml(src, src_sheet_path)
                    _, sst_list = _get_shared_strings(src)
                    _inline_shared_strings(sheet_root, sst_list)
                    _strip_extlst_only(sheet_root)   # <<< mantenemos estilos

                    dst_sheet_name = f"worksheets/sheet{next_sheet_idx}.xml"
                    dst_sheet_path = "xl/" + dst_sheet_name
                    dst_sheet_rels_path = f"xl/worksheets/_rels/sheet{next_sheet_idx}.xml.rels"

                    rels_map = {}
                    drel_root = None

                    used_rids = {
                        el.get("{%s}id" % _NS_REL_DOC)
                        for el in sheet_root.findall(".//*[@r:id]", NS)
                        if el.get("{%s}id" % _NS_REL_DOC)
                    }

                    src_sheet_rels_path = f"xl/worksheets/_rels/{os.path.basename(src_sheet_path)}.rels"
                    if src_sheet_rels_path in src_names:
                        srel_root = _read_xml(src, src_sheet_rels_path)

                        drel_root = ET.Element("Relationships")
                        drel_root.set("xmlns", _NS_REL_PKG)

                        def _add_rel(_type, _target, _mode=None):
                            rel = ET.SubElement(drel_root, "Relationship")
                            rel.set("Id", f"rId{len(list(drel_root)) + 1}")
                            rel.set("Type", _type)
                            rel.set("Target", _target)
                            if _mode:
                                rel.set("TargetMode", _mode)
                            return rel.get("Id")

                        for r in srel_root.findall("{%s}Relationship" % _NS_REL_PKG):
                            rId = r.get("Id")
                            rTyp = (r.get("Type") or "")
                            rTgt = (r.get("Target") or "")
                            rMode = r.get("TargetMode")

                            if used_rids and rId not in used_rids:
                                continue

                            if rTyp.endswith("/drawing"):
                                src_draw_path = _rels_target_to_zip_path(rTgt)
                                if src_draw_path in src_names:
                                    new_draw_name = f"drawing{next_drawing_idx}.xml"
                                    dst_draw_path = "xl/drawings/" + new_draw_name
                                    draw_xml = _read_xml(src, src_draw_path)

                                    src_draw_rels = f"xl/drawings/_rels/{os.path.basename(src_draw_path)}.rels"
                                    if src_draw_rels in src_names:
                                        drels_xml = _read_xml(
                                            src, src_draw_rels)
                                        for ir in drels_xml.findall("{%s}Relationship" % _NS_REL_PKG):
                                            ityp = (ir.get("Type") or "")
                                            itgt = (ir.get("Target") or "")
                                            if ityp.endswith("/image"):
                                                src_img = _rels_target_to_zip_path(
                                                    itgt)
                                                if src_img in src_names:
                                                    ext = os.path.splitext(
                                                        src_img)[1].lower()
                                                    new_img = f"image{next_image_idx}{ext}"
                                                    dst.writestr(
                                                        "xl/media/"+new_img, src.read(src_img))
                                                    if ext == ".png":
                                                        _ensure_default(
                                                            ct_root, "png", "image/png")
                                                    elif ext in (".jpg", ".jpeg"):
                                                        _ensure_default(
                                                            ct_root, ext[1:], "image/jpeg")
                                                    ir.set(
                                                        "Target", "../media/"+new_img)
                                                    next_image_idx += 1
                                            elif ityp.endswith("/chart"):
                                                src_chart = _rels_target_to_zip_path(
                                                    itgt)
                                                if src_chart in src_names:
                                                    new_chart = f"chart{next_chart_idx}.xml"
                                                    dst.writestr(
                                                        "xl/charts/"+new_chart, src.read(src_chart))
                                                    _ensure_override(ct_root, "xl/charts/"+new_chart,
                                                                     "application/vnd.openxmlformats-officedocument.drawingml.chart+xml")
                                                    src_chart_rels = f"xl/charts/_rels/{os.path.basename(src_chart)}.rels"
                                                    if src_chart_rels in src_names:
                                                        crels_xml = _read_xml(
                                                            src, src_chart_rels)
                                                        for cr in crels_xml.findall("{%s}Relationship" % _NS_REL_PKG):
                                                            ctyp = (
                                                                cr.get("Type") or "")
                                                            ctgt = (
                                                                cr.get("Target") or "")
                                                            if ctyp.endswith("/image"):
                                                                cimg = _rels_target_to_zip_path(
                                                                    ctgt)
                                                                if cimg in src_names:
                                                                    ext = os.path.splitext(
                                                                        cimg)[1].lower()
                                                                    new_img = f"image{next_image_idx}{ext}"
                                                                    dst.writestr(
                                                                        "xl/media/"+new_img, src.read(cimg))
                                                                    if ext == ".png":
                                                                        _ensure_default(
                                                                            ct_root, "png", "image/png")
                                                                    elif ext in (".jpg", ".jpeg"):
                                                                        _ensure_default(
                                                                            ct_root, ext[1:], "image/jpeg")
                                                                    cr.set(
                                                                        "Target", "../media/"+new_img)
                                                                    next_image_idx += 1
                                                            elif ctyp.endswith("/chartStyle"):
                                                                s = _rels_target_to_zip_path(
                                                                    ctgt)
                                                                if s in src_names:
                                                                    new = f"style{next_cstyle_idx}.xml"
                                                                    dst.writestr(
                                                                        "xl/charts/"+new, src.read(s))
                                                                    _ensure_override(
                                                                        ct_root, "xl/charts/"+new, "application/vnd.ms-office.chartstyle+xml")
                                                                    cr.set(
                                                                        "Target", new)
                                                                    next_cstyle_idx += 1
                                                            elif ctyp.endswith("/chartColorStyle"):
                                                                s = _rels_target_to_zip_path(
                                                                    ctgt)
                                                                if s in src_names:
                                                                    new = f"colors{next_ccolor_idx}.xml"
                                                                    dst.writestr(
                                                                        "xl/charts/"+new, src.read(s))
                                                                    _ensure_override(
                                                                        ct_root, "xl/charts/"+new, "application/vnd.ms-office.chartcolorstyle+xml")
                                                                    cr.set(
                                                                        "Target", new)
                                                                    next_ccolor_idx += 1
                                                        _write_xml(
                                                            dst, f"xl/charts/_rels/{new_chart}.rels", crels_xml)
                                                    next_chart_idx += 1
                                        _write_xml(
                                            dst, f"xl/drawings/_rels/{new_draw_name}.rels", drels_xml)

                                    _write_xml(dst, dst_draw_path, draw_xml)
                                    new_rel_id = _add_rel(
                                        rTyp, "../drawings/" + new_draw_name)
                                    _ensure_override(ct_root, "xl/drawings/" + new_draw_name,
                                                     "application/vnd.openxmlformats-officedocument.drawing+xml")
                                    # remapeo r:id del drawing
                                    rels_map[rId] = new_rel_id
                                    next_drawing_idx += 1
                                continue

                            if rTyp.endswith("/hyperlink"):
                                new_rel_id = _add_rel(rTyp, rTgt, _mode=rMode)
                                rels_map[rId] = new_rel_id
                                continue

                            if rTyp.endswith("/table"):
                                s = _rels_target_to_zip_path(rTgt)
                                if s in src_names:
                                    new = f"table{next_table_idx}.xml"
                                    dst.writestr("xl/tables/"+new, src.read(s))
                                    _ensure_override(
                                        ct_root, "xl/tables/"+new, "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml")
                                    new_rel_id = _add_rel(
                                        rTyp, "../tables/"+new)
                                    rels_map[rId] = new_rel_id
                                    next_table_idx += 1
                                continue

                            if rTyp.endswith("/comments"):
                                s = _rels_target_to_zip_path(rTgt)
                                if s in src_names:
                                    new = f"comments{next_comments_idx}.xml"
                                    dst.writestr("xl/"+new, src.read(s))
                                    _ensure_override(
                                        ct_root, "xl/"+new, "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml")
                                    new_rel_id = _add_rel(rTyp, "../"+new)
                                    rels_map[rId] = new_rel_id
                                    next_comments_idx += 1
                                continue

                            if rTyp.endswith("/vmlDrawing"):
                                s = _rels_target_to_zip_path(rTgt)
                                if s in src_names:
                                    new = f"vmlDrawing{next_vml_idx}.vml"
                                    dst.writestr("xl/drawings/" +
                                                 new, src.read(s))
                                    new_rel_id = _add_rel(
                                        rTyp, "../drawings/"+new)
                                    rels_map[rId] = new_rel_id
                                    next_vml_idx += 1
                                continue

                            if rTyp.endswith("/printerSettings"):
                                s = _rels_target_to_zip_path(rTgt)
                                if s in src_names:
                                    new = f"printerSettings{next_ps_idx}.bin"
                                    dst.writestr(
                                        "xl/printerSettings/"+new, src.read(s))
                                    _ensure_override(ct_root, "xl/printerSettings/"+new,
                                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings")
                                    new_rel_id = _add_rel(
                                        rTyp, "../printerSettings/"+new)
                                    rels_map[rId] = new_rel_id
                                    next_ps_idx += 1
                                continue

                            new_rel_id = _add_rel(rTyp, rTgt, _mode=rMode)
                            rels_map[rId] = new_rel_id

                        if list(drel_root):
                            _write_xml(dst, dst_sheet_rels_path, drel_root)
                    else:
                        if used_rids:
                            _strip_relationship_bound_elements(sheet_root)

                    for el in sheet_root.findall(".//*[@r:id]", NS):
                        rid = el.get("{%s}id" % _NS_REL_DOC)
                        if rid in rels_map:
                            el.set("{%s}id" % _NS_REL_DOC, rels_map[rid])

                    _write_xml(dst, dst_sheet_path, sheet_root)
                    _ensure_override(ct_root, dst_sheet_name,
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")

                    new_rid = _next_rid(wb_rels_root)
                    sheet_el = ET.SubElement(
                        sheets_node, "{%s}sheet" % _NS_MAIN)
                    raw_name = (sheet_names[i] if (
                        sheet_names and i < len(sheet_names)) else f"Report {i+1}")
                    nm = _safe_sheet_name(raw_name)
                    name = nm or "Sheet"
                    k = 2
                    while name.lower() in used_names:
                        cut = 31 - (len(str(k))+3)
                        cut = 1 if cut < 1 else cut
                        name = f"{(nm or 'Sheet')[:cut]} ({k})"
                        k += 1
                    used_names.add(name.lower())
                    sheet_el.set("name", name)
                    sheet_el.set("sheetId", str(next_sheet_id))
                    sheet_el.set("{%s}id" % _NS_REL_DOC, new_rid)  # DOC NS

                    wb_rel = ET.SubElement(
                        wb_rels_root, "{%s}Relationship" % _NS_REL_PKG)
                    wb_rel.set("Id", new_rid)
                    wb_rel.set(
                        "Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet")
                    wb_rel.set("Target", dst_sheet_name)

                    next_sheet_idx += 1
                    next_sheet_id += 1

            for i, src_path in enumerate(src_paths[1:], start=1):
                _add_sheet_from_src(src_path, i)

            titles = [(s.get("name") or "Sheet")
                      for s in wb_root.findall("m:sheets/m:sheet", NS)]
            if app_root is not None:
                app_fixed = _rewrite_app_xml(app_root, titles)
                if app_fixed is not None:
                    _write_xml(dst, "docProps/app.xml", app_fixed)

            _write_xml(dst, wb_xml_path, wb_root)
            _write_xml(dst, wb_rels_path, wb_rels_root)
            _write_ct(dst, ct_root)

            return titles

# ===== VISTA: merge y descarga =====


@login_required
@rol_requerido("supervisor", "pm", "admin", "facturacion")
def billing_merge_excel(request):
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    ids = []
    if request.method == "POST" and request.body:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            ids = [int(x)
                   for x in (payload.get("ids") or []) if str(x).isdigit()]
        except Exception:
            logger.exception("MERGE RUN %s | invalid JSON", run_id)
    if not ids:
        qs = (request.GET.get("ids") or "").strip()
        if qs:
            ids = [int(x) for x in qs.split(",") if x.isdigit()]
    if not ids:
        return HttpResponseBadRequest("Debes indicar ids, ej: ?ids=53,59 o POST JSON {'ids':[...]}")
    logger.info("MERGE RUN %s | ids=%s", run_id, ids)

    sesiones = {s.id: s for s in SesionBilling.objects.filter(id__in=ids)}
    ordered = [sesiones[i] for i in ids if i in sesiones]

    src_paths, sheet_names, skipped = [], [], []
    for s in ordered:
        rf = getattr(s, "reporte_fotografico", None)
        if not rf:
            skipped.append(str(s.id))
            continue
        try:
            rf.open("rb")
            rf.close()
        except Exception:
            skipped.append(str(s.id))
            continue
        try:
            tmp = _fetch_to_temp(rf)
            src_paths.append(tmp)
            sheet_names.append(
                (f"{(s.proyecto_id or '').strip()}-{s.id}")[:31] or f"proj-{s.id}")
        except Exception:
            skipped.append(str(s.id))

    if not src_paths:
        return JsonResponse({"error": "Ninguno de los proyectos seleccionados tiene un reporte XLSX disponible."}, status=400)

    out_tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    out_tmp.close()
    final_titles = merge_xlsx_files_preserving_images(
        src_paths, out_tmp.name, sheet_names=sheet_names)

    f = open(out_tmp.name, "rb")
    resp = FileResponse(
        f,
        as_attachment=True,
        filename="reportes_fotograficos_merged.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Length"] = os.path.getsize(out_tmp.name)
    from django.utils.http import http_date
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = http_date(0)

    resp["X-Debug-Run"] = run_id
    try:
        resp["X-Merged-Count"] = str(len(final_titles))
        resp["X-Merged-Sheets"] = ",".join(final_titles)
    except Exception:
        pass
    if skipped:
        resp["X-Skipped-Ids"] = ",".join(skipped)
    return resp
