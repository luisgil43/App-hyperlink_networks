from __future__ import annotations

from typing import Iterable, Optional

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from .permissions import PROJECT_PARAM_NAMES, user_has_project_access


class ProjectAccessMiddleware:
    """
    Middleware de autoprotección suave:
    - Si en la request aparece un parámetro que aparenta ser 'id de proyecto'
      (por defecto: 'proyecto_id', 'project_id', 'proyecto'), valida acceso.
    - Si el usuario NO tiene acceso, retorna 403 con 'usuarios/no_autorizado.html'.

    No hace nada si:
    - No hay parámetro de proyecto en la request
    - El usuario tiene bypass global (lo maneja user_has_project_access)
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.param_names: Iterable[str] = getattr(
            settings,
            "CORE_PROJECT_PARAM_NAMES",
            PROJECT_PARAM_NAMES,
        )

    def _extract_project_id(self, request: HttpRequest) -> Optional[int]:
        # 1) kwargs (cuando la vista ya resolvió la URL)
        resolver_match = getattr(request, "resolver_match", None)
        if resolver_match and resolver_match.kwargs:
            for name in self.param_names:
                if name in resolver_match.kwargs:
                    try:
                        return int(resolver_match.kwargs[name])
                    except (ValueError, TypeError):
                        pass

        # 2) Querystring / POST
        for name in self.param_names:
            raw = request.GET.get(name) or request.POST.get(name)
            if raw is not None:
                try:
                    return int(raw)
                except (ValueError, TypeError):
                    pass

        return None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        proyecto_id = self._extract_project_id(request)

        if proyecto_id is not None:
            if not user_has_project_access(request.user, proyecto_id):
                return render(request, "usuarios/no_autorizado.html", status=403)

        return self.get_response(request)