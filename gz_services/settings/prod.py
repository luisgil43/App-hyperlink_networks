"""from .base import *  # Importa todas las configuraciones base
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

# Middleware para WhiteNoise (importante para servir archivos en Render)
MIDDLEWARE.insert(
    MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
    'whitenoise.middleware.WhiteNoiseMiddleware'
)

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
"""

from django.contrib.auth import get_user_model
from .base import *  # Importa todas las configuraciones base
import os
import django
django.setup()

DEBUG = False

ALLOWED_HOSTS = ['app-gz.onrender.com']

# Archivos estáticos
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Middleware para WhiteNoise
MIDDLEWARE.insert(
    MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
    'whitenoise.middleware.WhiteNoiseMiddleware'
)

# Archivos multimedia
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Seguridad
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = True

# Logs informativos
print("🧪 En producción:")
print("🧪 USE_CLOUDINARY:", os.environ.get("CLOUDINARY_CLOUD_NAME") is not None)
try:
    from django.conf import settings
    print("🧪 DEFAULT_FILE_STORAGE:", settings.DEFAULT_FILE_STORAGE)
except Exception as e:
    print("⚠️ No se pudo importar DEFAULT_FILE_STORAGE:", e)

# Crear superusuario automáticamente en producción
User = get_user_model()

if not User.objects.filter(username='admin').exists():
    print("⚙️  Creando superusuario automáticamente en producción...")
    User.objects.create_superuser(
        username='admin',
        email='luisggil01@gmail.com',
        password='luis1992',
        identidad='267246793'  # Campo personalizado obligatorio
    )
else:
    print("✅ El superusuario ya existe.")
