# usuarios/middleware.py
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class SessionExpiryMiddleware:
    """
    Cierra sesión por INACTIVIDAD (SESSION_IDLE_TIMEOUT o IDLE_TIMEOUT_SECONDS)
    y opcionalmente por tiempo ABSOLUTO (SESSION_ABSOLUTE_TIMEOUT).
    Guarda 'last_activity' y 'login_time' en session.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.idle_timeout = int(
            getattr(settings, "SESSION_IDLE_TIMEOUT",
                    getattr(settings, "IDLE_TIMEOUT_SECONDS", 15 * 60))
        )
        self.absolute_timeout = getattr(
            settings, "SESSION_ABSOLUTE_TIMEOUT", None)

        try:
            self.login_path = reverse(
                getattr(settings, "LOGIN_URL_NAME", None) or settings.LOGIN_URL)
        except Exception:
            self.login_path = "/usuarios/login/"

        self.excluded_prefixes = ("/static/", "/media/")

    def __call__(self, request):
        path = request.path or ""

        if path.startswith(self.excluded_prefixes) or path == self.login_path:
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        now = int(time.time())
        session = request.session
        session.setdefault("last_activity", now)
        session.setdefault("login_time", now)

        # Inactividad
        if self.idle_timeout and (now - session["last_activity"] > self.idle_timeout):
            self._logout_with_message(
                request, "Your session was closed due to inactivity.")
            return redirect(self.login_path)

        # Tiempo absoluto
        if self.absolute_timeout and (now - session["login_time"] > int(self.absolute_timeout)):
            self._logout_with_message(
                request, "Your session expired due to the maximum session duration.")
            return redirect(self.login_path)

        session["last_activity"] = now
        return self.get_response(request)

    def _logout_with_message(self, request, msg):
        for k in ("last_activity", "login_time"):
            request.session.pop(k, None)
        try:
            messages.warning(request, msg)
        except Exception:
            pass
        logout(request)


from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import resolve
from django.utils import timezone


class TwoFactorEnforceMiddleware:
    """
    Middleware que obliga a los usuarios staff sin 2FA a ir a la pantalla
    de seguridad cuando la fecha límite ya pasó.

    Si se cumplen TODAS estas condiciones:
      - Usuario autenticado
      - Es staff (is_staff = True)
      - two_factor_enabled = False
      - Hoy >= TWO_FACTOR_ENFORCE_DATE

    Entonces solo se le permite acceder a:
      - Pantalla de configuración de 2FA (two_factor_setup)
      - Logout
      - Recuperación de contraseña (por si acaso)
      - Recursos estáticos / media
    Todo lo demás se redirige a 'usuarios:two_factor_setup'.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        # 1) Si no hay usuario o no está autenticado → no hacemos nada
        if not user or not user.is_authenticated:
            return self.get_response(request)

        # 2) Si no hay fecha configurada → no hacemos nada
        enforce_date = getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None)
        if not enforce_date:
            return self.get_response(request)

        today = timezone.now().date()
        # 3) Si todavía no llegamos a la fecha límite → solo mensaje de login (ya lo manejas allí)
        if today < enforce_date:
            return self.get_response(request)

        # 4) Solo aplica a staff sin 2FA
        if not user.is_staff or getattr(user, "two_factor_enabled", False):
            return self.get_response(request)

        # 5) Permitimos solo algunas vistas para evitar loops
        try:
            resolver_match = resolve(request.path_info)
            view_name = resolver_match.view_name or ""
        except Exception:
            view_name = ""

        allowed_view_names = {
            "usuarios:two_factor_setup",
            "usuarios:recuperar_contraseña",
            "usuarios:resetear_contraseña",
            "usuarios:login_unificado",
            "usuarios:csrf_error_view",
            "dashboard_admin:logout",
            "usuarios:logout",
        }

        # Static y media siempre se permiten
        if request.path.startswith(getattr(settings, "STATIC_URL", "/static/")) \
           or request.path.startswith(getattr(settings, "MEDIA_URL", "/media/")):
            return self.get_response(request)

        if view_name in allowed_view_names:
            return self.get_response(request)

        # 6) En este punto: staff, sin 2FA, fecha vencida, intentando ir a otra vista
        messages.warning(
            request,
            "Two-factor authentication is now mandatory for staff accounts. "
            "Please complete the setup before accessing the platform."
        )
        return redirect("usuarios:two_factor_setup")