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

# ============================================================
# Límites de ZIP para Client Submissions / Smartsheet
# ============================================================

SMARTSHEET_MAX_ZIP_PART_BYTES = 29_000_000

SMARTSHEET_MAX_ZIP_PARTS = 10

ZIP_SPOOL_MEMORY_LIMIT = 16 * 1024 * 1024


def _zip_size_bytes(
    spooled_file,
) -> int:
    """
    Obtiene el tamaño real de un ZIP ya cerrado.

    Conserva el cursor en el inicio para que el caller
    pueda leerlo posteriormente.
    """

    spooled_file.seek(
        0,
        os.SEEK_END,
    )

    size = spooled_file.tell()

    spooled_file.seek(
        0,
    )

    return int(
        size,
    )


def _build_spooled_zip_from_entries(
    entries: list[tuple[str, bytes]],
):
    """
    Construye un ZIP temporal a partir de:

        [
            (
                arcname,
                data,
            ),
        ]

    Devuelve el SpooledTemporaryFile posicionado al inicio.
    """

    spooled = SpooledTemporaryFile(
        max_size=ZIP_SPOOL_MEMORY_LIMIT,
    )

    try:
        with zipfile.ZipFile(
            spooled,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zf:
            for (
                arcname,
                data,
            ) in entries:
                zf.writestr(
                    arcname,
                    data,
                )

        spooled.seek(
            0,
        )

        return spooled

    except Exception:
        try:
            spooled.close()

        except Exception:
            pass

        raise


def _collect_photo_entries_for_zip(
    sesion: SesionBilling,
) -> tuple[
    list[tuple[str, bytes]],
    dict,
]:
    """
    Recopila las fotografías de una SesionBilling utilizando
    exactamente la misma estructura oficial del ZIP.

    Devuelve:

        (
            entries,
            stats,
        )

    entries:

        [
            (
                "PROJECT_ID/Requirement.jpg",
                b"...",
            ),
        ]
    """

    root_name = _safe_component_preserve(
        sesion.proyecto_id or f"Billing_{sesion.id}",
        max_len=80,
    )

    asignaciones = (
        sesion.tecnicos_sesion.select_related(
            "tecnico",
        )
        .prefetch_related(
            "evidencias__requisito",
        )
        .all()
    )

    entries: list[tuple[str, bytes]] = []

    total_agregadas = 0
    total_fallidas = 0
    total_vistas = 0

    used_paths = set()

    for asignacion in asignaciones:
        evs_rel = getattr(
            asignacion,
            "evidencias",
            None,
        )

        if not evs_rel:
            continue

        for ev in evs_rel.all():
            total_vistas += 1

            imagen_field = getattr(
                ev,
                "imagen",
                None,
            )

            if not imagen_field:
                total_fallidas += 1

                continue

            field_storage = getattr(
                imagen_field,
                "storage",
                None,
            )

            storage_name = (
                getattr(
                    imagen_field,
                    "name",
                    "",
                )
                or ""
            )

            public_url = ""

            try:
                public_url = imagen_field.url or ""

            except Exception:
                public_url = ""

            # =================================================
            # Título de la evidencia
            # =================================================

            if getattr(
                sesion,
                "proyecto_especial",
                False,
            ) and not getattr(
                ev,
                "requisito_id",
                None,
            ):
                req_title_raw = (
                    getattr(
                        ev,
                        "titulo_manual",
                        "",
                    )
                    or "Extra"
                )

            else:
                req = getattr(
                    ev,
                    "requisito",
                    None,
                )

                req_title_raw = (
                    getattr(
                        req,
                        "titulo",
                        "",
                    )
                    or "Extra"
                )

            # =================================================
            # Leer archivo desde Wasabi/storage o URL
            # =================================================

            data = _read_from_storage_or_url(
                field_storage,
                storage_name,
                public_url,
            )

            if data is None:
                total_fallidas += 1

                continue

            extension = _guess_ext(
                storage_name or public_url,
                default=".jpg",
            )

            file_title = _safe_component_preserve(
                req_title_raw,
                max_len=120,
            )

            # =================================================
            # Nombre interno inicial
            # =================================================

            arcname = f"{root_name}/" f"{file_title}" f"{extension}"

            # =================================================
            # Evitar nombres duplicados
            # =================================================

            if arcname in used_paths:
                arcname = f"{root_name}/" f"{file_title} " f"({ev.id})" f"{extension}"

                if arcname in used_paths:
                    counter = 2

                    while True:
                        arcname_try = (
                            f"{root_name}/"
                            f"{file_title} "
                            f"({ev.id})_"
                            f"{counter}"
                            f"{extension}"
                        )

                        if arcname_try not in used_paths:
                            arcname = arcname_try

                            break

                        counter += 1

            used_paths.add(
                arcname,
            )

            entries.append(
                (
                    arcname,
                    data,
                )
            )

            total_agregadas += 1

    stats = {
        "total_vistas": total_vistas,
        "total_agregadas": total_agregadas,
        "total_fallidas": total_fallidas,
    }

    return (
        entries,
        stats,
    )


def generar_fotos_zip_sesion(
    sesion: SesionBilling,
):
    """
    Genera el ZIP oficial de fotografías de una SesionBilling.

    Devuelve:
        (
            spooled_file,
            filename,
            stats
        )

    stats:
        {
            "total_vistas": int,
            "total_agregadas": int,
            "total_fallidas": int,
        }

    El caller es responsable de cerrar el archivo.
    """

    root_name = _safe_component_preserve(
        sesion.proyecto_id or f"Billing_{sesion.id}",
        max_len=80,
    )

    asignaciones = (
        sesion.tecnicos_sesion.select_related("tecnico")
        .prefetch_related("evidencias__requisito")
        .all()
    )

    spooled = SpooledTemporaryFile(
        max_size=100 * 1024 * 1024,
    )

    total_agregadas = 0
    total_fallidas = 0
    total_vistas = 0

    used_paths = set()

    try:
        with zipfile.ZipFile(
            spooled,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zf:

            for asignacion in asignaciones:
                evs_rel = getattr(
                    asignacion,
                    "evidencias",
                    None,
                )

                if not evs_rel:
                    continue

                for ev in evs_rel.all():
                    total_vistas += 1

                    imagen_field = getattr(
                        ev,
                        "imagen",
                        None,
                    )

                    if not imagen_field:
                        total_fallidas += 1
                        continue

                    field_storage = getattr(
                        imagen_field,
                        "storage",
                        None,
                    )

                    storage_name = (
                        getattr(
                            imagen_field,
                            "name",
                            "",
                        )
                        or ""
                    )

                    public_url = ""

                    try:
                        public_url = imagen_field.url or ""
                    except Exception:
                        public_url = ""

                    # ----------------------------------------
                    # Título de la evidencia
                    # ----------------------------------------

                    if getattr(
                        sesion,
                        "proyecto_especial",
                        False,
                    ) and not getattr(
                        ev,
                        "requisito_id",
                        None,
                    ):
                        req_title_raw = (
                            getattr(
                                ev,
                                "titulo_manual",
                                "",
                            )
                            or "Extra"
                        )

                    else:
                        req = getattr(
                            ev,
                            "requisito",
                            None,
                        )

                        req_title_raw = (
                            getattr(
                                req,
                                "titulo",
                                "",
                            )
                            or "Extra"
                        )

                    # ----------------------------------------
                    # Leer archivo
                    # ----------------------------------------

                    data = _read_from_storage_or_url(
                        field_storage,
                        storage_name,
                        public_url,
                    )

                    if data is None:
                        total_fallidas += 1
                        continue

                    ext = _guess_ext(
                        storage_name or public_url,
                        default=".jpg",
                    )

                    file_title = _safe_component_preserve(
                        req_title_raw,
                        max_len=120,
                    )

                    # ----------------------------------------
                    # Evitar colisiones
                    # ----------------------------------------

                    arcname = f"{root_name}/" f"{file_title}" f"{ext}"

                    if arcname in used_paths:
                        arcname = f"{root_name}/" f"{file_title} " f"({ev.id})" f"{ext}"

                        if arcname in used_paths:
                            n = 2

                            while True:
                                arcname_try = (
                                    f"{root_name}/"
                                    f"{file_title} "
                                    f"({ev.id})_"
                                    f"{n}"
                                    f"{ext}"
                                )

                                if arcname_try not in used_paths:
                                    arcname = arcname_try
                                    break

                                n += 1

                    used_paths.add(arcname)

                    try:
                        zf.writestr(
                            arcname,
                            data,
                        )

                        total_agregadas += 1

                    except Exception as exc:
                        total_fallidas += 1

                        logger.warning(
                            "ZIP fotos: fallo writestr '%s': %s",
                            arcname,
                            exc,
                        )

        logger.info(
            ("ZIP fotos sesion=%s -> " "vistas=%s agregadas=%s fallidas=%s"),
            sesion.id,
            total_vistas,
            total_agregadas,
            total_fallidas,
        )

        if total_agregadas == 0:
            spooled.close()

            raise RuntimeError("No photos available for this billing session.")

        spooled.seek(0)

        filename = f"{root_name}.zip"

        stats = {
            "total_vistas": total_vistas,
            "total_agregadas": total_agregadas,
            "total_fallidas": total_fallidas,
        }

        return (
            spooled,
            filename,
            stats,
        )

    except Exception:
        try:
            spooled.close()
        except Exception:
            pass

        raise


def generar_fotos_zip_partes_smartsheet(
    sesion: SesionBilling,
    *,
    max_part_bytes: int = SMARTSHEET_MAX_ZIP_PART_BYTES,
    max_parts: int = SMARTSHEET_MAX_ZIP_PARTS,
):
    """
    Genera ZIP divididos exclusivamente para Client Submissions.

    Esta función NO modifica la descarga normal de Operations.

    Reglas:

    - Cada ZIP puede pesar como máximo 29.000.000 bytes.
    - Puede generar como máximo 10 ZIP.
    - Una fotografía nunca se divide entre dos ZIP.
    - Conserva los nombres y carpetas internas del ZIP oficial.
    - Si una sola fotografía produce un ZIP mayor al límite,
      genera un error claro.

    Devuelve:

        (
            parts,
            stats,
        )

    Ejemplo de parts:

        [
            {
                "file": SpooledTemporaryFile,
                "filename": "0913RA_04_5005-009-7_part-01.zip",
                "size_bytes": 28500000,
                "photo_count": 14,
                "part_number": 1,
                "total_parts": 2,
            },
            {
                "file": SpooledTemporaryFile,
                "filename": "0913RA_04_5005-009-7_part-02.zip",
                "size_bytes": 9000000,
                "photo_count": 5,
                "part_number": 2,
                "total_parts": 2,
            },
        ]

    El caller es responsable de cerrar los archivos.
    """

    if max_part_bytes <= 0:
        raise ValueError("max_part_bytes must be greater than zero.")

    if max_parts <= 0:
        raise ValueError("max_parts must be greater than zero.")

    entries, stats = _collect_photo_entries_for_zip(
        sesion,
    )

    if not entries:
        raise RuntimeError("No photos available for this billing session.")

    root_name = _safe_component_preserve(
        sesion.proyecto_id or f"Billing_{sesion.id}",
        max_len=80,
    )

    completed_parts = []

    current_entries = []

    current_zip = None

    try:
        for arcname, data in entries:
            candidate_entries = [
                *current_entries,
                (
                    arcname,
                    data,
                ),
            ]

            candidate_zip = _build_spooled_zip_from_entries(
                candidate_entries,
            )

            candidate_size = _zip_size_bytes(
                candidate_zip,
            )

            # ================================================
            # Todavía cabe dentro de la parte actual
            # ================================================

            if candidate_size <= max_part_bytes:
                if current_zip is not None:
                    try:
                        current_zip.close()
                    except Exception:
                        pass

                current_entries = candidate_entries

                current_zip = candidate_zip

                continue

            # ================================================
            # Una sola fotografía ya supera el límite
            # ================================================

            if not current_entries:
                try:
                    candidate_zip.close()
                except Exception:
                    pass

                raise RuntimeError(
                    (
                        "A single evidence file exceeds the "
                        "maximum Smartsheet ZIP size. "
                        f"Evidence: {arcname}. "
                        f"ZIP size: {candidate_size} bytes. "
                        f"Maximum: {max_part_bytes} bytes."
                    )
                )

            # ================================================
            # La foto nueva no cabe en la parte actual
            # ================================================

            try:
                candidate_zip.close()
            except Exception:
                pass

            completed_parts.append(
                {
                    "file": current_zip,
                    "size_bytes": _zip_size_bytes(
                        current_zip,
                    ),
                    "photo_count": len(
                        current_entries,
                    ),
                }
            )

            current_zip = None

            current_entries = []

            if len(completed_parts) >= max_parts:
                raise RuntimeError(
                    (
                        "The project requires more than "
                        f"{max_parts} ZIP files for Smartsheet. "
                        "Reduce the size or number of photos."
                    )
                )

            # ================================================
            # Iniciar parte nueva con la foto pendiente
            # ================================================

            current_entries = [
                (
                    arcname,
                    data,
                ),
            ]

            current_zip = _build_spooled_zip_from_entries(
                current_entries,
            )

            current_size = _zip_size_bytes(
                current_zip,
            )

            if current_size > max_part_bytes:
                raise RuntimeError(
                    (
                        "A single evidence file exceeds the "
                        "maximum Smartsheet ZIP size. "
                        f"Evidence: {arcname}. "
                        f"ZIP size: {current_size} bytes. "
                        f"Maximum: {max_part_bytes} bytes."
                    )
                )

        # ================================================
        # Guardar última parte
        # ================================================

        if current_zip is not None and current_entries:
            completed_parts.append(
                {
                    "file": current_zip,
                    "size_bytes": _zip_size_bytes(
                        current_zip,
                    ),
                    "photo_count": len(
                        current_entries,
                    ),
                }
            )

            current_zip = None

            current_entries = []

        if not completed_parts:
            raise RuntimeError("No Smartsheet ZIP parts could be generated.")

        if len(completed_parts) > max_parts:
            raise RuntimeError(
                (
                    f"The project generated "
                    f"{len(completed_parts)} ZIP files, "
                    f"but only {max_parts} are allowed."
                )
            )

        total_parts = len(
            completed_parts,
        )

        for index, part in enumerate(
            completed_parts,
            start=1,
        ):
            if total_parts == 1:
                filename = f"{root_name}.zip"

            else:
                filename = f"{root_name}_part-" f"{index:02d}.zip"

            part["filename"] = filename

            part["part_number"] = index

            part["total_parts"] = total_parts

            part["file"].seek(
                0,
            )

        result_stats = {
            **stats,
            "total_parts": total_parts,
            "max_part_bytes": max_part_bytes,
            "max_parts": max_parts,
            "parts": [
                {
                    "filename": part["filename"],
                    "size_bytes": part["size_bytes"],
                    "photo_count": part["photo_count"],
                    "part_number": part["part_number"],
                }
                for part in completed_parts
            ],
        }

        logger.info(
            (
                "Smartsheet ZIP parts sesion=%s -> "
                "parts=%s vistas=%s agregadas=%s fallidas=%s"
            ),
            sesion.id,
            total_parts,
            result_stats.get(
                "total_vistas",
                0,
            ),
            result_stats.get(
                "total_agregadas",
                0,
            ),
            result_stats.get(
                "total_fallidas",
                0,
            ),
        )

        return (
            completed_parts,
            result_stats,
        )

    except Exception:
        if current_zip is not None:
            try:
                current_zip.close()
            except Exception:
                pass

        for part in completed_parts:
            part_file = part.get(
                "file",
            )

            if part_file is not None:
                try:
                    part_file.close()
                except Exception:
                    pass

        raise



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
def descargar_fotos_zip(
    request,
    sesion_id: int,
):
    """
    Descarga el ZIP oficial con todas las fotografías
    de una SesionBilling.
    """

    from django.contrib import messages
    from django.shortcuts import redirect

    s = get_object_or_404(
        SesionBilling,
        pk=sesion_id,
    )

    try:
        spooled, filename, _stats = generar_fotos_zip_sesion(
            s,
        )

    except RuntimeError:
        messages.warning(
            request,
            "No photos available for this billing session.",
        )

        return redirect(
            "operaciones:revisar_sesion",
            sesion_id=s.id,
        )

    resp = FileResponse(
        spooled,
        as_attachment=True,
        filename=filename,
        content_type="application/zip",
    )

    resp["Cache-Control"] = "no-store"

    return resp
