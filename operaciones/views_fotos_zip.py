# operaciones/views_fotos_zip.py
import logging
import os
import re
import zipfile
from tempfile import SpooledTemporaryFile
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.text import slugify

from operaciones.models import SesionBilling
from usuarios.decoradores import rol_requerido  # ajusta el import si aplica

logger = logging.getLogger(__name__)

_slug_cleanup_re = re.compile(r"[^-a-zA-Z0-9_.]+")

SAFE_REPLACEMENT = "–"  # en-dash para reemplazar / y \


def _safe_component_preserve(s: str, fallback="(sin-titulo)", max_len=120) -> str:
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
        path = urlparse(name_or_url).path if "://" in name_or_url else name_or_url
    except Exception:
        path = name_or_url
    _, ext = os.path.splitext(os.path.basename(path))
    return ext if ext else default


def _read_from_storage_or_url(storage_obj, storage_name: str, url: str):
    # 1) Storage del field (S3/Wasabi asociado al FileField)
    if storage_obj and storage_name:
        try:
            if storage_obj.exists(storage_name):
                with storage_obj.open(storage_name, "rb") as fh:
                    return fh.read()
        except Exception as e:
            logger.warning("ZIP fotos: fallo open storage '%s': %s", storage_name, e)

    # 2) Fallback: URL pública
    if url and (url.startswith("http://") or url.startswith("https://")):
        try:
            import requests
            r = requests.get(url, timeout=15)
            if r.ok:
                return r.content
            logger.warning("ZIP fotos: GET url %s -> status %s", url, r.status_code)
        except Exception as e:
            logger.warning("ZIP fotos: fallo GET url '%s': %s", url, e)

    return None


@login_required
@rol_requerido("supervisor", "admin", "pm")
def descargar_fotos_zip(request, sesion_id: int):
    """
    Genera un .zip con TODAS las fotos de la sesión en UNA SOLA CARPETA:
      <PROJECT_ID>/
        <TÍTULO>.ext   (si se repite, agrega sufijo)

    Importante: evita colisiones de nombres dentro del ZIP.
    """
    from django.contrib import messages
    from django.shortcuts import redirect

    s = get_object_or_404(SesionBilling, pk=sesion_id)

    root_name = _safe_component_preserve(
        s.proyecto_id or f"Billing_{s.id}",
        max_len=80
    )

    asignaciones = (
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("evidencias__requisito")
         .all()
    )

    spooled = SpooledTemporaryFile(max_size=100 * 1024 * 1024)  # 100MB antes de disco

    total_agregadas = 0
    total_fallidas = 0
    total_vistas = 0

    # Para evitar que ZIP sobreescriba archivos por nombre repetido
    used_paths = set()

    with zipfile.ZipFile(
        spooled,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6
    ) as zf:

        for a in asignaciones:
            evs_rel = getattr(a, "evidencias", None)
            if not evs_rel:
                continue

            for ev in evs_rel.all():
                total_vistas += 1

                imagen_field = getattr(ev, "imagen", None)
                if not imagen_field:
                    total_fallidas += 1
                    continue

                field_storage = getattr(imagen_field, "storage", None)
                storage_name = getattr(imagen_field, "name", "") or ""
                public_url = ""
                try:
                    public_url = imagen_field.url or ""
                except Exception:
                    public_url = ""

                # Título del requisito o "Extra"
                if getattr(s, "proyecto_especial", False) and not getattr(ev, "requisito_id", None):
                    req_title_raw = getattr(ev, "titulo_manual", "") or "Extra"
                else:
                    req = getattr(ev, "requisito", None)
                    req_title_raw = getattr(req, "titulo", "") or "Extra"

                data = _read_from_storage_or_url(field_storage, storage_name, public_url)
                if data is None:
                    total_fallidas += 1
                    continue

                ext = _guess_ext(storage_name or public_url, default=".jpg")
                file_title = _safe_component_preserve(req_title_raw, max_len=120)

                # --------- ✅ NOMBRE ÚNICO (evita overwrite en ZIP) ----------
                base_arc = f"{root_name}/{file_title}{ext}"
                arcname = base_arc

                if arcname in used_paths:
                    # Preferencia: agregar ev.id (siempre único) y si aún choca, contador
                    arcname = f"{root_name}/{file_title} ({ev.id}){ext}"
                    if arcname in used_paths:
                        n = 2
                        while True:
                            arcname_try = f"{root_name}/{file_title} ({ev.id})_{n}{ext}"
                            if arcname_try not in used_paths:
                                arcname = arcname_try
                                break
                            n += 1

                used_paths.add(arcname)
                # ------------------------------------------------------------

                try:
                    zf.writestr(arcname, data)
                    total_agregadas += 1
                except Exception as e:
                    total_fallidas += 1
                    logger.warning("ZIP fotos: fallo writestr '%s': %s", arcname, e)

    logger.info(
        "ZIP fotos sesion=%s -> vistas=%s agregadas=%s fallidas=%s",
        s.id, total_vistas, total_agregadas, total_fallidas
    )

    # ✅ En vez de tirar 404 feo, volvemos a la sesión con mensaje
    if total_agregadas == 0:
        messages.warning(request, "No photos available for this billing session.")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    spooled.seek(0)
    filename = f"{root_name}.zip"
    resp = FileResponse(
        spooled,
        as_attachment=True,
        filename=filename,
        content_type="application/zip"
    )
    resp["Cache-Control"] = "no-store"
    return resp