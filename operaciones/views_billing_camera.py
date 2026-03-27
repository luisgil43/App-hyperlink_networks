# operaciones/views_billing_camera.py

import json
import re
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import (Http404, HttpResponseBadRequest,
                         HttpResponseForbidden, JsonResponse)
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from usuarios.decoradores import rol_requerido

from .models import (EvidenciaFotoBilling, RequisitoFotoBilling,
                     SesionBillingTecnico)

# Reutilizamos helpers existentes en tu views_billing_exec.py
# (presign_wasabi ya existe como endpoint)
SAFE_PREFIX = getattr(settings, "DIRECT_UPLOADS_SAFE_PREFIX", "operaciones/reporte_fotografico/").rstrip("/") + "/"


def _is_asig_active(asig) -> bool:
    return getattr(asig, "is_active", True) is True


def _cp_from_project_id(project_id: str) -> str:
    """
    En tu ejemplo: 0161AA_12_CP11235 -> CP-11235
    Si no matchea, devolvemos CP-<last_digits> o CP-<project_id>
    """
    s = (project_id or "").strip()
    m = re.search(r"(CP[-_ ]?\d+)", s, re.IGNORECASE)
    if m:
        val = m.group(1).upper().replace("_", "").replace(" ", "").replace("CP", "")
        val = val.replace("-", "")
        return f"CP-{val}"
    # fallback a últimos dígitos si existen
    m2 = re.search(r"(\d{3,})$", s)
    if m2:
        return f"CP-{m2.group(1)}"
    return f"CP-{s}" if s else "CP-—"


def _safe_wasabi_key(key: str) -> bool:
    return isinstance(key, str) and key.startswith(SAFE_PREFIX) and ".." not in key and not key.startswith("/")


def _create_evidencia_from_key(
    asig: SesionBillingTecnico,
    req_id,
    key: str,
    nota: str,
    lat,
    lng,
    acc,
    taken_dt,
    titulo_manual: str = "",
    direccion_manual: str = "",
):
    ev = EvidenciaFotoBilling(
        tecnico_sesion=asig,
        requisito_id=req_id or None,
        nota=nota or "",
        lat=lat or None,
        lng=lng or None,
        gps_accuracy_m=acc or None,
        client_taken_at=taken_dt,
        titulo_manual=titulo_manual or "",
        direccion_manual=direccion_manual or "",
    )
    ev.imagen.name = key.strip()
    ev.save()
    return ev


@login_required
@rol_requerido("usuario")
@require_GET
def camera_take(request, asig_id: int):
    """
    Pantalla cámara + preview + cola para subir.
    req_id opcional: si viene, es para requisito; si no viene => Extra.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)
    if not _is_asig_active(a):
        raise Http404()

    # Regla de subir: igual que en upload_evidencias_ajax
    puede_subir = (a.estado == "en_proceso") or (a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    if not puede_subir:
        # Mantenemos coherencia: no dejar tomar fotos si no puede subir
        return HttpResponseForbidden("This assignment is not open for uploads.")

    req_id = (request.GET.get("req_id") or "").strip() or None
    req = None
    if req_id:
        req = get_object_or_404(RequisitoFotoBilling, pk=req_id, tecnico_sesion=a)

        # lock por título compartido (si ya está cubierto por el equipo)
        def _norm_title(s: str) -> str:
            return (s or "").strip().lower()

        shared_key = _norm_title(req.titulo)
        taken_titles = (
            EvidenciaFotoBilling.objects
            .filter(tecnico_sesion__sesion=a.sesion, requisito__isnull=False)
            .values_list("requisito__titulo", flat=True)
        )
        locked_set = {_norm_title(t) for t in taken_titles if t}
        if shared_key in locked_set:
            # ya está cubierto por el equipo
            return HttpResponseForbidden("This requirement is already covered by the team.")

    # Folder directo a Wasabi (igual que tu upload_evidencias)
    proj_id = (a.sesion.proyecto_id or "project").strip()
    proj_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", proj_id).strip("-").lower() or "project"
    sess_tag = f"{proj_slug}-{a.sesion_id}"

    tech = a.tecnico
    tech_name = (getattr(tech, "get_full_name", lambda: "")() or getattr(tech, "username", "") or f"user-{tech.id}").strip()
    tech_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", tech_name).strip("-").lower() or f"user-{tech.id}"

    direct_uploads_folder = f"operaciones/reporte_fotografico/{sess_tag}/{tech_slug}/evidencia/"

    ctx = {
        "a": a,
        "req": req,
        "req_id": req.id if req else "",
        "req_title": req.titulo if req else "Extra Photo",
        "direct_uploads_folder": direct_uploads_folder,
        "project_id": a.sesion.proyecto_id,
        "estado": a.sesion.estado,  # estado del proyecto
        "cp_text": _cp_from_project_id(a.sesion.proyecto_id),
        "can_delete": puede_subir,  # por consistencia UI
    }
    return render(request, "operaciones/billing_camera_take.html", ctx)


@login_required
@rol_requerido("usuario")
@require_POST
@csrf_protect
def camera_create_evidence_from_key(request, asig_id: int):
    """
    Confirma una foto ya subida a Wasabi (presigned POST) y crea EvidenciaFotoBilling.
    Espera JSON:
      {
        "key": "...",
        "req_id": 123 | null,
        "nota": "...",
        "lat": "...",
        "lng": "...",
        "acc": "...",
        "client_taken_at": "ISO",
        "address": "...."   (auto)
      }
    """
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)
    if not _is_asig_active(a):
        return JsonResponse({"ok": False, "error": "Assignment no longer available."}, status=404)

    puede_subir = (a.estado == "en_proceso") or (a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    if not puede_subir:
        return JsonResponse({"ok": False, "error": "Assignment not open for uploads."}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    key = (payload.get("key") or "").strip()
    if not _safe_wasabi_key(key):
        return JsonResponse({"ok": False, "error": "Invalid key."}, status=400)

    req_id = payload.get("req_id") or None
    nota = (payload.get("nota") or "").strip()

    lat = payload.get("lat") or None
    lng = payload.get("lng") or None
    acc = payload.get("acc") or None

    taken = (payload.get("client_taken_at") or "").strip()
    taken_dt = None
    if taken:
        try:
            taken_dt = timezone.make_aware(datetime.fromisoformat(taken.replace("Z", "+00:00")))
            taken_dt = timezone.localtime(taken_dt)
        except Exception:
            taken_dt = None

    address = (payload.get("address") or "").strip()

    # Si es requisito: validar que exista y que NO esté locked por equipo (misma regla)
    if req_id:
        req = get_object_or_404(RequisitoFotoBilling, pk=int(req_id), tecnico_sesion=a)

        def _norm_title(s: str) -> str:
            return (s or "").strip().lower()

        shared_key = _norm_title(req.titulo)
        taken_titles = (
            EvidenciaFotoBilling.objects
            .filter(tecnico_sesion__sesion=a.sesion, requisito__isnull=False)
            .values_list("requisito__titulo", flat=True)
        )
        locked_set = {_norm_title(t) for t in taken_titles if t}
        if shared_key in locked_set:
            return JsonResponse(
                {"ok": False, "error": "This requirement is already covered by the team."},
                status=409
            )

    # Crear evidencia apuntando al key ya subido (sin re-subir bytes)
    ev = _create_evidencia_from_key(
        asig=a,
        req_id=req_id,
        key=key,
        nota=nota,
        lat=lat,
        lng=lng,
        acc=acc,
        taken_dt=taken_dt,
        # Para tu caso: dirección automática => la guardamos aquí
        titulo_manual="",
        direccion_manual=address,
    )

    titulo = ev.requisito.titulo if ev.requisito_id else (ev.titulo_manual or "Extra")
    fecha_txt = timezone.localtime(ev.client_taken_at or ev.tomada_en).strftime("%Y-%m-%d %H:%M")

    return JsonResponse({
        "ok": True,
        "evidencia": {
            "id": ev.id,
            "url": ev.imagen.url,
            "titulo": titulo,
            "fecha": fecha_txt,
            "lat": str(ev.lat) if ev.lat is not None else None,
            "lng": str(ev.lng) if ev.lng is not None else None,
            "acc": str(ev.gps_accuracy_m) if ev.gps_accuracy_m is not None else None,
            "req_id": ev.requisito_id,
        }
    })