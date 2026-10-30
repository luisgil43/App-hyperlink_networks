
from functools import wraps
from django.shortcuts import redirect


def rol_requerido(*roles_esperados, url_redireccion='usuarios:no_autorizado'):
    """
    Comprueba el rol y, para permisos administrativos,
    exige autorización administrativa vigente.

    Las vistas exclusivamente técnicas conservan su flujo.
    """
    import time

    from django.conf import settings
    from django.http import JsonResponse
    from django.shortcuts import redirect

    admin_roles = {
        "admin",
        "rrhh",
        "pm",
        "supervisor",
        "facturacion",
        "logistica",
        "subcontrato",
        "flota",
        "prevencion",
        "bodeguero",
        "emision_facturacion",
    }

    expected_roles = {
        str(role).strip().lower()
        for role in roles_esperados
    }

    def decorator(view_func):
        # Esta vista muestra las asignaciones propias del técnico.
        # Admitir roles administrativos no la convierte en una
        # operación administrativa.
        technical_billing_view = (
            getattr(view_func, "__module__", "")
            == "operaciones.views_billing_exec"
            and getattr(view_func, "__name__", "")
            == "mis_assignments"
        )

        administrative_view = (
            bool(expected_roles & admin_roles)
            and not technical_billing_view
        )

        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user

            if not user.is_authenticated:
                return redirect(url_redireccion)

            permitted = user.is_superuser

            if not permitted and hasattr(user, "roles"):
                permitted = user.roles.filter(
                    nombre__in=roles_esperados
                ).exists()

            if not permitted:
                return redirect(url_redireccion)

            if administrative_view:
                now = int(time.time())
                timeout = int(
                    getattr(
                        settings,
                        "IDLE_TIMEOUT_SECONDS",
                        15 * 60,
                    )
                )

                previous = request.session.get(
                    "admin_last_activity"
                )

                try:
                    previous = int(previous)
                except (TypeError, ValueError):
                    previous = None

                valid = (
                    previous is not None
                    and previous <= now
                    and now - previous <= timeout
                )

                if not valid:
                    request.session.pop(
                        "admin_last_activity",
                        None,
                    )
                    request.session.modified = True

                    if request.headers.get(
                        "X-Requested-With"
                    ) == "XMLHttpRequest":
                        return JsonResponse(
                            {
                                "ok": False,
                                "error": (
                                    "Administrative authentication "
                                    "is required."
                                ),
                                "reauth_required": True,
                            },
                            status=403,
                        )

                    return redirect(
                        "usuarios:admin_reauthenticate"
                    )

            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def _noop(*dargs, **dkwargs):
    def _wrap(view):
        return view
    return _wrap


from django_ratelimit.decorators import ratelimit

try:
    # type: ignore[reportMissingImports]
    from axes.decorators import axes_dispatch as _axes
    axes_dispatch = _axes
except Exception:
    axes_dispatch = _noop


def axes_post_only(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.method.upper() == 'POST':
            # aplica Axes solo en el POST
            return axes_dispatch(view_func)(request, *args, **kwargs)
        return view_func(request, *args, **kwargs)
    return _wrapped
