# setting_dev

import os
from datetime import date

import dj_database_url

from .base import *

# ==============================
# Desarrollo
# ==============================

# Activar modo debug
DEBUG = True

# Hosts permitidos en desarrollo
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "172.20.10.3",
    "172.20.10.2",
    "192.168.1.84",
    "192.168.1.85",
    "192.168.1.82",
    "192.168.1.83",
    "192.168.1.86",
    "192.168.1.81",
    "192.168.1.87",
    "192.168.1.88",
    "192.168.1.44",
    "192.168.1.39",
    "192.168.1.51",
    
    ".ngrok-free.app",
    ".ngrok-free.dev",
]

CSRF_TRUSTED_ORIGINS = [
    "https://*.ngrok-free.app",
    "https://*.ngrok-free.dev",
]

# Permitir mostrar errores detallados en navegador
INTERNAL_IPS = ['127.0.0.1']


# ==============================

# Base de datos

# ==============================



DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

if DATABASE_URL:

    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            conn_health_checks=True,
        )
    }

    if "postgresql" in DATABASES["default"]["ENGINE"]:

        DATABASES["default"].setdefault("OPTIONS", {})

        DATABASES["default"]["OPTIONS"].update(
            {
                "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "10")),
            }
        )

else:

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                "timeout": 30,
            },
        }
    }
"""

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 30,
        },
    }
}
"""
# ==============================
# IA
# ==============================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Modelo usado por funcionalidades existentes. No tocar para no afectar procesos actuales.
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")

# Configuración exclusiva de DFN Plan Reader.
PLAN_READER_USE_OPENAI = os.getenv(
    "PLAN_READER_USE_OPENAI", "False"
).strip().lower() in [
    "1",
    "true",
    "yes",
    "y",
]

PLAN_READER_MODEL = os.getenv("PLAN_READER_MODEL", "gpt-5.2")
PLAN_READER_RENDER_ZOOM = os.getenv("PLAN_READER_RENDER_ZOOM", "3")

# Desactivar seguridad estricta en cookies y HTTPS
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# 2FA
TWO_FACTOR_ENFORCE_DATE = date(2026, 10, 28)


# ==============================

# Storage: Wasabi también en desarrollo

# ==============================

DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"

# ==============================

# Direct uploads a Wasabi en desarrollo

# ==============================

DIRECT_UPLOADS_ENABLED = True

DIRECT_UPLOADS_MAX_MB = 15

DIRECT_UPLOADS_SAFE_PREFIX = "operaciones/reporte_fotografico/"

# Aliases Wasabi para presigned POST en desarrollo

WASABI_BUCKET_NAME = AWS_STORAGE_BUCKET_NAME

WASABI_ENDPOINT_URL = AWS_S3_ENDPOINT_URL

WASABI_REGION_NAME = AWS_S3_REGION_NAME

WASABI_ACCESS_KEY_ID = AWS_ACCESS_KEY_ID

WASABI_SECRET_ACCESS_KEY = AWS_SECRET_ACCESS_KEY
