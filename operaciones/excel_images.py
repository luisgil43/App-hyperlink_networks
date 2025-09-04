# operaciones/excel_images.py
from io import BytesIO
from tempfile import NamedTemporaryFile
from django.core.files.storage import default_storage
from PIL import Image as PILImage

MAX_W, MAX_H = 1600, 1600   # ~2–3MP es suficiente para Excel
JPEG_QUALITY = 78           # peso/calidad

def tmp_jpeg_from_filefield(filefield, max_w=MAX_W, max_h=MAX_H, quality=JPEG_QUALITY):
    """
    Lee del storage (Wasabi) -> downscale -> recomprime a JPEG -> guarda en tmp file.
    Devuelve (tmp_path, width_px, height_px).
    """
    # abrir desde el storage (django-storages maneja Wasabi)
    filefield.open("rb")
    try:
        im = PILImage.open(filefield)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")

        # downscale manteniendo aspecto
        im.thumbnail((max_w, max_h))

        # recomprimir a JPEG
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        buf.seek(0)

        # volcar a archivo temporal (xlsxwriter puede insertar desde path)
        tmp = NamedTemporaryFile(delete=False, suffix=".jpg")
        with open(tmp.name, "wb") as out:
            out.write(buf.read())

        return tmp.name, im.size[0], im.size[1]
    finally:
        try:
            filefield.close()
        except Exception:
            pass
