import os
from datetime import date, timedelta
from pathlib import Path

import dj_database_url
from django.urls import reverse_lazy
from dotenv import load_dotenv

# ==============================
# BASE CONFIG
# ==============================
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Cargar variables de entorno:
# 1) .env (base)  2) .env.local (override en DEV)
load_dotenv(BASE_DIR / ".env", override=False)
load_dotenv(BASE_DIR / ".env.local", override=True)

DEBUG = os.environ.get("DEBUG", "False").strip().lower() == "true"

if DEBUG:
    SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-key")
else:
    SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]


ALLOWED_HOSTS = [
    'app-hyperlink-networks.onrender.com',
    'localhost',
    '127.0.0.1',
    '172.20.10.2',
    '0.0.0.0'

]

# Confiar en tu dominio para CSRF (producción)
CSRF_TRUSTED_ORIGINS = [
    'https://app-hyperlink-networks.onrender.com',
]

# Cookies seguras en prod
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


# ==============================
# APPLICATIONS
# ==============================
INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # WhiteNoise: desactiva el static del runserver y deja que WhiteNoise sirva estáticos
    "whitenoise.runserver_nostatic",
    # Third-party
    "django_select2",
    "storages",
    "dal",
    "dal_select2",
    "widget_tweaks",
    "django.contrib.humanize",
    "axes",
    # 'ratelimit',
    # Local apps
    "liquidaciones",
    "dashboard",
    "borelogs",
    "core",
    "fleet",
    "notifications",
    "operaciones",
    "prevencion",
    "underground",
    "rrhh",
    "logistica",
    "subcontrato",
    "facturacion",
    "invoicing",
    "usuarios",
    "cable_installation",
    "dashboard_admin.apps.DashboardAdminConfig",
]


AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',  # ← primero
    'django.contrib.auth.backends.ModelBackend',
]


# ==============================
# MIDDLEWARE
# ==============================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',

    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',

    # 👇 El mensaje flash necesita que este middleware vaya antes
    'django.contrib.messages.middleware.MessageMiddleware',

    # 👇 Middlewares propios que usan sesión / mensajes
    'usuarios.middleware.SessionExpiryMiddleware',
    'usuarios.middleware.TwoFactorEnforceMiddleware',

    # 👇 Lógica de proyectos (solo se ejecuta si la sesión sigue viva y 2FA ok)
    "core.middleware.ProjectAccessMiddleware",

    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # 👇 Axes debe ser el ÚLTIMO
    'axes.middleware.AxesMiddleware',
]


RATELIMIT_USE_CACHE = 'default'
# ==============================
# Configuración de Axes
# ==============================

AXES_ENABLED = True
AXES_FAILURE_LIMIT = int(os.environ.get('AXES_FAILURE_LIMIT', 3))
AXES_COOLOFF_TIME = timedelta(minutes=int(
    os.environ.get('AXES_COOLOFF_MINUTES', 15)))
AXES_LOCK_OUT_AT_FAILURE = True
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']
AXES_HANDLER = 'axes.handlers.database.AxesDatabaseHandler'  # simple y robusto
AXES_LOCKOUT_CALLABLE = None           # (dejamos default)
AXES_LOCKOUT_TEMPLATE = 'usuarios/login_bloqueado.html'
AXES_IPWARE_META_PRECEDENCE_ORDER = ['HTTP_X_FORWARDED_FOR', 'REMOTE_ADDR']
# ==============================
# URLS & WSGI
# ==============================
ROOT_URLCONF = 'hyperlink_networks.urls'
WSGI_APPLICATION = 'hyperlink_networks.wsgi.application'

# ==============================
# TEMPLATES
# ==============================
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
                'usuarios.context_processors.notificaciones_context',
                'usuarios.context_processors.ui_mode_context',
            ],
        },
    },
]

# ==============================
# DATABASE
# ==============================
DATABASES = {
    'default': dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
    )
}

# Opciones para SQLite (más tolerancia a bloqueos)
if 'sqlite' in DATABASES['default']['ENGINE']:
    DATABASES['default'].setdefault('OPTIONS', {})
    DATABASES['default']['OPTIONS'].setdefault('timeout', 30)  # segundos

# ==============================
# PASSWORD VALIDATION
# ==============================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ==============================
# INTERNATIONALIZATION
# ==============================
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/New_York'  # Producción en EE.UU.
USE_I18N = True
USE_TZ = True

# ==============================
# STATIC & MEDIA
# ==============================
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = "/media/"
MEDIA_ROOT = os.path.join(BASE_DIR, "media")

# ==============================
# CRON
# ==============================


PLANIX_LOGO_URL = os.getenv(
    "PLANIX_LOGO_URL",
    "https://res.cloudinary.com/dm6gqg4fb/image/upload/v1751574704/planixb_a4lorr.jpg",
)

# Tokens cron
FLOTA_CRON_TOKEN = os.environ.get("FLOTA_CRON_TOKEN", "")
CRON_GENERAL_TOKEN = os.environ.get("CRON_GENERAL_TOKEN", "")

# ==============================
# AUTH & LOGIN
# ==============================
AUTH_USER_MODEL = 'usuarios.CustomUser'
LOGIN_URL = reverse_lazy('usuarios:login_unificado')
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/usuarios/login/'


# ==============================
# STORAGE (Wasabi S3)
# ==============================
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_STORAGE_BUCKET_NAME = os.environ.get(
    'AWS_STORAGE_BUCKET_NAME', 'hyperlink-networks')
AWS_S3_ENDPOINT_URL = os.environ.get(
    'AWS_S3_ENDPOINT_URL', 'https://s3.us-east-1.wasabisys.com')
AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')

AWS_DEFAULT_ACL = None
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
AWS_S3_FILE_OVERWRITE = False

# Recomendado para Wasabi
AWS_S3_SIGNATURE_VERSION = "s3v4"
AWS_S3_ADDRESSING_STYLE = "path"  # Wasabi funciona mejor con path-style
AWS_S3_USE_SSL = True
AWS_S3_VERIFY = True

# URLs firmadas (recursos privados)
AWS_QUERYSTRING_AUTH = True

# Parámetros por defecto para objetos subidos desde Django
AWS_S3_OBJECT_PARAMETERS = {
    "CacheControl": "max-age=31536000, public",
}

# ==============================
# DIRECT UPLOADS (feature flags)
# ==============================
# Apagado por defecto. En Render: DIRECT_UPLOADS_ENABLED=1
DIRECT_UPLOADS_ENABLED = os.environ.get("DIRECT_UPLOADS_ENABLED", "0") == "1"

# Límite de tamaño por archivo (MB) al pedir presign
DIRECT_UPLOADS_MAX_MB = int(os.environ.get("DIRECT_UPLOADS_MAX_MB", "15"))

# Prefijo seguro (tus evidencias viven ahí)
DIRECT_UPLOADS_SAFE_PREFIX = os.environ.get(
    "DIRECT_UPLOADS_SAFE_PREFIX",
    "operaciones/reporte_fotografico/"
)

# Aliases Wasabi para reutilizar los mismos valores ya definidos en AWS_*
WASABI_BUCKET_NAME = AWS_STORAGE_BUCKET_NAME
WASABI_ENDPOINT_URL = AWS_S3_ENDPOINT_URL
WASABI_REGION_NAME = AWS_S3_REGION_NAME
WASABI_ACCESS_KEY_ID = AWS_ACCESS_KEY_ID
WASABI_SECRET_ACCESS_KEY = AWS_SECRET_ACCESS_KEY

# ==============================
# EMAIL (SMTP)
# ==============================
import os

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)

EMAIL_HOST = os.environ.get("EMAIL_HOST", "mail.grupogzs.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 465))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "False").strip().lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "True").strip().lower() == "true"

EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
# ==============================
# 2FA
# ==============================
TWO_FACTOR_ISSUER_NAME = "Hyperlink Networks"
TWO_FACTOR_ENFORCE_DATE = None

# ==============================
# SECURITY
# ==============================
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_FAILURE_VIEW = 'usuarios.views.csrf_error_view'
CORE_BYPASS_ROLES = ["admin"]
CORE_PROJECT_PARAM_NAMES = ("proyecto_id", "project_id", "proyecto")
# ==============================
# DEFAULTS
# ==============================
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Sesión
IDLE_TIMEOUT_SECONDS = 15 * 60          # 15 minutos de inactividad
SESSION_ABSOLUTE_TIMEOUT = None         # Ej: 8*60*60 para 8h si lo necesitas
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_SAVE_EVERY_REQUEST = False
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 24 * 7  # 7 días (ajusta)
MESSAGE_STORAGE = 'django.contrib.messages.storage.cookie.CookieStorage'
if DEBUG:
    CSRF_COOKIE_HTTPONLY = False
else:
    CSRF_COOKIE_HTTPONLY = True

SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name} :: {message}",
            "style": "{",
        },
        "simple": {"format": "[{levelname}] {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        # Eventos de django-axes (intentos fallidos, bloqueos, etc.)
        "axes": {
            "handlers": ["console"],
            "level": "INFO",   # DEBUG si quieres más ruido
            "propagate": False,
        },
        # Autenticación Django (login/logout, permisos)
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.auth": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        # Tu app de usuarios (para auditar el login unificado)
        "usuarios": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
