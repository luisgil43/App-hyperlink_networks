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
