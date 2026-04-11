# operaciones/views_billing_camera.py

from __future__ import annotations

import json
import re
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from usuarios.decoradores import rol_requerido

from .models import (EvidenciaFotoBilling, RequisitoFotoBilling,
                     SesionBillingTecnico)

SAFE_PREFIX = getattr(settings, "DIRECT_UPLOADS_SAFE_PREFIX", "operaciones/reporte_fotografico/").rstrip("/") + "/"


def _is_asig_active(asig) -> bool:
    return getattr(asig, "is_active", True) is True




def _safe_wasabi_key(key: str) -> bool:
    return isinstance(key, str) and key.startswith(SAFE_PREFIX) and ".." not in key and not key.startswith("/")


def _norm_title(s: str) -> str:
    return (s or "").strip().lower()


def _team_locked_titles_for_session(asig: SesionBillingTecnico) -> set[str]:
    taken_titles = (
        EvidenciaFotoBilling.objects
        .filter(tecnico_sesion__sesion=asig.sesion, requisito__isnull=False)
        .values_list("requisito__titulo", flat=True)
    )
    return {_norm_title(t) for t in taken_titles if t}


def _pending_reqs_for_asig(asig: SesionBillingTecnico) -> list[RequisitoFotoBilling]:
    """
    Pendientes = requisitos de esta asignación que aún NO están cubiertos por el equipo
    (lock por título compartido).
    """
    locked_set = _team_locked_titles_for_session(asig)

    qs = (
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion=asig)
        .order_by("orden", "id")
    )

    out: list[RequisitoFotoBilling] = []
    for r in qs:
        if _norm_title(r.titulo) in locked_set:
            continue
        out.append(r)
    return out


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

    puede_subir = (a.estado == "en_proceso") or (a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    if not puede_subir:
        return HttpResponseForbidden("This assignment is not open for uploads.")

    # Pendientes (para dropdown)
    pending_reqs = _pending_reqs_for_asig(a)

    # req_id actual (si viene en query) pero SOLO si está pendiente.
    req_id = (request.GET.get("req_id") or "").strip() or None
    req = None

    if req_id:
        # Si el req_id no está en pendientes, lo forzamos a Extra (para no mostrar completados/locked)
        req = next((x for x in pending_reqs if str(x.id) == str(req_id)), None)
        if not req:
            req_id = None

    # Si no vino req_id y hay pendientes, por defecto mostramos el primero pendiente.
    if not req_id and pending_reqs:
        req = pending_reqs[0]
        req_id = str(req.id)

    # Folder directo a Wasabi
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
        "req_id": (req.id if req else ""),
        "req_title": (req.titulo if req else "Extra Photo"),
        "direct_uploads_folder": direct_uploads_folder,
        "project_id": a.sesion.proyecto_id,
        "estado": a.sesion.estado,
        "can_delete": puede_subir,
        "req_options": [{"id": r.id, "title": r.titulo} for r in pending_reqs],
        "selected_req_id": (req.id if req else ""),
    }
    return render(request, "operaciones/billing_camera_take.html", ctx)


@login_required
@rol_requerido("usuario")
@require_GET
def camera_requirements_status(request, asig_id: int):
    """
    Devuelve JSON con:
    - pendientes (solo los que NO están cubiertos por el equipo)
    - next_req_id (primero pendiente) o null
    """
    a = get_object_or_404(SesionBillingTecnico, pk=asig_id, tecnico=request.user)
    if not _is_asig_active(a):
        return JsonResponse({"ok": False, "error": "Assignment no longer available."}, status=404)

    puede_subir = (a.estado == "en_proceso") or (a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    if not puede_subir:
        return JsonResponse({"ok": False, "error": "Assignment not open for uploads."}, status=403)

    pending_reqs = _pending_reqs_for_asig(a)
    next_req_id = pending_reqs[0].id if pending_reqs else None

    return JsonResponse(
        {
            "ok": True,
            "pending": [{"id": r.id, "title": r.titulo} for r in pending_reqs],
            "next_req_id": next_req_id,
        },
        status=200,
    )


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
        "address": "...."
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

        locked_set = _team_locked_titles_for_session(a)
        if _norm_title(req.titulo) in locked_set:
            return JsonResponse(
                {"ok": False, "error": "This requirement is already covered by the team."},
                status=409
            )

    ev = _create_evidencia_from_key(
        asig=a,
        req_id=req_id,
        key=key,
        nota=nota,
        lat=lat,
        lng=lng,
        acc=acc,
        taken_dt=taken_dt,
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
