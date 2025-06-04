import os
from pathlib import Path
import dj_database_url
import logging
from django.urls import reverse_lazy
logging.basicConfig(level=logging.DEBUG)

# Solo carga dotenv si estás en desarrollo local
if os.environ.get("DJANGO_DEVELOPMENT") == "true":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("Advertencia: python-dotenv no está instalado. Ignorando .env.")

# Ruta base del proyecto
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# URL genérica para login (puede redirigir a un login común)
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'  # Después de logout, ir al login
AUTH_USER_MODEL = 'usuarios.CustomUser'


# Seguridad
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'clave-insegura')
DEBUG = os.environ.get('DEBUG', 'False') == 'True'
ALLOWED_HOSTS = ['app-mv.onrender.com']

INSTALLED_APPS = [

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_select2',

    # Tus apps

    'liquidaciones',
    'tecnicos',
    'dashboard',
    'cloudinary',
    'cloudinary_storage',
    'operaciones',
    'prevencion',
    'rrhh',
    'logistica',
    'subcontrato',
    'facturacion',
    'usuarios',
    'dashboard_admin.apps.DashboardAdminConfig',
    'dal',
    'dal_select2',

]


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'mv_construcciones.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'mv_construcciones.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-es'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Archivos estáticos
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ✅ Archivos multimedia - Definimos MEDIA_ROOT y MEDIA_URL SIEMPRE
MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# ✅ Cloudinary en producción, disco local en desarrollo
# Se activa solo si todas las variables están presentes
USE_CLOUDINARY = all([
    os.environ.get("CLOUDINARY_CLOUD_NAME"),
    os.environ.get("CLOUDINARY_API_KEY"),
    os.environ.get("CLOUDINARY_API_SECRET"),
])

if USE_CLOUDINARY:
    DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'
    CLOUDINARY_STORAGE = {
        'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME'),
        'API_KEY': os.environ.get('CLOUDINARY_API_KEY'),
        'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET'),
    }
else:
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    # Puedes mostrar una advertencia opcional si estás en desarrollo
    if DEBUG:
        print("⚠️ Cloudinary desactivado: usando almacenamiento local")

# Correo electrónico
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = f"MV Construcciones <{EMAIL_HOST_USER}>"

# HTTPS en Render
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
