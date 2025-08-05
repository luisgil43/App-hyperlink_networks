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
    'default': dj_database_url.config(default=os.environ.get('DATABASE_URL'))
}

# Archivos estáticos
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',  # Necesario
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # Necesario
    'django.contrib.messages.middleware.MessageMiddleware',  # Necesario
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

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
        'file': {
            'level': 'ERROR',
            'class': 'logging.FileHandler',
            'filename': '/tmp/error.log',  # Directorio temporal de Render
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['file'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
