#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    # 1) Cargar variables de entorno: primero .env.local, luego .env (fallback)
    try:
        from dotenv import load_dotenv
        BASE_DIR = Path(__file__).resolve().parent
        # Si .env.local no existe, load_dotenv devuelve False; luego probamos .env
        if not load_dotenv(BASE_DIR / ".env.local"):
            load_dotenv(BASE_DIR / ".env")
    except Exception:
        # Si no está instalado python-dotenv, seguimos sin romper
        pass

    # 2) Elegir settings: usa lo que venga del entorno, si no, dev por defecto
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE",
        os.getenv("DJANGO_SETTINGS_MODULE", "hyperlink_networks.settings.dev"),
    )

    # (opcional) Si defines DJANGO_DEVELOPMENT=true en .env.local, fuerza dev:
    if os.getenv("DJANGO_DEVELOPMENT", "").lower() == "true":
        os.environ["DJANGO_SETTINGS_MODULE"] = "hyperlink_networks.settings.dev"

    # ✅ PIL tolerante a imágenes truncadas (tu línea)
    import hyperlink_networks.pil_config  # noqa: F401

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
