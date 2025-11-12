# core/mixins.py
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render

from core.permissions import (PROJECT_PARAM_NAMES, user_has_global_bypass,
                              user_has_project_access)


class ProjectAccessRequiredMixin:
    """
    Protege CBV por proyecto. Configura en tu vista:
      - project_param_names = ('proyecto_id',)  # o usa settings por defecto
      - resolver = callable(request, view) -> project_id (opcional)
      - deny_template = 'usuarios/no_autorizado.html'
      - required = True/False (si exige que venga algún project_id)
      - allow_bypass = True (respeta superuser/CORE_BYPASS_ROLES)
    """
    project_param_names = None
    resolver = None
    deny_template = 'usuarios/no_autorizado.html'
    required = True
    allow_bypass = True

    def _resolve_project_id(self):
        # Resolver explícito (si se configuró)
        if callable(self.resolver):
            try:
                pid = self.resolver(self.request, self)
            except TypeError:
                pid = self.resolver(self.request)
            if pid is not None:
                return pid

        names = self.project_param_names or getattr(
            settings,
            "CORE_PROJECT_PARAM_NAMES",
            PROJECT_PARAM_NAMES,
        )

        # kwargs
        for n in names:
            if n in self.kwargs:
                return self.kwargs.get(n)
        # GET
        for n in names:
            if n in self.request.GET:
                return self.request.GET.get(n)
        # POST
        for n in names:
            if n in self.request.POST:
                return self.request.POST.get(n)

        return None

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        # Bypass unificado (superuser o roles en CORE_BYPASS_ROLES)
        if self.allow_bypass and user_has_global_bypass(user):
            return super().dispatch(request, *args, **kwargs)

        # Resolver proyecto y validar acceso
        pid = self._resolve_project_id()
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = None

        if pid is None and self.required:
            return render(
                request,
                self.deny_template,
                {"motivo": "missing_project_id"},
                status=403,
            )

        if pid is not None and not user_has_project_access(user, pid):
            return render(
                request,
                self.deny_template,
                {"motivo": "project_denied", "proyecto_id": pid},
                status=403,
            )

        return super().dispatch(request, *args, **kwargs)