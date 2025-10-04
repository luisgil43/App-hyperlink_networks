# operaciones/views_fotos_zip.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.http import FileResponse, Http404
from django.utils.text import slugify
from datetime import datetime
from urllib.parse import urlparse
from tempfile import SpooledTemporaryFile
import zipfile
import os
import re
import logging

from operaciones.models import SesionBilling
from usuarios.decoradores import rol_requerido  # ajusta el import si aplica

logger = logging.getLogger(__name__)

# ---------- Utilidades de nombres ----------
_slug_cleanup_re = re.compile(r"[^-a-zA-Z0-9_.]+")


def _safe_slug(s: str, default="item"):
    if not s:
        return default
    s = slugify(str(s), allow_unicode=True)
    s = _slug_cleanup_re.sub("-", s).strip("-_.")
    return s or default


SAFE_REPLACEMENT = "–"  # en-dash para reemplazar / y \


def _safe_component_preserve(s: str, fallback="(sin-titulo)", max_len=120) -> str:
    """
    Mantiene acentos, mayúsculas y espacios, pero:
      - Reemplaza / y \ para no romper rutas
      - Quita caracteres de control
      - Recorta longitudes extremas
    """
    if not s:
        s = fallback
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    s = s.replace("/", SAFE_REPLACEMENT).replace("\\", SAFE_REPLACEMENT)
    s = s.strip() or fallback
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def _guess_ext(name_or_url: str, default=".jpg") -> str:
    if not name_or_url:
        return default
    try:
        path = urlparse(
            name_or_url).path if "://" in name_or_url else name_or_url
    except Exception:
        path = name_or_url
    _, ext = os.path.splitext(os.path.basename(path))
    return ext if ext else default


def _read_from_storage_or_url(storage_obj, storage_name: str, url: str) -> bytes | None:
    """
    Lee bytes desde el storage del campo (ev.imagen.storage).
    Si falla, intenta por URL pública (firmada).
    """
    # 1) Storage del field (S3/Wasabi asociado al FileField)
    if storage_obj and storage_name:
        try:
            if storage_obj.exists(storage_name):
                with storage_obj.open(storage_name, "rb") as fh:
                    return fh.read()
        except Exception as e:
            logger.warning(
                "ZIP fotos: fallo open storage '%s': %s", storage_name, e)

    # 2) Fallback: URL pública
    if url and (url.startswith("http://") or url.startswith("https://")):
        try:
            import requests
            r = requests.get(url, timeout=15)
            if r.ok:
                return r.content
            logger.warning("ZIP fotos: GET url %s -> status %s",
                           url, r.status_code)
        except Exception as e:
            logger.warning("ZIP fotos: fallo GET url '%s': %s", url, e)

    return None


# ---------- Vista principal ----------

@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def descargar_fotos_zip(request, sesion_id: int):
    """
    Genera un .zip con TODAS las fotos de la sesión en UNA SOLA CARPETA:
      <PROJECT_ID> /
        <TÍTULO EXACTO REQUERIMIENTO|Extra>.<ext>

    - No crea subcarpetas por técnico ni por requerimiento.
    - El título del requerimiento se conserva tal cual fue guardado (acentos/mayúsculas).
    - Solo se sanitizan '/' y '\\' para no romper rutas.
    - Lee los bytes desde el storage del propio campo (Wasabi), con fallback a URL.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    # Carpeta raíz: SOLO el Project ID (o fallback si no existe)
    root_name = _safe_component_preserve(
        s.proyecto_id or f"Billing_{s.id}",
        max_len=80
    )

    # Cargamos todas las evidencias de la sesión (vía asignaciones)
    asignaciones = (
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("evidencias__requisito")
         .all()
    )

    spooled = SpooledTemporaryFile(
        max_size=100 * 1024 * 1024)  # 100MB antes de disco
    total_agregadas = 0
    total_fallidas = 0
    total_encontradas = 0

    with zipfile.ZipFile(spooled, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for a in asignaciones:
            evs_rel = getattr(a, "evidencias", None)
            if not evs_rel:
                continue

            for ev in evs_rel.all():
                imagen_field = getattr(ev, "imagen", None)
                if not imagen_field:
                    continue

                # ✅ storage y key EXACTOS donde se guardó
                field_storage = getattr(imagen_field, "storage", None)
                storage_name = getattr(imagen_field, "name", "") or ""
                public_url = getattr(imagen_field, "url", "") or ""

                # ✅ Título EXACTO del requerimiento, o "Extra"
                if getattr(s, "proyecto_especial", False) and not getattr(ev, "requisito_id", None):
                    req_title_raw = getattr(ev, "titulo_manual", "") or "Extra"
                else:
                    req = getattr(ev, "requisito", None)
                    req_title_raw = getattr(req, "titulo", "") or "Extra"

                # ✅ Lee bytes (Wasabi) con fallback a URL
                data = _read_from_storage_or_url(
                    field_storage, storage_name, public_url)
                if data is None:
                    total_fallidas += 1
                    continue

                # Extensión por key/URL
                ext = _guess_ext(storage_name or public_url, default=".jpg")

                # ✅ Nombre final: plano en <PROJECT_ID>/ + "<Requisito>.ext"
                file_title = _safe_component_preserve(
                    req_title_raw, max_len=120)
                arcname = f"{root_name}/{file_title}{ext}"

                try:
                    zf.writestr(arcname, data)
                    total_encontradas += 1
                    total_agregadas += 1
                except Exception as e:
                    total_encontradas += 1
                    total_fallidas += 1
                    logger.warning(
                        "ZIP fotos: fallo writestr '%s': %s", arcname, e)
                    continue

    logger.info(
        "ZIP fotos sesion=%s -> encontradas=%s agregadas=%s fallidas=%s",
        s.id, total_encontradas, total_agregadas, total_fallidas
    )

    if total_agregadas == 0:
        raise Http404("No hay fotos disponibles para esta sesión.")

    spooled.seek(0)
    filename = f"{root_name}.zip"
    resp = FileResponse(spooled, as_attachment=True,
                        filename=filename, content_type="application/zip")
    resp["Cache-Control"] = "no-store"
    return resp
