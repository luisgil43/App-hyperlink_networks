# hyperlink_networks/wsgi.py
from django.core.wsgi import get_wsgi_application
import os
import dotenv

dotenv.load_dotenv()

# DS settings por ENV (cae a prod si no está seteado)
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.getenv("DJANGO_SETTINGS_MODULE", "hyperlink_networks.settings.prod"),
)

# ⬇️ Activa PIL tolerante a imágenes truncadas (debes tener pil_config.py creado)
import hyperlink_networks.pil_config  # noqa: F401

application = get_wsgi_application()
