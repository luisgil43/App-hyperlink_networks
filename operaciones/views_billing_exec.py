
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse, HttpResponseNotAllowed
from .models import SesionBilling
from django.shortcuts import get_object_or_404
import logging
import re
from django.http import HttpResponseRedirect
import time
from django.views.decorators.cache import never_cache
import tempfile
import hashlib
import os
from PIL import Image, ImageFile
from django.core.files.storage import default_storage as storage
from django.http import JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from .models import ReporteFotograficoJob
from django.views.decorators.http import require_GET
from django.http import JsonResponse
from django.core.files import File
from django.utils.http import http_date
from urllib.parse import urlencode
from tempfile import NamedTemporaryFile
import xlsxwriter
from operaciones.excel_images import tmp_jpeg_from_filefield
from django.db.models import OuterRef, Subquery, Sum, Value, DecimalField, Case, When, IntegerField
from botocore.client import Config
from datetime import timedelta
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from openpyxl import Workbook
from django.http import HttpResponse
import csv
from openpyxl import load_workbook  # asegúrate de tener openpyxl instalado
from django.http import FileResponse, Http404, HttpResponseForbidden, HttpResponseBadRequest
import uuid
import json
from django.conf import settings
import boto3
from decimal import Decimal
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import (
    Count, Sum, Subquery, OuterRef, DecimalField, Value
)
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import (
    SesionBilling, SesionBillingTecnico, ItemBillingTecnico,
    RequisitoFotoBilling, EvidenciaFotoBilling
)
from usuarios.decoradores import rol_requerido

from io import BytesIO
from PIL import Image, ExifTags, ImageFile
from pillow_heif import register_heif_opener


ImageFile.LOAD_TRUNCATED_IMAGES = True
register_heif_opener()  # habilita abrir .heic/.heif en Pillow
# ============================
# UTIL
# ============================


def storage_file_exists(filefield) -> bool:
    if not filefield or not getattr(filefield, "name", ""):
        return False
    try:
        return filefield.storage.exists(filefield.name)
    except Exception:
        return False


# ============================
# TÉCNICO
# ============================


@login_required
@rol_requerido('usuario', 'admin', 'pm', 'supervisor')
def mis_assignments(request):
    # Estados visibles (se excluyen todos los aprobados)
    visibles = [
        'asignado',
        'en_proceso',
        'en_revision_supervisor',
        'rechazado_supervisor',
        'rechazado_pm',
        'rechazado_finanzas',
    ]

    base_qs = (
        SesionBillingTecnico.objects
        .select_related("sesion")
        .filter(tecnico=request.user, estado__in=visibles)
    )

    # Subquery: total del técnico para cada sesión
    ibt = (
        ItemBillingTecnico.objects
        .filter(item__sesion=OuterRef("sesion_id"), tecnico=request.user)
        .values("tecnico")
        .annotate(total=Sum("subtotal"))
        .values("total")
    )

    dec_field = DecimalField(max_digits=12, decimal_places=2)

    asignaciones = (
        base_qs
        .annotate(
            my_total=Coalesce(
                Subquery(ibt, output_field=dec_field),
                Value(Decimal("0.00"), output_field=dec_field),
                output_field=dec_field
            ),
            # Prioridad de estado para ordenar
            estado_priority=Case(
                When(estado='asignado',               then=Value(1)),
                When(estado='en_proceso',             then=Value(2)),
                When(estado='en_revision_supervisor', then=Value(3)),
                When(estado='rechazado_supervisor',   then=Value(4)),
                When(estado='rechazado_pm',           then=Value(5)),
                When(estado='rechazado_finanzas',     then=Value(6)),
                default=Value(999),
                output_field=IntegerField(),
            ),
        )
        # Orden final: prioridad de estado, luego fecha de creación desc, luego id desc
        .order_by('estado_priority', '-sesion__creado_en', '-id')
    )

    return render(
        request,
        "operaciones/billing_mis_asignaciones.html",
        {"asignaciones": asignaciones}
    )


@login_required
@rol_requerido('usuario')
def detalle_assignment(request, pk):
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)
    items = (ItemBillingTecnico.objects
             .filter(item__sesion=a.sesion, tecnico=request.user)
             .select_related("item")
             .order_by("item__id"))
    return render(request, "operaciones/billing_detalle_asignacion.html", {
        "a": a, "items": items
    })


@login_required
@rol_requerido('usuario')
@require_POST
def start_assignment(request, pk):
    """
    El técnico acepta la tarea y la pone en 'en_proceso'.
    El proyecto pasa a 'en_proceso' si estaba 'rechazado_supervisor' o 'asignado'.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    if a.estado not in {"asignado", "rechazado_supervisor"} and not a.reintento_habilitado:
        messages.error(request, "This assignment cannot be started.")
        return redirect("operaciones:mis_assignments")

    a.estado = "en_proceso"
    a.aceptado_en = timezone.now()
    a.reintento_habilitado = False
    a.save(update_fields=["estado", "aceptado_en", "reintento_habilitado"])

    s = a.sesion
    if s.estado in {"rechazado_supervisor", "asignado"}:
        s.estado = "en_proceso"
        s.save(update_fields=["estado"])

    messages.success(request, "Assignment started.")
    return redirect("operaciones:mis_assignments")


def _to_jpeg_if_needed(uploaded_file):
    """
    Si es HEIC/HEIF (o un formato no-JPEG) lo convierte a JPEG (quality 92)
    conservando EXIF cuando exista. Devuelve un ContentFile listo para asignar
    a un ImageField/FileField (con nombre .jpg).
    """
    uploaded_file.seek(0)
    im = Image.open(uploaded_file)
    fmt = (im.format or "").upper()
    exif = im.info.get("exif")

    if fmt in {"HEIC", "HEIF"}:
        bio = BytesIO()
        im = im.convert("RGB")
        if exif:
            im.save(bio, format="JPEG", quality=92, exif=exif)
        else:
            im.save(bio, format="JPEG", quality=92)
        bio.seek(0)
        name = (uploaded_file.name.rsplit(".", 1)[0]) + ".jpg"
        return ContentFile(bio.read(), name=name)

    # Si es otra cosa (PNG, WEBP, etc.) lo dejamos igual
    uploaded_file.seek(0)
    return uploaded_file


def _exif_to_latlng_taken_at(image):
    """
    Extrae (lat, lng, taken_at) de EXIF si existen.
    Retorna (lat, lng, dt) o (None, None, None).
    """
    try:
        exif = getattr(image, "_getexif", lambda: None)()
        if not exif:
            return None, None, None

        tagmap = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        # Fecha/hora
        dt_raw = tagmap.get("DateTimeOriginal") or tagmap.get("DateTime")
        taken_at = None
        if dt_raw:
            from datetime import datetime
            try:
                taken_at = timezone.make_aware(
                    datetime.strptime(dt_raw, "%Y:%m:%d %H:%M:%S"))
            except Exception:
                taken_at = None

        # GPS
        gps_info = tagmap.get("GPSInfo")
        if not gps_info:
            return None, None, taken_at

        def _ratio_to_float(r):
            try:
                return float(r[0]) / float(r[1])
            except Exception:
                return float(r)

        def _dms_to_deg(dms, ref):
            deg = _ratio_to_float(dms[0])
            minutes = _ratio_to_float(dms[1])
            seconds = _ratio_to_float(dms[2])
            value = deg + (minutes / 60.0) + (seconds / 3600.0)
            if ref in ['S', 'W']:
                value = -value
            return value

        gps_tagmap = {ExifTags.GPSTAGS.get(
            k, k): v for k, v in gps_info.items()}
        lat = lng = None
        if all(k in gps_tagmap for k in ["GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef"]):
            lat = _dms_to_deg(
                gps_tagmap["GPSLatitude"], gps_tagmap["GPSLatitudeRef"])
            lng = _dms_to_deg(
                gps_tagmap["GPSLongitude"], gps_tagmap["GPSLongitudeRef"])

        return lat, lng, taken_at
    except Exception:
        return None, None, None


# --- VISTAS: copiar/pegar reemplazando las actuales ---

@login_required
@rol_requerido('usuario')
def upload_evidencias(request, pk):
    """
    Carga de evidencias con 'lock' por TÍTULO compartido a nivel de sesión.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    # ---------- helpers ----------
    def _norm_title(s: str) -> str:
        return (s or "").strip().lower()

    def _is_safe_wasabi_key(key: str) -> bool:
        return bool(key) and ".." not in key and not key.startswith("/")

    def _create_evidencia_from_key(
        req_id, key, nota, lat, lng, acc, taken_dt,
        titulo_manual: str = "", direccion_manual: str = ""
    ):
        return EvidenciaFotoBilling.objects.create(
            tecnico_sesion=a,
            requisito_id=req_id,
            imagen=key,
            nota=nota or "",
            lat=lat or None,
            lng=lng or None,
            gps_accuracy_m=acc or None,
            client_taken_at=taken_dt,
            titulo_manual=titulo_manual or "",
            direccion_manual=direccion_manual or "",
        )

    def _boolish(v):
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        if isinstance(v, int):
            return v != 0
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "t", "yes", "y", "on", "si", "sí"}
        return bool(v)

    # Flag ROBUSTO de proyecto especial:
    def _es_proyecto_especial(asig: SesionBillingTecnico) -> bool:
        s = asig.sesion
        candidatos = [
            getattr(s, "proyecto_especial", None),
            getattr(getattr(s, "servicio", None), "proyecto_especial", None),
            getattr(getattr(asig, "servicio", None),
                    "proyecto_especial", None),
            getattr(getattr(s, "proyecto", None), "proyecto_especial", None),
        ]
        for v in candidatos:
            if v is not None:
                return _boolish(v)
        # Heurística: si la sesión no tiene REQUISITOS, tratar como especial
        no_reqs = not RequisitoFotoBilling.objects.filter(
            tecnico_sesion__sesion=s
        ).exists()
        return no_reqs

    # Si el técnico fue agregado tarde y no tiene requisitos, clonar de la sesión
    def _ensure_requisitos_para_asignacion():
        if a.requisitos.exists():
            return
        base_qs = (RequisitoFotoBilling.objects
                   .filter(tecnico_sesion__sesion=a.sesion)
                   .order_by("orden", "id")
                   .select_related("tecnico_sesion"))
        to_create, seen = [], set()
        orden_fallback = 0
        for br in base_qs:
            key = _norm_title(br.titulo)
            if not key or key in seen:
                continue
            orden_fallback += 1
            to_create.append(RequisitoFotoBilling(
                tecnico_sesion=a,
                titulo=br.titulo,
                descripcion=br.descripcion,
                obligatorio=br.obligatorio,
                orden=br.orden or orden_fallback,
            ))
            seen.add(key)
        if to_create:
            RequisitoFotoBilling.objects.bulk_create(to_create)

    _ensure_requisitos_para_asignacion()

    # Permisos para subir según estado
    puede_subir = (a.estado == "en_proceso") or (
        a.estado == "rechazado_supervisor" and a.reintento_habilitado
    )
    if not puede_subir and request.method != "GET":
        messages.info(request, "This assignment is not open for uploads.")
        return redirect("operaciones:mis_assignments")

    s = a.sesion
    is_especial = _es_proyecto_especial(a)

    # -------------------- POST -------------------- (fallback no-AJAX)
    if request.method == "POST":
        req_id = request.POST.get("req_id") or None
        nota = (request.POST.get("nota") or "").strip()

        files = request.FILES.getlist("imagenes[]")
        wasabi_keys = request.POST.getlist(
            "wasabi_keys[]") if settings.DIRECT_UPLOADS_ENABLED else []

        lat = request.POST.get("lat") or None
        lng = request.POST.get("lng") or None
        acc = request.POST.get("acc") or None
        taken = request.POST.get("client_taken_at")
        taken_dt = parse_datetime(taken) if taken else None

        # Campos manuales para Extra en proyecto especial
        titulo_manual = (request.POST.get("titulo_manual") or "").strip()
        direccion_manual = (request.POST.get("direccion_manual") or "").strip()

        if is_especial and not req_id:
            if not titulo_manual:
                messages.error(
                    request, "Please enter a Title for the photo (special project).")
                return redirect("operaciones:upload_evidencias", pk=a.pk)
            if not direccion_manual:
                messages.error(
                    request, "Please enter an Address for the photo (special project).")
                return redirect("operaciones:upload_evidencias", pk=a.pk)

        # Lock por título (si es requisito)
        if req_id:
            req = get_object_or_404(
                RequisitoFotoBilling, pk=req_id, tecnico_sesion=a)
            shared_key = _norm_title(req.titulo)
            taken_titles = (EvidenciaFotoBilling.objects
                            .filter(tecnico_sesion__sesion=s, requisito__isnull=False)
                            .values_list("requisito__titulo", flat=True))
            locked_title_set = {_norm_title(t) for t in taken_titles if t}
            if shared_key in locked_title_set:
                messages.warning(
                    request,
                    "This requirement is already covered by the team. "
                    "Remove the existing photo to re-activate it."
                )
                return redirect("operaciones:upload_evidencias", pk=a.pk)

        # Wasabi keys
        n = 0
        for key in wasabi_keys:
            if _is_safe_wasabi_key(key):
                _create_evidencia_from_key(
                    req_id, key, nota, lat, lng, acc, taken_dt,
                    titulo_manual=titulo_manual, direccion_manual=direccion_manual
                )
                n += 1

        # Archivos
        for f in files:
            f_conv = _to_jpeg_if_needed(f)
            try:
                f_conv.seek(0)
                im = Image.open(f_conv)
                exif_lat, exif_lng, exif_dt = _exif_to_latlng_taken_at(im)
            except Exception:
                exif_lat = exif_lng = exif_dt = None
            finally:
                f_conv.seek(0)

            use_lat = lat or exif_lat
            use_lng = lng or exif_lng
            use_taken = taken_dt or exif_dt

            EvidenciaFotoBilling.objects.create(
                tecnico_sesion=a,
                requisito_id=req_id,
                imagen=f_conv,
                nota=nota,
                lat=use_lat,
                lng=use_lng,
                gps_accuracy_m=acc,
                client_taken_at=use_taken,
                titulo_manual=titulo_manual,
                direccion_manual=direccion_manual,
            )
            n += 1

        messages.success(request, f"{n} photo(s) uploaded.") if n else messages.info(
            request, "No files selected."
        )
        return redirect("operaciones:upload_evidencias", pk=a.pk)

    # -------------------- GET --------------------
    requisitos = (
        a.requisitos
         .annotate(uploaded=Count("evidencias"))
         .order_by("orden", "id")
    )

    taken_titles = (EvidenciaFotoBilling.objects
                    .filter(tecnico_sesion__sesion=s, requisito__isnull=False)
                    .values_list("requisito__titulo", flat=True))
    locked_title_set = {_norm_title(t) for t in taken_titles if t}
    locked_ids = [r.id for r in requisitos if _norm_title(
        r.titulo) in locked_title_set]

    required_titles = (RequisitoFotoBilling.objects
                       .filter(tecnico_sesion__sesion=s, obligatorio=True)
                       .values_list("titulo", flat=True))
    required_key_set = {_norm_title(t) for t in required_titles if t}
    covered_key_set = locked_title_set
    missing_keys = required_key_set - covered_key_set

    sample_titles = list(
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion__sesion=s, titulo__isnull=False)
        .values_list("titulo", flat=True)
    )
    sample_map = {_norm_title(t): t for t in sample_titles if t}
    faltantes_global = [sample_map.get(k, k) for k in sorted(missing_keys)]

    asignaciones = list(s.tecnicos_sesion.select_related("tecnico").all())
    pendientes_aceptar = []
    for asg in asignaciones:
        accepted = bool(asg.aceptado_en) or asg.estado != "asignado"
        if not accepted:
            name = getattr(asg.tecnico, "get_full_name",
                           lambda: "")() or asg.tecnico.username
            pendientes_aceptar.append(name)
    all_accepted = (len(pendientes_aceptar) == 0)

    can_finish = (a.estado == "en_proceso" and len(
        faltantes_global) == 0 and all_accepted)

    evidencias = (
        a.evidencias
         .select_related("requisito")
         .order_by("requisito__orden", "tomada_en", "id")
    )

    can_delete = puede_subir

    proj_id = (a.sesion.proyecto_id or "project").strip()
    proj_slug = slugify(proj_id) or "project"
    sess_tag = f"{proj_slug}-{a.sesion_id}"

    tech = a.tecnico
    tech_name = (
        getattr(tech, "get_full_name", lambda: "")()
        or getattr(tech, "username", "")
        or f"user-{tech.id}"
    )
    tech_slug = slugify(tech_name) or f"user-{tech.id}"

    direct_uploads_folder = f"operaciones/reporte_fotografico/{sess_tag}/{tech_slug}/evidencia/"

    return render(
        request,
        "operaciones/billing_upload_evidencias.html",
        {
            "a": a,
            "requisitos": requisitos,
            "evidencias": evidencias,
            "can_delete": can_delete,

            "locked_ids": locked_ids,
            "faltantes_global": faltantes_global,
            "pendientes_aceptar": pendientes_aceptar,
            "can_finish": can_finish,

            "direct_uploads_enabled": settings.DIRECT_UPLOADS_ENABLED,
            "direct_uploads_max_mb": getattr(settings, "DIRECT_UPLOADS_MAX_MB", 15),
            "direct_uploads_folder": direct_uploads_folder,
            "project_id": a.sesion.proyecto_id,
            "current_user_name": tech_name,

            # ✅ viene del helper robusto
            "is_proyecto_especial": is_especial,
        },
    )


@login_required
@rol_requerido('usuario')
@require_POST
def upload_evidencias_ajax(request, pk):
    """
    Subida AJAX (una imagen por request) al estilo GZ Services.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)
    s = a.sesion

    def _boolish(v):
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        if isinstance(v, int):
            return v != 0
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "t", "yes", "y", "on", "si", "sí"}
        return bool(v)

    def _es_proyecto_especial(asig: SesionBillingTecnico) -> bool:
        sess = asig.sesion
        candidatos = [
            getattr(sess, "proyecto_especial", None),
            getattr(getattr(sess, "servicio", None),
                    "proyecto_especial", None),
            getattr(getattr(asig, "servicio", None),
                    "proyecto_especial", None),
            getattr(getattr(sess, "proyecto", None),
                    "proyecto_especial", None),
        ]
        for v in candidatos:
            if v is not None:
                return _boolish(v)
        # Heurística: sin requisitos en la sesión => especial
        return not RequisitoFotoBilling.objects.filter(
            tecnico_sesion__sesion=sess
        ).exists()

    is_especial = _es_proyecto_especial(a)

    puede_subir = (a.estado == "en_proceso") or (
        a.estado == "rechazado_supervisor" and a.reintento_habilitado
    )
    if not puede_subir:
        return JsonResponse({"ok": False, "error": "Asignación no abierta para subir fotos."}, status=400)

    req_id = request.POST.get("req_id") or None
    nota = (request.POST.get("nota") or "").strip()
    lat = request.POST.get("lat") or None
    lng = request.POST.get("lng") or None
    acc = request.POST.get("acc") or None
    taken = request.POST.get("client_taken_at")
    taken_dt = parse_datetime(taken) if taken else None
    titulo_manual = (request.POST.get("titulo_manual") or "").strip()
    direccion_manual = (request.POST.get("direccion_manual") or "").strip()

    # En proyecto especial y sin req_id (Extra) exigir Título y Dirección
    if is_especial and not req_id:
        if not titulo_manual:
            return JsonResponse({"ok": False, "error": "Ingresa un Título (proyecto especial)."}, status=400)
        if not direccion_manual:
            return JsonResponse({"ok": False, "error": "Ingresa una Dirección (proyecto especial)."}, status=400)

    # 🔢 Límite global de Extra por sesión: 1000
    if not req_id:
        total_extra = EvidenciaFotoBilling.objects.filter(
            tecnico_sesion__sesion=s, requisito__isnull=True
        ).count()
        if total_extra >= 1000:
            return JsonResponse({"ok": False, "error": "Límite alcanzado: máximo 1000 fotos extra por proyecto."}, status=400)

    file = request.FILES.get("imagen")
    if not file:
        return JsonResponse({"ok": False, "error": "No llegó la imagen."}, status=400)

    f_conv = _to_jpeg_if_needed(file)
    try:
        f_conv.seek(0)
        im = Image.open(f_conv)
        exif_lat, exif_lng, exif_dt = _exif_to_latlng_taken_at(im)
    except Exception:
        exif_lat = exif_lng = exif_dt = None
    finally:
        f_conv.seek(0)

    use_lat = lat or exif_lat
    use_lng = lng or exif_lng
    use_taken = taken_dt or exif_dt

    ev = a.evidencias.create(
        requisito_id=req_id,
        imagen=f_conv,
        nota=nota,
        lat=use_lat, lng=use_lng, gps_accuracy_m=acc,
        client_taken_at=use_taken,
        titulo_manual=titulo_manual,
        direccion_manual=direccion_manual or "",
    )

    # extras_left tras esta subida (global por sesión)
    extras_left = max(0, 1000 - EvidenciaFotoBilling.objects.filter(
        tecnico_sesion__sesion=s, requisito__isnull=True
    ).count())

    titulo = ev.requisito.titulo if ev.requisito_id else (
        ev.titulo_manual or "Extra")
    fecha_txt = timezone.localtime(
        ev.client_taken_at or ev.tomada_en).strftime("%Y-%m-%d %H:%M")

    return JsonResponse({
        "ok": True,
        "evidencia": {
            "id": ev.id,
            "url": ev.imagen.url,
            "titulo": titulo,
            "fecha": fecha_txt,
            "lat": ev.lat, "lng": ev.lng, "acc": ev.gps_accuracy_m,
            "req_id": int(req_id) if req_id else None,
        },
        "extras_left": extras_left,
        "max_extra": 1000,
    })


@rol_requerido('usuario')
@login_required
def fotos_status_json(request, asig_id: int):
    """
    JSON para el polling del front (GZ-style):
    - can_finish
    - faltantes_global (por título)
    - requisitos (estado global/my_count)
    - evidencias_nuevas (id > after)
    - extras_left / max_extra
    """
    a = get_object_or_404(SesionBillingTecnico,
                          pk=asig_id, tecnico=request.user)
    s = a.sesion

    # ⛳️ MISMO FIX: si el técnico no tiene requisitos aún, clonarlos de la sesión.
    def _norm_title(s: str) -> str:
        return (s or "").strip().lower()

    if not a.requisitos.exists():
        base_qs = (RequisitoFotoBilling.objects
                   .filter(tecnico_sesion__sesion=s)
                   .order_by("orden", "id"))
        to_create, seen = [], set()
        orden_fallback = 0
        for br in base_qs:
            key = _norm_title(br.titulo)
            if not key or key in seen:
                continue
            orden_fallback += 1
            to_create.append(RequisitoFotoBilling(
                tecnico_sesion=a,
                titulo=br.titulo,
                descripcion=br.descripcion,
                obligatorio=br.obligatorio,
                orden=br.orden or orden_fallback,
            ))
            seen.add(key)
        if to_create:
            RequisitoFotoBilling.objects.bulk_create(to_create)

    after = int(request.GET.get("after", "0") or 0)

    # Requisitos de esta asignación (ya garantizados)
    reqs = list(
        a.requisitos
        .order_by("orden")
        .values("id", "titulo", "obligatorio")
    )

    # Conteo propio del técnico
    my_counts = {
        x["requisito_id"]: x["c"]
        for x in (EvidenciaFotoBilling.objects
                  .filter(tecnico_sesion=a, requisito_id__isnull=False)
                  .values("requisito_id")
                  .annotate(c=Count("id")))
    }

    # Títulos ya cubiertos por el EQUIPO en la sesión
    titles_done = {
        _norm_title(t)
        for t in (EvidenciaFotoBilling.objects
                  .filter(tecnico_sesion__sesion=s, requisito__isnull=False)
                  .values_list("requisito__titulo", flat=True)
                  .distinct())
        if t
    }

    requisitos_json, faltantes = [], []
    for r in reqs:
        titulo = r["titulo"] or ""
        global_done = (_norm_title(titulo) in titles_done)
        my_count = my_counts.get(r["id"], 0)
        if r["obligatorio"] and not global_done:
            faltantes.append(titulo)
        requisitos_json.append({
            "id": r["id"],
            "titulo": titulo,
            "obligatorio": r["obligatorio"],
            "team_count": 1 if global_done else 0,
            "my_count": my_count,
            "global_done": global_done,
        })

    # Evidencias nuevas desde 'after'
    nuevas_qs = (EvidenciaFotoBilling.objects
                 .filter(tecnico_sesion__sesion=s, id__gt=after)
                 .order_by("id"))
    evidencias_nuevas = [{
        "id": ev.id,
        "url": ev.imagen.url,
        "req_id": ev.requisito_id,
        "titulo": (ev.requisito.titulo if ev.requisito_id else (ev.titulo_manual or "Extra")),
        "fecha": timezone.localtime(ev.client_taken_at or ev.tomada_en).strftime("%Y-%m-%d %H:%M"),
        "lat": ev.lat, "lng": ev.lng, "acc": ev.gps_accuracy_m,
    } for ev in nuevas_qs]

    # Cupo global de extras (1000 por sesión)
    total_extra = EvidenciaFotoBilling.objects.filter(
        tecnico_sesion__sesion=s, requisito__isnull=True
    ).count()
    extras_left = max(0, 1000 - total_extra)

    # ¿Faltan aceptaciones?
    pendientes_aceptar = []
    for asg in s.tecnicos_sesion.select_related("tecnico"):
        accepted = bool(asg.aceptado_en) or asg.estado != "asignado"
        if not accepted:
            nombre = getattr(asg.tecnico, "get_full_name",
                             lambda: "")() or asg.tecnico.username
            pendientes_aceptar.append(nombre)

    # Finish (mismo criterio que la página)
    can_finish = (
        a.estado == "en_proceso" and not faltantes and not pendientes_aceptar)

    return JsonResponse({
        "ok": True,
        "can_finish": can_finish,
        "faltantes_global": faltantes,
        "requisitos": requisitos_json,
        "evidencias_nuevas": evidencias_nuevas,
        "extras_left": extras_left,
        "max_extra": 1000,
    })


@login_required
@rol_requerido('usuario')
def finish_assignment(request, pk):
    """
    Flujo de Finalización (en equipo):
    - Verifica que ESTA asignación esté en 'en_proceso'.
    - Verifica que TODOS los requisitos obligatorios (por título compartido)
      tengan al menos una foto en la sesión (subida por cualquier asignado).
    - Verifica que TODOS los asignados hayan aceptado (Start).
    - Si todo está OK: cambia TODAS las asignaciones de la sesión a
      'en_revision_supervisor' (sellando finalizado_en) y actualiza la sesión
      a 'en_revision_supervisor' en una sola transacción.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    # Debe estar en progreso para poder finalizar
    if a.estado != "en_proceso":
        messages.error(request, "This assignment is not in progress.")
        return redirect("operaciones:mis_assignments")

    # Utilidad local para normalizar títulos (sin helpers externos)
    def _norm_title(s: str) -> str:
        return (s or "").strip().lower()

    s = a.sesion

    # 1) Cobertura global de requisitos obligatorios por TÍTULO compartido
    required_titles = (
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion__sesion=s, obligatorio=True)
        .values_list("titulo", flat=True)
    )
    required_key_set = {_norm_title(t) for t in required_titles if t}

    taken_titles = (
        EvidenciaFotoBilling.objects
        .filter(tecnico_sesion__sesion=s, requisito__isnull=False)
        .values_list("requisito__titulo", flat=True)
    )
    covered_key_set = {_norm_title(t) for t in taken_titles if t}

    missing_keys = required_key_set - covered_key_set
    if missing_keys:
        # Armamos nombres "bonitos" a partir de cualquier muestra en la sesión
        sample_titles = list(
            RequisitoFotoBilling.objects
            .filter(tecnico_sesion__sesion=s, titulo__isnull=False)
            .values_list("titulo", flat=True)
        )
        sample_map = {_norm_title(t): t for t in sample_titles if t}
        pretty_missing = [sample_map.get(k, k) for k in sorted(missing_keys)]
        messages.error(request, "Missing required photos: " +
                       ", ".join(pretty_missing))
        return redirect("operaciones:upload_evidencias", pk=a.pk)

    # 2) Validar que TODOS los asignados hayan aceptado (Start)
    pendientes_aceptar = []
    asignaciones = list(s.tecnicos_sesion.select_related("tecnico").all())
    for asg in asignaciones:
        # Se considera aceptado si tiene timestamp o si su estado ya no es 'asignado'
        accepted = bool(asg.aceptado_en) or asg.estado != "asignado"
        if not accepted:
            name = getattr(asg.tecnico, "get_full_name",
                           lambda: "")() or asg.tecnico.username
            pendientes_aceptar.append(name)

    if pendientes_aceptar:
        messages.error(
            request,
            "Pending acceptance (Start): " + ", ".join(pendientes_aceptar)
        )
        return redirect("operaciones:upload_evidencias", pk=a.pk)

    # 3) Todo OK → marcar TODAS las asignaciones y la sesión en revisión de supervisor
    now = timezone.now()
    with transaction.atomic():
        # Poner a todos en 'en_revision_supervisor' y sellar finalizado_en
        s.tecnicos_sesion.update(
            estado="en_revision_supervisor", finalizado_en=now)

        # La sesión completa pasa a revisión del supervisor
        s.estado = "en_revision_supervisor"
        s.save(update_fields=["estado"])

    messages.success(
        request, "Submitted for supervisor review for all assignees.")
    return redirect("operaciones:mis_assignments")


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def revisar_assignment(request, pk):
    """
    Compat: antes se revisaba por asignación.
    Ahora redirigimos a la revisión unificada por PROYECTO.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk)
    return redirect("operaciones:revisar_sesion", sesion_id=a.sesion_id)


ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def _safe_prefix() -> str:
    return getattr(settings, "DIRECT_UPLOADS_SAFE_PREFIX", "operaciones/reporte_fotografico/")


def _build_key(folder: str, filename: str) -> str:
    """
    Genera una key segura bajo el prefijo permitido, manteniendo tu estructura.
    - folder debe comenzar con DIRECT_UPLOADS_SAFE_PREFIX (p.ej. operaciones/reporte_fotografico/<proj>/<tech>/evidencia/)
    - filename solo aporta la extensión; el nombre es uuid para evitar colisiones.
    """
    ext = (filename.rsplit(".", 1)[-1] or "jpg").lower()
    base = (folder or "").strip().lstrip("/")
    if not base.startswith(_safe_prefix()):
        # Fuerza a prefijo seguro si el cliente envía algo fuera de rango
        base = _safe_prefix().rstrip("/") + "/evidencia/"
    return f"{base.rstrip('/')}/{uuid.uuid4().hex}.{ext}"


@login_required
@require_POST
def presign_wasabi(request):
    if not getattr(settings, "DIRECT_UPLOADS_ENABLED", False):
        return HttpResponseBadRequest("Direct uploads disabled.")

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON.")

    filename = (data.get("filename") or "").strip()
    content_type = (data.get("contentType") or "").strip()
    size_bytes = int(data.get("sizeBytes") or 0)
    folder = (data.get("folder") or "").strip()
    meta = data.get("meta") or {}

    if not filename or content_type not in ALLOWED_MIME:
        return HttpResponseBadRequest("Invalid file type.")
    max_bytes = int(
        getattr(settings, "DIRECT_UPLOADS_MAX_MB", 15)) * 1024 * 1024
    if size_bytes <= 0 or size_bytes > max_bytes:
        return HttpResponseBadRequest("File too large.")

    key = _build_key(folder, filename)

    s3 = boto3.client(
        "s3",
        endpoint_url=getattr(settings, "WASABI_ENDPOINT_URL",
                             "https://s3.us-east-1.wasabisys.com"),
        region_name=getattr(settings, "WASABI_REGION_NAME", "us-east-1"),
        aws_access_key_id=getattr(settings, "WASABI_ACCESS_KEY_ID"),
        aws_secret_access_key=getattr(settings, "WASABI_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4", s3={
                      "addressing_style": "path"}),
        verify=getattr(settings, "AWS_S3_VERIFY", True),
    )

    bucket = getattr(settings, "WASABI_BUCKET_NAME")

    # ✅ NEW meta: address (optional). Title is saved in POST; address is useful in client JS too.
    meta_headers = {
        "x-amz-meta-lat": str(meta.get("lat") or ""),
        "x-amz-meta-lng": str(meta.get("lng") or ""),
        "x-amz-meta-taken_at": str(meta.get("taken_at") or timezone.now().isoformat()),
        "x-amz-meta-user": request.user.get_full_name() or request.user.username,
        "x-amz-meta-project_id": str(meta.get("project_id") or ""),
        "x-amz-meta-technician": str(meta.get("technician") or ""),
        "x-amz-meta-address": str(meta.get("address") or ""),
        "x-amz-meta-title": str(meta.get("title") or ""),  # NEW
    }

    fields = {
        "acl": "private",
        "Content-Type": content_type,
        "success_action_status": "201",
        **meta_headers,
    }
    conditions = [
        {"bucket": bucket},
        ["starts-with", "$key", key.rsplit("/", 1)[0] + "/"],
        {"acl": "private"},
        {"Content-Type": content_type},
        {"success_action_status": "201"},
        ["content-length-range", 1, max_bytes],
    ]
    for h, v in meta_headers.items():
        conditions.append({h: v})

    presigned = s3.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields=fields,
        Conditions=conditions,
        ExpiresIn=300,
    )
    presigned["url"] = f"{settings.WASABI_ENDPOINT_URL.rstrip('/')}/{bucket}"

    return JsonResponse({"url": presigned["url"], "fields": presigned["fields"], "key": key})


SAFE_EVIDENCE_PREFIX = getattr(
    settings, "DIRECT_UPLOADS_SAFE_PREFIX", "operaciones/reporte_fotografico/")


def _is_safe_wasabi_key(key: str) -> bool:
    """Acepta solo claves dentro del prefijo seguro y sin '..'."""
    return isinstance(key, str) and key.startswith(SAFE_EVIDENCE_PREFIX) and ".." not in key


def _create_evidencia_from_key(a, req_id, key, nota, lat, lng, acc, taken_dt,
                               titulo_manual="", direccion_manual=""):
    """
    Create EvidenciaFotoBilling pointing to an object ALREADY uploaded to Wasabi.
    Doesn't re-upload bytes: assigns .name to the FileField and saves.
    """
    ev = EvidenciaFotoBilling(
        tecnico_sesion=a,
        requisito_id=req_id or None,
        nota=nota or "",
        lat=lat, lng=lng, gps_accuracy_m=acc,
        client_taken_at=taken_dt or None,
        titulo_manual=titulo_manual or "",
        direccion_manual=direccion_manual or "",
    )
    ev.imagen.name = key.strip()
    ev.save()
    return ev


# ============================
# SUPERVISOR — Revisión POR PROYECTO (unificada)
# ============================


def _project_report_key(sesion: SesionBilling) -> str:
    """
    Ruta determinística para el reporte por PROYECTO **por sesión**.
    Ej: operaciones/reporte_fotografico/<proj>-<sesion_id>/project/<proj>-<sesion_id>.xlsx
    """
    proj_slug = slugify(
        sesion.proyecto_id or f"billing-{sesion.id}") or f"billing-{sesion.id}"
    sess_tag = f"{proj_slug}-{sesion.id}"
    return f"operaciones/reporte_fotografico/{sess_tag}/project/{sess_tag}.xlsx"


# ...tus otros imports (decoradores, modelos usados en el template, etc.)


# ---------- revisar_sesion ----------
@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def revisar_sesion(request, sesion_id):
    """
    Revisión por PROYECTO.
    - APPROVE: encola job para generar el XLSX final.
    - REJECT: marca rechazado (sin tocar Wasabi).
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    asignaciones = (
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("evidencias__requisito")
         .all()
    )

    # Mantén sincronizado el estado a partir de las asignaciones
    s.recomputar_estado_desde_asignaciones()

    can_review = s.estado in {"en_revision_supervisor"}

    if request.method == "POST":
        accion = (request.POST.get("accion") or "").strip().lower()
        comentario = (request.POST.get("comentario") or "").strip()

        if not can_review and accion in {"aprobar", "approve", "rechazar", "reject"}:
            messages.error(
                request, "This project is not ready for supervisor review.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        if accion in {"aprobar", "approve"}:
            from usuarios.schedulers import enqueue_reporte_fotografico

            last_job = (
                ReporteFotograficoJob.objects
                .filter(sesion=s)
                .exclude(log__icontains="[partial]")   # solo FINAL
                .order_by("-creado_en")
                .first()
            )
            if last_job and last_job.estado in ("pendiente", "procesando"):
                messages.info(
                    request, "Photographic report is already being generated in background. It will be attached automatically when it’s ready.")
                return redirect("operaciones:revisar_sesion", sesion_id=s.id)

            job = ReporteFotograficoJob.objects.create(sesion=s)
            enqueue_reporte_fotografico(job.id)

            messages.info(
                request, "Generating photographic report in background. It will be attached automatically when it’s ready.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        elif accion in {"rechazar", "reject"}:
            now = timezone.now()
            with transaction.atomic():
                s.estado = "rechazado_supervisor"
                s.save(update_fields=["estado"])
                for a in asignaciones:
                    a.estado = "rechazado_supervisor"
                    a.supervisor_comentario = comentario or "Rejected."
                    a.supervisor_revisado_en = now
                    a.reintento_habilitado = True
                    a.save(update_fields=[
                           "estado", "supervisor_comentario", "supervisor_revisado_en", "reintento_habilitado"])

            messages.warning(
                request, "Project rejected. Reupload enabled for technicians.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        messages.error(request, "Unknown action.")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    # GET: datos para template
    evidencias_por_asig = []
    for a in asignaciones:
        evs = (
            a.evidencias
             .select_related("requisito")
             .order_by("requisito__orden", "tomada_en", "id")
        )
        evidencias_por_asig.append((a, evs))

    # Archivo final existente (en storage)
    project_report_exists = bool(
        s.reporte_fotografico and storage_file_exists(s.reporte_fotografico))

    # Job FINAL en curso
    last_job = (
        ReporteFotograficoJob.objects
        .filter(sesion=s)
        .exclude(log__icontains="[partial]")   # solo FINAL
        .order_by("-creado_en")
        .first()
    )
    job_running = bool(last_job and last_job.estado in (
        "pendiente", "procesando"))

    # Solo consideramos "ready" si HOY el servidor dice que está aprobado
    server_approved = s.estado in {"aprobado_supervisor", "aprobado_pm"}
    project_report_effective_ready = server_approved and project_report_exists and not job_running

    status_url = reverse("operaciones:project_report_status",
                         kwargs={"sesion_id": s.id})

    return render(
        request,
        "operaciones/billing_revisar_sesion.html",
        {
            "s": s,
            "evidencias_por_asig": evidencias_por_asig,
            "can_review": can_review,
            "project_report_exists": project_report_effective_ready,
            "job_running": job_running,
            "project_report_url": s.reporte_fotografico.url if project_report_effective_ready else "",
            "status_url": status_url,
            "poll_ms": 1000,
            # 👈 para que el JS no pinte aprobado si no lo está
            "server_approved": server_approved,
        },
    )


# operaciones/views_billing.py


@login_required
@rol_requerido('supervisor', 'pm', 'admin')
@require_POST
def cancelar_reporte_proyecto(request, sesion_id: int):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    job = (
        ReporteFotograficoJob.objects
        .filter(sesion=s, estado__in=("pendiente", "procesando"))
        .order_by("-creado_en")
        .first()
    )
    if not job:
        return JsonResponse({"ok": False, "message": "No running job."}, status=404)

    job.cancel_requested = True
    job.save(update_fields=["cancel_requested"])
    return JsonResponse({"ok": True})


@login_required
@require_GET
@never_cache
def project_report_status(request, sesion_id: int):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    approved = s.estado in ("aprobado_supervisor", "aprobado_pm")

    job = (
        ReporteFotograficoJob.objects
        .filter(sesion_id=sesion_id)
        .exclude(log__icontains="[partial]")   # solo FINAL
        .order_by("-creado_en")
        .first()
    )

    # Antes de aprobación
    if not approved:
        if job and job.estado in ("pendiente", "procesando"):
            state_map = {"pendiente": "pending", "procesando": "processing"}
            return JsonResponse({
                "state": state_map[job.estado],
                "processed": job.procesadas or 0,
                "total": job.total or 0,
                "error": job.error or "",
                "cancel_requested": bool(getattr(job, "cancel_requested", False)),
                "approved": False,
            })
        return JsonResponse({"state": "none", "approved": False})

    # Ya aprobado
    if not job:
        return JsonResponse({"state": "none", "approved": True})

    state_map = {"pendiente": "pending",
                 "procesando": "processing", "ok": "ok", "error": "error"}
    return JsonResponse({
        "state": state_map.get(job.estado, job.estado),
        "processed": job.procesadas or 0,
        "total": job.total or 0,
        "error": job.error or "",
        "cancel_requested": bool(getattr(job, "cancel_requested", False)),
        "approved": True,
    })
# ============================
# REPORTE FOTOGRÁFICO — PROYECTO
# ============================


# --- helper: construir XLSX a DISCO desde un queryset de evidencias ---
def _xlsx_path_from_evqs(sesion: SesionBilling, ev_qs, progress_cb=None, should_cancel=None):
    """
    Construye XLSX en disco (streaming) con progreso y cancelación opcional.
    """
    import xlsxwriter
    from tempfile import NamedTemporaryFile

    tmp_xlsx = NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp_xlsx.close()
    wb = xlsxwriter.Workbook(tmp_xlsx.name, {"in_memory": False})
    ws = wb.add_worksheet("PHOTOGRAPHIC REPORT")
    ws.hide_gridlines(2)

    fmt_title = wb.add_format({"bold": True, "align": "center",
                              "valign": "vcenter", "border": 1, "bg_color": "#E8EEF7"})
    fmt_head = wb.add_format({"border": 1, "align": "center", "valign": "vcenter",
                             "bold": True, "text_wrap": True, "bg_color": "#F5F7FB", "font_size": 11})
    fmt_box = wb.add_format({"border": 1})
    fmt_info = wb.add_format({"border": 1, "align": "center",
                             "valign": "vcenter", "text_wrap": True, "font_size": 9})

    # layout
    BLOCK_COLS, SEP_COLS = 6, 1
    LEFT_COL = 0
    RIGHT_COL = LEFT_COL + BLOCK_COLS + SEP_COLS

    HEAD_ROWS, ROWS_IMG, ROW_INFO, ROW_SPACE = 1, 12, 1, 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    # px helpers
    COL_W = 13
    IMG_ROW_H = 18
    def col_px(w): return int(w * 7 + 5)
    def row_px(h): return int(h * 4 / 3)
    max_w_px = BLOCK_COLS * col_px(COL_W)
    max_h_px = ROWS_IMG * row_px(IMG_ROW_H)

    # cols
    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, COL_W)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, COL_W)

    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)
    cur_row = 2

    def draw_block(r, c, ev):
        # header
        if sesion.proyecto_especial and ev.requisito_id is None:
            titulo_req = (ev.titulo_manual or "").strip() or "Title (missing)"
        else:
            titulo_req = ((getattr(ev.requisito, "titulo", "")
                          or "").strip() or "Extra")
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c +
                       BLOCK_COLS - 1, titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        # image frame
        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, IMG_ROW_H)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        # image
        try:
            tmp_img_path, w, h = tmp_jpeg_from_filefield(
                ev.imagen, max_side_px=1600, quality=75)
            sx = max_w_px / float(w)
            sy = max_h_px / float(h)
            scale = min(sx, sy, 1.0)
            scaled_w = int(w * scale)
            scaled_h = int(h * scale)
            x_off = max((max_w_px - scaled_w)//2, 0)
            y_off = max((max_h_px - scaled_h)//2, 0)
            ws.insert_image(img_top, c, tmp_img_path, {
                "x_scale": scale, "y_scale": scale,
                "x_offset": x_off, "y_offset": y_off,
                "object_position": 1,
            })
        except Exception:
            pass

        # info row
        info_row = img_top + ROWS_IMG
        dt = ev.client_taken_at or ev.tomada_en
        taken_txt = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        lat_txt = f"{float(ev.lat):.6f}" if ev.lat is not None else ""
        lng_txt = f"{float(ev.lng):.6f}" if ev.lng is not None else ""
        addr_txt = (ev.direccion_manual or "").strip()

        if sesion.proyecto_especial and ev.requisito_id is None:
            ws.merge_range(info_row, c,     info_row, c + 2,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 3, info_row, c + 5,
                           f"Address\n{addr_txt}",   fmt_info)
        else:
            ws.merge_range(info_row, c,     info_row, c + 1,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 2, info_row, c + 3,
                           f"Lat\n{lat_txt}",       fmt_info)
            ws.merge_range(info_row, c + 4, info_row, c + 5,
                           f"Lng\n{lng_txt}",       fmt_info)
        ws.set_row(info_row, 30)

    # iteración + progreso + cancelación
    idx = 0
    for ev in ev_qs.iterator(chunk_size=100):
        # cancel?
        if callable(should_cancel) and should_cancel(idx):
            raise ReportCancelled()

        if idx % 2 == 0:
            draw_block(cur_row, LEFT_COL, ev)
        else:
            draw_block(cur_row, RIGHT_COL, ev)
            cur_row += BLOCK_ROWS + ROW_SPACE
        idx += 1

        if callable(progress_cb):
            try:
                progress_cb(idx)
            except ReportCancelled:
                raise
            except Exception:
                pass

    if idx % 2 == 1:
        cur_row += BLOCK_ROWS + ROW_SPACE

    wb.close()

    if idx == 0 and callable(progress_cb):
        try:
            progress_cb(0)
        except Exception:
            pass

    return tmp_xlsx.name


def _xlsx_path_reporte_fotografico_qs(sesion: SesionBilling, ev_qs=None, progress_cb=None, should_cancel=None) -> str:
    if ev_qs is None:
        ev_qs = (
            EvidenciaFotoBilling.objects
            .filter(tecnico_sesion__sesion=sesion)
            .select_related("requisito")
            .order_by("requisito__orden", "tomada_en", "id")
        )
    return _xlsx_path_from_evqs(sesion, ev_qs, progress_cb=progress_cb, should_cancel=should_cancel)


@login_required
@require_POST
@rol_requerido('supervisor', 'admin', 'pm')
def generar_reporte_parcial_proyecto(request, sesion_id):
    """
    Encola un NUEVO job PARCIAL y marca cancelación de parciales previos.
    Responde de inmediato (jamás bloquea el request).
    """
    from usuarios.schedulers import enqueue_reporte_parcial

    s = get_object_or_404(SesionBilling, pk=sesion_id)

    # Cancela cualquier parcial previo en curso
    (ReporteFotograficoJob.objects
        .filter(sesion=s, estado__in=("pendiente", "procesando"), log__icontains="[partial]")
        .update(cancel_requested=True))

    # Crea nuevo job parcial
    job = ReporteFotograficoJob.objects.create(
        sesion=s, log="[partial] queued\n", total=0, procesadas=0
    )

    # Arranca SOLO cuando el insert haya sido confirmado
    def _start():
        enqueue_reporte_parcial(job.id)

    transaction.on_commit(_start)

    messages.info(
        request,
        "Generating partial photographic report in background. It will be available to download when it’s ready."
    )
    return redirect("operaciones:revisar_sesion", sesion_id=s.id)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@never_cache
def estado_reporte_parcial(request, sesion_id):
    """
    Estado del ÚLTIMO job PARCIAL (los que tienen log con '[partial]').
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    job = (ReporteFotograficoJob.objects
           .filter(sesion=s, log__icontains="[partial]")
           .order_by("-creado_en").first())

    if not job:
        return JsonResponse({"state": "none"})

    state_map = {"pendiente": "pending",
                 "procesando": "processing", "ok": "ok", "error": "error"}
    log_tail = (job.log or "").splitlines()[-5:]

    return JsonResponse({
        "state": state_map.get(job.estado, job.estado),
        "processed": job.procesadas or 0,
        "total": job.total or 0,
        "log_tail": log_tail,
        "error": job.error or "",
        "cancel_requested": bool(getattr(job, "cancel_requested", False)),
    })


class ReportCancelled(Exception):
    pass


def _cache_key_for_ff(ff) -> str:
    """
    Intenta generar un key de cache estable por archivo + last_modified.
    Si el storage no soporta get_modified_time, usamos el nombre.
    """
    base = getattr(ff, "name", str(ff))
    try:
        mtime = storage.get_modified_time(ff.name)
        base = f"{base}:{int(mtime.timestamp())}"
    except Exception:
        pass
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def tmp_jpeg_from_filefield(ff, max_side_px=1600, quality=75):
    """
    Descarga/convierte a JPEG optimizado y devuelve (path, width, height).
    - Usa thumbnail() que es muy rápida y conserva proporción.
    - Progressive + optimize para tamaño/velocidad.
    - Cache local en /tmp/reporte_cache para no reconvertir en regeneraciones.
    """
    cache_dir = os.path.join(tempfile.gettempdir(), "reporte_cache")
    os.makedirs(cache_dir, exist_ok=True)
    key = _cache_key_for_ff(ff)
    cached_path = os.path.join(cache_dir, f"{key}.jpg")
    if os.path.exists(cached_path):
        with Image.open(cached_path) as im:
            w, h = im.size
        return cached_path, w, h

    # leer datos del storage
    ff.open("rb")
    raw = ff.read()
    ff.close()

    im = Image.open(io.BytesIO(raw))
    im = im.convert("RGB")
    im.draft("RGB", (max_side_px, max_side_px))  # acelera decode de JPEG
    im.thumbnail((max_side_px, max_side_px), Image.LANCZOS)

    tmp_path = cached_path  # guardamos directo en cache
    im.save(tmp_path, "JPEG", quality=quality, optimize=True,
            progressive=True, subsampling="4:2:0")

    w, h = im.size
    return tmp_path, w, h


@login_required
@require_POST
@rol_requerido('supervisor', 'admin', 'pm')
def generar_reporte_parcial_asignacion(request, asig_id):
    """Compat: generate partial report by assignment -> redirect to project version."""
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id)
    return redirect('operaciones:generar_reporte_parcial_proyecto', sesion_id=a.sesion_id)


def _open_file_with_retries(ff, attempts=3, delay=1.0):
    """
    Intenta abrir el FieldFile del storage con pequeños reintentos.
    Devuelve un file-like abierto o levanta la última excepción.
    """
    last = None
    for _ in range(attempts):
        try:
            return ff.open("rb")
        except Exception as e:
            last = e
            time.sleep(delay)
    raise last


@login_required
def descargar_reporte_fotos_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    allowed = (getattr(request.user, "rol", "") in ("supervisor", "pm", "admin")) \
        or s.tecnicos_sesion.filter(tecnico=request.user).exists()
    if not allowed:
        raise Http404()

    if not s.reporte_fotografico or not storage_file_exists(s.reporte_fotografico):
        messages.warning(
            request, "The photo report is not available. You can regenerate it now.")
        return redirect("operaciones:regenerar_reporte_fotografico_proyecto", sesion_id=s.id)

    # 1) intentamos abrir con reintentos
    try:
        _open_file_with_retries(s.reporte_fotografico, attempts=3, delay=0.8)
        f = s.reporte_fotografico  # ya está abierto en modo rb
        filename = f'PHOTOGRAPHIC REPORT {s.proyecto_id}.xlsx'
        resp = FileResponse(f, as_attachment=True, filename=filename)
        resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp["Pragma"] = "no-cache"
        resp["Expires"] = http_date(0)
        return resp
    except Exception:
        # 2) Fallback opcional: URL presignada corta (no carga el web worker)
        try:
            # django-storages S3: .url(expire=...)
            url = s.reporte_fotografico.storage.url(
                s.reporte_fotografico.name, expire=600)
            return HttpResponseRedirect(url)
        except Exception:
            messages.error(
                request, "Could not open the report right now. Please try again.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def descargar_reporte_parcial_proyecto(request, sesion_id):
    import os
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    job = (ReporteFotograficoJob.objects
           .filter(sesion=s, estado="ok", log__icontains="[partial]")
           .order_by("-creado_en").first())

    if not job or not job.resultado_key:
        messages.warning(
            request, "Partial report is not available. Please generate it again.")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    key_or_path = job.resultado_key
    if os.path.exists(key_or_path):
        f = open(key_or_path, "rb")
    else:
        from django.core.files.storage import default_storage as storage
        if storage.exists(key_or_path):
            f = storage.open(key_or_path, "rb")
        else:
            messages.warning(
                request, "Partial report not found. Please generate it again.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    proj_slug = slugify(
        s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    filename = f"PHOTOGRAPHIC REPORT (partial) {proj_slug}-{s.id}.xlsx"
    resp = FileResponse(f, as_attachment=True, filename=filename)
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    resp["Expires"] = http_date(0)
    return resp


def _bytes_excel_reporte_fotografico_qs(sesion: SesionBilling, ev_qs=None) -> bytes:
    """
    Igual que antes pero con centrado exacto de imagen.
    Mantiene in_memory=True (para este caso) y sin cambios en el orden/iteración.
    """
    import io
    import xlsxwriter
    from .models import EvidenciaFotoBilling

    if ev_qs is None:
        ev_qs = (
            EvidenciaFotoBilling.objects
            .filter(tecnico_sesion__sesion=sesion)
            .select_related("requisito")
            .order_by("requisito__orden", "tomada_en", "id")
        )

    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {"in_memory": True})
    ws = wb.add_worksheet("PHOTOGRAPHIC REPORT")
    ws.hide_gridlines(2)

    fmt_title = wb.add_format({"bold": True, "align": "center",
                              "valign": "vcenter", "border": 1, "bg_color": "#E8EEF7"})
    fmt_head = wb.add_format({"border": 1, "align": "center", "valign": "vcenter",
                             "bold": True, "text_wrap": True, "bg_color": "#F5F7FB", "font_size": 11})
    fmt_box = wb.add_format({"border": 1})
    fmt_info = wb.add_format({"border": 1, "align": "center",
                             "valign": "vcenter", "text_wrap": True, "font_size": 9})

    BLOCK_COLS, SEP_COLS = 6, 1
    LEFT_COL = 0
    RIGHT_COL = LEFT_COL + BLOCK_COLS + SEP_COLS

    # Filas/constantes ANTES de calcular píxeles
    HEAD_ROWS, ROWS_IMG, ROW_INFO, ROW_SPACE = 1, 12, 1, 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    # Conversión a píxeles
    COL_W = 13
    IMG_ROW_H = 18
    def col_px(w): return int(w * 7 + 5)
    def row_px(h): return int(h * 4 / 3)
    max_w_px = BLOCK_COLS * col_px(COL_W)
    max_h_px = ROWS_IMG * row_px(IMG_ROW_H)

    # Columnas
    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, COL_W)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, COL_W)

    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)
    cur_row = 2

    def draw_block(r, c, ev):
        if sesion.proyecto_especial and ev.requisito_id is None:
            titulo_req = (ev.titulo_manual or "").strip() or "Title (missing)"
        else:
            titulo_req = ((getattr(ev.requisito, "titulo", "")
                          or "").strip() or "Extra")
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c +
                       BLOCK_COLS - 1, titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, IMG_ROW_H)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        # Escala + centrado
        image_data = None
        x_scale = y_scale = 1.0
        scaled_w = scaled_h = None
        try:
            from PIL import Image
            ev.imagen.open("rb")
            raw = ev.imagen.read()
            image_data = io.BytesIO(raw)
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
            sx = max_w_px / float(w)
            sy = max_h_px / float(h)
            scale = min(sx, sy, 1.0)
            x_scale = y_scale = scale
            scaled_w = int(w * scale)
            scaled_h = int(h * scale)
        except Exception:
            try:
                ev.imagen.open("rb")
                image_data = io.BytesIO(ev.imagen.read())
                scaled_w = max_w_px
                scaled_h = max_h_px
            except Exception:
                image_data = None

        if image_data:
            x_off = max((max_w_px - (scaled_w or max_w_px)) // 2, 0)
            y_off = max((max_h_px - (scaled_h or max_h_px)) // 2, 0)
            ws.insert_image(img_top, c, ev.imagen.name, {
                "image_data": image_data,
                "x_scale": x_scale, "y_scale": y_scale,
                "x_offset": x_off, "y_offset": y_off,
                "object_position": 1,
            })

        info_row = img_top + ROWS_IMG
        dt = ev.client_taken_at or ev.tomada_en
        taken_txt = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        lat_txt = f"{float(ev.lat):.6f}" if ev.lat is not None else ""
        lng_txt = f"{float(ev.lng):.6f}" if ev.lng is not None else ""
        addr_txt = (ev.direccion_manual or "").strip()

        if sesion.proyecto_especial and ev.requisito_id is None:
            ws.merge_range(info_row, c, info_row, c + 2,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 3, info_row, c + 5,
                           f"Address\n{addr_txt}",   fmt_info)
        else:
            ws.merge_range(info_row, c, info_row, c + 1,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 2, info_row, c + 3,
                           f"Lat\n{lat_txt}",        fmt_info)
            ws.merge_range(info_row, c + 4, info_row, c + 5,
                           f"Lng\n{lng_txt}",        fmt_info)
        ws.set_row(info_row, 30)

    idx = 0
    for ev in ev_qs:
        if idx % 2 == 0:
            draw_block(cur_row, LEFT_COL, ev)
        else:
            draw_block(cur_row, RIGHT_COL, ev)
            cur_row += BLOCK_ROWS + ROW_SPACE
        idx += 1
    if idx % 2 == 1:
        cur_row += BLOCK_ROWS + ROW_SPACE

    wb.close()
    return bio.getvalue()


def _bytes_excel_reporte_fotografico(sesion: SesionBilling) -> bytes:
    """
    XLSX con imágenes embebidas (2 por fila) y centradas.
    Mantiene el uso de memoria/flujo original.
    """
    import io
    import xlsxwriter
    from .models import EvidenciaFotoBilling

    evs = (
        EvidenciaFotoBilling.objects
        .filter(tecnico_sesion__sesion=sesion)
        .select_related("requisito")
        .order_by("requisito__orden", "tomada_en", "id")
    )

    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {"in_memory": True})
    ws = wb.add_worksheet("PHOTOGRAPHIC REPORT")
    ws.hide_gridlines(2)

    fmt_title = wb.add_format({
        "bold": True, "align": "center", "valign": "vcenter",
        "border": 1, "bg_color": "#E8EEF7"
    })
    fmt_head = wb.add_format({
        "border": 1, "align": "center", "valign": "vcenter",
        "bold": True, "text_wrap": True, "bg_color": "#F5F7FB", "font_size": 11
    })
    fmt_box = wb.add_format({"border": 1})
    fmt_info = wb.add_format({
        "border": 1, "align": "center", "valign": "vcenter",
        "text_wrap": True, "font_size": 9
    })

    BLOCK_COLS, SEP_COLS = 6, 1
    LEFT_COL = 0
    RIGHT_COL = LEFT_COL + BLOCK_COLS + SEP_COLS

    # Filas/constantes ANTES de calcular píxeles
    HEAD_ROWS, ROWS_IMG, ROW_INFO, ROW_SPACE = 1, 12, 1, 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    # Conversión a píxeles
    COL_W = 13
    IMG_ROW_H = 18
    def col_px(w): return int(w * 7 + 5)
    def row_px(h): return int(h * 4 / 3)
    max_w_px = BLOCK_COLS * col_px(COL_W)
    max_h_px = ROWS_IMG * row_px(IMG_ROW_H)

    # Columnas
    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, COL_W)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, COL_W)

    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)
    cur_row = 2

    def draw_block(r, c, ev):
        if sesion.proyecto_especial and ev.requisito_id is None:
            titulo_req = (ev.titulo_manual or "").strip() or "Extra"
        else:
            titulo_req = ((getattr(ev.requisito, "titulo", "")
                          or "").strip() or "Extra")
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c +
                       BLOCK_COLS - 1, titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, IMG_ROW_H)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        # Escala + centrado
        image_data = None
        x_scale = y_scale = 1.0
        scaled_w = scaled_h = None
        try:
            from PIL import Image
            ev.imagen.open("rb")
            raw = ev.imagen.read()
            image_data = io.BytesIO(raw)
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
            sx = max_w_px / float(w)
            sy = max_h_px / float(h)
            scale = min(sx, sy, 1.0)
            x_scale = y_scale = scale
            scaled_w = int(w * scale)
            scaled_h = int(h * scale)
        except Exception:
            try:
                ev.imagen.open("rb")
                image_data = io.BytesIO(ev.imagen.read())
                scaled_w = max_w_px
                scaled_h = max_h_px
            except Exception:
                image_data = None

        if image_data:
            x_off = max((max_w_px - (scaled_w or max_w_px)) // 2, 0)
            y_off = max((max_h_px - (scaled_h or max_h_px)) // 2, 0)
            ws.insert_image(img_top, c, ev.imagen.name, {
                "image_data": image_data,
                "x_scale": x_scale, "y_scale": y_scale,
                "x_offset": x_off, "y_offset": y_off,
                "object_position": 1,
            })

        info_row = img_top + ROWS_IMG
        dt = ev.client_taken_at or ev.tomada_en
        taken_txt = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        lat_txt = f"{float(ev.lat):.6f}" if ev.lat is not None else ""
        lng_txt = f"{float(ev.lng):.6f}" if ev.lng is not None else ""
        addr_txt = (ev.direccion_manual or "").strip()

        if sesion.proyecto_especial and ev.requisito_id is None:
            ws.merge_range(info_row, c,         info_row, c + 2,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 3,     info_row, c + 5,
                           f"Address\n{addr_txt}",   fmt_info)
        else:
            ws.merge_range(info_row, c,         info_row, c + 1,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 2,     info_row, c + 3,
                           f"Lat\n{lat_txt}",        fmt_info)
            ws.merge_range(info_row, c + 4,     info_row, c + 5,
                           f"Lng\n{lng_txt}",        fmt_info)
        ws.set_row(info_row, 30)

    idx = 0
    for ev in evs:
        if idx % 2 == 0:
            draw_block(cur_row, LEFT_COL, ev)
        else:
            draw_block(cur_row, RIGHT_COL, ev)
            cur_row += BLOCK_ROWS + ROW_SPACE
        idx += 1
    if idx % 2 == 1:
        cur_row += BLOCK_ROWS + ROW_SPACE

    wb.close()
    return bio.getvalue()


@login_required
@rol_requerido('supervisor', 'pm', 'admin')
@require_POST
def regenerar_reporte_fotografico_proyecto(request, sesion_id):
    """
    Encola la regeneración del REPORTE FINAL (nunca bloquea).
    Si ya hay uno en curso, solo informa y redirige.
    """
    from usuarios.schedulers import enqueue_reporte_fotografico

    s = get_object_or_404(SesionBilling, pk=sesion_id)

    last_job = (ReporteFotograficoJob.objects
                .filter(sesion=s)
                .exclude(log__icontains="[partial]")
                .order_by("-creado_en").first())
    if last_job and last_job.estado in ("pendiente", "procesando"):
        messages.info(
            request,
            "Photographic report is already being generated in background. It will replace the current file when it’s ready."
        )
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    job = ReporteFotograficoJob.objects.create(
        sesion=s, log="[regen] queued\n")

    # Arranca el job solo después del commit de la creación
    def _start():
        enqueue_reporte_fotografico(job.id)

    transaction.on_commit(_start)

    messages.info(
        request,
        "Regenerating photographic report in background. It will replace the current file when it’s ready."
    )
    return redirect("operaciones:revisar_sesion", sesion_id=s.id)

# ============================
# CONFIGURAR REQUISITOS (¡la que faltaba!)
# ============================


# operaciones/views_billing_exec.py


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def configurar_requisitos(request, sesion_id):
    """
    Configure a SINGLE requirements list per PROJECT and replicate it
    to ALL assigned technicians (overwrites their lists).
    Expected form arrays (parallel):
      - name[]        (str, required)
      - order[]       (int, optional)
      - mandatory[]   ("0" / "1", optional)
      - delete_id[]   (UI-only; not used in backend)
    Additionally, accepts:
      - proyecto_especial (checkbox) -> marks the session as a special project.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    asignaciones = list(
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("requisitos")
         .all()
    )

    # Preload canonical list from the first technician, if any
    canonical = []
    if asignaciones and asignaciones[0].requisitos.exists():
        canonical = list(asignaciones[0].requisitos.order_by("orden", "id"))

    if request.method == "POST":
        try:
            with transaction.atomic():
                # ✅ NEW: update special-project flag from checkbox
                s.proyecto_especial = bool(
                    request.POST.get("proyecto_especial"))
                s.save(update_fields=["proyecto_especial"])

                # 1) Read the shared list from the form
                names = request.POST.getlist("name[]")
                orders = request.POST.getlist("order[]")
                mand = request.POST.getlist("mandatory[]")  # "0"/"1"

                # Build a normalized list (skip empty rows)
                normalized = []
                for i, nm in enumerate(names):
                    name = (nm or "").strip()
                    if not name:
                        continue
                    try:
                        order = int(orders[i]) if i < len(orders) else i
                    except Exception:
                        order = i
                    mandatory = (mand[i] == "1") if i < len(mand) else True
                    normalized.append((order, name, mandatory))

                # 2) Replicate to ALL assignments: delete and create
                for a in asignaciones:
                    RequisitoFotoBilling.objects.filter(
                        tecnico_sesion=a).delete()
                    to_create = [
                        RequisitoFotoBilling(
                            tecnico_sesion=a,
                            titulo=name,
                            descripcion="",
                            obligatorio=mandatory,
                            orden=order,
                        )
                        for (order, name, mandatory) in normalized
                    ]
                    if to_create:
                        RequisitoFotoBilling.objects.bulk_create(to_create)

            messages.success(
                request, "Photo requirements saved (project-wide).")
            return redirect("operaciones:listar_billing")

        except Exception as e:
            messages.error(request, f"Could not save requirements: {e}")

        # On error, re-render with posted data
        canonical = []

        class _Row:
            def __init__(self, orden, titulo, obligatorio):
                self.orden = orden
                self.titulo = titulo
                self.obligatorio = obligatorio

        # If normalized exists (may not if parsing failed before), reflect it
        try:
            for (order, name, mandatory) in normalized:
                canonical.append(_Row(order, name, mandatory))
        except Exception:
            pass

    return render(
        request,
        "operaciones/billing_configurar_requisitos.html",
        {
            "sesion": s,
            "requirements": canonical,
            # ✅ NEW: expose flag for checkbox rendering in the template
            "is_special": bool(s.proyecto_especial),
        },
    )


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def import_requirements_page(request, sesion_id):
    """
    Shows the import screen with download links for the template and
    a file input to upload the CSV/XLSX.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    return render(
        request,
        "operaciones/billing_import_requisitos.html",
        {"sesion": s},
    )


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def download_requirements_template(request, sesion_id, ext):
    """
    Returns a requirements template as CSV or XLSX.
    Columns: name, order, mandatory
    - name: string (required)
    - order: integer (optional)
    - mandatory: 1/0 or true/false (optional; defaults to 1/true)
    """
    ext = (ext or "").lower()
    filename_base = f"requirements_template_billing_{sesion_id}"

    if ext == "csv":
        content = (
            "name,order,mandatory\n"
            "Front door,0,1\n"
            "Back door,1,1\n"
            "Panorama of site,2,0\n"
        )
        resp = HttpResponse(content, content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.csv"'
        return resp

    if ext in ("xlsx", "xls"):
        wb = Workbook()
        ws = wb.active
        ws.title = "Requirements"
        ws.append(["name", "order", "mandatory"])
        ws.append(["Front door", 0, 1])
        ws.append(["Back door", 1, 1])
        ws.append(["Panorama of site", 2, 0])

        from io import BytesIO
        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)
        resp = HttpResponse(
            bio.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="{filename_base}.xlsx"'
        return resp

    messages.error(request, "Unsupported format. Use csv or xlsx.")
    return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@require_POST
def importar_requisitos(request, sesion_id):
    """
    Import project-shared requirements from .csv or .xlsx and replicate
    to ALL assigned technicians (overwrites their lists).
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    f = request.FILES.get("file")

    if not f:
        messages.error(request, "Please select a CSV or XLSX file.")
        return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)

    ext = (f.name.rsplit(".", 1)[-1] or "").lower()
    normalized = []

    try:
        if ext == "csv":
            raw = f.read().decode("utf-8", errors="ignore")
            lines = raw.splitlines()
            if not lines:
                messages.warning(request, "The file is empty.")
                return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)

            header_line = lines[0].lower()
            if "name" in header_line:
                reader = csv.DictReader(io.StringIO(raw))
                for row in reader:
                    name = (row.get("name") or "").strip()
                    if not name:
                        continue
                    try:
                        order = int(row.get("order")) if row.get(
                            "order") not in (None, "") else len(normalized)
                    except Exception:
                        order = len(normalized)
                    mval = str(row.get("mandatory") or "1").strip().lower()
                    mandatory = mval in ("1", "true", "yes", "y")
                    normalized.append((order, name, mandatory))
            else:
                reader = csv.reader(lines)
                for row in reader:
                    if not row:
                        continue
                    name = (row[0] or "").strip()
                    if not name:
                        continue
                    normalized.append((len(normalized), name, True))

        elif ext in ("xlsx", "xls"):
            wb = load_workbook(f, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                messages.warning(request, "The spreadsheet is empty.")
                return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)

            header = [str(x).strip().lower()
                      if x is not None else "" for x in rows[0]]
            headered = "name" in header
            start = 1 if headered else 0

            if headered:
                i_name = header.index("name")
                i_order = header.index("order") if "order" in header else None
                i_mand = header.index(
                    "mandatory") if "mandatory" in header else None

                for r in rows[start:]:
                    name = (str(r[i_name]) if i_name < len(r)
                            and r[i_name] is not None else "").strip()
                    if not name:
                        continue
                    # order
                    if i_order is not None and i_order < len(r) and r[i_order] not in (None, ""):
                        try:
                            order = int(r[i_order])
                        except Exception:
                            order = len(normalized)
                    else:
                        order = len(normalized)
                    # mandatory
                    if i_mand is not None and i_mand < len(r) and r[i_mand] not in (None, ""):
                        mval = str(r[i_mand]).strip().lower()
                        mandatory = mval in ("1", "true", "yes", "y")
                    else:
                        mandatory = True
                    normalized.append((order, name, mandatory))
            else:
                for r in rows:
                    if not r:
                        continue
                    name = (str(r[0]) if r[0] is not None else "").strip()
                    if not name:
                        continue
                    normalized.append((len(normalized), name, True))
        else:
            messages.error(
                request, "Unsupported file type. Use .csv or .xlsx.")
            return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)

    except Exception as e:
        messages.error(request, f"Could not parse the file: {e}")
        return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)

    if not normalized:
        messages.warning(request, "No valid rows found in the file.")
        return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)

    # Replicate to all assignees
    try:
        with transaction.atomic():
            asignaciones = list(
                s.tecnicos_sesion.select_related(
                    "tecnico").prefetch_related("requisitos").all()
            )
            for a in asignaciones:
                RequisitoFotoBilling.objects.filter(tecnico_sesion=a).delete()
                objs = [
                    RequisitoFotoBilling(
                        tecnico_sesion=a,
                        titulo=name,
                        descripcion="",
                        obligatorio=mandatory,
                        orden=order,
                    )
                    for (order, name, mandatory) in normalized
                ]
                if objs:
                    RequisitoFotoBilling.objects.bulk_create(objs)

        messages.success(
            request, f"Imported {len(normalized)} requirements and applied them to all assignees."
        )
        return redirect("operaciones:configurar_requisitos", sesion_id=sesion_id)

    except Exception as e:
        messages.error(request, f"Could not apply imported requirements: {e}")
        return redirect("operaciones:import_requirements_page", sesion_id=sesion_id)


# ============================
# PM — Aprobación/Rechazo PROYECTO
# ============================

@login_required
@rol_requerido('pm', 'admin')
def pm_aprobar_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    if s.estado not in ("aprobado_supervisor",):
        messages.error(
            request, "El proyecto aún no está aprobado por Supervisor.")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)
    s.estado = "aprobado_pm"
    s.save(update_fields=["estado"])
    messages.success(request, "Proyecto aprobado por PM.")
    return redirect("operaciones:listar_billing")


@login_required
@rol_requerido('pm', 'admin')
def pm_rechazar_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    s.estado = "rechazado_pm"
    s.save(update_fields=["estado"])
    messages.warning(request, "Proyecto rechazado por PM.")
    return redirect("operaciones:revisar_sesion", sesion_id=s.id)


# ============================
# ELIMINAR EVIDENCIA (corregido)
# ============================

@login_required
@rol_requerido('usuario', 'supervisor', 'admin', 'pm')
@require_POST
def eliminar_evidencia(request, pk, evidencia_id):
    """
    El técnico puede borrar en 'en_proceso' o si fue rechazado con reintento.
    Supervisor/Admin/PM pueden borrar mientras el proyecto NO esté aprobado por supervisor/PM.
    Una vez que el supervisor aprueba (o PM aprueba), no se permite borrar.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk)
    s = a.sesion

    # 🔒 Candado por estado del proyecto: si ya fue aprobado por supervisor o PM, no se permite borrar
    if s.estado in ("aprobado_supervisor", "aprobado_pm"):
        messages.error(
            request, "Photos cannot be deleted after supervisor approval.")
        next_url = (
            request.POST.get("next")
            or (reverse("operaciones:upload_evidencias", args=[a.pk]) if a.tecnico_id == request.user.id else reverse("operaciones:revisar_sesion", args=[s.pk]))
        )
        return redirect(next_url)

    # ¿Quién es?
    is_owner = (a.tecnico_id == request.user.id)
    is_staff_role = getattr(request.user, "rol", None) in {
        "supervisor", "admin", "pm"}

    # Reglas para técnico: sólo en proceso o rechazado con reintento habilitado
    can_owner_delete = (
        a.estado == "en_proceso"
        or (a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    )

    # Staff puede borrar mientras NO esté aprobado (ya validado arriba)
    if not (is_staff_role or (is_owner and can_owner_delete)):
        return HttpResponseForbidden("You can't delete photos at this stage.")

    ev = get_object_or_404(EvidenciaFotoBilling,
                           pk=evidencia_id, tecnico_sesion=a)

    # Eliminar archivo físico si existe (ignorar errores del storage)
    try:
        ev.imagen.delete(save=False)
    except Exception:
        pass

    # Eliminar registro
    ev.delete()

    # Mensaje al usuario (en inglés)
    messages.success(request, "Photo deleted.")

    # Redirección: usar 'next' si viene, si no, a la vista apropiada (técnico vs staff)
    next_url = (
        request.POST.get("next")
        or (reverse("operaciones:upload_evidencias", args=[a.pk]) if is_owner else reverse("operaciones:revisar_sesion", args=[s.pk]))
    )
    return redirect(next_url)


log = logging.getLogger(__name__)


def _has_ops_role(u):
    return (
        getattr(u, "es_pm", False) or
        getattr(u, "es_facturacion", False) or
        getattr(u, "es_admin_general", False) or
        u.is_superuser
    )


# views.py

# asume tu modelo


@csrf_protect
def update_semana_pago_real(request, sesion_id):
    """
    Inline update for 'Real pay week' (YYYY-W##).
    Returns JSON always, with user-facing messages in English.
    """
    # --- Method check ---
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    # --- Detect AJAX/XHR ---
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    # --- Auth check (avoid 302 for AJAX) ---
    if not request.user.is_authenticated:
        if is_ajax:
            return JsonResponse(
                {"ok": False, "error": "Your session has expired. Please sign in again."},
                status=401,
            )
        return redirect_to_login(request.get_full_path())

    # --- Role check (admin | pm | facturacion). Ajusta a tu helper real ---
    allowed = False
    for attr in ("tiene_rol", "has_role"):
        fn = getattr(request.user, attr, None)
        if callable(fn) and fn("admin", "pm", "facturacion"):
            allowed = True
            break
    if request.user.is_superuser:
        allowed = True
    if not allowed:
        return JsonResponse(
            {"ok": False, "error": "You do not have permission to edit the real pay week."},
            status=403,
        )

    # --- Load session ---
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    # --- Business lock: PAID only admin/superuser can change ---
    if getattr(s, "finance_status", None) == "paid" and not request.user.is_superuser:
        return JsonResponse(
            {"ok": False, "error": "Locked (PAID). Only admins can edit."},
            status=403,
        )

    # --- Read value ---
    raw = (request.POST.get("semana") or "").strip()

    # 1) Empty => clear
    if raw == "":
        s.semana_pago_real = ""
        s.save(update_fields=["semana_pago_real"])
        return JsonResponse({"ok": True, "semana": ""})

    # 2) Normalization
    v = raw.lower().replace(" ", "")
    now = timezone.now()
    cur_year = now.isocalendar().year

    # Parse
    if re.fullmatch(r"\d{4}-w?\d{1,2}", v):            # 2025-w3, 2025-W34
        y, w = re.split(r"-w?", v)
        year = int(y)
        week = int(w)
    elif re.fullmatch(r"w?\d{1,2}", v):                # w34, 34 -> current year
        year = cur_year
        week = int(v.lstrip("w"))
    elif re.fullmatch(r"\d{1,2}/\d{4}", v):            # 34/2025
        w, y = v.split("/")
        year = int(y)
        week = int(w)
    elif re.fullmatch(r"\d{4}/\d{1,2}", v):            # 2025/34
        y, w = v.split("/")
        year = int(y)
        week = int(w)
    elif re.fullmatch(r"\d{4}-W\d{2}", raw):           # already correct
        s.semana_pago_real = raw
        s.save(update_fields=["semana_pago_real"])
        return JsonResponse({"ok": True, "semana": s.semana_pago_real})
    else:
        return JsonResponse(
            {
                "ok": False,
                "error": "Invalid format. Use: 2025-W34, W34, 34, 34/2025, or 2025/34.",
            },
            status=400,
        )

    # 3) Range check
    if not (1 <= week <= 53):
        return JsonResponse(
            {"ok": False, "error": "Week must be between 1 and 53."},
            status=400,
        )

    # 4) Save normalized YYYY-W##
    value_norm = f"{year}-W{week:02d}"
    s.semana_pago_real = value_norm
    s.save(update_fields=["semana_pago_real"])
    return JsonResponse({"ok": True, "semana": value_norm})
