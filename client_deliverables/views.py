import hashlib
import html
import io
import mimetypes
import os
import re
import tempfile
import threading
import urllib.request
import zipfile
from urllib.parse import quote, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import DeliveryPackageForm
from .models import (ClientProjectAssignment, DeliveryAccessLog,
                     DeliveryPackage, DeliveryPackageFile, DeliveryZipJob)
from .permissions import (can_manage_deliverables, can_publish_deliverables,
                          can_revoke_deliverables, can_view_client_portal,
                          filter_delivery_packages_by_user,
                          user_can_access_delivery_package)
from .services import (build_available_deliverables_for_sessions,
                       discover_project_deliverables)
from .utils import log_delivery_access, package_or_404_if_unavailable

# ============================================================
# Helpers
# ============================================================


class _ZipBuildRequestContext:
    def __init__(self, host, port):
        self._host = host
        self._port = port

    def get_host(self):
        return self._host

    def get_port(self):
        return self._port


def _safe_project_folder_name(project_id):
    """
    Genera una carpeta corta y compatible con Windows para cada Project ID.

    Se limita la longitud para evitar que la ruta final del archivo supere
    el límite admitido por el extractor integrado de Windows.
    """
    raw = str(project_id or "").strip()

    slug = slugify(raw).replace("-", "_") or "no_project_id"
    slug = slug[:60].rstrip("._-")

    if not slug:
        slug = "no_project_id"

    return f"Project_{slug}"


def _temporary_storage_download_url(
    field_file,
    filename,
    expiration_seconds=900,
):
    """
    Genera una URL temporal para descargar directamente desde Wasabi/S3.

    El archivo no pasa por Django ni por Render.

    La URL:
    - Expira después de 15 minutos por defecto.
    - Fuerza la descarga con el nombre indicado.
    - Mantiene el bucket privado.
    """
    if not field_file:
        return ""

    file_name = str(getattr(field_file, "name", "") or "").strip()

    if not file_name:
        return ""

    storage = field_file.storage
    safe_filename = _safe_zip_filename(
        filename,
        fallback="Hyperlink_Deliverables",
    )

    content_disposition = (
        "attachment; "
        f'filename="{safe_filename}"; '
        f"filename*=UTF-8''{quote(safe_filename)}"
    )

    # django-storages con S3Storage / S3Boto3Storage.
    try:
        connection = getattr(storage, "connection", None)

        if connection is not None:
            client = connection.meta.client

            bucket_name = getattr(storage, "bucket_name", "") or getattr(
                settings, "AWS_STORAGE_BUCKET_NAME", ""
            )

            if bucket_name:
                return client.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={
                        "Bucket": bucket_name,
                        "Key": file_name,
                        "ResponseContentDisposition": content_disposition,
                        "ResponseContentType": "application/zip",
                    },
                    ExpiresIn=int(expiration_seconds),
                )
    except Exception:
        pass

    # Compatibilidad con backends que permiten parámetros en storage.url().
    try:
        return storage.url(
            file_name,
            parameters={
                "ResponseContentDisposition": content_disposition,
                "ResponseContentType": "application/zip",
            },
            expire=int(expiration_seconds),
        )
    except TypeError:
        pass
    except Exception:
        return ""

    return ""


def _delivery_package_content_signature(package):
    """
    Genera una firma estable del contenido activo de un DeliveryPackage.

    Permite reutilizar un ZIP existente solamente cuando los entregables
    activos continúan siendo exactamente los mismos.

    La firma cambia cuando:
    - Se agrega o elimina un DeliveryPackageFile.
    - Cambia el Project ID.
    - Cambia el tipo de archivo.
    - Cambia el nombre visible.
    - Cambia la URL de origen.
    - Cambia el source_key.
    - Cambia el archivo físico asociado.
    - Cambia el orden.
    - Cambia el estado activo.
    """
    digest = hashlib.sha256()

    files = package.files.filter(is_active=True).order_by(
        "project_id",
        "order",
        "id",
    )

    for file_obj in files:
        stored_file_name = ""

        if file_obj.file and getattr(file_obj.file, "name", ""):
            stored_file_name = str(file_obj.file.name)

        parts = [
            str(file_obj.id),
            str(file_obj.project_id or ""),
            str(file_obj.file_type or ""),
            str(file_obj.display_name or ""),
            str(file_obj.source_url or ""),
            str(file_obj.source_key or ""),
            stored_file_name,
            str(file_obj.size_bytes or ""),
            str(file_obj.order or 0),
            str(bool(file_obj.is_active)),
            str(file_obj.created_at.isoformat() if file_obj.created_at else ""),
        ]

        digest.update(
            "\x1f".join(parts).encode(
                "utf-8",
                errors="replace",
            )
        )
        digest.update(b"\x1e")

    return digest.hexdigest()


def _clean_filename_piece(value, fallback="deliverable"):
    """
    Limpia y limita un nombre de archivo manteniendo su extensión.

    El nombre base se limita a 80 caracteres para mantener cortas las rutas
    internas del ZIP y garantizar compatibilidad con Windows.
    """
    value = str(value or "").strip()
    value = html.unescape(value)

    value = value.replace("\\", "_").replace("/", "_")
    value = value.replace(":", "_").replace("*", "_")
    value = value.replace("?", "_").replace('"', "_")
    value = value.replace("<", "_").replace(">", "_")
    value = value.replace("|", "_")

    base, ext = os.path.splitext(value)

    base = slugify(base).replace("-", "_") or fallback
    base = base[:80].rstrip("._-")

    if not base:
        base = fallback

    ext = (ext or "").lower()

    # Evita extensiones anormalmente largas o dañadas.
    if len(ext) > 12:
        ext = ""

    return base, ext


def _extension_from_content_type(content_type):
    ct = (content_type or "").lower()

    if "spreadsheetml.sheet" in ct:
        return ".xlsx"
    if "application/vnd.ms-excel" in ct:
        return ".xls"
    if "text/csv" in ct:
        return ".csv"
    if "application/pdf" in ct:
        return ".pdf"
    if "wordprocessingml.document" in ct:
        return ".docx"
    if "application/msword" in ct:
        return ".doc"
    if "application/zip" in ct or "x-zip-compressed" in ct:
        return ".zip"
    if "image/jpeg" in ct:
        return ".jpg"
    if "image/png" in ct:
        return ".png"
    if "image/webp" in ct:
        return ".webp"

    return ""


def _extension_from_file_type(file_obj):
    file_type = str(getattr(file_obj, "file_type", "") or "").strip()

    if file_type == DeliveryPackageFile.FILE_CLIENT_REPORT:
        return ".xlsx"
    if file_type == DeliveryPackageFile.FILE_OPERATIONAL_REPORT:
        return ".xlsx"
    if file_type == DeliveryPackageFile.FILE_LIGHT_LEVELS:
        return ".xlsx"
    if file_type == DeliveryPackageFile.FILE_PHOTO_REPORT:
        return ".pdf"
    if file_type == DeliveryPackageFile.FILE_PHOTOS_ZIP:
        return ".zip"

    return ""


def _filename_from_content_disposition(content_disposition):
    header = str(content_disposition or "")

    if not header:
        return ""

    match_star = re.search(
        r"filename\*\s*=\s*UTF-8''([^;]+)",
        header,
        flags=re.IGNORECASE,
    )

    if match_star:
        try:
            from urllib.parse import unquote

            return unquote(match_star.group(1).strip().strip('"'))
        except Exception:
            return match_star.group(1).strip().strip('"')

    match = re.search(
        r'filename\s*=\s*"([^"]+)"',
        header,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    match_plain = re.search(
        r"filename\s*=\s*([^;]+)",
        header,
        flags=re.IGNORECASE,
    )

    if match_plain:
        return match_plain.group(1).strip().strip('"')

    return ""


def _final_download_filename(file_obj, response_filename="", content_type=""):
    candidates = []

    if response_filename:
        candidates.append(response_filename)

    if file_obj.file and getattr(file_obj.file, "name", ""):
        candidates.append(os.path.basename(file_obj.file.name))

    candidates.append(file_obj.safe_filename())
    candidates.append(file_obj.display_name)

    forced_ext = _extension_from_content_type(
        content_type
    ) or _extension_from_file_type(file_obj)

    for candidate in candidates:
        base, ext = _clean_filename_piece(candidate)

        if ext:
            return f"{base}{ext}"

        if forced_ext:
            return f"{base}{forced_ext}"

    base, _ = _clean_filename_piece(file_obj.display_name or "deliverable")

    if forced_ext:
        return f"{base}{forced_ext}"

    return f"{base}.bin"


def _safe_zip_filename(name, fallback="deliverable"):
    base, ext = _clean_filename_piece(name, fallback=fallback)

    if not ext:
        ext = ".bin"

    return f"{base}{ext}"


def _unique_name(existing_names, filename):
    filename = _safe_zip_filename(filename)
    base, ext = os.path.splitext(filename)

    candidate = filename
    counter = 2

    while candidate in existing_names:
        candidate = f"{base}_{counter}{ext}"
        counter += 1

    existing_names.add(candidate)
    return candidate


def _delivery_zip_filename(package, job):
    """
    Genera un nombre corto, único y descriptivo para el ZIP completo.

    No utiliza package.name porque puede contener muchos Project IDs y producir
    nombres incompatibles con la extracción integrada de Windows.
    """
    project_ids = []

    for project_id in (
        package.files.filter(is_active=True)
        .exclude(project_id="")
        .values_list("project_id", flat=True)
        .distinct()
    ):
        clean_project_id = str(project_id or "").strip()

        if clean_project_id and clean_project_id not in project_ids:
            project_ids.append(clean_project_id)

    project_count = len(project_ids)
    unique_suffix = str(job.id).replace("-", "")[:8]

    if project_count == 1:
        project_slug = (
            slugify(project_ids[0]).replace("-", "_")[:50].rstrip("._-") or "project"
        )

        return f"Hyperlink_Deliverables_{project_slug}_{unique_suffix}.zip"

    if project_count > 1:
        return (
            f"Hyperlink_Deliverables_"
            f"{project_count}_Projects_"
            f"{unique_suffix}.zip"
        )

    return f"Hyperlink_Deliverables_{unique_suffix}.zip"


def _response_to_bytes(response):
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content)

    return response.content


def _internal_source_path(request_context, source_url):
    source_url = html.unescape(str(source_url or "").strip())

    if not source_url:
        return ""

    parsed = urlparse(source_url)

    if parsed.scheme and parsed.netloc:
        current_host = request_context.get_host()

        if parsed.netloc != current_host:
            return ""

        path = parsed.path or "/"

        if parsed.query:
            path = f"{path}?{parsed.query}"

        return path

    if source_url.startswith("/"):
        return source_url

    return "/" + source_url


def _fetch_delivery_file_bytes(request_context, package, file_obj):
    fallback_filename = _final_download_filename(file_obj)

    if file_obj.file:
        try:
            with file_obj.file.open("rb") as fh:
                content = fh.read()

            content_type = (
                mimetypes.guess_type(file_obj.file.name)[0]
                or mimetypes.guess_type(fallback_filename)[0]
                or "application/octet-stream"
            )

            filename = _final_download_filename(
                file_obj,
                response_filename=os.path.basename(file_obj.file.name),
                content_type=content_type,
            )

            return {
                "ok": True,
                "content": content,
                "content_type": content_type,
                "filename": filename,
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Could not read stored file: {e}",
            }

    source_url = html.unescape(str(file_obj.source_url or "").strip())

    if not source_url:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Missing source URL.",
        }

    parsed = urlparse(source_url)

    if (
        parsed.scheme in ("http", "https")
        and parsed.netloc != request_context.get_host()
    ):
        try:
            with urllib.request.urlopen(source_url, timeout=120) as response:
                content = response.read()
                content_type = response.headers.get(
                    "Content-Type",
                    "application/octet-stream",
                )
                cd = response.headers.get("Content-Disposition", "")

            response_filename = _filename_from_content_disposition(cd)

            filename = _final_download_filename(
                file_obj,
                response_filename=response_filename,
                content_type=content_type,
            )

            return {
                "ok": True,
                "content": content,
                "content_type": content_type or "application/octet-stream",
                "filename": filename,
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Could not fetch external source: {e}",
            }

    internal_path = _internal_source_path(request_context, source_url)

    if not internal_path:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Invalid internal source URL.",
        }

    if internal_path.startswith("/client-deliverables/"):
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Invalid recursive delivery source URL.",
        }

    try:
        client = DjangoTestClient(
            HTTP_HOST=request_context.get_host(),
            SERVER_NAME=request_context.get_host().split(":")[0],
            SERVER_PORT=request_context.get_port() or "80",
        )

        if package.created_by_id and package.created_by:
            client.force_login(package.created_by)

        response = client.get(
            internal_path,
            follow=True,
            HTTP_HOST=request_context.get_host(),
        )

        if response.status_code != 200:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Internal source returned status {response.status_code}.",
            }

        content = _response_to_bytes(response)

        content_type = response.get("Content-Type", "") or "application/octet-stream"
        cd = response.get("Content-Disposition", "")

        if "text/html" in content_type.lower():
            return {
                "ok": False,
                "content": b"",
                "content_type": content_type,
                "filename": fallback_filename,
                "error": "Internal source returned HTML instead of a downloadable file.",
            }

        response_filename = _filename_from_content_disposition(cd)

        filename = _final_download_filename(
            file_obj,
            response_filename=response_filename,
            content_type=content_type,
        )

        return {
            "ok": True,
            "content": content,
            "content_type": content_type,
            "filename": filename,
            "error": "",
        }

    except Exception as e:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": f"Could not generate internal source: {e}",
        }


def _build_delivery_zip_job(job_id, host, port):
    """
    Construye el ZIP general de un DeliveryPackage.

    Características:
    - Nombre exterior corto y compatible con Windows.
    - Carpetas por Project ID.
    - Nombres internos limitados y únicos.
    - Progreso real guardado después de cada archivo procesado.
    - Firma del contenido para reutilización.
    - Expiración temporal del ZIP.
    """
    job = None
    temp_path = None

    try:
        job = DeliveryZipJob.objects.select_related(
            "package",
            "package__created_by",
        ).get(pk=job_id)

        package = job.package

        files = list(
            package.files.filter(is_active=True).order_by(
                "project_id",
                "order",
                "id",
            )
        )

        current_signature = _delivery_package_content_signature(package)

        job.status = DeliveryZipJob.STATUS_PROCESSING
        job.started_at = timezone.now()
        job.finished_at = None
        job.error_message = ""
        job.errors = []
        job.total_files = len(files)
        job.files_added = 0
        job.files_failed = 0
        job.content_signature = current_signature
        job.expires_at = None

        job.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "error_message",
                "errors",
                "total_files",
                "files_added",
                "files_failed",
                "content_signature",
                "expires_at",
            ]
        )

        if not files:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.error_message = "This package does not have active deliverables."
            job.finished_at = timezone.now()

            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "finished_at",
                ]
            )
            return

        request_context = _ZipBuildRequestContext(
            host=host,
            port=port,
        )

        used_names_by_folder = {}
        total_files_added = 0
        total_files_failed = 0
        errors = []

        final_filename = _delivery_zip_filename(package, job)

        fd, temp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as zip_file:
            for file_obj in files:
                project_id = str(file_obj.project_id or "NO_PROJECT_ID").strip()

                folder_name = _safe_project_folder_name(project_id)

                if folder_name not in used_names_by_folder:
                    used_names_by_folder[folder_name] = set()

                result = _fetch_delivery_file_bytes(
                    request_context,
                    package,
                    file_obj,
                )

                if not result["ok"]:
                    total_files_failed += 1

                    errors.append(
                        {
                            "project_id": project_id,
                            "display_name": file_obj.display_name,
                            "source_url": file_obj.source_url,
                            "error": result["error"],
                        }
                    )

                    job.files_added = total_files_added
                    job.files_failed = total_files_failed
                    job.errors = errors

                    job.save(
                        update_fields=[
                            "files_added",
                            "files_failed",
                            "errors",
                        ]
                    )
                    continue

                filename = _unique_name(
                    used_names_by_folder[folder_name],
                    result["filename"],
                )

                archive_path = f"{folder_name}/{filename}"

                zip_file.writestr(
                    archive_path,
                    result["content"],
                )

                total_files_added += 1

                job.files_added = total_files_added
                job.files_failed = total_files_failed

                job.save(
                    update_fields=[
                        "files_added",
                        "files_failed",
                    ]
                )

            manifest_lines = [
                "Hyperlink Networks - Delivery Package",
                "",
                f"Package: {package.name}",
                f"Generated at: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                f"Files included: {total_files_added}",
                f"Files with errors: {total_files_failed}",
                "",
            ]

            if errors:
                manifest_lines.append("Errors:")

                for error in errors:
                    manifest_lines.extend(
                        [
                            "",
                            f"Project ID: {error['project_id']}",
                            f"Deliverable: {error['display_name']}",
                            f"Source: {error['source_url']}",
                            f"Error: {error['error']}",
                        ]
                    )

            zip_file.writestr(
                "README.txt",
                "\n".join(manifest_lines),
            )

        if total_files_added == 0:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.files_added = 0
            job.files_failed = total_files_failed
            job.errors = errors
            job.error_message = (
                "The ZIP could not be generated because none of the "
                "deliverables could be downloaded."
            )
            job.finished_at = timezone.now()

            job.save(
                update_fields=[
                    "status",
                    "files_added",
                    "files_failed",
                    "errors",
                    "error_message",
                    "finished_at",
                ]
            )
            return

        # Verificamos que el package no haya cambiado mientras se construía.
        final_signature = _delivery_package_content_signature(package)

        if final_signature != current_signature:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.error_message = (
                "The package contents changed while the ZIP was being built. "
                "Please start the download again."
            )
            job.finished_at = timezone.now()

            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "finished_at",
                ]
            )
            return

        with open(temp_path, "rb") as fh:
            job.zip_file.save(
                final_filename,
                File(fh),
                save=False,
            )

        job.status = DeliveryZipJob.STATUS_READY
        job.filename = final_filename
        job.files_added = total_files_added
        job.files_failed = total_files_failed
        job.errors = errors
        job.content_signature = final_signature
        job.finished_at = timezone.now()
        job.error_message = ""
        job.set_default_expiration()

        job.save(
            update_fields=[
                "status",
                "zip_file",
                "filename",
                "files_added",
                "files_failed",
                "errors",
                "content_signature",
                "finished_at",
                "error_message",
                "expires_at",
            ]
        )

    except Exception as e:
        if job:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.error_message = str(e)
            job.finished_at = timezone.now()

            job.save(
                update_fields=[
                    "status",
                    "error_message",
                    "finished_at",
                ]
            )

    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def _start_zip_job_background(job, request):
    host = request.get_host()
    port = request.get_port() or "80"

    thread = threading.Thread(
        target=_build_delivery_zip_job,
        args=(str(job.id), host, port),
        daemon=True,
    )
    thread.start()


def _absolute_url(request, path):
    return request.build_absolute_uri(path)


def _require_manager(user):
    if not can_manage_deliverables(user):
        raise PermissionDenied("You do not have permission to manage deliverables.")


def _require_publisher(user):
    if not can_publish_deliverables(user):
        raise PermissionDenied("You do not have permission to publish deliverables.")


def _require_revoker(user):
    if not can_revoke_deliverables(user):
        raise PermissionDenied("You do not have permission to revoke deliverables.")


def _package_public_url(request, package):
    return _absolute_url(
        request,
        reverse("client_deliverables:public_package_detail", args=[package.token]),
    )


def _package_project_summary(package):
    grouped = {}

    files = package.files.filter(is_active=True).order_by("project_id", "order", "id")

    for f in files:
        project_id = (f.project_id or "NO_PROJECT_ID").strip()
        grouped.setdefault(project_id, []).append(f)

    return grouped


def _build_outlook_subject(package):
    project_ids = package.project_ids()

    if len(project_ids) == 1:
        return f"Project Deliverables Available - Project {project_ids[0]}"

    if len(project_ids) > 1:
        joined = " / ".join(project_ids[:4])

        if len(project_ids) > 4:
            joined += f" + {len(project_ids) - 4} more"

        return f"Project Deliverables Available - Projects {joined}"

    return f"Project Deliverables Available - {package.name}"


def _build_outlook_body(request, package):
    public_url = _package_public_url(request, package)
    grouped = _package_project_summary(package)

    lines = []
    lines.append("Hello,")
    lines.append("")
    lines.append("We are sharing the deliverables for the following project(s):")
    lines.append("")

    if grouped:
        for project_id, files in grouped.items():
            lines.append(f"Project ID: {project_id}")
            lines.append("Included deliverables:")

            for f in files:
                lines.append(f"- {f.display_name or f.get_file_type_display()}")

            lines.append("")
    else:
        lines.append(f"Package: {package.name}")
        lines.append("Included deliverables will be available through the link below.")
        lines.append("")

    lines.append("Access link:")
    lines.append(public_url)
    lines.append("")

    if package.expires_at:
        expires_txt = timezone.localtime(package.expires_at).strftime(
            "%m/%d/%Y %I:%M %p"
        )
        lines.append(f"This link will expire on {expires_txt}.")
    else:
        lines.append("This link does not have an expiration date.")

    lines.append("")

    if package.requires_access_key:
        lines.append(
            "For security reasons, the access key will be provided separately."
        )
        lines.append("")

    lines.append("Regards,")
    lines.append("Hyperlink Networks")

    return "\n".join(lines)


def _is_package_unlocked(request, package):
    if not package.requires_access_key:
        return True

    return bool(request.session.get(f"delivery_package_unlocked_{package.id}") is True)


def _mark_package_unlocked(request, package):
    request.session[f"delivery_package_unlocked_{package.id}"] = True
    request.session.modified = True

def _render_public_package_unavailable(request, package, reason="unavailable"):
    title = "Delivery link unavailable"
    message = "This delivery link is no longer available."
    icon = "⚠️"

    if reason == "revoked":
        title = "Delivery link revoked"
        message = (
            "This secure delivery link has been revoked and is no longer available. "
            "Please contact Hyperlink Networks if you need a new link."
        )
        icon = "🔒"

    elif reason == "expired":
        title = "Delivery link expired"
        message = (
            "This secure delivery link has expired. "
            "Please contact Hyperlink Networks if you need a new link."
        )
        icon = "⏰"

    elif reason == "no_files":
        title = "No files available"
        message = (
            "This delivery package does not currently have available files."
        )
        icon = "📁"

    return render(
        request,
        "client_deliverables/public_package_unavailable.html",
        {
            "package": package,
            "title": title,
            "message": message,
            "reason": reason,
            "icon": icon,
        },
        status=410,
    )


def _public_package_unavailable_response(request, package):
    if package.status == DeliveryPackage.STATUS_REVOKED:
        return _render_public_package_unavailable(
            request,
            package,
            reason="revoked",
        )

    if package.is_expired():
        return _render_public_package_unavailable(
            request,
            package,
            reason="expired",
        )

    return None


def _client_has_project_assignment(user, project_id):
    project_id = str(project_id or "").strip()

    if not project_id:
        return False

    return ClientProjectAssignment.objects.filter(
        user=user,
        project_id=project_id,
        is_active=True,
    ).exists()


def _detail_url_with_project(package, project_id, anchor="load-deliverables-section"):
    base = reverse("client_deliverables:admin_package_detail", args=[package.id])
    project_id = str(project_id or "").strip()

    if project_id:
        url = f"{base}?project_id={quote(project_id)}"
    else:
        url = base

    if anchor:
        url = f"{url}#{anchor}"

    return url


# ============================================================
# Admin / Internal portal
# ============================================================


@login_required
def admin_package_list(request):
    _require_manager(request.user)

    packages_qs = (
        DeliveryPackage.objects.select_related("created_by", "published_by")
        .prefetch_related("files")
        .order_by("-created_at")
    )

    packages_qs = filter_delivery_packages_by_user(packages_qs, request.user)

    # Seguridad final:
    # El filtro anterior ayuda a reducir resultados en DB, pero aquí validamos
    # estrictamente package por package para evitar mostrar un package donde
    # el usuario tenga acceso solo a 1 proyecto, pero no a todos.
    safe_packages = []

    for package in packages_qs:
        if user_can_access_delivery_package(request.user, package):
            safe_packages.append(package)

    cantidad = request.GET.get("cantidad", "10")

    if cantidad not in ["5", "10", "20", "50", "100"]:
        cantidad = "10"

    paginator = Paginator(safe_packages, int(cantidad))
    page_number = request.GET.get("page")
    pagina = paginator.get_page(page_number)

    for package in pagina.object_list:
        package.public_url_for_copy = _package_public_url(request, package)
        package.visible_access_key = getattr(package, "access_key_plain", "") or ""

        project_ids = []

        try:
            files = package.files.all()
        except Exception:
            files = []

        for file_obj in files:
            project_id = str(getattr(file_obj, "project_id", "") or "").strip()

            if project_id and project_id not in project_ids:
                project_ids.append(project_id)

        package.preview_project_ids = project_ids[:1]
        package.extra_project_ids = project_ids[1:]
        package.extra_project_count = len(package.extra_project_ids)

        client_suffix = ""

        if str(package.name or "").startswith("Project - ") and " - " in str(
            package.name or ""
        ):
            possible_client = str(package.name or "").rsplit(" - ", 1)[-1].strip()

            if possible_client and possible_client not in project_ids:
                client_suffix = f" - {possible_client}"

        if project_ids:
            first_project = project_ids[0]

            if package.extra_project_count:
                package.list_display_name = (
                    f"Project - {first_project} + {package.extra_project_count} more"
                    f"{client_suffix}"
                )
            else:
                package.list_display_name = f"Project - {first_project}{client_suffix}"
        else:
            package.list_display_name = package.name

    qs = request.GET.copy()
    qs.pop("page", None)
    qs.pop("cantidad", None)
    qs_keep = qs.urlencode()

    return render(
        request,
        "client_deliverables/admin_package_list.html",
        {
            "pagina": pagina,
            "packages": pagina.object_list,
            "total_packages": paginator.count,
            "cantidad": cantidad,
            "qs_keep": qs_keep,
        },
    )


@login_required
@require_POST
def admin_package_delete(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to delete this package.")

    package_name = package.name
    package.delete()

    messages.warning(
        request,
        f"Delivery package deleted: {package_name}.",
    )

    return redirect("client_deliverables:admin_package_list")


@login_required
def admin_package_create(request):
    _require_manager(request.user)

    generated_key = None
    recommended_session_key = "delivery_package_recommended_key_new"

    if not request.session.get(recommended_session_key):
        request.session[recommended_session_key] = DeliveryPackage.generate_access_key()
        request.session.modified = True

    recommended_access_key = request.session.get(recommended_session_key, "")

    if request.method == "POST":
        posted_recommended_key = (
            request.POST.get("recommended_access_key")
            or recommended_access_key
            or DeliveryPackage.generate_access_key()
        )

        form = DeliveryPackageForm(
            request.POST,
            recommended_access_key=posted_recommended_key,
        )

        if form.is_valid():
            package = form.save(commit=False)
            package.created_by = request.user
            package.save()

            generated_key = form.generated_key

            if generated_key:
                request.session[f"delivery_package_key_{package.id}"] = generated_key

            request.session.pop(recommended_session_key, None)
            request.session.modified = True

            messages.success(request, "Delivery package created successfully.")
            return redirect("client_deliverables:admin_package_detail", pk=package.id)
    else:
        form = DeliveryPackageForm(
            recommended_access_key=recommended_access_key,
            initial={
                "recommended_access_key": recommended_access_key,
            },
        )

    return render(
        request,
        "client_deliverables/admin_package_form.html",
        {
            "form": form,
            "mode": "create",
            "generated_key": generated_key,
            "recommended_access_key": recommended_access_key,
        },
    )


@login_required
def admin_package_detail(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    public_url = _package_public_url(request, package)
    grouped = _package_project_summary(package)

    generated_key = request.session.pop(f"delivery_package_key_{package.id}", None)
    visible_access_key = getattr(package, "access_key_plain", "") or generated_key or ""

    outlook_subject = _build_outlook_subject(package)
    outlook_body = _build_outlook_body(request, package)

    outlook_href = "mailto:?subject={}&body={}".format(
        quote(outlook_subject),
        quote(outlook_body),
    )

    project_lookup_id = (request.GET.get("project_id") or "").strip()
    project_lookup = None

    selected_source_keys = set(
        package.files.filter(is_active=True)
        .exclude(source_key="")
        .values_list("source_key", flat=True)
    )

    if project_lookup_id:
        project_lookup = discover_project_deliverables(
            request.user,
            project_lookup_id,
        )

    return render(
        request,
        "client_deliverables/admin_package_detail.html",
        {
            "package": package,
            "public_url": public_url,
            "grouped": grouped,
            "generated_key": generated_key,
            "visible_access_key": visible_access_key,
            "outlook_subject": outlook_subject,
            "outlook_body": outlook_body,
            "outlook_href": outlook_href,
            "project_lookup_id": project_lookup_id,
            "project_lookup": project_lookup,
            "selected_source_keys": selected_source_keys,
        },
    )


@login_required
@require_POST
def admin_package_add_selected(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    if package.status == DeliveryPackage.STATUS_REVOKED:
        messages.error(request, "You cannot add deliverables to a revoked package.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="package-actions-section",
            )
        )

    project_id = (request.POST.get("project_id") or "").strip()
    selected_keys = request.POST.getlist("selected_deliverables")

    if not project_id:
        messages.error(request, "Project ID is required.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="load-deliverables-section",
            )
        )

    if not selected_keys:
        messages.error(request, "Select at least one deliverable.")
        return redirect(
            _detail_url_with_project(
                package,
                project_id,
                anchor="load-deliverables-section",
            )
        )

    project_lookup = discover_project_deliverables(request.user, project_id)

    if not project_lookup or not project_lookup.get("ok"):
        messages.error(
            request,
            project_lookup.get("message", "Project deliverables are not available."),
        )
        return redirect(
            _detail_url_with_project(
                package,
                project_id,
                anchor="load-deliverables-section",
            )
        )

    available_by_key = {
        item["source_key"]: item for item in project_lookup.get("deliverables", [])
    }

    created_count = 0
    skipped_count = 0

    for source_key in selected_keys:
        item = available_by_key.get(source_key)

        if not item:
            skipped_count += 1
            continue

        exists = DeliveryPackageFile.objects.filter(
            package=package,
            source_key=item["source_key"],
            is_active=True,
        ).exists()

        if exists:
            skipped_count += 1
            continue

        DeliveryPackageFile.objects.create(
            package=package,
            project_id=item["project_id"],
            file_type=item["file_type"],
            display_name=item["title"],
            source_url=item["source_url"],
            source_key=item["source_key"],
            order=package.files.count() + 1,
            is_active=True,
            created_by=request.user,
        )

        created_count += 1

    if created_count:
        messages.success(
            request,
            f"{created_count} deliverable(s) added to the package.",
        )

    if skipped_count:
        messages.warning(
            request,
            f"{skipped_count} deliverable(s) were skipped because they were already added or unavailable.",
        )

    return redirect(
        _detail_url_with_project(
            package,
            project_id,
            anchor="included-deliverables-section",
        )
    )


@login_required
@require_POST
def admin_package_publish(request, pk):
    _require_publisher(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to publish this package.")

    if not package.files.filter(is_active=True).exists():
        messages.error(request, "You cannot publish a package without files.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="included-deliverables-section",
            )
        )

    # Publicar nuevamente debe limpiar cualquier marca anterior de revocación.
    package.publish(user=request.user)
    package.revoked_at = None
    package.revoked_by = None

    # También limpiamos bloqueos anteriores por seguridad.
    package.failed_attempts = 0
    package.locked_until = None

    package.save(
        update_fields=[
            "status",
            "published_at",
            "published_by",
            "revoked_at",
            "revoked_by",
            "failed_attempts",
            "locked_until",
            "updated_at",
        ]
    )

    messages.success(request, "Delivery package published successfully.")
    return redirect("client_deliverables:admin_package_list")


@login_required
@require_POST
def admin_package_revoke(request, pk):
    _require_revoker(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to revoke this package.")

    package.revoke(user=request.user)
    package.save()

    messages.warning(request, "Delivery package revoked.")
    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="package-actions-section",
        )
    )


@login_required
def admin_package_outlook(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    subject = _build_outlook_subject(package)
    body = _build_outlook_body(request, package)

    href = "mailto:?subject={}&body={}".format(
        quote(subject),
        quote(body),
    )

    return redirect(href)


@login_required
@require_POST
def admin_package_delete_file(request, pk, file_id):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    file_obj = get_object_or_404(
        DeliveryPackageFile,
        pk=file_id,
        package=package,
    )

    project_id = file_obj.project_id
    display_name = file_obj.display_name

    file_obj.delete()

    messages.warning(
        request,
        f"Deliverable removed: {display_name} / Project ID {project_id}.",
    )

    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="included-deliverables-section",
        )
    )


# ============================================================
# Public package link
# ============================================================


def public_package_detail(request, token):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    unavailable_response = _public_package_unavailable_response(request, package)

    if unavailable_response:
        log_delivery_access(
            request,
            package,
            DeliveryAccessLog.ACTION_VIEW,
        )
        return unavailable_response

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_VIEW,
    )

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    grouped = _package_project_summary(package)

    return render(
        request,
        "client_deliverables/public_package_detail.html",
        {
            "package": package,
            "grouped": grouped,
        },
    )

def public_package_unlock(request, token):
    package = get_object_or_404(DeliveryPackage, token=token)

    unavailable_response = _public_package_unavailable_response(request, package)

    if unavailable_response:
        return unavailable_response

    if not package.requires_access_key:
        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    if _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    if request.method == "POST":
        access_key = (request.POST.get("access_key") or "").strip()

        if package.check_access_key(access_key):
            package.reset_failed_attempts()
            package.save(update_fields=["failed_attempts", "locked_until"])

            _mark_package_unlocked(request, package)

            log_delivery_access(
                request,
                package,
                DeliveryAccessLog.ACTION_UNLOCK_SUCCESS,
            )

            return redirect(
                "client_deliverables:public_package_detail",
                token=package.token,
            )

        package.register_failed_attempt()
        package.save(update_fields=["failed_attempts", "locked_until"])

        log_delivery_access(
            request,
            package,
            DeliveryAccessLog.ACTION_UNLOCK_FAILED,
        )

        messages.error(request, "Invalid access key.")

        if package.is_locked():
            messages.error(
                request,
                "This link has been temporarily locked due to multiple failed attempts.",
            )

    return render(
        request,
        "client_deliverables/public_package_unlock.html",
        {
            "package": package,
        },
    )


def public_download_file(request, token, file_id):
    package = get_object_or_404(
        DeliveryPackage.objects.select_related("created_by"),
        token=token,
    )

    unavailable_response = _public_package_unavailable_response(request, package)

    if unavailable_response:
        return unavailable_response

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    file_obj = get_object_or_404(
        DeliveryPackageFile,
        pk=file_id,
        package=package,
        is_active=True,
    )

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_DOWNLOAD_FILE,
        file=file_obj,
    )

    result = _fetch_delivery_file_bytes(request, package, file_obj)

    if not result["ok"]:
        messages.error(
            request,
            f"This deliverable could not be downloaded. {result['error']}",
        )
        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    filename = _safe_zip_filename(result["filename"], fallback="deliverable")

    response = HttpResponse(
        result["content"],
        content_type=result["content_type"] or "application/octet-stream",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"

    return response


def public_download_all(request, token):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    unavailable_response = _public_package_unavailable_response(
        request,
        package,
    )

    if unavailable_response:
        return unavailable_response

    if package.requires_access_key and not _is_package_unlocked(
        request,
        package,
    ):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    active_files = package.files.filter(is_active=True)
    files_count = active_files.count()

    if not files_count:
        return _render_public_package_unavailable(
            request,
            package,
            reason="no_files",
        )

    content_signature = _delivery_package_content_signature(package)

    # ============================================================
    # 1) Limpiar ZIP vencidos de este package
    # ============================================================
    expired_jobs = DeliveryZipJob.objects.filter(
        package=package,
        status=DeliveryZipJob.STATUS_READY,
        expires_at__isnull=False,
        expires_at__lte=timezone.now(),
    )

    for expired_job in expired_jobs:
        expired_job.delete_zip_file(save=False)
        expired_job.status = DeliveryZipJob.STATUS_EXPIRED
        expired_job.zip_file = None
        expired_job.filename = ""

        expired_job.save(
            update_fields=[
                "status",
                "zip_file",
                "filename",
            ]
        )

    # ============================================================
    # 2) Reutilizar un ZIP vigente con el mismo contenido
    # ============================================================
    reusable_jobs = DeliveryZipJob.objects.filter(
        package=package,
        status=DeliveryZipJob.STATUS_READY,
        content_signature=content_signature,
    ).order_by("-finished_at", "-created_at")

    for reusable_job in reusable_jobs:
        if reusable_job.can_be_reused(content_signature):
            log_delivery_access(
                request,
                package,
                DeliveryAccessLog.ACTION_DOWNLOAD_ALL,
                extra={
                    "zip_job_id": str(reusable_job.id),
                    "reused": True,
                },
            )

            return redirect(
                "client_deliverables:public_download_all_status",
                token=package.token,
                job_id=reusable_job.id,
            )

    # ============================================================
    # 3) Reutilizar un job que ya se está construyendo
    # ============================================================
    existing_processing_job = (
        DeliveryZipJob.objects.filter(
            package=package,
            content_signature=content_signature,
            status__in=[
                DeliveryZipJob.STATUS_PENDING,
                DeliveryZipJob.STATUS_PROCESSING,
            ],
        )
        .order_by("-created_at")
        .first()
    )

    if existing_processing_job:
        log_delivery_access(
            request,
            package,
            DeliveryAccessLog.ACTION_DOWNLOAD_ALL,
            extra={
                "zip_job_id": str(existing_processing_job.id),
                "reused_processing_job": True,
            },
        )

        return redirect(
            "client_deliverables:public_download_all_status",
            token=package.token,
            job_id=existing_processing_job.id,
        )

    # ============================================================
    # 4) Crear un ZIP nuevo
    # ============================================================
    job = DeliveryZipJob.objects.create(
        package=package,
        status=DeliveryZipJob.STATUS_PENDING,
        total_files=files_count,
        files_added=0,
        files_failed=0,
        content_signature=content_signature,
        requested_by=(request.user if request.user.is_authenticated else None),
    )

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_DOWNLOAD_ALL,
        extra={
            "zip_job_id": str(job.id),
            "reused": False,
        },
    )

    _start_zip_job_background(job, request)

    return redirect(
        "client_deliverables:public_download_all_status",
        token=package.token,
        job_id=job.id,
    )


def public_download_all_status(request, token, job_id):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    unavailable_response = _public_package_unavailable_response(request, package)

    if unavailable_response:
        return unavailable_response

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    job = get_object_or_404(
        DeliveryZipJob,
        pk=job_id,
        package=package,
    )

    return render(
        request,
        "client_deliverables/public_download_all_status.html",
        {
            "package": package,
            "job": job,
        },
    )


def public_download_all_status_json(request, token, job_id):
    package = get_object_or_404(
        DeliveryPackage,
        token=token,
    )

    unavailable_response = _public_package_unavailable_response(
        request,
        package,
    )

    if unavailable_response:
        return JsonResponse(
            {
                "ok": False,
                "status": "unavailable",
                "message": "This delivery link is no longer available.",
            },
            status=410,
        )

    if package.requires_access_key and not _is_package_unlocked(
        request,
        package,
    ):
        return JsonResponse(
            {
                "ok": False,
                "status": "locked",
                "message": "Access key required.",
            },
            status=403,
        )

    job = get_object_or_404(
        DeliveryZipJob,
        pk=job_id,
        package=package,
    )

    if job.status == DeliveryZipJob.STATUS_READY and job.is_expired():
        job.delete_zip_file(save=False)
        job.status = DeliveryZipJob.STATUS_EXPIRED
        job.zip_file = None
        job.filename = ""

        job.save(
            update_fields=[
                "status",
                "zip_file",
                "filename",
            ]
        )

    download_url = ""

    if job.is_ready():
        download_url = reverse(
            "client_deliverables:public_download_all_file",
            args=[
                package.token,
                job.id,
            ],
        )

    return JsonResponse(
        {
            "ok": True,
            "status": job.status,
            "files_added": job.files_added,
            "files_failed": job.files_failed,
            "processed_files": job.processed_files,
            "total_files": job.total_files,
            "progress_percentage": job.progress_percentage,
            "error_message": job.error_message,
            "download_url": download_url,
            "expires_at": (job.expires_at.isoformat() if job.expires_at else None),
        }
    )


def public_download_all_file(request, token, job_id):
    package = get_object_or_404(
        DeliveryPackage,
        token=token,
    )

    unavailable_response = _public_package_unavailable_response(
        request,
        package,
    )

    if unavailable_response:
        return unavailable_response

    if package.requires_access_key and not _is_package_unlocked(
        request,
        package,
    ):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    job = get_object_or_404(
        DeliveryZipJob,
        pk=job_id,
        package=package,
    )

    if job.status == DeliveryZipJob.STATUS_READY and job.is_expired():
        job.delete_zip_file(save=False)
        job.status = DeliveryZipJob.STATUS_EXPIRED
        job.zip_file = None
        job.filename = ""

        job.save(
            update_fields=[
                "status",
                "zip_file",
                "filename",
            ]
        )

        messages.error(
            request,
            "This temporary ZIP file has expired. Please generate it again.",
        )

        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    if not job.is_ready():
        messages.error(
            request,
            "The ZIP file is not ready or is no longer available.",
        )

        return redirect(
            "client_deliverables:public_download_all_status",
            token=package.token,
            job_id=job.id,
        )

    filename = job.filename or f"{slugify(package.name).replace('-', '_')}.zip"

    download_url = _temporary_storage_download_url(
        field_file=job.zip_file,
        filename=filename,
        expiration_seconds=900,
    )

    if not download_url:
        messages.error(
            request,
            "The temporary download link could not be generated. " "Please try again.",
        )

        return redirect(
            "client_deliverables:public_download_all_status",
            token=package.token,
            job_id=job.id,
        )

    job.mark_downloaded()

    return redirect(download_url)


# ============================================================
# Client portal
# ============================================================


@login_required
def client_my_deliverables(request):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    assignments = ClientProjectAssignment.objects.filter(
        user=request.user,
        is_active=True,
    ).order_by("project_id")

    return render(
        request,
        "client_deliverables/client_my_deliverables.html",
        {
            "assignments": assignments,
        },
    )


@login_required
def client_project_search(request):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    project_id = (request.GET.get("project_id") or "").strip()

    if project_id:
        return redirect(
            "client_deliverables:client_project_detail",
            project_id=project_id,
        )

    return redirect("client_deliverables:client_my_deliverables")


@login_required
def client_project_detail(request, project_id):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    project_id = str(project_id or "").strip()

    if not _client_has_project_assignment(request.user, project_id):
        return render(
            request,
            "client_deliverables/client_project_detail.html",
            {
                "project_id": project_id,
                "files": [],
                "packages": [],
                "not_found": True,
            },
        )

    files = (
        DeliveryPackageFile.objects.select_related("package")
        .filter(
            project_id=project_id,
            is_active=True,
            package__status=DeliveryPackage.STATUS_PUBLISHED,
        )
        .exclude(package__status=DeliveryPackage.STATUS_REVOKED)
        .order_by("-package__published_at", "order", "id")
    )

    valid_files = []

    for f in files:
        package = f.package

        if package.is_expired():
            continue

        valid_files.append(f)

    package_ids = list({f.package_id for f in valid_files})

    packages = DeliveryPackage.objects.filter(id__in=package_ids).order_by(
        "-published_at"
    )

    return render(
        request,
        "client_deliverables/client_project_detail.html",
        {
            "project_id": project_id,
            "files": valid_files,
            "packages": packages,
            "not_found": False,
        },
    )


@login_required
def client_project_download_all(request, project_id):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    project_id = str(project_id or "").strip()

    if not _client_has_project_assignment(request.user, project_id):
        raise PermissionDenied("You do not have access to this project.")

    return render(
        request,
        "client_deliverables/public_placeholder.html",
        {
            "title": "Client download all",
            "message": f"Client download all pending implementation for project {project_id}",
        },
    )


@login_required
@require_POST
def admin_package_reopen(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to reopen this package.")

    if package.status != DeliveryPackage.STATUS_REVOKED:
        messages.warning(request, "Only revoked packages can be reopened.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="package-actions-section",
            )
        )

    package.status = DeliveryPackage.STATUS_DRAFT
    package.revoked_at = None
    package.revoked_by = None
    package.published_at = None
    package.published_by = None
    package.save(
        update_fields=[
            "status",
            "revoked_at",
            "revoked_by",
            "published_at",
            "published_by",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "Package reopened as draft. You can add deliverables now.",
    )

    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="package-actions-section",
        )
    )


@login_required
def admin_package_edit(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    generated_key = None
    recommended_session_key = f"delivery_package_recommended_key_{package.id}"

    if not request.session.get(recommended_session_key):
        request.session[recommended_session_key] = DeliveryPackage.generate_access_key()
        request.session.modified = True

    recommended_access_key = request.session.get(recommended_session_key, "")

    if request.method == "POST":
        posted_recommended_key = (
            request.POST.get("recommended_access_key")
            or recommended_access_key
            or DeliveryPackage.generate_access_key()
        )

        form = DeliveryPackageForm(
            request.POST,
            instance=package,
            recommended_access_key=posted_recommended_key,
        )

        if form.is_valid():
            package = form.save(commit=False)
            package.save()

            generated_key = form.generated_key

            if generated_key:
                request.session[f"delivery_package_key_{package.id}"] = generated_key
                request.session.pop(recommended_session_key, None)
                request.session.modified = True

            messages.success(request, "Delivery package updated successfully.")
            return redirect("client_deliverables:admin_package_detail", pk=package.id)
    else:
        form = DeliveryPackageForm(
            instance=package,
            recommended_access_key=recommended_access_key,
            initial={
                "recommended_access_key": recommended_access_key,
            },
        )

    return render(
        request,
        "client_deliverables/admin_package_form.html",
        {
            "form": form,
            "mode": "edit",
            "package": package,
            "generated_key": generated_key,
            "recommended_access_key": recommended_access_key,
        },
    )


@login_required
def admin_package_from_invoices(request):
    """
    Flujo especial desde Finance / Invoices.

    Paso 1:
      GET /client-deliverables/from-invoices/?ids=1,2,3
      Muestra los deliverables disponibles de los invoices seleccionados.

    Paso 2:
      POST action=review_settings
      Muestra el formulario de configuración del package.

    Paso 3:
      POST action=create_package
      Crea el DeliveryPackage y los DeliveryPackageFile seleccionados.

    No toca el flujo normal de Client Deliverables.
    No modifica SesionBilling.
    No crea package en GET.
    """
    _require_manager(request.user)

    def parse_invoice_ids(raw_ids):
        parsed = []

        for raw in str(raw_ids or "").split(","):
            raw = raw.strip()
            if not raw:
                continue

            try:
                parsed.append(int(raw))
            except ValueError:
                continue

        return list(dict.fromkeys(parsed))

    def get_allowed_invoices(invoice_ids):
        from operaciones.models import SesionBilling

        invoices_qs = (
            SesionBilling.objects.filter(id__in=invoice_ids)
            .prefetch_related("items", "tecnicos_sesion")
            .order_by("-creado_en")
        )

        invoices = list(invoices_qs)

        if not invoices:
            return []

        allowed = []

        is_full_history_user = request.user.is_superuser or getattr(
            request.user,
            "es_usuario_historial",
            False,
        )

        allowed_keys = set()

        if not is_full_history_user:
            from core.permissions import filter_queryset_by_access
            from facturacion.models import Proyecto

            proyectos_user = filter_queryset_by_access(
                Proyecto.objects.all(),
                request.user,
                "id",
            )

            for p in proyectos_user:
                if getattr(p, "nombre", None):
                    allowed_keys.add(str(p.nombre).strip())
                    allowed_keys.add(str(p.nombre).strip().lower())

                if getattr(p, "codigo", None):
                    allowed_keys.add(str(p.codigo).strip())
                    allowed_keys.add(str(p.codigo).strip().lower())

                allowed_keys.add(str(p.id).strip())
                allowed_keys.add(str(p.id).strip().lower())

        for invoice in invoices:
            proyecto_key = str(getattr(invoice, "proyecto", "") or "").strip()
            proyecto_id_key = str(getattr(invoice, "proyecto_id", "") or "").strip()

            if is_full_history_user:
                allowed.append(invoice)
                continue

            if (
                proyecto_key in allowed_keys
                or proyecto_id_key in allowed_keys
                or proyecto_key.lower() in allowed_keys
                or proyecto_id_key.lower() in allowed_keys
            ):
                allowed.append(invoice)

        return allowed

    def build_available_from_invoices(invoices):
        invoices_by_project = {}

        for invoice in invoices:
            project_id = str(getattr(invoice, "proyecto_id", "") or "").strip()

            if not project_id:
                continue

            invoices_by_project.setdefault(project_id, []).append(invoice)

        grouped = {}
        available_by_key = {}

        for project_id, project_invoices in invoices_by_project.items():
            deliverables = build_available_deliverables_for_sessions(
                project_invoices,
                project_id,
            )

            clean_items = []

            for item in deliverables:
                source_key = str(item.get("source_key") or "").strip()
                source_url = str(item.get("source_url") or "").strip()

                if not source_key or not source_url:
                    continue

                item["project_id"] = str(item.get("project_id") or project_id).strip()
                item["title"] = str(item.get("title") or "Deliverable").strip()
                item["file_type"] = str(
                    item.get("file_type") or DeliveryPackageFile.FILE_OTHER
                ).strip()
                item["source_key"] = source_key
                item["source_url"] = source_url

                clean_items.append(item)
                available_by_key[source_key] = item

            grouped[project_id] = clean_items

        return grouped, available_by_key

    raw_ids = (request.GET.get("ids") or request.POST.get("invoice_ids") or "").strip()

    invoice_ids = parse_invoice_ids(raw_ids)

    if not invoice_ids:
        messages.error(request, "Invalid project selection.")
        return redirect("facturacion:invoices")

    allowed_invoices = get_allowed_invoices(invoice_ids)

    if not allowed_invoices:
        messages.error(request, "You do not have access to the selected projects.")
        return redirect("facturacion:invoices")

    grouped_available, available_by_key = build_available_from_invoices(
        allowed_invoices
    )

    if not available_by_key:
        messages.warning(
            request,
            "No available deliverables were found for the selected projects.",
        )

    first = allowed_invoices[0]
    client = str(getattr(first, "cliente", "") or "").strip()

    project_ids = []

    for invoice in allowed_invoices:
        project_id = str(getattr(invoice, "proyecto_id", "") or "").strip()

        if project_id and project_id not in project_ids:
            project_ids.append(project_id)

    if project_ids:
        project_summary = ", ".join(project_ids)
        default_name = f"Project - {project_summary}"
    else:
        default_name = f"Project - {len(allowed_invoices)} project(s)"

    if client:
        default_name = f"{default_name} - {client}"

    default_message = "Please find the selected project documentation available through this secure delivery link."

    recommended_session_key = "delivery_package_from_invoices_recommended_key"

    if not request.session.get(recommended_session_key):
        request.session[recommended_session_key] = DeliveryPackage.generate_access_key()
        request.session.modified = True

    recommended_access_key = request.session.get(recommended_session_key, "")

    action = request.POST.get("action", "")

    # ============================================================
    # PASO 1: Mostrar selector de archivos
    # ============================================================
    if request.method == "GET" or action == "":
        form = DeliveryPackageForm(
            recommended_access_key=recommended_access_key,
            initial={
                "name": default_name[:180],
                "message": default_message,
                "requires_access_key": True,
                "generate_access_key": True,
                "recommended_access_key": recommended_access_key,
            },
        )

        return render(
            request,
            "client_deliverables/admin_package_from_invoices.html",
            {
                "step": "select_files",
                "form": form,
                "invoice_ids_csv": ",".join(str(i) for i in invoice_ids),
                "allowed_invoices": allowed_invoices,
                "grouped_available": grouped_available,
                "recommended_access_key": recommended_access_key,
            },
        )

    # ============================================================
    # VOLVER AL PASO 1: Selector de archivos
    # ============================================================
    if action == "back_to_files":
        form = DeliveryPackageForm(
            recommended_access_key=recommended_access_key,
            initial={
                "name": default_name[:180],
                "message": default_message,
                "requires_access_key": True,
                "generate_access_key": True,
                "recommended_access_key": recommended_access_key,
            },
        )

        return render(
            request,
            "client_deliverables/admin_package_from_invoices.html",
            {
                "step": "select_files",
                "form": form,
                "invoice_ids_csv": ",".join(str(i) for i in invoice_ids),
                "allowed_invoices": allowed_invoices,
                "grouped_available": grouped_available,
                "recommended_access_key": recommended_access_key,
            },
        )

    # ============================================================
    # PASO 2: Revisar settings del package
    # ============================================================
    if action == "review_settings":
        selected_keys = request.POST.getlist("selected_deliverables")

        selected_keys = [key for key in selected_keys if key in available_by_key]

        if not selected_keys:
            messages.error(request, "Select at least one deliverable.")
            form = DeliveryPackageForm(
                recommended_access_key=recommended_access_key,
                initial={
                    "name": default_name[:180],
                    "message": default_message,
                    "requires_access_key": True,
                    "generate_access_key": True,
                    "recommended_access_key": recommended_access_key,
                },
            )

            return render(
                request,
                "client_deliverables/admin_package_from_invoices.html",
                {
                    "step": "select_files",
                    "form": form,
                    "invoice_ids_csv": ",".join(str(i) for i in invoice_ids),
                    "allowed_invoices": allowed_invoices,
                    "grouped_available": grouped_available,
                    "recommended_access_key": recommended_access_key,
                },
            )

        form = DeliveryPackageForm(
            recommended_access_key=recommended_access_key,
            initial={
                "name": default_name[:180],
                "message": default_message,
                "requires_access_key": True,
                "generate_access_key": True,
                "recommended_access_key": recommended_access_key,
            },
        )

        selected_items = [available_by_key[key] for key in selected_keys]

        return render(
            request,
            "client_deliverables/admin_package_from_invoices.html",
            {
                "step": "settings",
                "form": form,
                "invoice_ids_csv": ",".join(str(i) for i in invoice_ids),
                "allowed_invoices": allowed_invoices,
                "grouped_available": grouped_available,
                "selected_keys": selected_keys,
                "selected_items": selected_items,
                "recommended_access_key": recommended_access_key,
            },
        )

    # ============================================================
    # PASO 3: Crear package
    # ============================================================
    if action == "create_package":
        selected_keys = request.POST.getlist("selected_deliverables")

        selected_keys = [key for key in selected_keys if key in available_by_key]

        if not selected_keys:
            messages.error(request, "Select at least one deliverable.")
            return redirect(
                f"{reverse('client_deliverables:admin_package_from_invoices')}?ids={quote(','.join(str(i) for i in invoice_ids))}"
            )

        posted_recommended_key = (
            request.POST.get("recommended_access_key")
            or recommended_access_key
            or DeliveryPackage.generate_access_key()
        )

        form = DeliveryPackageForm(
            request.POST,
            recommended_access_key=posted_recommended_key,
        )

        if not form.is_valid():
            selected_items = [available_by_key[key] for key in selected_keys]

            return render(
                request,
                "client_deliverables/admin_package_from_invoices.html",
                {
                    "step": "settings",
                    "form": form,
                    "invoice_ids_csv": ",".join(str(i) for i in invoice_ids),
                    "allowed_invoices": allowed_invoices,
                    "grouped_available": grouped_available,
                    "selected_keys": selected_keys,
                    "selected_items": selected_items,
                    "recommended_access_key": posted_recommended_key,
                },
            )

        with transaction.atomic():
            package = form.save(commit=False)
            package.created_by = request.user
            package.save()

            generated_key = form.generated_key

            if generated_key:
                request.session[f"delivery_package_key_{package.id}"] = generated_key

            request.session.pop(recommended_session_key, None)
            request.session.modified = True

            created_count = 0
            skipped_count = 0

            invoice_by_id = {str(invoice.id): invoice for invoice in allowed_invoices}

            for source_key in selected_keys:
                item = available_by_key.get(source_key)

                if not item:
                    skipped_count += 1
                    continue

                source_url = str(item.get("source_url") or "").strip()
                display_name = str(item.get("title") or "Deliverable").strip()
                file_type = str(
                    item.get("file_type") or DeliveryPackageFile.FILE_OTHER
                ).strip()
                project_id = str(item.get("project_id") or "").strip()
                session_id = str(item.get("session_id") or "").strip()

                if not source_url or not project_id:
                    skipped_count += 1
                    continue

                exists = DeliveryPackageFile.objects.filter(
                    package=package,
                    source_key=source_key,
                    is_active=True,
                ).exists()

                if exists:
                    skipped_count += 1
                    continue

                DeliveryPackageFile.objects.create(
                    package=package,
                    billing_session=invoice_by_id.get(session_id),
                    project_id=project_id,
                    file_type=file_type[:40],
                    display_name=display_name[:255],
                    source_url=source_url,
                    source_key=source_key,
                    order=package.files.count() + 1,
                    is_active=True,
                    created_by=request.user,
                )

                created_count += 1

        if created_count:
            if not can_publish_deliverables(request.user):
                messages.success(
                    request,
                    f"Delivery package created with {created_count} file(s), but it was left as Draft because you do not have permission to publish packages.",
                )
            else:
                package.publish(user=request.user)
                package.revoked_at = None
                package.revoked_by = None
                package.failed_attempts = 0
                package.locked_until = None

                package.save(
                    update_fields=[
                        "status",
                        "published_at",
                        "published_by",
                        "revoked_at",
                        "revoked_by",
                        "failed_attempts",
                        "locked_until",
                        "updated_at",
                    ]
                )

                messages.success(
                    request,
                    f"Delivery package created and published with {created_count} file(s).",
                )

        if skipped_count:
            messages.warning(
                request,
                f"{skipped_count} deliverable(s) were skipped because they were unavailable or duplicated.",
            )

        return redirect("client_deliverables:admin_package_list")

    messages.error(request, "Invalid action.")
    return redirect("facturacion:invoices")
