# api/admin_views.py

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import ApiFeature
from .security import clear_api_feature_cache, ensure_default_api_features


def _can_manage_api(user):
    """
    Solo superuser o rol admin puede gestionar APIs.
    """
    if not user.is_authenticated:
        return False

    if getattr(user, "is_superuser", False):
        return True

    rol = getattr(user, "rol", None)
    if rol and str(rol) == "admin":
        return True

    if hasattr(user, "roles"):
        try:
            return (
                user.roles.filter(nombre="admin").exists()
                or user.roles.filter(name="admin").exists()
            )
        except Exception:
            return False

    return False


@login_required
def api_feature_list(request):
    """
    Lista de APIs disponibles para activar/desactivar.
    """
    if not _can_manage_api(request.user):
        messages.error(request, "You do not have permission to manage APIs.")
        return redirect("/dashboard_admin/")

    ensure_default_api_features()

    features = ApiFeature.objects.all().order_by("code")

    return render(
        request,
        "api/api_feature_list.html",
        {
            "features": features,
        },
    )


@login_required
@require_POST
def api_feature_toggle(request, pk):
    """
    Activa o desactiva una API feature.
    """
    if not _can_manage_api(request.user):
        messages.error(request, "You do not have permission to manage APIs.")
        return redirect("/dashboard_admin/")

    try:
        feature = ApiFeature.objects.get(pk=pk)
    except ApiFeature.DoesNotExist:
        messages.error(request, "API feature not found.")
        return redirect("api:api_feature_list")

    feature.is_enabled = not feature.is_enabled
    feature.updated_by = request.user
    feature.save(update_fields=["is_enabled", "updated_by", "updated_at"])

    clear_api_feature_cache(feature.code)

    if feature.is_enabled:
        messages.success(request, f"{feature.name} enabled.")
    else:
        messages.warning(request, f"{feature.name} disabled.")

    return redirect("api:api_feature_list")


@login_required
@require_POST
def api_feature_superuser_toggle(request, pk):
    """
    Activa o desactiva restricción solo superuser.
    """
    if not _can_manage_api(request.user):
        messages.error(request, "You do not have permission to manage APIs.")
        return redirect("/dashboard_admin/")

    try:
        feature = ApiFeature.objects.get(pk=pk)
    except ApiFeature.DoesNotExist:
        messages.error(request, "API feature not found.")
        return redirect("api:api_feature_list")

    feature.only_superusers = not feature.only_superusers
    feature.updated_by = request.user
    feature.save(update_fields=["only_superusers", "updated_by", "updated_at"])

    clear_api_feature_cache(feature.code)

    if feature.only_superusers:
        messages.warning(request, f"{feature.name} now requires superuser.")
    else:
        messages.success(request, f"{feature.name} no longer requires superuser only.")

    return redirect("api:api_feature_list")
