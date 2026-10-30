# usuarios/middleware.py
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class SessionExpiryMiddleware:
    """
    Control de inactividad independiente por interfaz.

    La actividad técnica nunca renueva el contador administrativo.
    """

    TECHNICIAN_EXACT_PATHS = frozenset({
        "/dashboard/",
        "/dashboard/inicio/",
        "/dashboard/dashboard/",
        "/operaciones/mis-rendiciones/",
        "/operaciones/billing/my/",
        "/operaciones/produccion/mia/",
        "/operaciones/mi-produccion/pagos/",
        "/operaciones/project-downloads/",
        "/rrhh/mis-contratos/",
        "/rrhh/mis-documentos/",
        "/rrhh/vacaciones/",
        "/rrhh/fichas/ingreso/",
        "/rrhh/adelantos/usuario/",
        "/rrhh/my-rate-sheets/",
    })

    TECHNICIAN_PREFIXES = (
        "/dashboard/",
        "/api/billing/my/",
        "/operaciones/billing/my/",
        "/operaciones/mi-produccion/pagos/",
        "/operaciones/mis-rendiciones/",
        "/operaciones/mis-assignments/",
        "/cable-installation/assignment/",
        "/cable-installation/assignment-row/",
        "/maps/technician/",
    )

    BACKGROUND_SUFFIXES = (
        "/status/",
        "/status-json/",
        "/requirements-status/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

        self.admin_timeout = int(
            getattr(
                settings,
                "SESSION_IDLE_TIMEOUT",
                getattr(settings, "IDLE_TIMEOUT_SECONDS", 15 * 60),
            )
        )

        self.technician_timeout = (
            int(getattr(settings, "TECHNICIAN_SESSION_TIMEOUT_HOURS", 6))
            * 60 * 60
        )

        self.absolute_timeout = getattr(
            settings, "SESSION_ABSOLUTE_TIMEOUT", None
        )

        try:
            self.login_path = reverse(
                getattr(settings, "LOGIN_URL_NAME", None)
                or settings.LOGIN_URL
            )
        except Exception:
            self.login_path = "/usuarios/login/"

        self.excluded_prefixes = ("/static/", "/media/")

    def _has_technician_role(self, user):
        try:
            return user.roles.filter(nombre="usuario").exists()
        except (AttributeError, TypeError, ValueError):
            return False

    def _is_technician_request(self, request):
        if request.session.get("ui_mode") != "user":
            return False

        if not self._has_technician_role(request.user):
            return False

        path = request.path or ""

        return (
            path in self.TECHNICIAN_EXACT_PATHS
            or path.startswith(self.TECHNICIAN_PREFIXES)
        )

    def _is_background(self, request):
        path = request.path or ""

        if path.startswith("/usuarios/notificacion/"):
            return True

        return path.endswith(self.BACKGROUND_SUFFIXES)

    def __call__(self, request):
        path = request.path or ""

        # El cambio de interfaz y la selección de rol no renuevan
        # automáticamente el contador administrativo.
        if path in (
            "/usuarios/ui-mode/toggle/",
            "/usuarios/seleccionar-rol/",
            "/usuarios/admin/reauth/",
            "/dashboard_admin/logout/",
            "/usuarios/logout/",
        ):
            return self.get_response(request)

        if (
            path.startswith(self.excluded_prefixes)
            or path == self.login_path
        ):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        now = int(time.time())
        session = request.session

        session.setdefault("login_time", now)

        if self.absolute_timeout and (
            now - int(session["login_time"])
            > int(self.absolute_timeout)
        ):
            self._logout_with_message(
                request,
                "Your session expired due to the maximum session duration.",
            )
            return redirect(self.login_path)

        # Las rutas administrativas requieren autorización propia.
        # El modo visual nunca constituye una autorización.
        admin_route = (
            path.startswith("/dashboard_admin/")
            or path.startswith("/admin/")
        )

        if path.startswith("/dashboard/rrhh/liquidaciones/admin/"):
            admin_route = True

        if not admin_route:
            try:
                from django.urls import resolve

                callback = resolve(path).func
                visited = set()

                while callback is not None and id(callback) not in visited:
                    visited.add(id(callback))

                    closure = getattr(callback, "__closure__", None)
                    code = getattr(callback, "__code__", None)

                    if closure and code:
                        values = dict(
                            zip(
                                code.co_freevars,
                                (cell.cell_contents for cell in closure),
                            )
                        )

                        if values.get("administrative_view") is True:
                            admin_route = True
                            break

                    callback = getattr(callback, "__wrapped__", None)

            except Exception:
                # La vista conservará sus propios controles de acceso.
                pass

        if admin_route:
            admin_previous = session.get("admin_last_activity")

            try:
                admin_previous = int(admin_previous)
            except (TypeError, ValueError):
                admin_previous = None

            admin_valid = (
                admin_previous is not None
                and admin_previous <= now
                and now - admin_previous <= self.admin_timeout
            )

            if not admin_valid:
                return redirect("usuarios:admin_reauthenticate")

        technician_request = not admin_route

        if technician_request:
            key = "technician_last_activity"
            timeout = self.technician_timeout

            previous = session.get(key)

            if previous is None:
                # Sesión técnica anterior al cambio de contadores.
                # Evitar el ciclo login -> dashboard -> login:
                # cerrar la sesión autenticada antes de redirigir.
                self._logout_with_message(
                    request,
                    "Your previous session expired. Please sign in again.",
                )
                return redirect(self.login_path)

        else:
            key = "admin_last_activity"
            timeout = self.admin_timeout

            previous = session.get(key)

            if previous is None:
                # Nunca utilizar la actividad técnica para renovar
                # el contador administrativo.
                return redirect("usuarios:admin_reauthenticate")

        if timeout and now - int(previous) > timeout:
            if not technician_request:
                session.pop("admin_last_activity", None)
                session.modified = True

            if not technician_request and self._has_technician_role(request.user):
                technician_previous = session.get(
                    "technician_last_activity"
                )

                technician_valid = False

                try:
                    technician_previous = int(technician_previous)
                    technician_valid = (
                        technician_previous <= now
                        and now - technician_previous <= self.technician_timeout
                    )
                except (TypeError, ValueError):
                    technician_valid = False

                if technician_valid:
                    session["ui_mode"] = "user"
                    session.modified = True

                    messages.warning(
                        request,
                        "Administrator access expired. "
                        "Please authenticate again before returning to Administration.",
                    )

                    return redirect("usuarios:admin_reauthenticate")

            if not technician_request:
                session["ui_mode"] = "user"
                session.modified = True
                messages.warning(
                    request,
                    "Administrator access expired. "
                    "Please authenticate again.",
                )
                return redirect("usuarios:admin_reauthenticate")

            self._logout_with_message(
                request,
                "Your session was closed due to inactivity.",
            )

            return redirect(self.login_path)

        if not self._is_background(request):
            if technician_request:
                session["technician_last_activity"] = now
                session["last_activity"] = now
            elif admin_route:
                # Solo actividad administrativa autorizada.
                session["admin_last_activity"] = now

        return self.get_response(request)

    def _logout_with_message(self, request, msg):
        for key in (
            "last_activity",
            "login_time",
            "technician_last_activity",
            "admin_last_activity",
        ):
            request.session.pop(key, None)

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

        # El acceso técnico no exige configurar 2FA.
        technician_role = (
            hasattr(user, "roles")
            and user.roles.filter(nombre="usuario").exists()
        )

        # Las funciones de usuario no requieren 2FA administrativo.
        # El middleware de sesión determina las rutas administrativas.
        if technician_role and request.session.get("ui_mode") == "user":
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