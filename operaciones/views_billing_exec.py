# operaciones/views_billing_exec.py

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
from PIL import Image, ExifTags
from pillow_heif import register_heif_opener

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


@login_required
@rol_requerido('usuario')
def upload_evidencias(request, pk):
    """
    Upload evidence with team-wide locking by shared requirement title:
    - As soon as *anyone* uploads at least one photo for a given requirement *title*
      in the session, that title is "locked" for everyone (no more uploads for that title).
    - Deleting the last photo with that title will unlock it automatically (because
      we derive the lock from current evidences).
    - 'Finish' is enabled only if:
        (a) all mandatory shared titles have at least one photo (by anyone), AND
        (b) all assignees have accepted (started).
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    # tiny local utilities (requested: no helpers outside the views)
    def _norm_title(s: str) -> str:
        return (s or "").strip().lower()

    def _is_safe_wasabi_key(key: str) -> bool:
        return bool(key) and ".." not in key and not key.startswith("/")

    # ➕ UPDATED: allow saving manual title/address for special projects (when req is None)
    def _create_evidencia_from_key(req_id, key, nota, lat, lng, acc, taken_dt,
                                   titulo_manual="", direccion_manual=""):
        return EvidenciaFotoBilling.objects.create(
            tecnico_sesion=a,
            requisito_id=req_id,
            imagen=key,  # your storage accepts the direct key
            nota=nota or "",
            lat=lat or None,
            lng=lng or None,
            gps_accuracy_m=acc or None,
            client_taken_at=taken_dt,
            titulo_manual=titulo_manual or "",
            direccion_manual=direccion_manual or "",
        )

    # Can upload in current state?
    puede_subir = (a.estado == "en_proceso") or (
        a.estado == "rechazado_supervisor" and a.reintento_habilitado
    )
    if not puede_subir and request.method != "GET":
        messages.info(request, "This assignment is not open for uploads.")
        return redirect("operaciones:mis_assignments")

    s = a.sesion

    # -------------------- POST (upload) --------------------
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

        # ✅ NEW: manual fields (only required if it's a special project AND no req_id = "Extra")
        titulo_manual = (request.POST.get("titulo_manual") or "").strip()
        direccion_manual = (request.POST.get("direccion_manual") or "").strip()

        if s.proyecto_especial and not req_id:
            if not titulo_manual:
                messages.error(
                    request, "Please enter a Title for the photo (special project).")
                return redirect("operaciones:upload_evidencias", pk=a.pk)
            if not direccion_manual:
                messages.error(
                    request, "Please enter an Address for the photo (special project).")
                return redirect("operaciones:upload_evidencias", pk=a.pk)

        # If uploading for a specific requirement, enforce shared lock by title
        if req_id:
            req = get_object_or_404(
                RequisitoFotoBilling, pk=req_id, tecnico_sesion=a)
            shared_key = _norm_title(req.titulo)

            # All titles that already have evidence anywhere in the same session
            taken_titles = EvidenciaFotoBilling.objects.filter(
                tecnico_sesion__sesion=s,
                requisito__isnull=False,
            ).values_list("requisito__titulo", flat=True)

            locked_title_set = {_norm_title(t) for t in taken_titles if t}
            if shared_key in locked_title_set:
                messages.warning(
                    request,
                    "This requirement is already covered by the team. "
                    "Remove the existing photo to re-activate it."
                )
                return redirect("operaciones:upload_evidencias", pk=a.pk)

        # Create evidence entries
        n = 0
        for key in wasabi_keys:
            if _is_safe_wasabi_key(key):
                _create_evidencia_from_key(
                    req_id, key, nota, lat, lng, acc, taken_dt,
                    titulo_manual=titulo_manual, direccion_manual=direccion_manual
                )
                n += 1

        for f in files:
            # 1) Convert HEIC/HEIF to JPEG if needed
            f_conv = _to_jpeg_if_needed(f)

            # 2) Try EXIF if form didn't bring geo/time
            try:
                f_conv.seek(0)
                im = Image.open(f_conv)
                exif_lat, exif_lng, exif_dt = _exif_to_latlng_taken_at(im)
            except Exception:
                exif_lat = exif_lng = exif_dt = None
            finally:
                f_conv.seek(0)

            # 3) Final metadata (priority: form → EXIF)
            use_lat = lat or exif_lat
            use_lng = lng or exif_lng
            use_taken = taken_dt or exif_dt

            EvidenciaFotoBilling.objects.create(
                tecnico_sesion=a,
                requisito_id=req_id,
                imagen=f_conv,              # already converted if it was HEIC
                nota=nota,
                lat=use_lat,
                lng=use_lng,
                gps_accuracy_m=acc,
                client_taken_at=use_taken,  # your template/report already prioritizes client_taken_at
                # NEW: keep manual fields if special project + extra
                titulo_manual=titulo_manual,
                direccion_manual=direccion_manual,
            )
            n += 1

        if n:
            messages.success(request, f"{n} photo(s) uploaded.")
        else:
            messages.info(request, "No files selected.")
        return redirect("operaciones:upload_evidencias", pk=a.pk)

    # -------------------- GET (page context) --------------------

    # (1) Your own requirements + your uploaded count
    requisitos = (
        a.requisitos
         .annotate(uploaded=Count("evidencias"))
         .order_by("orden", "id")
    )

    # (2) Team-wide locked titles: any title with at least one evidence in the session
    taken_titles = EvidenciaFotoBilling.objects.filter(
        tecnico_sesion__sesion=s,
        requisito__isnull=False,
    ).values_list("requisito__titulo", flat=True)
    locked_title_set = {_norm_title(t) for t in taken_titles if t}
    locked_ids = [r.id for r in requisitos if _norm_title(
        r.titulo) in locked_title_set]

    # (3) Which mandatory titles (globally) are missing?
    required_titles = (
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion__sesion=s, obligatorio=True)
        .values_list("titulo", flat=True)
    )
    required_key_set = {_norm_title(t) for t in required_titles if t}
    covered_key_set = locked_title_set  # alias semantic
    missing_keys = required_key_set - covered_key_set

    # make “pretty” labels from any sample in the session
    sample_titles = list(
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion__sesion=s, titulo__isnull=False)
        .values_list("titulo", flat=True)
    )
    sample_map = {_norm_title(t): t for t in sample_titles if t}
    faltantes_global = [sample_map.get(k, k) for k in sorted(missing_keys)]

    # (4) Who has accepted (started)?
    asignaciones = list(
        s.tecnicos_sesion.select_related("tecnico").all()
    )
    pendientes_aceptar = []
    for asg in asignaciones:
        accepted = bool(asg.aceptado_en) or asg.estado != "asignado"
        if not accepted:
            name = getattr(asg.tecnico, "get_full_name",
                           lambda: "")() or asg.tecnico.username
            pendientes_aceptar.append(name)

    all_accepted = (len(pendientes_aceptar) == 0)

    # (5) Finish button rule (per your spec)
    can_finish = (
        a.estado == "en_proceso" and
        len(faltantes_global) == 0 and
        all_accepted
    )

    # Evidences list (for right column)
    evidencias = (
        a.evidencias
         .select_related("requisito")
         .order_by("requisito__orden", "tomada_en", "id")
    )

    can_delete = puede_subir  # only let them delete while they can upload

    # -------- Direct uploads context (unchanged UI; only data for JS) --------
    proj_id = (a.sesion.proyecto_id or "project").strip()
    proj_slug = slugify(proj_id) or "project"
    sess_tag = f"{proj_slug}-{a.sesion_id}"  # <-- ÚNICO CAMBIO: tag por sesión

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

            # NEW: team-wide locking / finish-reasons
            "locked_ids": locked_ids,
            "faltantes_global": faltantes_global,
            "pendientes_aceptar": pendientes_aceptar,
            "can_finish": can_finish,

            # Direct uploads flags
            "direct_uploads_enabled": settings.DIRECT_UPLOADS_ENABLED,
            "direct_uploads_max_mb": getattr(settings, "DIRECT_UPLOADS_MAX_MB", 15),
            "direct_uploads_folder": direct_uploads_folder,
            "project_id": a.sesion.proyecto_id,
            "current_user_name": tech_name,

            # ✅ NEW: flag for template logic
            "is_proyecto_especial": s.proyecto_especial,
        },
    )


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


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@transaction.atomic
def revisar_sesion(request, sesion_id):
    """
    Revisión unificada POR PROYECTO.
    - Supervisor aprueba/rechaza.
    - Al aprobar se genera y guarda UN Excel con imágenes embebidas.
    - Botones visibles si el proyecto está 'en_revision_supervisor'.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    asignaciones = (
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("evidencias__requisito")
         .all()
    )

    # Sincroniza estado del proyecto con sus asignaciones
    s.recomputar_estado_desde_asignaciones()

    can_review = s.estado in {"en_revision_supervisor"}

    if request.method == "POST":
        accion = request.POST.get("accion")
        comentario = (request.POST.get("comentario") or "").strip()

        if not can_review:
            messages.error(
                request, "This project is not ready for supervisor review.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        if accion == "aprobar":
            # Generar XLSX con imágenes embebidas
            try:
                bytes_excel = _bytes_excel_reporte_fotografico(s)
            except Exception as e:
                messages.error(request, f"No se pudo generar el informe: {e}")
                return redirect("operaciones:revisar_sesion", sesion_id=s.id)

            # Reemplazar archivo anterior si existía
            try:
                if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
                    s.reporte_fotografico.delete(save=False)
            except Exception:
                pass

            # p.ej. operaciones/reporte_fotografico/g033b-42/project/g033b-42.xlsx
            report_key = _project_report_key(s)
            s.reporte_fotografico.save(
                report_key, ContentFile(bytes_excel), save=False)

            # >>> NUEVO: fijar semana real = semana siguiente a la aprobación (si no existe)
            now = timezone.now()
            if not s.semana_pago_real:
                # próximo lunes respecto a 'now'
                next_monday = now + timedelta(days=(7 - now.weekday()))
                iso_year, iso_week, _ = next_monday.isocalendar()
                s.semana_pago_real = f"{iso_year}-W{iso_week:02d}"
            # <<< NUEVO

            # Actualizar estados
            s.estado = "aprobado_supervisor"
            s.save(update_fields=["reporte_fotografico",
                   "estado", "semana_pago_real"])

            for a in asignaciones:
                a.estado = "aprobado_supervisor"
                a.supervisor_comentario = comentario
                a.supervisor_revisado_en = now
                a.reintento_habilitado = False
                a.save(update_fields=[
                    "estado", "supervisor_comentario", "supervisor_revisado_en", "reintento_habilitado"
                ])

            messages.success(
                request, "Project approved by Supervisor. Photo report generated.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        elif accion == "rechazar":
            s.estado = "rechazado_supervisor"
            s.save(update_fields=["estado"])

            now = timezone.now()
            for a in asignaciones:
                a.estado = "rechazado_supervisor"
                a.supervisor_comentario = comentario or "Rejected."
                a.supervisor_revisado_en = now
                a.reintento_habilitado = True
                a.save(update_fields=[
                    "estado", "supervisor_comentario", "supervisor_revisado_en", "reintento_habilitado"
                ])

            messages.warning(
                request, "Project rejected. Reupload enabled for technicians.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        else:
            messages.error(request, "Acción no reconocida.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    # Para template
    evidencias_por_asig = []
    for a in asignaciones:
        evs = (a.evidencias
               .select_related("requisito")
               .order_by("requisito__orden", "tomada_en", "id"))
        evidencias_por_asig.append((a, evs))

    project_report_exists = bool(
        s.reporte_fotografico and getattr(s.reporte_fotografico, "name", "")
    )

    return render(request, "operaciones/billing_revisar_sesion.html", {
        "s": s,
        "evidencias_por_asig": evidencias_por_asig,
        "can_review": can_review,
        "project_report_exists": project_report_exists,
        "project_report_url": s.reporte_fotografico.url if project_report_exists else "",
    })

# ============================
# REPORTE FOTOGRÁFICO — PROYECTO
# ============================


def _xlsx_path_from_evqs(sesion: SesionBilling, ev_qs) -> str:
    """
    Genera el XLSX en un archivo temporal y devuelve su ruta (path).
    Usa imágenes recomprimidas/resizeadas en archivos temporales.
    RAM baja + sin timeouts del worker al enviar el archivo.
    """
    # 1) crear archivo temporal para el XLSX
    tmp_xlsx = NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp_xlsx.close()

    # 2) Workbook a disco (no BytesIO)
    wb = xlsxwriter.Workbook(tmp_xlsx.name, {"in_memory": False})
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

    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)

    HEAD_ROWS, ROWS_IMG, ROW_INFO, ROW_SPACE = 1, 12, 1, 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)
    cur_row = 2

    # contenedor (px) donde se “encaja” la imagen
    max_w_px = BLOCK_COLS * 60
    max_h_px = ROWS_IMG * 18

    def draw_block(r, c, ev):
        # Encabezado por bloque
        if sesion.proyecto_especial and ev.requisito_id is None:
            # Fuerza usar el título manual en proyectos especiales (fotos “extra”)
            titulo_req = (ev.titulo_manual or "").strip() or "Title (missing)"
        else:
            # Caso normal: requisito > (fallback) Extra
            titulo_req = ((getattr(ev.requisito, "titulo", "") or "").strip()
                          or "Extra")
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c +
                       BLOCK_COLS - 1, titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, 18)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        # === NUEVO: crear JPEG reducido en tmp ===
        try:
            tmp_img_path, w, h = tmp_jpeg_from_filefield(ev.imagen)
            sx = max_w_px / float(w)
            sy = max_h_px / float(h)
            scale = min(sx, sy, 1.0)
            scaled_w = int(w * scale)
            scaled_h = int(h * scale)
            x_off = max((max_w_px - scaled_w) // 2, 0)
            y_off = max((max_h_px - scaled_h) // 2, 0)

            ws.insert_image(img_top, c, tmp_img_path, {
                "x_scale": scale, "y_scale": scale,
                "x_offset": x_off, "y_offset": y_off,
                "object_position": 1,
            })
        except Exception:
            # si la imagen está corrupta, continua sin romper todo
            pass

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
                           f"Address\n{addr_txt}", fmt_info)
        else:
            ws.merge_range(info_row, c,     info_row, c + 1,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 2, info_row, c + 3,
                           f"Lat\n{lat_txt}",       fmt_info)
            ws.merge_range(info_row, c + 4, info_row, c + 5,
                           f"Lng\n{lng_txt}",       fmt_info)
        ws.set_row(info_row, 30)

    # iterar sin cargar todo en RAM
    idx = 0
    for ev in ev_qs.iterator():
        if idx % 2 == 0:
            draw_block(cur_row, LEFT_COL, ev)
        else:
            draw_block(cur_row, RIGHT_COL, ev)
            cur_row += BLOCK_ROWS + ROW_SPACE
        idx += 1
    if idx % 2 == 1:
        cur_row += BLOCK_ROWS + ROW_SPACE

    wb.close()
    return tmp_xlsx.name


def _xlsx_path_reporte_fotografico(sesion: SesionBilling) -> str:
    from .models import EvidenciaFotoBilling
    ev_qs = (
        EvidenciaFotoBilling.objects
        .filter(tecnico_sesion__sesion=sesion)
        .select_related("requisito")
        .order_by("requisito__orden", "tomada_en", "id")
    )
    return _xlsx_path_from_evqs(sesion, ev_qs)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def generar_reporte_parcial_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    # ⛔ Si ya está aprobado (o más), redirigir a regenerar (sustituye el final)
    if s.estado in ("aprobado_supervisor", "aprobado_pm"):
        messages.info(
            request, "Project already approved — regenerating final report instead.")
        return redirect("operaciones:regenerar_reporte_fotografico_proyecto", sesion_id=s.id)

    # (el resto tal cual: generar bytes y devolver FileResponse sin guardar)
    bytes_excel = _bytes_excel_reporte_fotografico_qs(s, ev_qs=None)
    proj_slug = slugify(
        s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    filename = f"PHOTOGRAPHIC REPORT (partial) {proj_slug}-{s.id}.xlsx"
    from io import BytesIO
    return FileResponse(BytesIO(bytes_excel), as_attachment=True, filename=filename)


@login_required
@rol_requerido('supervisor', 'admin', 'pm', 'usuario')
def generar_reporte_parcial_asignacion(request, asignacion_id):
    a = get_object_or_404(SesionBillingTecnico, pk=asignacion_id)
    is_owner = (a.tecnico_id == request.user.id)
    is_staff = getattr(request.user, "rol", "") in (
        "supervisor", "pm", "admin")
    if not (is_owner or is_staff):
        raise Http404()

    ev_qs = (a.evidencias.select_related("requisito")
             .order_by("requisito__orden", "tomada_en", "id"))
    xlsx_path = _xlsx_path_reporte_fotografico_qs(a.sesion, ev_qs=ev_qs)

    proj_slug = slugify(
        a.sesion.proyecto_id or f"billing-{a.sesion.id}") or f"billing-{a.sesion.id}"
    tech_slug = slugify(a.tecnico.get_full_name(
    ) or a.tecnico.username or f"user-{a.tecnico_id}") or f"user-{a.tecnico_id}"
    filename = f"PHOTOGRAPHIC REPORT {proj_slug}-{a.sesion.id} - {tech_slug}.xlsx"
    return FileResponse(open(xlsx_path, "rb"), as_attachment=True, filename=filename)


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def generar_reporte_parcial_proyecto(request, sesion_id):
    """
    Genera un XLSX con todas las fotos actuales de la sesión **sin** cambiar estados
    ni guardar como 'reporte_fotografico'. Sirve para avances diarios.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    bytes_excel = _bytes_excel_reporte_fotografico_qs(s, ev_qs=None)

    proj_slug = slugify(
        s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    filename = f"PHOTOGRAPHIC REPORT (partial) {proj_slug}-{s.id}.xlsx"

    from io import BytesIO
    return FileResponse(BytesIO(bytes_excel), as_attachment=True, filename=filename)


@login_required
@rol_requerido('supervisor', 'admin', 'pm', 'usuario')
def generar_reporte_parcial_asignacion(request, asignacion_id):
    """
    XLSX solo con las evidencias de **una** asignación (tecnico_sesion).
    - El técnico puede descargar el suyo.
    - Supervisor/PM/Admin pueden descargar el de cualquiera.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=asignacion_id)

    # permisos: dueño o staff
    is_owner = (a.tecnico_id == request.user.id)
    is_staff = getattr(request.user, "rol", "") in (
        "supervisor", "pm", "admin")
    if not (is_owner or is_staff):
        raise Http404()

    ev_qs = (
        a.evidencias.select_related("requisito")
         .order_by("requisito__orden", "tomada_en", "id")
    )
    bytes_excel = _bytes_excel_reporte_fotografico_qs(a.sesion, ev_qs=ev_qs)

    proj_slug = slugify(
        a.sesion.proyecto_id or f"billing-{a.sesion.id}") or f"billing-{a.sesion.id}"
    tech_slug = slugify(a.tecnico.get_full_name(
    ) or a.tecnico.username or f"user-{a.tecnico_id}") or f"user-{a.tecnico_id}"
    filename = f"PHOTOGRAPHIC REPORT {proj_slug}-{a.sesion.id} - {tech_slug}.xlsx"

    from io import BytesIO
    return FileResponse(BytesIO(bytes_excel), as_attachment=True, filename=filename)


@login_required
def regenerar_reporte_fotografico_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    if getattr(request.user, "rol", "") not in ("supervisor", "pm", "admin"):
        raise Http404()
    try:
        xlsx_path = _xlsx_path_reporte_fotografico(s)
        # reemplazar archivo anterior si existía
        if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
            try:
                s.reporte_fotografico.delete(save=False)
            except Exception:
                pass
        # subir el archivo desde disco (stream) al FileField
        with open(xlsx_path, "rb") as f:
            filename = f"PHOTOGRAPHIC REPORT {s.proyecto_id}.xlsx"
            s.reporte_fotografico.save(
                filename, ContentFile(f.read()), save=True)

        messages.success(
            request, "Project photographic report regenerated successfully.")
        return redirect("operaciones:descargar_reporte_fotos_proyecto", sesion_id=s.id)
    except Exception as e:
        messages.error(
            request, f"Could not generate the photographic report: {e}")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)


@login_required
def descargar_reporte_fotos_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    # Permisos: supervisor/pm/admin o técnicos asignados al proyecto
    allowed = (getattr(request.user, "rol", "") in ("supervisor", "pm", "admin")) \
        or s.tecnicos_sesion.filter(tecnico=request.user).exists()
    if not allowed:
        raise Http404()

    if not s.reporte_fotografico or not storage_file_exists(s.reporte_fotografico):
        messages.warning(
            request, "The photo report is not available. You can regenerate it now.")
        return redirect("operaciones:regenerar_reporte_fotografico_proyecto", sesion_id=s.id)

    return FileResponse(s.reporte_fotografico.open("rb"), as_attachment=True, filename="photo_report.xlsx")


def _bytes_excel_reporte_fotografico_qs(sesion: SesionBilling, ev_qs=None) -> bytes:
    """
    Igual a _bytes_excel_reporte_fotografico, pero permite inyectar un queryset de evidencias.
    Si ev_qs = None, usa todas las evidencias de la sesión.
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

    # ==== (copia del cuerpo de _bytes_excel_reporte_fotografico, pero usando ev_qs) ====
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
    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)

    HEAD_ROWS, ROWS_IMG, ROW_INFO, ROW_SPACE = 1, 12, 1, 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)
    cur_row = 2

    def draw_block(r, c, ev):
        if sesion.proyecto_especial and ev.requisito_id is None:
            # Fuerza usar el título manual en proyectos especiales (fotos “extra”)
            titulo_req = (ev.titulo_manual or "").strip() or "Title (missing)"
        else:
            # Caso normal: requisito > (fallback) Extra
            titulo_req = ((getattr(ev.requisito, "titulo", "") or "").strip()
                          or "Extra")
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c +
                       BLOCK_COLS - 1, titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, 18)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        max_w_px = BLOCK_COLS * 60
        max_h_px = ROWS_IMG * 18

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
                           f"Address\n{addr_txt}", fmt_info)
        else:
            ws.merge_range(info_row, c, info_row, c + 1,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 2, info_row, c +
                           3, f"Lat\n{lat_txt}", fmt_info)
            ws.merge_range(info_row, c + 4, info_row, c +
                           5, f"Lng\n{lng_txt}", fmt_info)
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
    XLSX with embedded images (2 per row).
    - Block header = requirement name, or custom title for 'extra' when special project.
    - Image centered inside a bordered box.
    - Info row:
        * Normal: Taken / Lat / Lng
        * Special project + extra: Taken / Address (no Lat/Lng)
    - Gridlines hidden.
    """
    import io
    import xlsxwriter
    from .models import EvidenciaFotoBilling

    # All evidences in project order
    evs = (
        EvidenciaFotoBilling.objects
        .filter(tecnico_sesion__sesion=sesion)
        .select_related("requisito")
        .order_by("requisito__orden", "tomada_en", "id")
    )

    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {"in_memory": True})
    ws = wb.add_worksheet("PHOTOGRAPHIC REPORT")

    # Hide gridlines (screen and print)
    ws.hide_gridlines(2)

    # ====== Formats ======
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

    # ====== Layout (2 per row) ======
    BLOCK_COLS = 6   # columns per block
    SEP_COLS = 1     # separator column
    LEFT_COL = 0
    RIGHT_COL = LEFT_COL + BLOCK_COLS + SEP_COLS  # 7

    # Column widths
    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)  # separator
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)

    # Row heights per block
    HEAD_ROWS = 1
    ROWS_IMG = 12
    ROW_INFO = 1
    ROW_SPACE = 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    # Sheet title
    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)

    cur_row = 2

    def draw_block(r, c, ev):
        # ----- Header: requirement title or custom title for extra -----
        if sesion.proyecto_especial and ev.requisito_id is None:
            # Fuerza usar el título manual en proyectos especiales (fotos “extra”)
            titulo_req = (ev.titulo_manual or "").strip() or "Title (missing)"
        else:
            # Caso normal: requisito > (fallback) Extra
            titulo_req = ((getattr(ev.requisito, "titulo", "") or "").strip()
                          or "Extra")
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c +
                       BLOCK_COLS - 1, titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        # ----- Image area (bordered) -----
        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, 18)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        # Approx container dimensions (px)
        max_w_px = BLOCK_COLS * 60
        max_h_px = ROWS_IMG * 18

        # Read image, scale, and center
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

        # ----- Info row -----
        info_row = img_top + ROWS_IMG
        dt = ev.client_taken_at or ev.tomada_en
        taken_txt = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        lat_txt = f"{float(ev.lat):.6f}" if ev.lat is not None else ""
        lng_txt = f"{float(ev.lng):.6f}" if ev.lng is not None else ""
        addr_txt = (ev.direccion_manual or "").strip()

        if sesion.proyecto_especial and ev.requisito_id is None:
            # Special project + extra: show Taken / Address (two wide blocks)
            ws.merge_range(info_row, c,         info_row, c + 2,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 3,     info_row, c + 5,
                           f"Address\n{addr_txt}",   fmt_info)
        else:
            # Normal: Taken / Lat / Lng
            ws.merge_range(info_row, c,         info_row, c + 1,
                           f"Taken at\n{taken_txt}", fmt_info)
            ws.merge_range(info_row, c + 2,     info_row, c + 3,
                           f"Lat\n{lat_txt}",        fmt_info)
            ws.merge_range(info_row, c + 4,     info_row, c + 5,
                           f"Lng\n{lng_txt}",        fmt_info)

        ws.set_row(info_row, 30)

    # Paint 2 per row
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
def regenerar_reporte_fotografico_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    # Only supervisor/pm/admin
    if getattr(request.user, "rol", "") not in ("supervisor", "pm", "admin"):
        raise Http404()

    try:
        bytes_excel = _bytes_excel_reporte_fotografico(s)

        if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
            try:
                s.reporte_fotografico.delete(save=False)
            except Exception:
                pass

        filename = f"PHOTOGRAPHIC REPORT {s.proyecto_id}.xlsx"
        s.reporte_fotografico.save(
            filename, ContentFile(bytes_excel), save=True
        )
        messages.success(
            request, "Project photographic report regenerated successfully."
        )
        return redirect("operaciones:descargar_reporte_fotos_proyecto", sesion_id=s.id)

    except Exception as e:
        messages.error(
            request, f"Could not generate the photographic report: {e}"
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


@login_required
@rol_requerido('admin', 'pm', 'facturacion')
@require_POST
def update_semana_pago_real(request, sesion_id):
    from django.utils import timezone
    import re

    s = get_object_or_404(SesionBilling, pk=sesion_id)
    raw = (request.POST.get("semana") or "").strip()

    # 1) Vacío => limpiar y mostrar "—" en la vista
    if raw == "":
        s.semana_pago_real = ""
        s.save(update_fields=["semana_pago_real"])
        return JsonResponse({"ok": True, "semana": ""})

    # 2) Normalizador de formatos
    v = raw.lower().replace(" ", "")
    now = timezone.now()
    cur_year = now.isocalendar().year

    # patrones
    m = None
    # yyyy-w## o yyyy-w#  (e.g. 2025-w3, 2025-W34)
    if re.fullmatch(r"\d{4}-w?\d{1,2}", v):
        y, w = re.split(r"-w?", v)
        year = int(y)
        week = int(w)
    # w## o ##  (e.g. w34, 34) => usa año actual
    elif re.fullmatch(r"w?\d{1,2}", v):
        year = cur_year
        week = int(v.lstrip("w"))
    # ##/yyyy  (e.g. 34/2025)
    elif re.fullmatch(r"\d{1,2}/\d{4}", v):
        w, y = v.split("/")
        year = int(y)
        week = int(w)
    # yyyy/##  (e.g. 2025/34)
    elif re.fullmatch(r"\d{4}/\d{1,2}", v):
        y, w = v.split("/")
        year = int(y)
        week = int(w)
    # ya en formato correcto yyyy-W##
    elif re.fullmatch(r"\d{4}-W\d{2}", raw):
        s.semana_pago_real = raw
        s.save(update_fields=["semana_pago_real"])
        return JsonResponse({"ok": True, "semana": s.semana_pago_real})
    else:
        return JsonResponse({"ok": False, "error": "Use formats like 2025-W34, W34, 34, 34/2025."}, status=400)

    # 3) Validación de rango
    if not (1 <= week <= 53):
        return JsonResponse({"ok": False, "error": "Week must be between 1 and 53."}, status=400)

    # 4) Formateo final YYYY-W##
    value_norm = f"{year}-W{week:02d}"
    s.semana_pago_real = value_norm
    s.save(update_fields=["semana_pago_real"])
    return JsonResponse({"ok": True, "semana": value_norm})
