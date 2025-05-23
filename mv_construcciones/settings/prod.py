from .base import *

DEBUG = False

ALLOWED_HOSTS = ['app-mv.onrender.com']

# Seguridad recomendada para producción
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31536000
SECURE_SSL_REDIRECT = True
