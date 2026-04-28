# setting_dev

import os
from datetime import date

from .base import *

# ==============================
# Desarrollo
# ==============================

# Activar modo debug
DEBUG = True

# Hosts permitidos en desarrollo
ALLOWED_HOSTS = [
    'localhost', '127.0.0.1',
    '172.20.10.3', '172.20.10.2',
    '192.168.1.84', '192.168.1.85',
    '192.168.1.82', '192.168.1.83',
    '192.168.1.86', '192.168.1.81',
    '192.168.1.87'
]

# Permitir mostrar errores detallados en navegador
INTERNAL_IPS = ['127.0.0.1']

# Base de datos local (SQLite)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ==============================
# IA
# ==============================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")

# Desactivar seguridad estricta en cookies y HTTPS
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# 2FA
TWO_FACTOR_ENFORCE_DATE = date(2026, 10, 28)


# ==============================
# Storage: Wasabi también en desarrollo
# ==============================
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
