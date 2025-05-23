from .base import *

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# Opcional: permite mostrar errores detallados en el navegador
INTERNAL_IPS = ['127.0.0.1']

# Si usas SQLite para desarrollo local
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# No usar HTTPS obligatorio ni cookies seguras en desarrollo
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False

# Email simulado (no se envían realmente)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
