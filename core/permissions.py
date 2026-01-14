from __future__ import annotations

from functools import wraps
from typing import Iterable, Optional, Set

from django.apps import apps
from django.conf import settings
from django.db import models
from django.db import models as dj_models
from django.utils import timezone

# Intentamos ubicar modelos sin acoplar el proyecto
try:
    from usuarios.models import \
        ProyectoAsignacion  # through con include_history/start_at
except Exception:
    ProyectoAsignacion = None  # type: ignore

try:
    from facturacion.models import Proyecto
except Exception:
    Proyecto = None  # type: ignore


# === Configuración por settings (opcionales) ===
BYPASS_ROLES: Set[str] = set(getattr(settings, "CORE_BYPASS_ROLES", ["admin"]))
PROJECT_PARAM_NAMES: Iterable[str] = getattr(
    settings,
    "CORE_PROJECT_PARAM_NAMES",
    ("proyecto_id", "project_id", "proyecto"),
)


def _user_has_role(user, role_names: Iterable[str]) -> bool:
    # Soporta M2M 'roles' (modelo Rol con nombre)
    try:
        return user.roles.filter(nombre__in=list(role_names)).exists()
    except Exception:
        return False


def user_has_global_bypass(user) -> bool:
    """
    Bypass global si es superuser o tiene alguno de los roles en CORE_BYPASS_ROLES.
    Por defecto, solo 'admin' (además de superuser).
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    if BYPASS_ROLES and _user_has_role(user, BYPASS_ROLES):
        return True
    return False


def user_has_project_access(user, proyecto_id: Optional[int]) -> bool:
    """
    Regresa True si el usuario puede ver el proyecto indicado.
    - Superuser o rol-bypass => True
    - Con through ProyectoAsignacion:
        include_history=True => acceso
        include_history=False => acceso si start_at <= ahora
    - Con M2M directo user.proyectos => acceso si está asociado
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if proyecto_id is None:
        # Si no hay proyecto específico, no negamos (se valida en decorador/middleware solo si existe param)
        return True
    if user_has_global_bypass(user):
        return True

    # A través de ProyectoAsignacion
    if ProyectoAsignacion is not None:
        now = timezone.now()
        try:
            exists = ProyectoAsignacion.objects.filter(
                usuario=user, proyecto_id=proyecto_id
            ).filter(
                models.Q(include_history=True)
                | models.Q(include_history=False, start_at__lte=now)
            ).exists()
            if exists:
                return True
        except Exception:
            pass

    # M2M directo en el usuario
    try:
        if hasattr(user, "proyectos") and user.proyectos.filter(id=proyecto_id).exists():
            return True
    except Exception:
        pass

    return False


def projects_ids_for_user(user) -> Set[int]:
    """
    Retorna el conjunto de IDs de proyectos a los que el usuario tiene acceso.
    Si tiene bypass global, devuelve todos (si el modelo Proyecto está disponible).
    """
    ids: Set[int] = set()
    if not getattr(user, "is_authenticated", False):
        return ids

    # Bypass => todos los proyectos si es posible
    if user_has_global_bypass(user) and Proyecto is not None:
        try:
            return set(Proyecto.objects.values_list("id", flat=True))
        except Exception:
            pass

    # Through ProyectoAsignacion
    if ProyectoAsignacion is not None:
        now = timezone.now()
        try:
            asign_ids = ProyectoAsignacion.objects.filter(
                usuario=user
            ).filter(
                models.Q(include_history=True)
                | models.Q(include_history=False, start_at__lte=now)
            ).values_list("proyecto_id", flat=True)
            ids.update(asign_ids)
        except Exception:
            pass

    # M2M directo
    try:
        if hasattr(user, "proyectos"):
            ids.update(user.proyectos.values_list("id", flat=True))
    except Exception:
        pass

    return ids


def filter_queryset_by_access(qs, user, project_lookup: str) -> models.QuerySet:
    """
    Filtra un queryset por los proyectos a los que el usuario tiene acceso.
    - qs: QuerySet a filtrar
    - project_lookup: lookup al campo del proyecto (ej: 'proyecto_id', 'sesion__proyecto_id')
    """
    if user_has_global_bypass(user):
        return qs
    allowed = projects_ids_for_user(user)
    if not allowed:
        # Sin acceso => queryset vacío
        return qs.none()
    return qs.filter(**{f"{project_lookup}__in": list(allowed)})

from functools import wraps


def _extract_project_id_from_request(request, kwargs, param_names) -> int | None:
    """Busca un proyecto_id en kwargs o GET/POST usando los nombres configurados."""
    for name in param_names:
        val = kwargs.get(name) or request.GET.get(name) or request.POST.get(name)
        if val:
            try:
                return int(val)
            except (ValueError, TypeError):
                continue
    return None


def _extract_project_id_from_object(object_kw: str | None,
                                    model_label: str | None,
                                    project_attr: str,
                                    kwargs) -> int | None:
    """
    Si se pasa object_kw y model_label, carga la instancia y devuelve el project id.
    - model_label: 'app_label.ModelName'
    - project_attr: ej. 'proyecto_id' o 'proyecto'
    """
    if not object_kw or not model_label:
        return None
    try:
        Model = apps.get_model(model_label)
        pk = kwargs.get(object_kw)
        if pk is None:
            return None
        obj = Model.objects.only(project_attr).get(pk=pk)
        value = getattr(obj, project_attr, None)
        # Si project_attr es FK ('proyecto'), obtener su id
        if isinstance(value, dj_models.Model):
            return getattr(value, "id", None)
        # Si ya es *_id, devolver tal cual
        if isinstance(value, int):
            return value
        # Cadenas numéricas
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    except Exception:
        return None


def project_object_access_required(_view_func=None, *,
                                   model: str | None = None,
                                   object_kw: str | None = None,
                                   project_attr: str = "proyecto_id",
                                   request_param_names=PROJECT_PARAM_NAMES):
    """
    Decorador compatible con:
      @project_object_access_required
      @project_object_access_required(model='app.Model', object_kw='pk', project_attr='proyecto_id')

    Lógica:
    - Si podemos deducir un proyecto_id desde el objeto (model+object_kw) o desde parámetros de request/kwargs,
      validamos con user_has_project_access. Si NO tiene acceso -> 403.
    - Si no podemos deducir proyecto_id, dejamos pasar (las vistas de listados deberían filtrar vía filter_queryset_by_access).
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            # bypass global (superuser/roles)
            if user_has_global_bypass(request.user):
                return view_func(request, *args, **kwargs)

            proyecto_id = None

            # 1) Intentar desde objeto (detalle/edición)
            proyecto_id = _extract_project_id_from_object(object_kw, model, project_attr, kwargs)

            # 2) Intentar desde parámetros (listados u otras vistas)
            if proyecto_id is None:
                proyecto_id = _extract_project_id_from_request(request, kwargs, request_param_names)

            # 3) Validar acceso si tenemos un id concreto
            if proyecto_id is not None and not user_has_project_access(request.user, proyecto_id):
                from django.http import HttpResponseForbidden
                return HttpResponseForbidden("You don't have access to this project.")

            # 4) Si no hay proyecto deducible, dejar pasar (la vista aplicará filtro a nivel de queryset)
            return view_func(request, *args, **kwargs)
        return _wrapped

    # Uso sin paréntesis: @project_object_access_required
    if callable(_view_func):
        return decorator(_view_func)

    # Uso con kwargs: @project_object_access_required(...)
    return decorator


from django.db.models import Q
from django.utils import timezone

from usuarios.models import ProyectoAsignacion


def filter_queryset_by_assignment_history(qs, user, project_field: str, date_field: str):
    """
    Restringe un queryset por asignaciones ProyectoAsignacion:
      - include_history=True  -> ve todo el historial del proyecto
      - include_history=False -> ve solo desde start_at (inclusive)

    project_field: nombre del campo en el modelo qs (ej: 'proyecto_id')
    date_field:    nombre del campo fecha en el modelo qs (ej: 'fecha' o 'creado_en')
    """
    asignaciones = ProyectoAsignacion.objects.filter(usuario=user)
    if not asignaciones.exists():
        return qs.none()

    # Detectar si date_field es DateField o DateTimeField
    model = qs.model
    field = model._meta.get_field(date_field)
    is_datefield = field.get_internal_type() == "DateField"

    cond = Q()
    for a in asignaciones:
        pid = a.proyecto_id

        # history completo
        if a.include_history or not a.start_at:
            cond |= Q(**{project_field: pid})
            continue

        # desde start_at
        start_val = a.start_at
        if is_datefield:
            start_val = start_val.date()

        cond |= Q(**{
            project_field: pid,
            f"{date_field}__gte": start_val,
        })

    return qs.filter(cond)