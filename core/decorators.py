from __future__ import annotations

from functools import wraps
from typing import Callable, Iterable, Optional

from django.apps import apps
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .permissions import PROJECT_PARAM_NAMES, user_has_project_access


def project_access_required(
    *, param_names: Iterable[str] = PROJECT_PARAM_NAMES
) -> Callable:
    """
    Decorador sencillo: busca un parámetro de proyecto en kwargs o GET/POST
    (por defecto: 'proyecto_id', 'project_id', 'proyecto').
    Si existe y el usuario NO tiene acceso, devuelve 403 con usuarios/no_autorizado.html
    """
    param_names = tuple(param_names)

    def _decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            proyecto_id: Optional[int] = None

            # 1) kwargs
            for name in param_names:
                if name in kwargs and kwargs[name] is not None:
                    try:
                        proyecto_id = int(kwargs[name])
                        break
                    except (ValueError, TypeError):
                        pass

            # 2) GET/POST si no vino en kwargs
            if proyecto_id is None:
                for name in param_names:
                    raw = request.GET.get(name) or request.POST.get(name)
                    if raw is not None:
                        try:
                            proyecto_id = int(raw)
                            break
                        except (ValueError, TypeError):
                            pass

            # Si encontramos un proyecto concreto, validar
            if proyecto_id is not None:
                if not user_has_project_access(request.user, proyecto_id):
                    return render(request, "usuarios/no_autorizado.html", status=403)

            return view_func(request, *args, **kwargs)

        return _wrapped

    return _decorator


def project_object_access_required(
    *,
    model: str,
    object_kw: str = "pk",
    project_attr: str = "proyecto_id",
) -> Callable:
    """
    Decorador alternativo para vistas cuyo parámetro NO es el proyecto sino
    un objeto que apunta a un proyecto (ej.: servicio_id).
    - model: string 'app.Model' (se resuelve con apps.get_model)
    - object_kw: nombre del kwarg con el id del objeto
    - project_attr: atributo en el objeto que contiene el ID del proyecto
    """
    def _decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            Model = apps.get_model(model)
            obj_id = kwargs.get(object_kw)
            if obj_id is None:
                # Si no podemos resolver el objeto, dejamos pasar
                return view_func(request, *args, **kwargs)
            try:
                instance = Model.objects.only(project_attr).get(pk=obj_id)
            except Model.DoesNotExist:
                # Si el objeto no existe, dejamos que la vista lo maneje (404)
                return view_func(request, *args, **kwargs)

            proyecto_id = getattr(instance, project_attr, None)
            if proyecto_id is not None:
                try:
                    proyecto_id = int(proyecto_id)
                except (ValueError, TypeError):
                    proyecto_id = None

            if proyecto_id is not None:
                from .permissions import user_has_project_access
                if not user_has_project_access(request.user, proyecto_id):
                    return render(request, "usuarios/no_autorizado.html", status=403)

            return view_func(request, *args, **kwargs)

        return _wrapped
    return _decorator