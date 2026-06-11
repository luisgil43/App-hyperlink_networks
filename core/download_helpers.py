# core/download_helpers.py

import os
import re
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.shortcuts import redirect
from django.urls import reverse


def _safe_filename(filename: str, default: str = "download.xlsx") -> str:
    filename = filename or default
    filename = os.path.basename(filename)
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)
    return filename or default


def _filename_from_content_disposition(content_disposition: str, default: str) -> str:
    """
    Extrae filename desde:
    Content-Disposition: attachment; filename="archivo.xlsx"
    """
    if not content_disposition:
        return default

    match = re.search(r'filename="?([^"]+)"?', content_disposition)
    if match:
        return match.group(1)

    return default


def is_app_like_download_request(request) -> bool:
    """
    Decide cuándo usar la página intermedia.

    Web normal:
      - mantiene descarga directa.

    Teléfono / PWA / WebView / app:
      - usa página intermedia para evitar:
        - PK...
        - Preparing download...
        - descarga bloqueada
    """

    ua = (request.META.get("HTTP_USER_AGENT") or "").lower()

    # Permite forzar manualmente:
    # ?prepared_download=1
    forced = (
        request.GET.get("prepared_download") == "1"
        or request.POST.get("prepared_download") == "1"
    )

    if forced:
        return True

    is_mobile = (
        "iphone" in ua
        or "ipad" in ua
        or "ipod" in ua
        or "android" in ua
        or "mobile" in ua
    )

    # iOS PWA instalada / WebView puede venir distinto a Safari normal.
    is_ios_webview_like = (
        "iphone" in ua or "ipad" in ua or "ipod" in ua
    ) and "safari" not in ua

    # Algunas apps WebView en Mac no se identifican como Safari/Chrome normal.
    is_mac_webview_like = (
        "macintosh" in ua
        and "applewebkit" in ua
        and "safari" not in ua
        and "chrome" not in ua
    )

    return is_mobile or is_ios_webview_like or is_mac_webview_like


def prepared_download_response(request, response, filename=None):
    """
    Convierte una respuesta binaria, por ejemplo XLSX/PDF/CSV,
    en una página intermedia compatible con teléfono/PWA/WebView.

    Uso:
        response = HttpResponse(...)
        response["Content-Disposition"] = 'attachment; filename="archivo.xlsx"'
        return prepared_download_response(request, response, "archivo.xlsx")
    """

    content_disposition = response.headers.get("Content-Disposition", "")
    filename = filename or _filename_from_content_disposition(
        content_disposition,
        "download.xlsx",
    )
    filename = _safe_filename(filename)

    content_type = response.headers.get(
        "Content-Type",
        "application/octet-stream",
    )

    # Si la respuesta no tiene .content, no la podemos guardar así.
    # Para StreamingHttpResponse/FileResponse conviene adaptar la view directamente.
    if not hasattr(response, "content"):
        return response

    file_bytes = response.content

    folder = "temporary_downloads"
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    storage_path = f"{folder}/{unique_name}"

    saved_path = default_storage.save(storage_path, ContentFile(file_bytes))
    file_url = default_storage.url(saved_path)

    request.session["prepared_download"] = {
        "file_url": file_url,
        "filename": filename,
        "content_type": content_type,
    }

    return redirect(reverse("usuarios:download_ready"))


def smart_download_response(request, response, filename=None):
    """
    Mantiene la descarga web normal sin dañarla.

    Web normal:
        return response

    Teléfono / app / PWA / WebView:
        guarda el archivo temporalmente y redirige a la página:
        Your file is ready
    """

    if is_app_like_download_request(request):
        return prepared_download_response(request, response, filename)

    return response
