
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render

from usuarios.decoradores import rol_requerido


def _can_access_notifications(user) -> bool:
    return bool(
        getattr(user, "es_admin_general", False)
        or getattr(user, "es_pm", False)
        or getattr(user, "es_supervisor", False)
        or getattr(user, "is_superuser", False)
    )


@login_required
@rol_requerido("pm", "admin", "supervisor")
def notification_center(request):
    if not _can_access_notifications(request.user):
        return HttpResponseForbidden("You do not have access to Notifications.")
    return render(request, "notifications/center.html")