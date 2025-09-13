from .base import *  # Importa todas las configuraciones base
import os
import dj_database_url

DEBUG = False

ALLOWED_HOSTS = [
    'app-hyperlink-networks.onrender.com',
    'localhost',
    '127.0.0.1',
    '172.20.10.2'
]


# --- Base de datos (Render PostgreSQL) ---
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get(
            'DATABASE_URL', f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
    )
}

# Archivos estáticos
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',

    'whitenoise.middleware.WhiteNoiseMiddleware',  # 2°
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',  # ← primero
    'django.contrib.auth.backends.ModelBackend',
]

# ==============================
# Configuración de Axes
# ==============================

AXES_ENABLED = True
AXES_FAILURE_LIMIT = int(os.environ.get('AXES_FAILURE_LIMIT', 3))
AXES_COOLOFF_TIME = timedelta(minutes=int(
    os.environ.get('AXES_COOLOFF_MINUTES', 20)))
AXES_LOCK_OUT_AT_FAILURE = True
AXES_RESET_ON_SUCCESS = True

AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']
AXES_HANDLER = 'axes.handlers.database.AxesDatabaseHandler'  # simple y robusto
AXES_LOCKOUT_CALLABLE = None           # (dejamos default)
AXES_LOCKOUT_TEMPLATE = 'usuarios/login_bloqueado.html'

# Archivos multimedia (ajustado para Wasabi)
# Aunque Wasabi maneja los archivos, definimos MEDIA_URL apuntando al bucket
MEDIA_URL = f"{os.environ.get('AWS_S3_ENDPOINT_URL')}/{os.environ.get('AWS_STORAGE_BUCKET_NAME')}/"
# Django lo ignora por DEFAULT_FILE_STORAGE
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Seguridad para producción
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = True

# Logs informativos para verificar en consola que todo esté correcto
print("🧪 En producción:")
print("🧪 USE_WASABI:", os.environ.get("AWS_STORAGE_BUCKET_NAME") is not None)
try:
    from django.conf import settings
    print("🧪 DEFAULT_FILE_STORAGE:", settings.DEFAULT_FILE_STORAGE)
except Exception as e:
    print("⚠️ No se pudo importar DEFAULT_FILE_STORAGE:", e)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'ERROR',
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
