# hyperlink_networks/pil_config.py
from PIL import ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True  # evita cuelgues con imágenes truncadas

# Soporte HEIC/HEIF (opcional, si tienes pillow-heif instalado)
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass
