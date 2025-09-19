# usuarios/middleware.py
import time
from django.conf import settings
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse
from django.contrib.auth import logout


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
