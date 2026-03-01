import json

from django.db.models import Q  # ✅ Esto faltaba

from .models import Notificacion


def notificaciones_context(request):
    if request.user.is_authenticated:
        queryset = Notificacion.objects.filter(
            usuario=request.user).order_by('leido', '-fecha')
        return {
            'notificaciones_no_leidas': queryset.filter(leido=False).count(),
            'notificaciones_recientes': queryset[:10]
        }
    return {}


# usuarios/context_processors.py

def ui_mode_context(request):
    """
    Expone ui_mode y can_switch_ui_mode a TODOS los templates.

    - can_switch_ui_mode: SOLO si el usuario tiene rol usuario + rol admin.
    """
    user = getattr(request, "user", None)

    try:
        ui_mode = (request.session.get("ui_mode") or "user").lower()
    except Exception:
        ui_mode = "user"

    if not user or not getattr(user, "is_authenticated", False):
        return {
            "ui_mode": ui_mode,
            "ui_mode_is_admin": ui_mode == "admin",
            "ui_mode_is_user": ui_mode == "user",
            "can_switch_ui_mode": False,
        }

    roles_nombres = []
    try:
        if hasattr(user, "roles"):
            roles_nombres = list(user.roles.values_list("nombre", flat=True))
    except Exception:
        roles_nombres = []

    roles = {str(r).strip().lower() for r in roles_nombres if r}

    has_user_role = ("usuario" in roles)

    admin_roles_set = {
        "admin", "rrhh", "pm", "supervisor", "facturacion",
        "logistica", "subcontrato", "flota", "prevencion", "bodeguero",
        "emision_facturacion",
    }
    has_admin_role = bool(roles.intersection(admin_roles_set)) or user.is_staff or user.is_superuser

    can_switch_ui_mode = bool(has_user_role and has_admin_role)

    return {
        "ui_mode": ui_mode,
        "ui_mode_is_admin": ui_mode == "admin",
        "ui_mode_is_user": ui_mode == "user",
        "can_switch_ui_mode": can_switch_ui_mode,
    }