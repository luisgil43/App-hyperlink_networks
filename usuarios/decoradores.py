
from functools import wraps
from django.shortcuts import redirect


def rol_requerido(*roles_esperados, url_redireccion='usuarios:no_autorizado'):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            user = request.user
            if user.is_authenticated:
                if user.is_superuser:
                    return view_func(request, *args, **kwargs)
                if hasattr(user, 'roles') and user.roles.filter(nombre__in=roles_esperados).exists():
                    return view_func(request, *args, **kwargs)
            return redirect(url_redireccion)
        return _wrapped_view
    return decorator


def _noop(*dargs, **dkwargs):
    def _wrap(view):
        return view
    return _wrap


try:
    # type: ignore[reportMissingImports]
    from ratelimit.decorators import ratelimit as _rl
    ratelimit = _rl
except Exception:
    ratelimit = _noop

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
