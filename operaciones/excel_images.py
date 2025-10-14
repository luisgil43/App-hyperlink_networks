# operaciones/excel_images.py
"""
Descarga estricta (con reintentos) de imágenes desde Wasabi/S3 y conversión a JPEG
optimizado para insertar en Excel. NO omite fotos: si no logra descargar, levanta error.
"""

import io
import os
import time
from tempfile import NamedTemporaryFile

from PIL import Image, ImageOps

# Soporte HEIC/HEIF opcional (si está instalado)
try:
    from pillow_heif import register_heif_opener  # pip install pillow-heif
    register_heif_opener()
except Exception:
    pass

import boto3
from botocore.config import Config as BotoConfig

# === Parámetros ajustables por ENV ===
MAX_PX = int(os.getenv("REPORT_IMG_MAX_PX", "1600")
             )              # tamaño máx. lado largo
JPG_QUALITY = int(os.getenv("REPORT_IMG_JPG_QUALITY", "82"))      # calidad JPG
# timeout connect (s)
DL_CONNECT_TO = int(os.getenv("REPORT_IMG_CONNECT_TIMEOUT", "5"))
DL_READ_TO = int(os.getenv("REPORT_IMG_READ_TIMEOUT", "30")
                 )      # timeout read (s)
# intentos por imagen
DL_MAX_ATTEMPTS = int(os.getenv("REPORT_IMG_MAX_ATTEMPTS", "12"))
DL_BACKOFF_BASE = float(
    os.getenv("REPORT_IMG_BACKOFF_BASE", "0.8"))  # base exponencial

# Reutiliza endpoint/region de django-storages si están en ENV
_S3_ENDPOINT = os.getenv(
    "AWS_S3_ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL")
_S3_REGION = os.getenv("AWS_S3_REGION_NAME") or os.getenv("AWS_REGION")

_BOTO_CFG = BotoConfig(
    connect_timeout=DL_CONNECT_TO,
    read_timeout=DL_READ_TO,
    retries={"max_attempts": 5, "mode": "standard"},
    max_pool_connections=10,
)


def _download_fieldfile_bytes_strict(fieldfile) -> bytes:
    """
    Descarga el FieldFile desde Wasabi/S3 con reintentos exponenciales.
    - Obtiene bucket y key desde el storage de django-storages.
    - Si no hay bucket/key, usa .open()/.read() del propio FieldFile.
    - Si agota intentos, levanta RuntimeError (para NO omitir la foto).
    """
    storage = getattr(fieldfile, "storage", None)
    bucket = getattr(storage, "bucket_name", None)
    key = fieldfile.name

    # Fallback genérico (puede bloquear más, pero mantiene compatibilidad)
    if not bucket or not key:
        fieldfile.open("rb")
        data = fieldfile.read()
        fieldfile.close()
        return data

    # Cliente S3 apuntando al endpoint de Wasabi (o el que definas en ENV)
    client_kwargs = {"config": _BOTO_CFG}
    if _S3_ENDPOINT:
        client_kwargs["endpoint_url"] = _S3_ENDPOINT
    if _S3_REGION:
        client_kwargs["region_name"] = _S3_REGION

    client = boto3.client("s3", **client_kwargs)

    delay = DL_BACKOFF_BASE
    last_err = None
    for attempt in range(1, DL_MAX_ATTEMPTS + 1):
        try:
            obj = client.get_object(Bucket=bucket, Key=key)
            body = obj["Body"]  # botocore.response.StreamingBody
            chunks = []
            while True:
                chunk = body.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception as e:
            last_err = e
            # Backoff exponencial con tope de 60s por espera
            time.sleep(min(60, delay))
            delay *= 2.0

    raise RuntimeError(
        f"No se pudo descargar la imagen tras {DL_MAX_ATTEMPTS} intentos: {last_err}")


def tmp_jpeg_from_filefield(fieldfile, max_px=MAX_PX, quality=JPG_QUALITY):
    """
    Descarga (sí o sí) la imagen asociada a un FieldFile, normaliza orientación,
    convierte a RGB si hace falta, reduce a 'max_px' y guarda como JPEG optimizado
    en un archivo temporal. Devuelve (tmp_path, width, height).
    """
    raw = _download_fieldfile_bytes_strict(fieldfile)

    with Image.open(io.BytesIO(raw)) as im:
        # Corrige orientación EXIF y normaliza a RGB
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")

        # Reducción agresiva para acelerar inserción y bajar uso de red/CPU
        im.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        w, h = im.size

        tmp = NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()

        # JPEG optimizado y progresivo
        im.save(tmp_path, "JPEG", quality=quality,
                optimize=True, progressive=True)

    return tmp_path, w, h
