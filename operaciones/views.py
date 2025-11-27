# operaciones/views.py

import calendar
import csv
import io
import json
import locale
import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from copy import copy as _copy
from datetime import date
from datetime import date as _date
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO
from tempfile import NamedTemporaryFile
from typing import Optional
from urllib.parse import urlencode
from uuid import uuid4

import boto3
import pandas as pd
import requests
import xlsxwriter
import xlwt
from botocore.client import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError
from django.core.files.storage import default_storage
from django.core.files.storage import default_storage as storage
from django.core.paginator import Paginator
from django.db import models
from django.db import models as dj_models
from django.db import transaction
from django.db.models import (Case, Count, DecimalField, Exists, F, FloatField,
                              IntegerField, OuterRef, Prefetch, Q, Sum, Value,
                              When)
from django.db.models.functions import Coalesce, Length, Substr, Upper
from django.http import (FileResponse, HttpResponse, HttpResponseBadRequest,
                         HttpResponseForbidden, HttpResponseNotAllowed,
                         HttpResponseRedirect, HttpResponseServerError,
                         JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.html import escape
from django.utils.http import urlencode
from django.utils.text import slugify
from django.utils.timezone import is_aware, now
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as XLImage  # para copiar imágenes
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from core.decorators import project_object_access_required
from core.permissions import (filter_queryset_by_access, projects_ids_for_user,
                              user_has_project_access)
from facturacion.models import CartolaMovimiento, Proyecto
from operaciones.forms import PaymentApproveForm, PaymentRejectForm
from operaciones.models import AdjustmentEntry  # <-- IMPORTA EL MODELO
from operaciones.models import ItemBillingTecnico, SesionBilling, WeeklyPayment
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser  # ajusta si tu user model es otro
from usuarios.utils import \
    crear_notificacion  # asegúrate de tener esta función

from .forms import MovimientoUsuarioForm  # crearemos este form
from .forms import PrecioActividadTecnicoForm  # lo definimos abajo
from .forms import (ImportarPreciosForm, PaymentApproveForm,  # <-- TUS FORMS
                    PaymentMarkPaidForm, PaymentRejectForm)
from .models import PrecioActividadTecnico  # <-- TU MODELO DE PRECIOS
from .models import SesionBilling  # ajusta a tu ruta real
from .models import (AdjustmentEntry, EvidenciaFotoBilling, ItemBilling,
                     ItemBillingTecnico, SesionBillingTecnico, WeeklyPayment)
from .services.weekly import \
    materialize_week_for_payments  # crea/actualiza solo la semana indicada
from .services.weekly import \
    sync_weekly_totals_no_create  # versión que NO crea

try:
    from operaciones.models import AdjustmentEntry
except Exception:
    AdjustmentEntry = None

# 👇 nuevo
from facturacion.models import Proyecto  # ajusta el app si está en otro lado

  # type: ignore




# operaciones/views.py





WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
# --- Direct upload (receipts/rendiciones) ---


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
@rol_requerido('usuario', 'facturacion', 'pm', 'admin')
@require_POST
def presign_rendicion(request, pk=None):
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
    s3 = _s3_client()  # ← usa el cliente único
    try:
        s3.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=ruta)
        return True
    except ClientError:
        return False


@login_required
@rol_requerido('usuario')
def mis_rendiciones(request):
    user = request.user

    # --- Query + paginación ---
    cantidad_str = request.GET.get('cantidad', '10')
    try:
        per_page = 1000000 if cantidad_str == 'todos' else int(cantidad_str)
    except (TypeError, ValueError):
        per_page = 10
        cantidad_str = '10'

    movimientos_qs = CartolaMovimiento.objects.filter(usuario=user).order_by('-fecha')
    paginator = Paginator(movimientos_qs, per_page)
    pagina = paginator.get_page(request.GET.get('page'))

    # --- Saldos ---
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

    # === claves presign (si vienen de un intento anterior fallido) ===
    wasabi_key_post = (request.POST.get('wasabi_key') or '').strip()
    wasabi_key_odo_post = (request.POST.get('wasabi_key_foto_tablero') or '').strip()

    # 🔒 proyectos permitidos para ESTE usuario
    allowed_ids = projects_ids_for_user(user)

    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES)

        # 🔒 Limitar el combo de proyectos del form (si existe) al usuario actual
        if hasattr(form, 'fields') and 'proyecto' in form.fields:
            form.fields['proyecto'].queryset = form.fields['proyecto'].queryset.filter(id__in=allowed_ids).order_by('nombre')

        if form.is_valid():
            mov: CartolaMovimiento = form.save(commit=False)
            mov.usuario = user
            mov.fecha = timezone.now()
            mov.status = 'pendiente_abono_usuario' if (
                mov.tipo and mov.tipo.categoria == "abono") else 'pendiente_supervisor'

            # 🔒 Validación servidor: el proyecto elegido debe estar asignado al usuario
            proj = form.cleaned_data.get('proyecto')
            if not proj or proj.id not in allowed_ids:
                form.add_error('proyecto', "No estás asignado a ese proyecto.")
                ctx = {
                    'pagina': pagina,
                    'cantidad': cantidad_str,
                    'saldo_disponible': saldo_disponible,
                    'saldo_pendiente': saldo_pendiente,
                    'saldo_rendido': saldo_rendido,
                    'form': form,
                    'direct_uploads_receipts_enabled': True,
                    'receipt_max_mb': int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25)),
                    'wasabi_key': wasabi_key_post,
                    'wasabi_key_foto_tablero': wasabi_key_odo_post,
                }
                return render(request, 'operaciones/mis_rendiciones.html', ctx)

            # ====== recibo (comprobante) ======
            if wasabi_key_post:
                mov.comprobante.name = wasabi_key_post  # subida directa
            else:
                mov.comprobante = form.cleaned_data.get('comprobante') or mov.comprobante

            # ====== foto tablero (odómetro) ======
            if wasabi_key_odo_post:
                mov.foto_tablero.name = wasabi_key_odo_post
            else:
                mov.foto_tablero = form.cleaned_data.get('foto_tablero') or mov.foto_tablero

            # ====== kilometraje ======
            mov.kilometraje = form.cleaned_data.get('kilometraje')

            mov.save()

            # Verificación opcional en Wasabi cuando hubo subida directa
            import time
            if wasabi_key_post:
                for _ in range(3):
                    if verificar_archivo_wasabi(mov.comprobante.name):
                        break
                    time.sleep(1)
                else:
                    mov.delete()
                    messages.error(request, "Error uploading the receipt. Please try again.")
                    return redirect('operaciones:mis_rendiciones')

            if wasabi_key_odo_post:
                for _ in range(3):
                    if verificar_archivo_wasabi(mov.foto_tablero.name):
                        break
                    time.sleep(1)
                else:
                    mov.delete()
                    messages.error(request, "Error uploading the odometer photo. Please try again.")
                    return redirect('operaciones:mis_rendiciones')

            messages.success(request, "Expense report registered successfully.")
            return redirect('operaciones:mis_rendiciones')

        # ---> Form inválido: re-render conservando claves para NO re-subir
        ctx = {
            'pagina': pagina,
            'cantidad': cantidad_str,
            'saldo_disponible': saldo_disponible,
            'saldo_pendiente': saldo_pendiente,
            'saldo_rendido': saldo_rendido,
            'form': form,
            'direct_uploads_receipts_enabled': True,
            'receipt_max_mb': int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25)),
            'wasabi_key': wasabi_key_post,
            'wasabi_key_foto_tablero': wasabi_key_odo_post,
        }
        return render(request, 'operaciones/mis_rendiciones.html', ctx)

    # GET: instanciar form y limitar queryset de proyectos
    form = MovimientoUsuarioForm()
    if hasattr(form, 'fields') and 'proyecto' in form.fields:
        form.fields['proyecto'].queryset = form.fields['proyecto'].queryset.filter(id__in=allowed_ids).order_by('nombre')

    return render(request, 'operaciones/mis_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad_str,
        'saldo_disponible': saldo_disponible,
        'saldo_pendiente': saldo_pendiente,
        'saldo_rendido': saldo_rendido,
        'form': form,
        'direct_uploads_receipts_enabled': True,
        'receipt_max_mb': int(getattr(settings, "RECEIPT_DIRECT_UPLOADS_MAX_MB", 25)),
        'wasabi_key': '',
        'wasabi_key_foto_tablero': '',
    })


# Cerca de donde defines MULTIPART_EXPIRES_SECONDS
MULTIPART_EXPIRES_SECONDS = 900  # 15 min

RECEIPT_ALLOWED_MIME = set(getattr(
    settings,
    "RECEIPT_ALLOWED_MIME",
    {
        "application/pdf",
        "image/jpeg", "image/jpg",
        "image/png",
        "image/webp",
        "image/heic", "image/heif",
    }
))

# (Opcional) compatibilidad si en otro punto quedó el nombre viejo
ALLOWED_MIME = RECEIPT_ALLOWED_MIME




@login_required
@rol_requerido('usuario', 'facturacion', 'pm', 'admin')
@require_POST
def multipart_create(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    filename = (data.get("filename") or "").strip()
    ctype = (data.get("contentType") or "").strip()

    # ✅ Validación correcta + indentación correcta
    if not filename or (ctype and ctype not in RECEIPT_ALLOWED_MIME):
        return HttpResponseBadRequest("Invalid file type.")

    key = _build_receipt_key(request.user.id, filename)
    s3 = _s3_client()
    try:
        resp = s3.create_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key,
            ACL="private",
            ContentType=ctype or "application/octet-stream",
        )
    except ClientError as e:
        return HttpResponseBadRequest(str(e))

    return JsonResponse({
        "uploadId": resp["UploadId"],
        "key": key,
        "bucket": settings.AWS_STORAGE_BUCKET_NAME
    })
# --- 2) Firmar una parte ---


@login_required
@rol_requerido('usuario', 'facturacion', 'pm', 'admin')
@require_POST
def multipart_sign_part(request):
    """
    Body: { "key": "...", "uploadId": "...", "partNumber": 1 }
    Resp: { "url": "https://...presigned...", "partNumber": 1, "expiresIn": 900 }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    key = (data.get("key") or "").strip()
    upload_id = (data.get("uploadId") or "").strip()
    part_number = int(data.get("partNumber") or 0)
    if not key or not upload_id or part_number <= 0:
        return HttpResponseBadRequest("Missing params.")

    s3 = _s3_client()
    try:
        url = s3.generate_presigned_url(
            ClientMethod="upload_part",
            Params={
                "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=MULTIPART_EXPIRES_SECONDS,
        )
    except ClientError as e:
        return HttpResponseBadRequest(str(e))

    return JsonResponse({"url": url, "partNumber": part_number, "expiresIn": MULTIPART_EXPIRES_SECONDS})

# --- 3) Completar upload ---


@login_required
@rol_requerido('usuario', 'facturacion', 'pm', 'admin')
@require_POST
def multipart_complete(request):
    """
    Body: { "key": "...", "uploadId": "...", "parts": [{"ETag":"...", "PartNumber":1}, ...] }
    Resp: { "ok": true }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    key = (data.get("key") or "").strip()
    upload_id = (data.get("uploadId") or "").strip()
    parts = data.get("parts") or []
    if not key or not upload_id or not parts:
        return HttpResponseBadRequest("Missing params.")

    s3 = _s3_client()
    try:
        s3.complete_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key,
            MultipartUpload={"Parts": sorted(
                parts, key=lambda p: p["PartNumber"])},
            UploadId=upload_id,
        )
    except ClientError as e:
        return HttpResponseBadRequest(str(e))

    return JsonResponse({"ok": True})

# --- 4) Abortar upload (por si algo falla) ---


@login_required
@rol_requerido('usuario', 'facturacion', 'pm', 'admin')
@require_POST
def multipart_abort(request):
    """
    Body: { "key": "...", "uploadId": "..." }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    key = (data.get("key") or "").strip()
    upload_id = (data.get("uploadId") or "").strip()
    if not key or not upload_id:
        return HttpResponseBadRequest("Missing params.")

    s3 = _s3_client()
    try:
        s3.abort_multipart_upload(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=key,
            UploadId=upload_id,
        )
    except ClientError:
        pass  # idempotente

    return JsonResponse({"ok": True})


@login_required
@rol_requerido('usuario')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def aprobar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Deposit approved successfully.")
    return redirect('operaciones:mis_rendiciones')


@login_required
@rol_requerido('usuario')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def rechazar_abono(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)
    if request.method == "POST":
        motivo = request.POST.get("motivo", "")
        mov.status = "rechazado_abono_usuario"
        mov.motivo_rechazo = motivo
        mov.save()
        messages.error(request, "Deposit rejected and sent to Finance for review.")
    return redirect('operaciones:mis_rendiciones')



@login_required
@rol_requerido('usuario')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def editar_rendicion(request, pk):
    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(request, "You cannot edit an already approved expense report.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        form = MovimientoUsuarioForm(request.POST, request.FILES, instance=rendicion)

        if form.is_valid():
            campos_editados = [f for f in form.changed_data if f not in ['status', 'actualizado']]
            if campos_editados and rendicion.status in [
                'rechazado_abono_usuario', 'rechazado_supervisor', 'rechazado_pm', 'rechazado_finanzas'
            ]:
                rendicion.status = 'pendiente_supervisor'

            form.save()
            messages.success(request, "Expense report successfully updated.")
            return redirect('operaciones:mis_rendiciones')
    else:
        form = MovimientoUsuarioForm(instance=rendicion)

    return render(request, 'operaciones/editar_rendicion.html', {'form': form})


@login_required
@rol_requerido('usuario')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def eliminar_rendicion(request, pk):
    rendicion = get_object_or_404(CartolaMovimiento, pk=pk, usuario=request.user)

    if rendicion.status in ['aprobado_abono_usuario', 'aprobado_finanzas']:
        messages.error(request, "You cannot delete an already approved expense report.")
        return redirect('operaciones:mis_rendiciones')

    if request.method == 'POST':
        rendicion.delete()
        messages.success(request, "Expense report deleted successfully.")
        return redirect('operaciones:mis_rendiciones')

    return render(request, 'operaciones/eliminar_rendicion.html', {'rendicion': rendicion})


def _parse_fecha_fragmento(s: str):
    s = (s or "").strip()
    if not s:
        return {}

    parts = s.replace("/", "-").split("-")
    try:
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            d, m, y = parts
            return {
                "fecha__date__day": int(d),
                "fecha__date__month": int(m),
                "fecha__date__year": int(y),
            }
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            a, b = parts
            # dd-mm
            if len(a) <= 2 and len(b) <= 2:
                return {"fecha__date__day": int(a), "fecha__date__month": int(b)}
            # mm-yyyy
            if len(a) <= 2 and len(b) == 4:
                return {"fecha__date__month": int(a), "fecha__date__year": int(b)}
        if s.isdigit():
            val = int(s)
            if len(s) == 4:
                return {"fecha__date__year": val}
            return {"_day_or_month": val}
    except Exception:
        return {}
    return {}


@login_required
@rol_requerido('pm', 'admin', 'supervisor')
def vista_rendiciones(request):
    user = request.user

    # Base visible según rol
    if user.is_superuser:
        movimientos = CartolaMovimiento.objects.all()
    else:
        base = Q()
        if getattr(user, 'es_supervisor', False):
            base |= Q(status='pendiente_supervisor') | Q(status='rechazado_supervisor')
        if getattr(user, 'es_pm', False):
            base |= Q(status='aprobado_supervisor') | Q(status='rechazado_pm') | Q(status='aprobado_pm')
        if getattr(user, 'es_facturacion', False):
            base |= Q(status='aprobado_pm') | Q(status='rechazado_finanzas') | Q(status='aprobado_finanzas')
        movimientos = CartolaMovimiento.objects.filter(base) if base else CartolaMovimiento.objects.none()

    # 🔒 Limitar por proyectos asignados al usuario
    movimientos = filter_queryset_by_access(movimientos, request.user, 'proyecto_id')

    # ---------- Filtros ----------
    du = request.GET.get('du', '').strip()
    fecha_txt = request.GET.get('fecha', '').strip()
    proyecto = request.GET.get('proyecto', '').strip()
    tipo_txt = request.GET.get('tipo', '').strip()
    estado = request.GET.get('estado', '').strip()

    q = Q()
    if du:
        q &= (Q(usuario__first_name__icontains=du) |
              Q(usuario__last_name__icontains=du) |
              Q(usuario__username__icontains=du))

    if proyecto:
        q &= Q(proyecto__nombre__icontains=proyecto)

    if tipo_txt:
        q &= Q(tipo__nombre__icontains=tipo_txt)

    if estado:
        q &= Q(status=estado)

    # Fecha flexible
    if fecha_txt:
        fd = _parse_fecha_fragmento(fecha_txt)
        if fd:
            day_or_month = fd.pop("_day_or_month", None)
            if fd:
                q &= Q(**fd)
            if day_or_month is not None:
                q &= (Q(fecha__day=day_or_month) | Q(fecha__month=day_or_month))

    if q:
        movimientos = movimientos.filter(q)

    # Orden personalizado
    movimientos = movimientos.annotate(
        orden_status=Case(
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado',  then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by('orden_status', '-fecha')

    # Totales
    total = movimientos.aggregate(total=Sum('cargos'))['total'] or 0
    pendientes = movimientos.filter(status__startswith='pendiente').aggregate(total=Sum('cargos'))['total'] or 0
    rechazados = movimientos.filter(status__startswith='rechazado').aggregate(total=Sum('cargos'))['total'] or 0

    # Paginación
    cantidad = request.GET.get('cantidad', '10')
    cantidad_pag = 1000000 if cantidad == 'todos' else int(cantidad)
    paginator = Paginator(movimientos, cantidad_pag)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # Choices del modelo
    estado_choices = CartolaMovimiento._meta.get_field('status').choices

    base_qs = request.GET.copy()
    base_qs.pop('page', None)
    base_qs = base_qs.urlencode()

    return render(request, 'operaciones/vista_rendiciones.html', {
        'pagina': pagina,
        'cantidad': cantidad,
        'total': total,
        'pendientes': pendientes,
        'rechazados': rechazados,
        'filtros': {
            'du': du, 'fecha': fecha_txt, 'proyecto': proyecto,
            'tipo': tipo_txt, 'estado': estado
        },
        'estado_choices': estado_choices,
        'base_qs': base_qs,
    })


@login_required
@rol_requerido('pm', 'admin', 'supervisor', 'facturacion')
@project_object_access_required(model='facturacion.CartolaMovimiento',object_kw='pk',project_attr='proyecto_id')
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
@project_object_access_required(model='facturacion.CartolaMovimiento',object_kw='pk',project_attr='proyecto_id')
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
@rol_requerido('pm', 'admin')  # Si quieres, agrega 'supervisor', 'facturacion'
def exportar_rendiciones(request):
    from datetime import datetime

    import xlwt
    from django.db.models import Case, IntegerField, Q, Value, When
    from django.http import HttpResponse
    from django.utils.timezone import is_aware

    # ===== Base visible (misma lógica que vista_rendiciones) =====
    if request.user.is_superuser:
        base = CartolaMovimiento.objects.all()
    else:
        u = request.user
        visible_q = Q()
        if getattr(u, 'es_supervisor', False):
            visible_q |= Q(status='pendiente_supervisor') | Q(status='rechazado_supervisor')
        if getattr(u, 'es_pm', False):
            visible_q |= Q(status='aprobado_supervisor') | Q(status='rechazado_pm') | Q(status='aprobado_pm')
        if getattr(u, 'es_facturacion', False):
            visible_q |= Q(status='aprobado_pm') | Q(status='rechazado_finanzas') | Q(status='aprobado_finanzas')

        base = CartolaMovimiento.objects.filter(visible_q) if visible_q else CartolaMovimiento.objects.none()

    # Limitar SIEMPRE a proyectos asignados al usuario
    base = filter_queryset_by_access(
        base.select_related('usuario', 'proyecto', 'tipo'),
        request.user,
        'proyecto_id'
    )

    # --------- Filtros (idénticos al listado) ----------
    du        = (request.GET.get('du') or '').strip()
    fecha_txt = (request.GET.get('fecha') or '').strip()
    proyecto  = (request.GET.get('proyecto') or '').strip()
    tipo_txt  = (request.GET.get('tipo') or '').strip()
    estado    = (request.GET.get('estado') or '').strip()

    q = Q()
    if du:
        q &= (Q(usuario__first_name__icontains=du) |
              Q(usuario__last_name__icontains=du) |
              Q(usuario__username__icontains=du))
    if proyecto:
        q &= Q(proyecto__nombre__icontains=proyecto)
    if tipo_txt:
        q &= Q(tipo__nombre__icontains=tipo_txt)
    if estado:
        q &= Q(status=estado)

    if fecha_txt:
        fd = _parse_fecha_fragmento(fecha_txt)
        if fd:
            day_or_month = fd.pop("_day_or_month", None)
            # Normaliza claves antiguas 'fecha__date__*' → 'fecha__*'
            if any(k.startswith('fecha__date__') for k in fd.keys()):
                fd = {k.replace('fecha__date__', 'fecha__'): v for k, v in fd.items()}
            if fd:
                q &= Q(**fd)
            if day_or_month is not None:
                q &= (Q(fecha__day=day_or_month) | Q(fecha__month=day_or_month))

    movimientos = base.filter(q) if q else base

    # ===== Orden FINAL (igual que en vista_rendiciones) =====
    movimientos = movimientos.annotate(
        orden_status=Case(
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado',  then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by('orden_status', '-fecha', '-id')
    # (Si quieres puramente por fecha, usa solo: .order_by('-fecha', '-id'))

    # ----- Excel -----
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="expense_reports.xls"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Expense Reports')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style   = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = ["User", "Date", "Project", "Type", "Remarks", "Amount", "Status", "Odometer (km)"]
    for col_num, title in enumerate(columns):
        ws.write(0, col_num, title, header_style)

    for row_num, mov in enumerate(movimientos, start=1):
        ws.write(row_num, 0, str(mov.usuario))

        fecha_excel = mov.fecha
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()
        ws.write(row_num, 1, fecha_excel, date_style)

        ws.write(row_num, 2, str(getattr(mov.proyecto, "nombre", mov.proyecto or "")))
        ws.write(row_num, 3, str(getattr(mov.tipo, "nombre", mov.tipo or "")))
        ws.write(row_num, 4, mov.observaciones or "")
        ws.write(row_num, 5, float(mov.cargos or 0))
        ws.write(row_num, 6, mov.get_status_display())
        ws.write(row_num, 7, int(mov.kilometraje) if mov.kilometraje is not None else "")

    wb.save(response)
    return response

@login_required
@rol_requerido('usuario')
def exportar_mis_rendiciones(request):
    from datetime import datetime

    import xlwt
    from django.http import HttpResponse
    from django.utils.timezone import is_aware

    user = request.user

    # Base: solo mis movimientos
    base = (
        CartolaMovimiento.objects
        .filter(usuario=user)
        .select_related('usuario', 'proyecto', 'tipo')
        .order_by('-fecha')
    )
    # Limitar a proyectos donde el usuario tiene acceso
    movimientos = filter_queryset_by_access(base, user, 'proyecto_id')

    # Crear archivo Excel
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="my_expense_reports.xls"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('My Expense Reports')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    # Columnas (incluye Odometer (km))
    columns = [
        "User",
        "Date",
        "Project",
        "Type",
        "Expenses (USD)",
        "Credits (USD)",
        "Remarks",
        "Status",
        "Odometer (km)",
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
        ws.write(row_num, 2, str(mov.proyecto or ""))
        ws.write(row_num, 3, str(mov.tipo or ""))
        ws.write(row_num, 4, float(mov.cargos or 0))
        ws.write(row_num, 5, float(mov.abonos or 0))
        ws.write(row_num, 6, mov.observaciones or "")
        ws.write(row_num, 7, mov.get_status_display())
        ws.write(row_num, 8, int(mov.kilometraje) if mov.kilometraje is not None else "")

    wb.save(response)
    return response

from django.http import JsonResponse


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'facturacion')
def listar_precios_tecnico(request):
    # ---- Cantidad por página (solo estos valores) ----
    cantidad_str = request.GET.get('cantidad', '10')
    allowed_page_sizes = {"5", "10", "20", "50", "100"}

    if cantidad_str not in allowed_page_sizes:
        cantidad_str = "10"

    cantidad = int(cantidad_str)

    # ---- Filtros (GET) ----
    f_tecnico = (request.GET.get('f_tecnico') or '').strip()
    f_ciudad  = (request.GET.get('f_ciudad') or '').strip()
    f_proy    = (request.GET.get('f_proyecto') or '').strip()
    f_codigo  = (request.GET.get('f_codigo') or '').strip()

    qs = (
        PrecioActividadTecnico.objects
        .select_related('tecnico')   # NO agregamos 'proyecto' hasta que sea FK
        .order_by('-fecha_creacion')
    )

    # 🔒 Limitar por proyectos asignados al usuario actual SOLO si 'proyecto' es FK
    try:
        f = PrecioActividadTecnico._meta.get_field('proyecto')
        if isinstance(f, dj_models.ForeignKey):
            qs = filter_queryset_by_access(qs, request.user, 'proyecto_id')
    except Exception:
        pass

    if f_tecnico:
        qs = qs.filter(
            Q(tecnico__first_name__icontains=f_tecnico) |
            Q(tecnico__last_name__icontains=f_tecnico) |
            Q(tecnico__username__icontains=f_tecnico)
        )
    if f_ciudad:
        qs = qs.filter(ciudad__icontains=f_ciudad)
    if f_proy:
        try:
            qs = qs.filter(
                Q(proyecto__nombre__icontains=f_proy) |
                Q(proyecto__codigo__icontains=f_proy)
            )
        except FieldError:
            qs = qs.filter(proyecto__icontains=f_proy)
    if f_codigo:
        qs = qs.filter(codigo_trabajo__icontains=f_codigo)

    paginator   = Paginator(qs, cantidad)
    page_number = request.GET.get('page')
    pagina      = paginator.get_page(page_number)

    ctx = {
        'pagina': pagina,
        'cantidad': cantidad_str,
        'f_tecnico': f_tecnico,
        'f_ciudad': f_ciudad,
        'f_proyecto': f_proy,
        'f_codigo': f_codigo,
    }
    return render(request, 'operaciones/listar_precios_tecnico.html', ctx)


try:
    from usuarios.models import \
        ProyectoAsignacion  # usuario, proyecto, include_history, start_at
except Exception:
    ProyectoAsignacion = None

def _to2(val):
    try:
        return float(Decimal(str(val)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _tecnicos_de_proyecto_qs(proyecto: Optional[Proyecto]):
    """
    Devuelve usuarios asignados al proyecto (sin filtrar por rol):
    1) via ProyectoAsignacion (preferido)
    2) via M2M User.proyectos
    3) via sesiones (SesionBillingTecnico -> sesion.proyecto_id) comparando contra
       [proyecto.codigo, str(proyecto.id), proyecto.nombre]
    """
    User = get_user_model()
    if not proyecto:
        return User.objects.none()

    # 1) Through table
    try:
        user_ids = proyecto.asignaciones.values_list("usuario_id", flat=True)
        qs_pa = User.objects.filter(id__in=user_ids).order_by("first_name", "last_name", "username")
        if qs_pa.exists():
            return qs_pa
    except Exception:
        pass

    # 2) M2M directo
    try:
        qs_m2m = User.objects.filter(proyectos=proyecto).order_by("first_name", "last_name", "username")
        if qs_m2m.exists():
            return qs_m2m
    except Exception:
        pass

    # 3) Fallback por sesiones (usa posibles llaves)
    keys = []
    for k in (getattr(proyecto, "codigo", None), getattr(proyecto, "id", None), getattr(proyecto, "nombre", None)):
        if k is not None and str(k).strip():
            keys.append(str(k).strip())

    if not keys:
        return User.objects.none()

    tech_ids = (
        SesionBillingTecnico.objects
        .filter(sesion__proyecto_id__in=keys)
        .values_list("tecnico_id", flat=True)
        .distinct()
    )
    return User.objects.filter(id__in=tech_ids).order_by("first_name", "last_name", "username")


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm')
def importar_precios(request):
    """
    GET  -> muestra form; si viene ?proyecto_id, filtra técnicos.
    POST -> valida, arma preview y muestra conflictos.
    """
    proyectos_qs = filter_queryset_by_access(Proyecto.objects.all(), request.user, 'id')

    # ---------------- GET ----------------
    if request.method == 'GET':
        form = ImportarPreciosForm()
        proyecto_id_get = (request.GET.get('proyecto_id') or '').strip()

        proyecto_sel = None
        if proyecto_id_get and proyectos_qs.filter(pk=proyecto_id_get).exists():
            proyecto_sel = proyectos_qs.get(pk=proyecto_id_get)

        # filtra técnicos según proyecto seleccionado
        form.fields['tecnicos'].queryset = _tecnicos_de_proyecto_qs(proyecto_sel)

        return render(
            request,
            'operaciones/importar_precios.html',
            {
                'form': form,
                'proyectos': proyectos_qs,
                'proyecto_sel': proyecto_sel,
            }
        )

    # ---------------- POST ----------------
    form = ImportarPreciosForm(request.POST, request.FILES)

    proyecto_id = (request.POST.get('proyecto_id') or '').strip()
    if not proyecto_id:
        messages.error(request, "Please select a Project.")
        return redirect('operaciones:importar_precios')

    if not proyectos_qs.filter(pk=proyecto_id).exists():
        messages.error(request, "Selected Project not found or not allowed.")
        return redirect('operaciones:importar_precios')

    proyecto = proyectos_qs.get(pk=proyecto_id)

    # 🔒 restringe 'tecnicos' ANTES de validar el form
    form.fields['tecnicos'].queryset = _tecnicos_de_proyecto_qs(proyecto)

    if not form.is_valid():
        messages.error(request, "Invalid form.")
        return redirect(f"{reverse('operaciones:importar_precios')}?proyecto_id={proyecto.id}")

    try:
        archivo = request.FILES['archivo']
        tecnicos = form.cleaned_data['tecnicos']

        # 1) extensión
        if not archivo.name.endswith('.xlsx'):
            messages.error(request, "The file must be in .xlsx format.")
            return redirect(f"{reverse('operaciones:importar_precios')}?proyecto_id={proyecto.id}")

        # 2) leer excel
        df = pd.read_excel(archivo, header=0)
        if df.empty:
            messages.error(request, "The uploaded Excel file is empty.")
            return redirect(f"{reverse('operaciones:importar_precios')}?proyecto_id={proyecto.id}")

        # 3) normalizar columnas
        df.columns = df.columns.str.strip().str.lower().str.replace(r'\s+', '_', regex=True)

        colmap = {
            'city': ['city', 'ciudad'],
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
                raise KeyError(f"Required column not found for '{colkey}'. Available columns: {list(df.columns)}")
            return None

        c_city = resolve('city')
        c_code = resolve('code')
        c_desc = resolve('description')
        c_uom  = resolve('uom')
        c_tp   = resolve('technical_price')
        c_cp   = resolve('company_price')
        c_office = resolve('office', required=False)
        c_client = resolve('client', required=False)
        c_wtype  = resolve('work_type', required=False)

        # 4) preview
        preview_data = []
        for _, row in df.iterrows():
            r = {
                'ciudad': row.get(c_city),
                'proyecto': proyecto.nombre,
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

            missing = []
            if not r['ciudad']:         missing.append('city')
            if not r['codigo_trabajo']: missing.append('code')
            if not r['descripcion']:    missing.append('description')
            if not r['uom']:            missing.append('uom')
            if r['precio_tecnico'] is None:
                r['error'] += (" | " if r['error'] else "") + "Invalid Technical Price"
            if r['precio_empresa'] is None:
                r['error'] += (" | " if r['error'] else "") + "Invalid Company Price"
            if missing:
                r['error'] += (" | " if r['error'] else "") + f"Missing fields: {', '.join(missing)}"

            preview_data.append(r)

        request.session['preview_data'] = preview_data
        request.session['selected_proyecto_id'] = proyecto.id

        # 5) conflictos por (tecnico, proyecto*, codigo_trabajo)
        codes = {r['codigo_trabajo'] for r in preview_data if r.get('codigo_trabajo')}
        has_conflicts = False
        conflicts_by_tech = {}

        for t in tecnicos:
            qs_conf = PrecioActividadTecnico.objects.filter(tecnico=t, codigo_trabajo__in=codes)

            # Soporta ambos esquemas de proyecto en PrecioActividadTecnico
            try:
                # Si existe proyecto_id (FK/char)
                qs_conf = qs_conf.filter(proyecto_id=proyecto.id)
            except FieldError:
                # Fallback: campo legacy 'proyecto' (texto)
                nome = str(getattr(proyecto, 'nombre', '')).strip()
                cod  = str(getattr(proyecto, 'codigo', '')).strip()
                cond = dj_models.Q()
                if nome:
                    cond |= dj_models.Q(proyecto__iexact=nome)
                if cod:
                    cond |= dj_models.Q(proyecto__iexact=cod)
                if cond:
                    qs_conf = qs_conf.filter(cond)
                else:
                    qs_conf = qs_conf.none()

            conflicts = list(qs_conf.values_list('codigo_trabajo', flat=True).distinct())
            conflicts_by_tech[t.id] = conflicts
            if conflicts:
                has_conflicts = True

        return render(
            request,
            'operaciones/preview_import.html',
            {
                'preview_data': preview_data,
                'tecnicos': tecnicos,
                'has_conflicts': has_conflicts,
                'conflicts_by_tech': conflicts_by_tech,
                'proyecto_sel': proyecto,
            }
        )

    except KeyError as ke:
        messages.error(request, f"Column not found or incorrectly assigned: {ke}")
        return redirect(f"{reverse('operaciones:importar_precios')}?proyecto_id={proyecto.id}")
    except Exception as e:
        messages.error(request, f"Error during import: {str(e)}")
        return redirect(f"{reverse('operaciones:importar_precios')}?proyecto_id={proyecto.id}")
    

@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm')
def api_tecnicos_por_proyecto(request):
    """
    Devuelve en JSON los técnicos asignados a un proyecto visible para el usuario.

    Respuesta:
      {
        "tecnicos": [
          { "id": 1, "name": "Juan Pérez", "username": "jperez" },
          ...
        ]
      }
    """
    proyectos_qs = filter_queryset_by_access(
        Proyecto.objects.all(),
        request.user,
        'id'
    )

    pid = (request.GET.get('proyecto_id') or '').strip()
    data = {"tecnicos": []}

    if pid and proyectos_qs.filter(pk=pid).exists():
        proyecto = proyectos_qs.get(pk=pid)
        for u in _tecnicos_de_proyecto_qs(proyecto):
            full_name = (u.get_full_name() or "").strip()
            label = full_name or u.username or f"User {u.id}"
            data["tecnicos"].append({
                "id": u.id,
                "name": label,
                "username": u.username,
            })

    return JsonResponse(data)


from django.contrib.auth import get_user_model
# ---------- CONFIRMAR / GUARDAR ----------
from django.db import transaction


@login_required
@rol_requerido('admin', 'pm')
def confirmar_importar_precios(request):
    if request.method != 'POST':
        return redirect('operaciones:importar_precios')

    try:
        preview_data = request.session.get('preview_data', [])
        proyecto_id  = request.session.get('selected_proyecto_id')  # <-- clave
        if not preview_data or not proyecto_id:
            messages.error(request, "No data to save. Please try again.")
            return redirect('operaciones:importar_precios')

        replace = request.POST.get('replace') == 'yes'
        created_total = updated_total = skipped_total = 0
        User = get_user_model()

        with transaction.atomic():
            for row in preview_data:
                if row.get('error'):
                    continue

                tecnico_ids = row.get('tecnico', [])
                tecnicos = User.objects.filter(id__in=tecnico_ids)

                for tecnico in tecnicos:
                    lookup = dict(
                        tecnico=tecnico,
                        proyecto_id=proyecto_id,                  # <-- FK real
                        ciudad=row.get('ciudad') or "",
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
                        # opcional: reflejar nombre legacy si mantienes ese campo CharField
                        # proyecto=row.get('proyecto') or "",
                    )

                    if replace:
                        obj, created = PrecioActividadTecnico.objects.update_or_create(
                            **lookup, defaults=defaults
                        )
                        if created: created_total += 1
                        else:       updated_total += 1
                    else:
                        obj, created = PrecioActividadTecnico.objects.get_or_create(
                            **lookup, defaults=defaults
                        )
                        if created: skipped = False; created_total += 1
                        else:       skipped_total += 1

        msg = f"Import completed. Created: {created_total}, updated: {updated_total}"
        if skipped_total:
            msg += f", skipped (already existing): {skipped_total}"
        messages.success(request, msg)

        # limpiar sesión
        request.session.pop('preview_data', None)
        request.session.pop('selected_proyecto_id', None)

        return redirect('operaciones:listar_precios_tecnico')

    except Exception as e:
        messages.error(request, f"An error occurred during the import: {str(e)}")
        return redirect('operaciones:importar_precios')
    
    
# ---------- CRUD EDIT/DELETE ----------


from core.permissions import project_object_access_required  # <-- NUEVO


@login_required
@rol_requerido('admin', 'pm')
@project_object_access_required(model='operaciones.PrecioActividadTecnico', object_kw='pk', project_attr='proyecto_id')
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
@project_object_access_required(model='operaciones.PrecioActividadTecnico', object_kw='pk', project_attr='proyecto_id')
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

    # 🔒 Solo puede borrar los que están dentro de sus proyectos
    qs = filter_queryset_by_access(
        PrecioActividadTecnico.objects.filter(id__in=ids).select_related('proyecto'),
        request.user,
        'proyecto_id'
    )

    deleted_count = qs.count()
    qs.delete()

    messages.success(request, f"{deleted_count} price(s) deleted successfully.")

    # reconstruye URL de retorno preservando filtros/paginación...
    # (tu código original aquí, igual)
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

    # --- Seguridad extra: solo exportar billings de proyectos visibles para este usuario ---
    # Admin/superuser puede exportar todo; el resto queda limitado a sus proyectos.
    sesiones = list(sesiones)
    if not request.user.is_superuser:
        proyectos_visibles = filter_queryset_by_access(
            Proyecto.objects.all(),
            request.user,
            "id",
        )
        if proyectos_visibles.exists():
            allowed_proj_ids = {
                str(pk) for pk in proyectos_visibles.values_list("id", flat=True)
            }
            sesiones = [
                s for s in sesiones
                if getattr(s, "proyecto", None) in allowed_proj_ids
            ]
        else:
            # Si no tiene proyectos visibles, no exportamos nada
            sesiones = []

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
        # ---- parseo ids + nota + daily_number ----
    ids, note, daily_number = [], "", ""
    ctype = (request.content_type or "").lower()

    if "application/json" in ctype:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except Exception:
            return JsonResponse({"ok": False, "error": "INVALID_JSON"}, status=400)
        ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
        note = (payload.get("note") or "").strip()
        daily_number = (payload.get("daily_number") or "").strip()
    else:
        raw = (request.POST.get("ids") or "").strip()
        ids = [int(x) for x in raw.split(",") if x.isdigit()]
        note = (request.POST.get("note") or "").strip()
        daily_number = (request.POST.get("daily_number") or "").strip()

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

    # --- Seguridad extra: solo billings de proyectos a los que el usuario tiene acceso ---
    # Admin/superuser puede enviar todo; PM queda restringido a sus proyectos visibles.
    forbidden_ids = set()
    if not request.user.is_superuser:
        proyectos_visibles = filter_queryset_by_access(
            Proyecto.objects.all(),
            request.user,
            "id",
        )
        if proyectos_visibles.exists():
            allowed_proj_ids = {
                str(pk) for pk in proyectos_visibles.values_list("id", flat=True)
            }
            filtered_rows = []
            for s in rows:
                # En SesionBilling guardamos el PK del proyecto en s.proyecto (como string)
                if getattr(s, "proyecto", None) in allowed_proj_ids:
                    filtered_rows.append(s)
                else:
                    forbidden_ids.add(s.id)
            rows = filtered_rows
        else:
            # Si el usuario no tiene proyectos visibles, ningún billing es enviable
            forbidden_ids = {s.id for s in rows}
            rows = []

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

    # Agregar también los billings que se intentaron enviar pero pertenecen a proyectos no autorizados
    for bid in forbidden_ids:
        skipped.append({
            "id": bid,
            "estado": None,
            "is_direct_discount": None,
            "finance_status": None,
            "skip_reason": "FORBIDDEN_PROJECT",
        })

    # aplicar updates con lock
    by_id_new = {i: st for (i, st) in plan}
    if by_id_new:
        for s in SesionBilling.objects.select_for_update().filter(id__in=by_id_new.keys()):
            new_status = by_id_new[s.id]
            s.finance_status = new_status

            touched_fields = ["finance_status"]

            if hasattr(s, "finance_updated_at"):
                s.finance_updated_at = now
                touched_fields.append("finance_updated_at")

            if hasattr(s, "finance_sent_at") and new_status in ("sent", "review_discount"):
                s.finance_sent_at = now
                touched_fields.append("finance_sent_at")

            # 👇 Guardar Daily Number (mismo para todos los seleccionados)
            if daily_number:
                s.finance_daily_number = daily_number
                touched_fields.append("finance_daily_number")

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
    # Usuarios privilegiados que pueden ver TODO el historial (sin filtro por proyecto)
    user = request.user
    can_view_legacy_history = (
        user.is_superuser or
        getattr(user, "es_usuario_historial", False)
    )

    visible_filter = (
        # ⬇️ descuento directo todavía sin enviar
        (Q(is_direct_discount=True) & Q(finance_sent_at__isnull=True) & ~Q(finance_status='paid'))
        |
        # ⬇️ flujo normal: ocultar enviados / en proceso de cobro
        (Q(is_direct_discount=False) & ~Q(finance_status__in=['sent', 'pending', 'paid', 'in_review']))
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
                        queryset=ItemBillingTecnico.objects.select_related("tecnico"),
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

    # 🔒 Limitar visibilidad por PROYECTO (columna "Project" = nombre)
    # Solo se aplica a usuarios NORMALES. Los de historial ven todo.
    if not can_view_legacy_history:
        # Obtenemos los proyectos a los que el usuario tiene acceso y usamos su nombre
        try:
            proyectos_user = filter_queryset_by_access(
                Proyecto.objects.all(),  # modelo de proyectos
                request.user,
                'id',                    # el permiso se define por id de Proyecto
            )
        except Exception:
            proyectos_user = Proyecto.objects.none()

        if proyectos_user.exists():
            allowed_keys = set()

            for p in proyectos_user:
                # Nombre que se muestra en la columna "Project"
                nombre = (getattr(p, "nombre", "") or "").strip()
                if nombre:
                    allowed_keys.add(nombre)

                # (Opcional) añadimos id/código por compatibilidad con datos viejos,
                # por si SesionBilling.proyecto guarda id o código.
                codigo = getattr(p, "codigo", None)
                if codigo:
                    allowed_keys.add(str(codigo).strip())
                allowed_keys.add(str(p.id).strip())

            # SesionBilling.proyecto es el campo que luego se mapea a s.proyecto_nombre
            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            # Si no tiene proyectos asignados, no ve ningún billing
            qs = qs.none()

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

    # 🔍 Project ID → sigue filtrando por el campo numérico proyecto_id (columna "Project ID")
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
                (("aprobado supervisor", "approved by supervisor"), Q(estado="aprobado_supervisor")),
                (("rechazado supervisor", "rejected by supervisor"), Q(estado="rechazado_supervisor")),
                (("en revision", "supervisor review", "in supervisor review"), Q(estado="en_revision_supervisor")),
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

    # ========= Mapear ID → nombre de Proyecto de forma segura =========
    # s.proyecto puede ser:
    #   - ID numérico (nuevo esquema)
    #   - texto con el nombre (datos viejos)
    proj_ids = set()
    for s in pagina.object_list:
        val = s.proyecto
        if val is None:
            continue
        try:
            proj_ids.add(int(val))
        except (TypeError, ValueError):
            # era un nombre, lo dejamos tal cual
            continue

    if proj_ids:
        proyectos_map = {
            p.id: getattr(p, "nombre", str(p))
            for p in Proyecto.objects.filter(id__in=proj_ids)
        }
    else:
        proyectos_map = {}

    for s in pagina.object_list:
        val = s.proyecto
        try:
            key = int(val)
        except (TypeError, ValueError):
            # ya es nombre legible
            s.proyecto_nombre = val
        else:
            s.proyecto_nombre = proyectos_map.get(key, val)
    # ================================================================

    can_edit_real_week = (
        getattr(request.user, "es_pm", False)
        or getattr(request.user, "es_facturacion", False)
        or getattr(request.user, "es_admin_general", False)
        or request.user.is_superuser
    )
    can_edit_items = bool(
        getattr(request.user, "es_admin_general", False) or request.user.is_superuser
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

    # ========= Proyectos visibles para este usuario =========
    proyectos_visibles = filter_queryset_by_access(
        Proyecto.objects.all(),
        request.user,
        'id',
    )

    # Técnicos con al menos una tarifa cargada
    # PERO solo en proyectos a los que el usuario tiene acceso
    if proyectos_visibles.exists():
        tecnicos = (
            Usuario.objects
            .filter(
                is_active=True,
                precioactividadtecnico__isnull=False,
                precioactividadtecnico__proyecto_id__in=proyectos_visibles
                    .values_list("id", flat=True),
            )
            .distinct()
            .order_by("first_name", "last_name", "username")
        )
    else:
        # Si el usuario no tiene proyectos asociados, no ve técnicos
        tecnicos = Usuario.objects.none()

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

    # 🔒 Proyectos visibles para el usuario
    proyectos_qs = filter_queryset_by_access(
        Proyecto.objects.all(),
        request.user,
        'id',
    )

    # ========= Resolver proyecto seleccionado (para el select "Project") =========
    proyecto_sel = None

    # 1) Primero intentamos con sesion.proyecto (puede ser id numérico o nombre)
    raw = (getattr(sesion, "proyecto", "") or "").strip()
    if raw:
        try:
            # ¿es un id numérico?
            pid = int(raw)
        except (TypeError, ValueError):
            # no es número → buscamos por nombre/código
            proyecto_sel = proyectos_qs.filter(
                Q(nombre__iexact=raw) |
                Q(codigo__iexact=raw)
            ).first()
        else:
            proyecto_sel = proyectos_qs.filter(pk=pid).first()

       # 2) Si aún no encontramos, intentamos con proyecto_id (NB6790, etc.)
        if not proyecto_sel and sesion.proyecto_id:
            code = str(sesion.proyecto_id).strip()
            proyecto_sel = proyectos_qs.filter(
                Q(codigo__iexact=code) |
                Q(nombre__icontains=code)
            ).first()

         # 3) Normalizar valor y etiqueta para el <select id="project">
        if proyecto_sel:
        # Este ES el valor que debe viajar en el <option value="...">
            proyecto_value = proyecto_sel.id
        # Lo que mostramos al usuario (nombre del proyecto)
            proyecto_label = getattr(proyecto_sel, "nombre", str(proyecto_sel))
        else:
        # Fallback para datos viejos por si no encontramos el Proyecto
            raw_label = (sesion.proyecto or sesion.proyecto_id or "").strip()
            proyecto_value = raw_label
            proyecto_label = raw_label   

        
    # =========================================================================

    return render(request, "operaciones/billing_editar.html", {
        "sesion": sesion,
        "clientes": list(clientes),
        "tecnicos": tecnicos,
        "items": items,
        "ids_tecnicos": ids_tecnicos,
        "proyectos": proyectos_qs,
        "proyecto_sel": proyecto_sel,
        "proyecto_value": proyecto_value,   # 👈
        "proyecto_label": proyecto_label,   # 👈
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

    Permisos:
      - superuser o usuario_historial: pueden enviar cualquier billing que vean
        en la lista (sin filtro extra por proyecto).
      - resto (admin/pm normales): solo pueden enviar billings de proyectos a los que
        tengan acceso en Project Visibility.
    """
    user = request.user

    # === 1) parsear ids + nota ===
    ids, note = [], ""
    if request.content_type and "application/json" in (request.content_type or ""):
        import json
        try:
            payload = json.loads(request.body.decode("utf-8"))
            ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
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

    # === 2) misma lógica de VISIBILIDAD que en listar_billing ===
    # Usuarios privilegiados de historial
    can_view_legacy_history = bool(
        user.is_superuser
        or getattr(user, "es_usuario_historial", False)
        or getattr(user, "usuario_historial", False)
    )

    # Mismo filtro de visibilidad que en listar_billing
    visible_filter = (
        # descuento directo todavía sin enviar
        (Q(is_direct_discount=True) & Q(finance_sent_at__isnull=True) & ~Q(finance_status='paid'))
        |
        # flujo normal: ocultar enviados / en proceso de cobro
        (Q(is_direct_discount=False) & ~Q(finance_status__in=['sent', 'pending', 'paid', 'in_review']))
    )

    # Base queryset: SOLO los billings que el usuario podría ver en la lista
    qs = (
        SesionBilling.objects
        .select_for_update()
        .filter(visible_filter, id__in=ids)
        .only(
            "id",
            "is_direct_discount",
            "estado",
            "finance_status",
            "finance_sent_at",
            "finance_updated_at",
            "finance_note",
            "proyecto",
            "proyecto_id",
        )
    )

    # === 3) Para usuarios normales, limitar por proyectos asignados (igual que listar_billing) ===
    if not can_view_legacy_history:
        try:
            proyectos_user = filter_queryset_by_access(
                Proyecto.objects.all(),  # modelo de proyectos
                user,
                "id",                    # permiso por id de Proyecto
            )
        except Exception:
            proyectos_user = Proyecto.objects.none()

        if proyectos_user.exists():
            allowed_keys = set()
            for p in proyectos_user:
                nombre = (getattr(p, "nombre", "") or "").strip()
                if nombre:
                    allowed_keys.add(nombre)

                codigo = getattr(p, "codigo", None)
                if codigo:
                    allowed_keys.add(str(codigo).strip())

                allowed_keys.add(str(p.id).strip())

            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            # sin proyectos asignados -> no puede enviar nada
            qs = SesionBilling.objects.none().select_for_update()

    # A partir de aquí, 'qs' contiene EXACTAMENTE los billings que
    # el usuario puede ver en la lista y sobre los que tiene permiso de enviar.

    updated_ids = []
    skipped = {}  # id -> reason (paid, invalid_status, ...)

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
                    s.finance_note + "\n" if s.finance_note else ""
                ) + prefix + note
            s.save(
                update_fields=[
                    "finance_status",
                    "finance_sent_at",
                    "finance_updated_at",
                    "finance_note",
                ]
            )
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
                    s.finance_note + "\n" if s.finance_note else ""
                ) + prefix + note
            s.save(
                update_fields=[
                    "finance_status",
                    "finance_sent_at",
                    "finance_updated_at",
                    "finance_note",
                ]
            )
            updated_ids.append(s.id)

    # Nota: si algún id vino en el POST pero NO estaba en 'qs',
    # simplemente no se procesa (es como si fuera "forbidden").
    # Eso garantiza que solo se envía lo que realmente ve en la lista.

    payload = {"ok": True, "count": len(updated_ids), "updated_ids": updated_ids}
    if skipped:
        payload["skipped"] = skipped

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(payload)

    if updated_ids:
        messages.success(request, f"Sent to Finance: {len(updated_ids)}.")
    if skipped:
        msg = ", ".join([f"#{i}: {r}" for i, r in skipped.items()])
        messages.warning(request, f"Skipped: {msg}")
    return redirect("operaciones:listar_billing")


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


@transaction.atomic
def _guardar_billing(request, sesion: SesionBilling | None = None):
    # ======================== Helpers locales ========================= #
    import json
    import re

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




# ===== Búsquedas / AJAX =====
def _precio_empresa(cliente, ciudad, proyecto, oficina, codigo):
    q = PrecioActividadTecnico.objects.filter(
        cliente__iexact=cliente,
        ciudad__iexact=ciudad,
        proyecto_id=proyecto,                  # ← antes proyecto__iexact
        oficina__iexact=oficina or "-",
        codigo_trabajo__iexact=codigo,
    ).first()
    return money(q.precio_empresa if q else 0)


def _tarifa_tecnico(tecnico_id, cliente, ciudad, proyecto, oficina, codigo):
    q = PrecioActividadTecnico.objects.filter(
        tecnico_id=tecnico_id,
        cliente__iexact=cliente,
        ciudad__iexact=ciudad,
        proyecto_id=proyecto,                  # ← antes proyecto__iexact
        oficina__iexact=oficina or "-",
        codigo_trabajo__iexact=codigo,
    ).first()
    return money(q.precio_tecnico if q else 0)


def _meta_codigo(cliente, ciudad, proyecto, oficina, codigo):
    p = PrecioActividadTecnico.objects.filter(
        cliente__iexact=cliente,
        ciudad__iexact=ciudad,
        proyecto_id=proyecto,                  # ← antes proyecto__iexact
        oficina__iexact=oficina or "-",
        codigo_trabajo__iexact=codigo,
    ).first()
    if not p:
        return None
    return {
        "tipo_trabajo": p.tipo_trabajo,
        "descripcion": p.descripcion,
        "unidad_medida": p.unidad_medida,
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
    cliente  = request.GET.get("client", "")
    ciudad   = request.GET.get("city", "")
    tech_ids = [int(x) for x in request.GET.getlist("tech_ids[]") if str(x).isdigit()]

    data = []

    if cliente and ciudad:
        # 🔒 Proyectos a los que ESTE usuario tiene acceso
        proyectos_visibles_qs = filter_queryset_by_access(
            Proyecto.objects.all(),
            request.user,
            'id',
        )
        visible_ids = list(proyectos_visibles_qs.values_list("id", flat=True))

        if visible_ids:
            qs = (
                PrecioActividadTecnico.objects
                .filter(
                    cliente__iexact=cliente,
                    ciudad__iexact=ciudad,
                    proyecto_id__in=visible_ids,  # 👈 solo proyectos visibles para el usuario
                )
                .select_related("proyecto")
                .order_by("proyecto_id")
            )

            # 👉 si hay técnicos seleccionados, solo proyectos donde esos técnicos tengan precios
            if tech_ids:
                qs = qs.filter(tecnico_id__in=tech_ids)

            seen = set()
            for p in qs:
                if not p.proyecto_id or p.proyecto_id in seen or p.proyecto is None:
                    continue
                seen.add(p.proyecto_id)
                data.append({
                    "id": p.proyecto_id,      # PK de facturacion.Proyecto
                    "label": str(p.proyecto), # cómo se muestra (nombre del proyecto)
                })
        else:
            # Usuario sin proyectos visibles → no hay opciones
            data = []

    return JsonResponse({"results": data})


@login_required
def ajax_oficinas(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto_id = request.GET.get("project", "")  # viene el PK del select

    data = []
    if cliente and ciudad and proyecto_id:
        data = list(
            PrecioActividadTecnico.objects.filter(
                cliente__iexact=cliente,
                ciudad__iexact=ciudad,
                proyecto_id=proyecto_id,
            )
            .values_list("oficina", flat=True)
            .distinct()
            .order_by("oficina")
        )

    return JsonResponse({"results": data})


@login_required
def ajax_buscar_codigos(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto_id = request.GET.get("project", "")   # ← es el PK
    oficina = request.GET.get("office", "")
    q = (request.GET.get("q") or "").strip()

    if not (cliente and ciudad and proyecto_id and oficina):
        return JsonResponse({"error": "missing_filters"}, status=400)

    qs = PrecioActividadTecnico.objects.filter(
        cliente__iexact=cliente,
        ciudad__iexact=ciudad,
        proyecto_id=proyecto_id,              # ← ahora por FK
        oficina__iexact=oficina or "-",
    )
    if q:
        qs = qs.filter(codigo_trabajo__istartswith=q)

    data = list(
        qs.values("codigo_trabajo", "tipo_trabajo", "descripcion", "unidad_medida")
          .distinct()
          .order_by("codigo_trabajo")[:20]
    )
    return JsonResponse({"results": data})


@login_required
def ajax_detalle_codigo(request):
    cliente = request.GET.get("client", "")
    ciudad = request.GET.get("city", "")
    proyecto = request.GET.get("project", "")   # PK
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
            "tarifa_efectiva": f"{(base * (Decimal(pct) / 100)):.2f}",
        })

    return JsonResponse({
        "tipo_trabajo": meta["tipo_trabajo"],
        "descripcion": meta["descripcion"],
        "unidad_medida": meta["unidad_medida"],
        "precio_empresa": f"{precio_emp:.2f}",
        "desglose_tecnico": desglose,
    })


ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}


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





@login_required
@rol_requerido('admin', 'supervisor', 'pm', 'facturacion')
def produccion_admin(request):
    """
    Producción por técnico (vista Admin) con filtros + paginación.
    - Lista sesiones aprobadas (o descuentos directos) desglosadas por técnico.
    - Incluye también los ajustes manuales (bonus, advance, fixed_salary).
    - En esta vista, **advance cuenta POSITIVO**.
    - La semana usada para filtrar es la REAL:
        sesiones -> 'semana_pago_real'
        ajustes  -> 'week'

    NOTA: Usuarios "privilegiados" (superuser o rol usuario_historial)
    pueden ver TODO el historial (modo antiguo).
    El resto se rige por asignación de proyectos + ventana de visibilidad (lógica nueva).
    """
    import re
    from decimal import Decimal
    from urllib.parse import urlencode

    from django.core.paginator import Paginator
    from django.db.models import CharField, Q
    from django.db.models.functions import Cast
    from django.utils import timezone

    # ---------------- helpers ----------------
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

    def _week_sort_key(week_str: str):
        """
        Acepta variantes como '2025-W40', '2025W40', 'W40'.
        Devuelve (año, semana) para ordenar. Si no hay dato, (-inf).
        """
        if not week_str:
            return (-1, -1)

        s = str(week_str).upper().replace("WEEK", "W").replace(" ", "")
        # 1) YYYY-W##
        m = re.search(r'(\d{4})-?W(\d{1,2})', s)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        # 2) Solo W##
        m = re.search(r'W(\d{1,2})', s)
        if m:
            # Si no hay año, usamos 0 para que queden al final
            return (0, int(m.group(1)))
        return (-1, -1)

    # ---------------- configuración ----------------
    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    current_week = _iso_week_str(timezone.now())

    # Usuarios privilegiados que pueden ver TODO el historial
    user = request.user
    can_view_legacy_history = (
        user.is_superuser or
        getattr(user, "es_usuario_historial", False)
    )

    # ---------------- Filtros GET ----------------
    f_project = (request.GET.get("f_project") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_client = (request.GET.get("f_client") or "").strip()

    exact_week, week_token = parse_week_query(f_week_input)

    # ---------------- Proyectos visibles para el usuario ----------------
    # Igual que en listar_billing: aquí ya se respeta "history" vs fecha_inicio.
    # PERO si es usuario de historial o superuser, ve TODOS los proyectos.
    try:
        base_proyectos = Proyecto.objects.all()
        if can_view_legacy_history:
            proyectos_user = base_proyectos
        else:
            proyectos_user = filter_queryset_by_access(
                base_proyectos,
                request.user,
                'id',
            )
    except Exception:
        proyectos_user = Proyecto.objects.none()

    if proyectos_user.exists():
        allowed_keys = set()
        for p in proyectos_user:
            # nombre legible del proyecto
            nombre = (getattr(p, "nombre", "") or "").strip()
            if nombre:
                allowed_keys.add(nombre)

            # compatibilidad: código y id
            codigo = getattr(p, "codigo", None)
            if codigo:
                allowed_keys.add(str(codigo).strip())
            allowed_keys.add(str(p.id).strip())
    else:
        # sin proyectos asignados → no ve nada (para usuarios normales)
        allowed_keys = set()

    # ---------------- Query base: Sesiones ----------------
    qs = (
        SesionBilling.objects
        .filter(Q(estado__in=estados_ok) | Q(is_direct_discount=True))
        .order_by("-creado_en")
        .prefetch_related(
            "tecnicos_sesion__tecnico",
            "items__desglose_tecnico",
        )
        .distinct()
    )

    # 🔒 limitar por proyectos asignados (campo texto "proyecto")
    # SOLO para usuarios normales; los de historial ven todo.
    if not can_view_legacy_history:
        if allowed_keys:
            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            qs = SesionBilling.objects.none()

    # Semana REAL en sesiones (semana_pago_real)
    if exact_week:
        token = exact_week.split("-", 1)[-1].upper()  # 'W##'
        qs = qs.filter(
            Q(semana_pago_real__iexact=exact_week) |
            Q(semana_pago_real__icontains=token)
        )
    elif week_token:
        qs = qs.filter(semana_pago_real__icontains=week_token)

    # Otros filtros de sesiones
    if f_project:
        qs = qs.annotate(proyecto_id_str=Cast('proyecto_id', CharField()))
        qs = qs.filter(
            Q(proyecto_id_str__icontains=f_project) |
            Q(proyecto__icontains=f_project)
        )
    if f_client:
        qs = qs.filter(cliente__icontains=f_client)

    # ---------------- Construcción de filas ----------------
    filas = []

    # 1) Sesiones por técnico
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

                # rate y qty ya vienen con signo (qty negativa para descuento)
                rate = bd.tarifa_efectiva if isinstance(
                    bd.tarifa_efectiva, Decimal) else Decimal(str(bd.tarifa_efectiva or 0))
                qty = it.cantidad if isinstance(
                    it.cantidad, Decimal) else Decimal(str(it.cantidad or 0))

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
                "week": s.semana_pago_real or "—",  # columna principal = semana REAL
                "status": s.estado,
                "is_discount": bool(getattr(s, "is_direct_discount", False)),
                "client": s.cliente,
                "city": s.ciudad,
                "project": s.proyecto,
                "office": s.oficina,
                "real_week": s.semana_pago_real or "—",
                "proj_week": s.semana_pago_proyectada or "—",
                "total_tecnico": total_tecnico,
                "detalle": detalle,
                "adjustment_type": "",
            })

    # 2) Ajustes manuales (si existe el modelo)
    if AdjustmentEntry is not None:
        adj_qs = AdjustmentEntry.objects.select_related("technician")

        # 🔒 limitar ajustes a los proyectos asignados
        if not can_view_legacy_history:
            if allowed_keys:
                adj_qs = adj_qs.filter(
                    Q(project__in=allowed_keys) |
                    Q(project_id__in=allowed_keys)
                )
            else:
                adj_qs = AdjustmentEntry.objects.none()

        # Semana real para ajustes (campo week)
        if exact_week:
            token = exact_week.split("-", 1)[-1].upper()  # 'W##'
            adj_qs = adj_qs.filter(Q(week__iexact=exact_week)
                                   | Q(week__icontains=token))
        elif week_token:
            adj_qs = adj_qs.filter(week__icontains=week_token)

        # Filtros Project / Client / Tech para ajustes (campos “ligeros”)
        if f_project:
            adj_qs = adj_qs.annotate(project_id_str=Cast('project_id', CharField()))
            adj_qs = adj_qs.filter(
                Q(project_id_str__icontains=f_project) |
                Q(project__icontains=f_project)
            )
        if f_client:
            adj_qs = adj_qs.filter(client__icontains=f_client)
        if f_tech:
            target = f_tech
            adj_qs = adj_qs.filter(
                Q(technician__first_name__icontains=target) |
                Q(technician__last_name__icontains=target) |
                Q(technician__username__icontains=target)
            )

        for a in adj_qs:
            t = a.technician
            amt = a.amount if isinstance(
                a.amount, Decimal) else Decimal(str(a.amount or 0))
            # En esta vista, bonus/advance/fixed_salary SIEMPRE POSITIVOS
            signed_amount = amt.copy_abs()

            filas.append({
                "sesion": None,
                "tecnico": t,
                "project_id": "-",
                "week": a.week or "—",
                "status": "",
                "is_discount": False,
                "client": a.client,
                "city": a.city,
                "project": a.project,
                "office": a.office,
                "real_week": a.week or "—",
                "proj_week": a.week or "—",
                "total_tecnico": signed_amount,
                "detalle": [],
                "adjustment_type": a.adjustment_type,
                "adjustment_id": a.id,
            })

    # --------- Resolver label de proyecto (nombre) para cada fila ---------
    # Igual que en billing: usamos SOLO los proyectos visibles (proyectos_user)
    proyectos_list = list(proyectos_user)
    by_id = {p.id: p for p in proyectos_list}
    by_code = {
        (p.codigo or "").strip().lower(): p
        for p in proyectos_list
        if getattr(p, "codigo", None)
    }
    by_name = {
        (p.nombre or "").strip().lower(): p
        for p in proyectos_list
        if getattr(p, "nombre", None)
    }

    def _resolve_project_label(row):
        s = row.get("sesion")
        proj_text = None
        proj_id = None

        if s is not None:
            proj_text = (getattr(s, "proyecto", "") or "").strip()
            proj_id = getattr(s, "proyecto_id", None)
        else:
            proj_text = (row.get("project") or "").strip()
            proj_id = row.get("project_id", None)

        proyecto_sel = None

        # 1) intentar interpretar proj_text como PK
        if proj_text:
            try:
                pid = int(proj_text)
            except (TypeError, ValueError):
                key = proj_text.lower()
                proyecto_sel = by_code.get(key) or by_name.get(key)
            else:
                proyecto_sel = by_id.get(pid)

        # 2) si no, probar con project_id
        if not proyecto_sel and proj_id not in (None, "", "-"):
            try:
                pid2 = int(proj_id)
            except (TypeError, ValueError):
                key2 = str(proj_id).strip().lower()
                proyecto_sel = by_code.get(key2) or by_name.get(key2)
            else:
                proyecto_sel = by_id.get(pid2)

        if proyecto_sel:
            return getattr(proyecto_sel, "nombre", str(proyecto_sel))

        # Fallback: lo que ya teníamos
        if proj_text:
            return proj_text
        if proj_id not in (None, "", "-"):
            return str(proj_id)
        return ""

    for row in filas:
        row["project_label"] = _resolve_project_label(row)

    # --------- Filtro adicional por texto de Project (incluye project_label) ---------
    if f_project:
        needle = f_project.lower()

        def _match_project_text(row):
            return (
                needle in str(row.get("project_id") or "").lower() or
                needle in str(row.get("project") or "").lower() or
                needle in str(row.get("project_label") or "").lower()
            )

        filas = [r for r in filas if _match_project_text(r)]

    # --------------- Filtro defensivo por semana en memoria ---------------
    if exact_week or week_token:
        token = (week_token or exact_week.split("-", 1)[-1]).upper()  # 'W##'
        exact_norm = exact_week.upper() if exact_week else None

        def _match_row_real(r):
            rw = _normalize_week_str(r["real_week"])
            if exact_norm:
                return (rw == exact_norm) or (token in rw)
            return token in rw

        filas = [r for r in filas if _match_row_real(r)]

    # --------- Ventana de visibilidad por ProyectoAsignacion ---------
    # Solo se aplica a usuarios "normales". Los de historial ven todo.
    try:
        asignaciones = list(
            ProyectoAsignacion.objects
            .filter(usuario=request.user, proyecto__in=proyectos_list)
            .select_related("proyecto")
        )
    except Exception:
        asignaciones = []

    if asignaciones and not can_view_legacy_history:
        # Mapa PK de proyecto -> reglas de acceso
        access_by_pk = {}
        for a in asignaciones:
            if a.include_history or not a.start_at:
                access_by_pk[a.proyecto_id] = {
                    "include_history": True,
                    "start_week": None,
                }
            else:
                access_by_pk[a.proyecto_id] = {
                    "include_history": False,
                    # semana ISO a partir de la fecha de inicio de visibilidad
                    "start_week": _iso_week_str(a.start_at),
                }

        def _project_pk_from_row(row):
            """
            Intenta obtener el PK de Proyecto para la fila, usando primero
            sesion.proyecto_id y, si no, el nombre/código ya resuelto.
            """
            s = row.get("sesion")

            # 1) intentar con proyecto_id de la sesión (charfield)
            if s is not None:
                raw = getattr(s, "proyecto_id", None)
                if raw not in (None, "", "-"):
                    try:
                        return int(raw)
                    except (TypeError, ValueError):
                        pass

            # 2) intentar por nombre/código usando los mapas by_name / by_code
            text = (
                str(row.get("project_label") or "").strip()
                or str(row.get("project") or "").strip()
            )
            key = text.lower()
            if key:
                p = by_name.get(key)
                if p:
                    return p.id
                p = by_code.get(key)
                if p:
                    return p.id
            return None

        def _row_allowed(row):
            """
            Devuelve True si el usuario puede ver esta fila
            según include_history / start_at de ProyectoAsignacion.
            """
            pk = _project_pk_from_row(row)
            if pk is None:
                # fila sin proyecto asociado o no asignado al usuario
                return False

            access = access_by_pk.get(pk)
            if not access:
                return False

            # Si tiene historial completo, no restringimos por semana
            if access["include_history"] or access["start_week"] is None:
                return True

            # Comparar la semana REAL de la fila vs la semana de inicio
            week_str = _normalize_week_str(row.get("real_week"))
            if not week_str:
                return False

            return _week_sort_key(week_str) >= _week_sort_key(access["start_week"])

        filas = [r for r in filas if _row_allowed(r)]

    # --------------- Paginación ---------------
    cantidad = request.GET.get("cantidad", "10")

    # Solo aceptamos estos tamaños; cualquier otro valor cae a 10
    allowed_page_sizes = {"5", "10", "20", "50", "100"}
    if cantidad not in allowed_page_sizes:
        cantidad = "10"

    try:
        per_page = int(cantidad)
    except ValueError:
        per_page = 10

    paginator = Paginator(filas, per_page)
    page_number = request.GET.get("page") or 1
    pagina = paginator.get_page(page_number)

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
    Exporta a Excel:
      Project ID | Real pay week | Status | Technician | Client | City | Project | Office | Technical Billing

    MUY IMPORTANTE:
    - Usa EXACTAMENTE la misma lógica de filtros/visibilidad que produccion_admin
      (proyectos asignados + ventana ProyectoAsignacion + rol usuario_historial).
    - Por lo tanto, el número de filas exportadas SIEMPRE coincide con las que ve el usuario
      en la vista Producción Admin (ignorando solo la paginación).
    """
    import re
    from decimal import Decimal

    from django.db.models import CharField, Q
    from django.db.models.functions import Cast
    from django.http import HttpResponse
    from django.utils import timezone
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    from facturacion.models import Proyecto
    from operaciones.models import SesionBilling
    try:
        from operaciones.models import AdjustmentEntry
    except Exception:
        AdjustmentEntry = None

    from usuarios.models import ProyectoAsignacion  # ventana de visibilidad

    # ---------------- helpers (copiados de produccion_admin) ----------------
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

    def _week_sort_key(week_str: str):
        """
        Acepta variantes como '2025-W40', '2025W40', 'W40'.
        Devuelve (año, semana) para ordenar. Si no hay dato, (-inf).
        """
        if not week_str:
            return (-1, -1)

        s = str(week_str).upper().replace("WEEK", "W").replace(" ", "")
        # 1) YYYY-W##
        m = re.search(r'(\d{4})-?W(\d{1,2})', s)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        # 2) Solo W##
        m = re.search(r'W(\d{1,2})', s)
        if m:
            # Si no hay año, usamos 0 para que queden al final
            return (0, int(m.group(1)))
        return (-1, -1)

    def _status_label_export(sesion_estado: str, is_discount: bool) -> str:
        """Etiqueta en inglés para el Excel."""
        if is_discount:
            return "Direct discount"
        mapping = {
            "aprobado_pm": "Approved by PM",
            "aprobado_supervisor": "Approved by Supervisor",
            "aprobado_finanzas": "Approved by Finance",
            "rechazado_pm": "Rejected by PM",
            "rechazado_supervisor": "Rejected by Supervisor",
            "en_revision_supervisor": "In Supervisor Review",
            "finalizado": "Finished (pending review)",
            "en_proceso": "In Progress",
            "asignado": "Assigned",
        }
        return mapping.get((sesion_estado or "").lower(), (sesion_estado or ""))

    # ---------------- configuración (igual que produccion_admin) ----------------
    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    current_week = _iso_week_str(timezone.now())

    user = request.user
    can_view_legacy_history = (
        user.is_superuser or
        getattr(user, "es_usuario_historial", False)
    )

    # ---------------- Filtros GET (igual que produccion_admin) ----------------
    f_project = (request.GET.get("f_project") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_client = (request.GET.get("f_client") or "").strip()

    exact_week, week_token = parse_week_query(f_week_input)

    # ---------------- Proyectos visibles para el usuario ----------------
    try:
        base_proyectos = Proyecto.objects.all()
        if can_view_legacy_history:
            proyectos_user = base_proyectos
        else:
            proyectos_user = filter_queryset_by_access(
                base_proyectos,
                request.user,
                'id',
            )
    except Exception:
        proyectos_user = Proyecto.objects.none()

    if proyectos_user.exists():
        allowed_keys = set()
        for p in proyectos_user:
            nombre = (getattr(p, "nombre", "") or "").strip()
            if nombre:
                allowed_keys.add(nombre)

            codigo = getattr(p, "codigo", None)
            if codigo:
                allowed_keys.add(str(codigo).strip())
            allowed_keys.add(str(p.id).strip())
    else:
        allowed_keys = set()

    # ---------------- Query base: Sesiones ----------------
    qs = (
        SesionBilling.objects
        .filter(Q(estado__in=estados_ok) | Q(is_direct_discount=True))
        .order_by("-creado_en")
        .prefetch_related(
            "tecnicos_sesion__tecnico",
            "items__desglose_tecnico",
        )
        .distinct()
    )

    # Igual que en produccion_admin: solo limitamos por proyectos si NO es historial
    if not can_view_legacy_history:
        if allowed_keys:
            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            qs = SesionBilling.objects.none()

    # Semana REAL en sesiones (semana_pago_real)
    if exact_week:
        token = exact_week.split("-", 1)[-1].upper()
        qs = qs.filter(
            Q(semana_pago_real__iexact=exact_week) |
            Q(semana_pago_real__icontains=token)
        )
    elif week_token:
        qs = qs.filter(semana_pago_real__icontains=week_token)

    if f_project:
        qs = qs.annotate(proyecto_id_str=Cast('proyecto_id', CharField()))
        qs = qs.filter(
            Q(proyecto_id_str__icontains=f_project) |
            Q(proyecto__icontains=f_project)
        )
    if f_client:
        qs = qs.filter(cliente__icontains=f_client)

    # ---------------- Construcción de filas (IGUAL QUE produccion_admin) ----------------
    filas = []

    # 1) Sesiones por técnico
    for s in qs:
        for asig in s.tecnicos_sesion.all():
            tecnico = asig.tecnico

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
                "week": s.semana_pago_real or "—",
                "status": s.estado,
                "is_discount": bool(getattr(s, "is_direct_discount", False)),
                "client": s.cliente,
                "city": s.ciudad,
                "project": s.proyecto,
                "office": s.oficina,
                "real_week": s.semana_pago_real or "—",
                "proj_week": s.semana_pago_proyectada or "—",
                "total_tecnico": total_tecnico,
                "detalle": detalle,
                "adjustment_type": "",
            })

    # 2) Ajustes manuales
    if AdjustmentEntry is not None:
        adj_qs = AdjustmentEntry.objects.select_related("technician")

        if not can_view_legacy_history:
            if allowed_keys:
                adj_qs = adj_qs.filter(
                    Q(project__in=allowed_keys) |
                    Q(project_id__in=allowed_keys)
                )
            else:
                adj_qs = AdjustmentEntry.objects.none()

        if exact_week:
            token = exact_week.split("-", 1)[-1].upper()
            adj_qs = adj_qs.filter(Q(week__iexact=exact_week)
                                   | Q(week__icontains=token))
        elif week_token:
            adj_qs = adj_qs.filter(week__icontains=week_token)

        if f_project:
            adj_qs = adj_qs.annotate(project_id_str=Cast('project_id', CharField()))
            adj_qs = adj_qs.filter(
                Q(project_id_str__icontains=f_project) |
                Q(project__icontains=f_project)
            )
        if f_client:
            adj_qs = adj_qs.filter(client__icontains=f_client)
        if f_tech:
            target = f_tech
            adj_qs = adj_qs.filter(
                Q(technician__first_name__icontains=target) |
                Q(technician__last_name__icontains=target) |
                Q(technician__username__icontains=target)
            )

        for a in adj_qs:
            t = a.technician
            amt = a.amount if isinstance(
                a.amount, Decimal) else Decimal(str(a.amount or 0))
            signed_amount = amt.copy_abs()  # SIEMPRE POSITIVO

            filas.append({
                "sesion": None,
                "tecnico": t,
                "project_id": "-",
                "week": a.week or "—",
                "status": "",
                "is_discount": False,
                "client": a.client,
                "city": a.city,
                "project": a.project,
                "office": a.office,
                "real_week": a.week or "—",
                "proj_week": a.week or "—",
                "total_tecnico": signed_amount,
                "detalle": [],
                "adjustment_type": a.adjustment_type,
                "adjustment_id": a.id,
            })

    # --------- Resolver label de proyecto (igual que produccion_admin) ---------
    proyectos_list = list(proyectos_user)
    by_id = {p.id: p for p in proyectos_list}
    by_code = {
        (p.codigo or "").strip().lower(): p
        for p in proyectos_list
        if getattr(p, "codigo", None)
    }
    by_name = {
        (p.nombre or "").strip().lower(): p
        for p in proyectos_list
        if getattr(p, "nombre", None)
    }

    def _resolve_project_label(row):
        s = row.get("sesion")
        proj_text = None
        proj_id = None

        if s is not None:
            proj_text = (getattr(s, "proyecto", "") or "").strip()
            proj_id = getattr(s, "proyecto_id", None)
        else:
            proj_text = (row.get("project") or "").strip()
            proj_id = row.get("project_id", None)

        proyecto_sel = None

        if proj_text:
            try:
                pid = int(proj_text)
            except (TypeError, ValueError):
                key = proj_text.lower()
                proyecto_sel = by_code.get(key) or by_name.get(key)
            else:
                proyecto_sel = by_id.get(pid)

        if not proyecto_sel and proj_id not in (None, "", "-"):
            try:
                pid2 = int(proj_id)
            except (TypeError, ValueError):
                key2 = str(proj_id).strip().lower()
                proyecto_sel = by_code.get(key2) or by_name.get(key2)
            else:
                proyecto_sel = by_id.get(pid2)

        if proyecto_sel:
            return getattr(proyecto_sel, "nombre", str(proyecto_sel))

        if proj_text:
            return proj_text
        if proj_id not in (None, "", "-"):
            return str(proj_id)
        return ""

    for row in filas:
        row["project_label"] = _resolve_project_label(row)

    # --------- Filtro adicional por texto de Project ---------
    if f_project:
        needle = f_project.lower()

        def _match_project_text(row):
            return (
                needle in str(row.get("project_id") or "").lower() or
                needle in str(row.get("project") or "").lower() or
                needle in str(row.get("project_label") or "").lower()
            )

        filas = [r for r in filas if _match_project_text(r)]

    # --------- Filtro defensivo por semana ---------
    if exact_week or week_token:
        token = (week_token or exact_week.split("-", 1)[-1]).upper()
        exact_norm = exact_week.upper() if exact_week else None

        def _match_row_real(r):
            rw = _normalize_week_str(r["real_week"])
            if exact_norm:
                return (rw == exact_norm) or (token in rw)
            return token in rw

        filas = [r for r in filas if _match_row_real(r)]

    # --------- Ventana de visibilidad por ProyectoAsignacion ---------
    try:
        asignaciones = list(
            ProyectoAsignacion.objects
            .filter(usuario=request.user, proyecto__in=proyectos_list)
            .select_related("proyecto")
        )
    except Exception:
        asignaciones = []

    if asignaciones and not can_view_legacy_history:
        access_by_pk = {}
        for a in asignaciones:
            if a.include_history or not a.start_at:
                access_by_pk[a.proyecto_id] = {
                    "include_history": True,
                    "start_week": None,
                }
            else:
                access_by_pk[a.proyecto_id] = {
                    "include_history": False,
                    "start_week": _iso_week_str(a.start_at),
                }

        def _project_pk_from_row(row):
            s = row.get("sesion")

            if s is not None:
                raw = getattr(s, "proyecto_id", None)
                if raw not in (None, "", "-"):
                    try:
                        return int(raw)
                    except (TypeError, ValueError):
                        pass

            text = (
                str(row.get("project_label") or "").strip()
                or str(row.get("project") or "").strip()
            )
            key = text.lower()
            if key:
                p = by_name.get(key)
                if p:
                    return p.id
                p = by_code.get(key)
                if p:
                    return p.id
            return None

        def _row_allowed(row):
            pk = _project_pk_from_row(row)
            if pk is None:
                return False

            access = access_by_pk.get(pk)
            if not access:
                return False

            if access["include_history"] or access["start_week"] is None:
                return True

            week_str = _normalize_week_str(row.get("real_week"))
            if not week_str:
                return False

            return _week_sort_key(week_str) >= _week_sort_key(access["start_week"])

        filas = [r for r in filas if _row_allowed(r)]

    # --------------- Orden por semana real (misma que vista) ---------------
    filas.sort(key=lambda r: _week_sort_key(r["real_week"]), reverse=True)

    # ============ A PARTIR DE AQUÍ SOLO GENERAMOS EL EXCEL ============

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

        # Status: sesiones vs ajustes
        if r.get("adjustment_type"):
            status = {
                "bonus": "Bonus",
                "advance": "Advance",
                "fixed_salary": "Fixed salary",
            }.get(r["adjustment_type"], r["adjustment_type"])
        else:
            status = _status_label_export(r.get("status", ""), r.get("is_discount", False))

        project_cell = (
            r.get("project_label")
            or r.get("project")
            or "-"
        )

        ws.append([
            r.get("project_id", "-") or "-",
            r.get("week", "") or r.get("real_week", ""),
            status or "",
            tech_name,
            r.get("client", "-") or "-",
            r.get("city", "-") or "-",
            project_cell,
            r.get("office", "-") or "-",
            float(r.get("total_tecnico") or 0.0),
        ])

    # Auto ancho + formato numérico
    for col in ws.columns:
        max_len = 0
        letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                max_len = max(max_len, len(str(cell.value or "")))
            except Exception:
                pass
        ws.column_dimensions[letter].width = min(max(10, max_len + 2), 50)

    # Columna de monto
    last_col = len(headers)
    for col_cells in ws.iter_cols(
        min_col=last_col, max_col=last_col, min_row=2, values_only=False
    ):
        for c in col_cells:
            c.number_format = '#,##0.00'

    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = 'attachment; filename="production_export.xlsx"'
    wb.save(resp)
    return resp



@login_required
@rol_requerido('usuario')
def produccion_usuario(request):
    """
    Producción del técnico logueado.
    - sesiones aprobadas (Supervisor/PM/Finanzas)
    - descuentos directos (is_direct_discount=True)
    - sesiones con línea negativa del técnico
    - ajustes (Bonus/Advance/Fixed salary) SIEMPRE positivos
    - orden: semana actual, luego pasadas (W40,W39,...) descendente, luego futuras y sin semana
    - paginación: ?cantidad=5|10|20|todos y ?page=N
    """
    import re
    from decimal import Decimal
    from urllib.parse import urlencode

    from django.core.paginator import Paginator
    from django.db.models import Q
    from django.utils import timezone

    tecnico = request.user

    def _iso_week_str(dt):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{int(w):02d}"

    def _parse_iso_week(s: str):
        """Devuelve (year, week) o None (si no es válido)."""
        if not s:
            return None
        s = s.strip().upper().replace(" ", "")
        m = re.fullmatch(r"(\d{4})-?W(\d{1,2})", s)
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)))

    estados_ok = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}
    current_week = _iso_week_str(timezone.now())
    current_tuple = _parse_iso_week(current_week)

    # filtro por semana REAL: "all" o "YYYY-W##"
    week_filter = (request.GET.get("week") or "all").strip()
    weeks_wanted = None if week_filter.lower() == "all" else {
        week_filter.upper()}

    filas = []
    total_semana_actual = Decimal("0")

    # -------- Sesiones de producción --------
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

    for s in qs:
        rw = (s.semana_pago_real or "").upper()
        if weeks_wanted is not None and rw not in weeks_wanted:
            continue

        detalle = []
        total_tecnico = Decimal("0")
        tiene_linea_negativa = False

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

        is_discount_row = bool(
            getattr(s, "is_direct_discount", False) or tiene_linea_negativa)

        if rw == current_week:
            total_semana_actual += total_tecnico  # incluye negativos

        filas.append({
            "sesion": s,
            "project_id": s.proyecto_id,
            "week": s.semana_pago_proyectada or "—",
            "status": s.estado,
            "is_discount": is_discount_row,
            "client": s.cliente,
            "city": s.ciudad,
            "project": s.proyecto,
            "office": s.oficina,
            "real_week": s.semana_pago_real or "—",
            "total_tecnico": total_tecnico,
            "detalle": detalle,
            "adjustment_type": "",
            "adjustment_label": "",
        })

    # -------- Ajustes (Bonus / Advance / Fixed salary) --------
    adj_qs = AdjustmentEntry.objects.filter(technician=tecnico)
    if weeks_wanted is not None:
        adj_qs = adj_qs.filter(week__in=weeks_wanted)

    for a in adj_qs:
        amt = a.amount if isinstance(
            a.amount, Decimal) else Decimal(str(a.amount or 0))
        amt_pos = abs(amt)  # SIEMPRE positivo
        rw = (a.week or "—").upper()

        if rw == current_week:
            total_semana_actual += amt_pos

        filas.append({
            "sesion": None,
            "project_id": a.project_id or "",
            "week": a.week or "—",
            "status": "",
            "is_discount": False,
            "client": a.client or "-",
            "city": a.city or "-",
            "project": a.project or "-",
            "office": a.office or "-",
            "real_week": rw,
            "total_tecnico": amt_pos,
            "detalle": [],
            "adjustment_type": a.adjustment_type,
            "adjustment_label": a.get_adjustment_type_display(),
        })

    # -------- Orden requerido --------
    def sort_key(row):
        t = _parse_iso_week(row["real_week"])
        if t is None:
            return (3, 9999, 99)          # sin semana
        if t == current_tuple:
            return (0, 0, 0)              # actual arriba
        if t < current_tuple:
            return (1, -t[0], -t[1])      # pasadas: descendente (W40,W39,...)
        return (2, t[0], t[1])            # futuras

    filas.sort(key=sort_key)

    # -------- Paginación (igual Admin) --------
    cantidad = (request.GET.get("cantidad") or "10").strip().lower()
    if cantidad != "todos":
        try:
            per_page = max(5, min(int(cantidad), 100))
        except ValueError:
            per_page = 10
            cantidad = "10"
        paginator = Paginator(filas, per_page)
        page_number = request.GET.get("page") or 1
        pagina = paginator.get_page(page_number)
    else:
        class _OnePage:
            number = 1
            has_previous = False
            has_next = False

            @property
            def paginator(self):
                class P:
                    num_pages = 1
                return P()
            object_list = filas
        pagina = _OnePage()

    # mantener filtros en los links
    keep = {"week": week_filter, "cantidad": cantidad}
    filters_qs = urlencode({k: v for k, v in keep.items() if v})

    return render(request, "operaciones/produccion_usuario.html", {
        "pagina": pagina,
        "cantidad": cantidad,
        "current_week": current_week,
        "total_semana_actual": total_semana_actual,
        "week_filter": week_filter,
        "filters_qs": filters_qs,
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
    Sincroniza WeeklyPayment con la producción **y** ajustes:
    - amount = (producción aprobada + líneas negativas) + (bonus/advance/fixed_salary)
    - Si cambia el monto: pasa a pending_payment cuando estaba approved_user.
    - Elimina registros sin producción/ajustes (total == 0) si no están pagados.
    - create_missing=True crea los que faltan (solo si total != 0).
    """
    # ===== 1) PRODUCCIÓN (ItemBillingTecnico) =====
    base_items = (
        ItemBillingTecnico.objects
        .filter(item__sesion__semana_pago_real__gt="")
        .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
    )
    if week:
        base_items = base_items.filter(item__sesion__semana_pago_real=week)

    dec0 = Value(Decimal("0.00"), output_field=DecimalField(
        max_digits=18, decimal_places=2))

    agg_items = (
        base_items
        .values("tecnico_id", "item__sesion__semana_pago_real")
        .annotate(total=Coalesce(Sum("subtotal"), dec0,
                                 output_field=DecimalField(max_digits=18, decimal_places=2)))
    )

    items_totals = {
        (r["tecnico_id"], r["item__sesion__semana_pago_real"]): (r["total"] or Decimal("0.00"))
        for r in agg_items
    }

    # ===== 2) AJUSTES (AdjustmentEntry) =====
    try:
        from operaciones.models import AdjustmentEntry
    except Exception:
        AdjustmentEntry = None

    adj_totals = {}
    if AdjustmentEntry is not None:
        adj_qs = AdjustmentEntry.objects.all()
        if week:
            adj_qs = adj_qs.filter(week=week)

        agg_adj = (
            adj_qs
            .values("technician_id", "week")
            .annotate(total=Coalesce(Sum("amount"), dec0,
                                     output_field=DecimalField(max_digits=18, decimal_places=2)))
        )
        adj_totals = {
            (r["technician_id"], r["week"]): (r["total"] or Decimal("0.00"))
            for r in agg_adj
        }

    # ===== 3) SUMA (producción + ajustes) POR (tecnico, week) =====
    from collections import defaultdict
    merged = defaultdict(lambda: Decimal("0.00"))
    for k, v in items_totals.items():
        merged[k] += v
    for k, v in adj_totals.items():
        merged[k] += v

    # descarta exactamente 0
    prod_totals = {k: v for k, v in merged.items() if v != 0}

    updated = deleted = created = 0

    # ===== 4) Actualiza / elimina existentes =====
    for wp in WeeklyPayment.objects.select_for_update():
        if week and wp.week != week:
            continue

        key = (wp.technician_id, wp.week)
        if key not in prod_totals:
            if wp.status != "paid":
                wp.delete()
                deleted += 1
            continue

        total = prod_totals.pop(key)
        if wp.amount != total:
            wp.amount = total
            save_fields = ["amount", "updated_at"]
            if wp.status == "approved_user":
                wp.status = "pending_payment"
                save_fields.append("status")
            wp.save(update_fields=save_fields)
            updated += 1

    # ===== 5) Crea faltantes =====
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



# ...

# ================================ ADMIN / PM ================================ #

def _visible_tech_ids_for_user(user):
    """
    Devuelve:
      - None  => sin restricción (ve a todos)
      - set() => IDs de técnicos que el usuario puede ver

    Regla:
      - admin / superuser -> todos
      - facturación SIN ser pm/supervisor -> todos
      - pm / supervisor (aunque tengan facturación) -> solo usuarios
        que comparten al menos un proyecto con ellos (+ ellos mismos)
      - otros -> solo ellos mismos
    """
    from usuarios.models import \
        ProyectoAsignacion  # import local para evitar ciclos

    # Siempre puede verse a sí mismo
    ids = {user.id}

    tiene_rol = getattr(user, "tiene_rol", None)
    if not callable(tiene_rol):
        # Por seguridad: si no tenemos helper de roles, solo él mismo
        return ids

    # 1) Admin / superuser -> sin filtro
    if user.is_superuser or getattr(user, "es_admin_general", False):
        return None

    # 2) Facturación pura (NO pm/supervisor) -> sin filtro
    if getattr(user, "es_facturacion", False) and not (
        tiene_rol("pm") or tiene_rol("supervisor")
    ):
        return None

    # 3) Si NO es pm ni supervisor -> sólo él mismo
    if not (tiene_rol("pm") or tiene_rol("supervisor")):
        return ids

    # 4) pm / supervisor -> técnicos con proyectos en común
    my_project_ids = ProyectoAsignacion.objects.filter(
        usuario=user
    ).values_list("proyecto_id", flat=True)

    if not my_project_ids:
        return ids  # sólo él mismo si no tiene proyectos asignados

    others = ProyectoAsignacion.objects.filter(
        proyecto_id__in=my_project_ids
    ).values_list("usuario_id", flat=True).distinct()

    ids.update(others)
    return ids


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@never_cache
def admin_weekly_payments(request):
    """
    Pagos semanales:
    - TOP: semana actual (no pagados; crea faltantes). Muestra desglose por proyecto y
      además líneas para Direct discount y para ajustes (Fixed salary/Bonus/Advance).
    - Bottom (Paid): historial con filtros + paginación y el mismo desglose.

    ⚠️ Visibilidad:
      - Admin / superuser -> todos los técnicos
      - Facturación sola -> todos los técnicos
      - PM / Supervisor -> solo técnicos que comparten proyecto con él (+ él mismo)
      - Otros -> solo él mismo
    """
    import re
    from collections import defaultdict

    # ========= Helpers locales =========
    def _norm_week_input(raw: str) -> str:
        s = (raw or "").strip().upper()
        if not s:
            return ""
        m_year = re.match(r"^(\d{4})[- ]?W?(\d{1,2})$", s)
        if m_year:
            yy = int(m_year.group(1))
            ww = int(m_year.group(2))
            return f"{yy}-W{ww:02d}"
        y, w, _ = timezone.localdate().isocalendar()
        m_now = re.match(r"^W?(\d{1,2})$", s)
        if m_now:
            ww = int(m_now.group(1))
            return f"{y}-W{ww:02d}"
        return s

    def _dec0():
        return Value(
            Decimal("0.00"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )

    # Etiquetas legibles para ajustes
    ADJ_LABEL = {
        "fixed_salary": "Fixed salary",
        "bonus": "Bonus",
        "advance": "Advance",
    }
    ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    # ========= Semana actual + sync =========
    y, w, _ = timezone.localdate().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    # Crea/ajusta weekly payments para la semana actual (incluye ajustes)
    _sync_weekly_totals(week=current_week, create_missing=True)

    # ========= Visibilidad por usuario =========
    visible_tech_ids = _visible_tech_ids_for_user(request.user)

    # ========= TOP (This week) =========
    top_qs = (
        WeeklyPayment.objects
        .filter(week=current_week, amount__gt=0)   # sólo pagables
        .exclude(status="paid")
        .select_related("technician")
        .order_by("status", "technician__first_name", "technician__last_name")
    )

    # Limitar por técnicos visibles
    if visible_tech_ids is not None:
        top_qs = top_qs.filter(technician_id__in=visible_tech_ids)

    top = list(top_qs)

    # ---- Desglose (This week): producción + ajustes
    tech_ids_top = {wp.technician_id for wp in top}
    details_map_top: dict[tuple[int, str], list] = {}

    # 1) Producción por proyecto, separando si fue "descuento directo"
    if tech_ids_top:
        det_prod = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id__in=tech_ids_top,
                item__sesion__semana_pago_real=current_week,
            )
            .filter(
                Q(item__sesion__estado__in=ESTADOS_OK) |
                Q(subtotal__lt=0)
            )
            .values(
                "tecnico_id",
                "item__sesion__semana_pago_real",
                "item__sesion__proyecto_id",
                is_discount=F("item__sesion__is_direct_discount"),
            )
            .annotate(
                subtotal=Coalesce(
                    Sum("subtotal"),
                    _dec0(),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
            .order_by("item__sesion__proyecto_id")
        )
        for r in det_prod:
            key = (r["tecnico_id"], r["item__sesion__semana_pago_real"])
            label = "Direct discount" if r["is_discount"] else (
                r["item__sesion__proyecto_id"] or "—"
            )
            details_map_top.setdefault(key, []).append(
                {
                    "project_label": str(label),
                    "subtotal": r["subtotal"] or Decimal("0.00"),
                }
            )

    # 2) Ajustes de la semana actual (Fixed salary / Bonus / Advance)
    try:
        from operaciones.models import AdjustmentEntry
    except Exception:
        AdjustmentEntry = None

    if AdjustmentEntry is not None and tech_ids_top:
        det_adj = (
            AdjustmentEntry.objects
            .filter(technician_id__in=tech_ids_top, week=current_week)
            .values("technician_id", "week", "adjustment_type")
            .annotate(
                total=Coalesce(
                    Sum("amount"),
                    _dec0(),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
        )
        for r in det_adj:
            key = (r["technician_id"], r["week"])
            label = ADJ_LABEL.get(r["adjustment_type"], r["adjustment_type"])
            details_map_top.setdefault(key, []).append(
                {
                    "project_label": label,
                    "subtotal": r["total"] or Decimal("0.00"),
                }
            )

    # adjuntar al objeto
    for wp in top:
        wp.details = details_map_top.get((wp.technician_id, wp.week), [])

    # ========= BOTTOM (historial Paid) =========
    f_tech = (request.GET.get("f_tech") or "").strip()
    f_week_input = (request.GET.get("f_week") or "").strip()
    f_paid_week_input = (request.GET.get("f_paid_week") or "").strip()
    f_receipt = (request.GET.get("f_receipt") or "").strip()   # "", "with", "without"

    f_week = _norm_week_input(f_week_input)
    f_paid_week = _norm_week_input(f_paid_week_input)

    bottom_qs = WeeklyPayment.objects.filter(
        status="paid"
    ).select_related("technician")

    # Limitar por técnicos visibles
    if visible_tech_ids is not None:
        bottom_qs = bottom_qs.filter(technician_id__in=visible_tech_ids)

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
        bottom_qs = bottom_qs.exclude(
            Q(receipt__isnull=True) | Q(receipt="")
        )
    elif f_receipt == "without":
        bottom_qs = bottom_qs.filter(
            Q(receipt__isnull=True) | Q(receipt="")
        )

    bottom_qs = bottom_qs.order_by(
        "-paid_week",
        "-week",
        "technician__first_name",
        "technician__last_name",
    )

    # ========= Paginación =========
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

    # ---- Desglose (Paid): producción + ajustes
    wp_list = list(pagina) if not isinstance(pagina, list) else pagina
    tech_ids_bottom = {wp.technician_id for wp in wp_list}
    weeks_bottom = {wp.week for wp in wp_list}

    details_map_bottom: dict[tuple[int, str], list] = {}
    if tech_ids_bottom and weeks_bottom:
        # Producción
        det_b_prod = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id__in=tech_ids_bottom,
                item__sesion__semana_pago_real__in=weeks_bottom,
            )
            .filter(
                Q(item__sesion__estado__in=ESTADOS_OK) |
                Q(subtotal__lt=0)
            )
            .values(
                "tecnico_id",
                "item__sesion__semana_pago_real",
                "item__sesion__proyecto_id",
                is_discount=F("item__sesion__is_direct_discount"),
            )
            .annotate(
                subtotal=Coalesce(
                    Sum("subtotal"),
                    _dec0(),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
            .order_by(
                "item__sesion__semana_pago_real",
                "item__sesion__proyecto_id",
            )
        )
        for r in det_b_prod:
            key = (r["tecnico_id"], r["item__sesion__semana_pago_real"])
            label = "Direct discount" if r["is_discount"] else (
                r["item__sesion__proyecto_id"] or "—"
            )
            details_map_bottom.setdefault(key, []).append(
                {
                    "project_label": str(label),
                    "subtotal": r["subtotal"] or Decimal("0.00"),
                }
            )

        # Ajustes
        if AdjustmentEntry is not None:
            det_b_adj = (
                AdjustmentEntry.objects
                .filter(
                    technician_id__in=tech_ids_bottom,
                    week__in=weeks_bottom,
                )
                .values("technician_id", "week", "adjustment_type")
                .annotate(
                    total=Coalesce(
                        Sum("amount"),
                        _dec0(),
                        output_field=DecimalField(max_digits=18, decimal_places=2),
                    )
                )
            )
            for r in det_b_adj:
                key = (r["technician_id"], r["week"])
                label = ADJ_LABEL.get(
                    r["adjustment_type"],
                    r["adjustment_type"],
                )
                details_map_bottom.setdefault(key, []).append(
                    {
                        "project_label": label,
                        "subtotal": r["total"] or Decimal("0.00"),
                    }
                )

    for wp in wp_list:
        wp.details = details_map_bottom.get((wp.technician_id, wp.week), [])

    # ========= Querystring para mantener filtros =========
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
        "top": top,
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
    - Crea (si faltan) y actualiza WeeklyPayment por cada semana donde tenga
      producción o ajustes (igual que hace admin con create_missing=True).
    - Lista sus WeeklyPayment.
    - Adjunta 'details' (producción + ajustes) y 'display_amount' por semana.
    """
    from django.db.models.functions import Upper  # (no imprescindible)

    user_id = request.user.id

    # =========================================
    # ⛔ Saltar sincronización si venimos de aprobar
    # =========================================
    if request.GET.get("skip_sync") != "1":
        # 0) Sincroniza SOLO este técnico, sin crear weeklies (tu lógica original)
        sync_weekly_totals_no_create(technician_id=user_id)

        # 0.1) Detectar TODAS las semanas relevantes de este técnico
        #      (producción real y ajustes), y crear las faltantes como hace admin.
        weeks_prod = set(
            ItemBillingTecnico.objects
            .filter(tecnico_id=user_id, item__sesion__semana_pago_real__gt="")
            .values_list("item__sesion__semana_pago_real", flat=True)
            .distinct()
        )

        weeks_adj_raw = set(
            AdjustmentEntry.objects
            .filter(technician_id=user_id)
            .exclude(amount=0)
            .values_list("week", flat=True)
            .distinct()
        )

        # Nota: algunos ajustes pueden venir como 'W40' y otros como '2025-W40'.
        # No normalizamos aquí para no tocar datos; pedimos sync por cada string tal cual.
        weeks_to_sync = set(filter(None, weeks_prod.union(weeks_adj_raw)))

        # 0.2) Crear/actualizar WeeklyPayment por cada semana detectada
        #      (replica la idea del admin con create_missing=True)
        for wk in weeks_to_sync:
            try:
                _sync_weekly_totals(week=str(wk), create_missing=True)
            except Exception:
                # no abortar por una semana malformada
                pass

    # 1) Semana actual (solo para mostrar arriba)
    y, w, _ = timezone.localdate().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    ESTADOS_OK = {"aprobado_supervisor", "aprobado_pm", "aprobado_finanzas"}

    # --------- Subqueries para decidir qué semanas mostrar ----------
    # Producción (aprobada o descuentos) con total != 0
    prod_exists = (
        ItemBillingTecnico.objects
        .filter(
            tecnico_id=user_id,
            item__sesion__semana_pago_real=OuterRef("week"),
        )
        .filter(Q(item__sesion__estado__in=ESTADOS_OK) | Q(subtotal__lt=0))
        .values("tecnico_id")
        .annotate(total=Sum("subtotal"))
        .exclude(total=0)
    )

    # Ajustes presentes (monto ≠ 0) en la misma semana exacta
    adj_exists = (
        AdjustmentEntry.objects
        .filter(technician_id=user_id, week=OuterRef("week"))
        .exclude(amount=0)
        .values("id")[:1]
    )

    # Borra weeklies huérfanos (no paid) sin producción ni ajustes vigentes
    (WeeklyPayment.objects
        .filter(technician_id=user_id)
        .annotate(has_prod=Exists(prod_exists), has_adj=Exists(adj_exists))
        .filter(has_prod=False, has_adj=False)
        .exclude(status="paid")
        .delete())

    # Lista solo los que sí tienen producción o ajustes
    mine_qs = (
        WeeklyPayment.objects
        .filter(technician_id=user_id)
        .annotate(has_prod=Exists(prod_exists), has_adj=Exists(adj_exists))
        .filter(Q(has_prod=True) | Q(has_adj=True))
        .select_related("technician")
        .order_by("-week")
    )
    mine = list(mine_qs)

    # --------- Desglose y total por semana ----------
    weeks = {wp.week for wp in mine}
    details_map = {}
    totals_map = {}

    # 1) Producción (por Project ID)
    if weeks:
        det_prod = (
            ItemBillingTecnico.objects
            .filter(
                tecnico_id=user_id,
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
        for r in det_prod:
            week = r["item__sesion__semana_pago_real"]
            sub = r["subtotal"] or Decimal("0")
            details_map.setdefault(week, []).append({
                "project_id": r["project_id"],
                "subtotal": sub,
            })
            totals_map[week] = totals_map.get(week, Decimal("0")) + sub

    # 2) Ajustes (bonos / salario / advance) — considerar montos ≠ 0
    LABEL = {"bonus": "Bonus",
             "fixed_salary": "Fixed salary", "advance": "Advance"}
    if weeks:
        det_adj = list(
            AdjustmentEntry.objects
            .filter(technician_id=user_id, week__in=weeks)
            .exclude(amount=0)
            .values("week", "adjustment_type", "amount", "project_id")
        )
    else:
        det_adj = []

    for a in det_adj:
        week = a["week"]
        amt = Decimal(a["amount"] or 0)
        # Mostrar ajustes en positivo en esta vista
        amt = abs(amt)
        details_map.setdefault(week, []).append({
            "project_id": a.get("project_id") or "-",
            "label": LABEL.get(a["adjustment_type"], a["adjustment_type"]),
            "subtotal": amt,
        })
        totals_map[week] = totals_map.get(week, Decimal("0")) + amt

    # Adjuntar a cada WP
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
    # 👇 evitamos que la siguiente vista vuelva a sincronizar y deshaga el cambio
    return redirect(f"{reverse('operaciones:user_weekly_payments')}?skip_sync=1")


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
    # ⬇️ Evita que la vista de lista vuelva a sincronizar y revierta el estado
    return redirect(f"{reverse('operaciones:user_weekly_payments')}?skip_sync=1")


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
