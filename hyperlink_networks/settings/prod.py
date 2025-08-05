from .base import *  # Importa todas las configuraciones base
from .base import *
import os

DEBUG = False

ALLOWED_HOSTS = ['app-gz.onrender.com']


DEBUG = False

ALLOWED_HOSTS = ['app-gz.onrender.com']

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

# Archivos multimedia
# Aunque Cloudinary los maneja, Django sigue exigiendo estos valores definidos
MEDIA_URL = '/media/'
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
print("🧪 USE_CLOUDINARY:", os.environ.get("CLOUDINARY_CLOUD_NAME") is not None)
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
            'filename': '/tmp/error.log',  # ✅ Usar directorio temporal de Render
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
