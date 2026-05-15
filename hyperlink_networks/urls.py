from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import (LogoutView, PasswordResetCompleteView,
                                       PasswordResetConfirmView,
                                       PasswordResetDoneView,
                                       PasswordResetView)
from django.http import HttpResponse
from django.urls import include, path
from django.views.generic.base import RedirectView

from dashboard import views as dashboard_views


def health_check(request):
    return HttpResponse("OK", status=200)


urlpatterns = [
    # Health check
    path("healthz", health_check),
    path("logout/", LogoutView.as_view(next_page="/usuarios/login/"), name="logout"),
    # Panel de administración personalizado
    path(
        "dashboard_admin/",
        include(
            ("dashboard_admin.urls", "dashboard_admin"),
            namespace="dashboard_admin",
        ),
    ),
    # Dashboard técnico
    path(
        "dashboard/",
        include(("dashboard.urls", "dashboard"), namespace="dashboard"),
    ),
    # Usuarios
    path(
        "usuarios/",
        include(("usuarios.urls", "usuarios"), namespace="usuarios"),
    ),
    # Recuperación de contraseña
    path("password_reset/", PasswordResetView.as_view(), name="password_reset"),
    path(
        "password_reset/done/",
        PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    # Liquidaciones
    path(
        "liquidaciones/",
        include(("liquidaciones.urls", "liquidaciones"), namespace="liquidaciones"),
    ),
    # Redirección raíz a dashboard
    path("", RedirectView.as_view(url="/dashboard/", permanent=False)),
    # Django Select2
    path("select2/", include("django_select2.urls")),
    # RRHH
    path("rrhh/", include("rrhh.urls", namespace="rrhh")),
    # Admin Django
    path("admin/", admin.site.urls),
    path(
        "dashboard_admin/login/",
        RedirectView.as_view(url="/usuarios/login/", permanent=False),
    ),
    # Apps
    path("logistica/", include("logistica.urls", namespace="logistica")),
    path("operaciones/", include("operaciones.urls")),
    path("facturacion/", include("facturacion.urls")),
    path("invoicing/", include(("invoicing.urls", "invoicing"), namespace="invoicing")),
    path("fleet/", include("fleet.urls")),
    path("notifications/", include("notifications.urls")),
    path("underground/", include("underground.urls")),
    path("borelogs/", include("borelogs.urls")),
    # Iconos/legacy en raíz
    # IMPORTANTE:
    # No usar staticfiles_storage.url() aquí porque en producción con ManifestStaticFilesStorage
    # puede romper migrate si falta una entrada exacta en staticfiles.json.
    path(
        "apple-touch-icon.png",
        RedirectView.as_view(
            url="/static/icons/apple-touch-icon.png",
            permanent=True,
        ),
        name="apple_touch_icon",
    ),
    path(
        "apple-touch-icon-120x120.png",
        RedirectView.as_view(
            url="/static/icons/apple-touch-icon-120x120.png",
            permanent=True,
        ),
        name="apple_touch_icon_120",
    ),
    path(
        "apple-touch-icon-120x120-precomposed.png",
        RedirectView.as_view(
            url="/static/icons/apple-touch-icon-120x120.png",
            permanent=True,
        ),
        name="apple_touch_icon_120_pre",
    ),
    path(
        "apple-touch-icon-180x180.png",
        RedirectView.as_view(
            url="/static/icons/apple-touch-icon-180x180.png",
            permanent=True,
        ),
        name="apple_touch_icon_180",
    ),
    path(
        "favicon.ico",
        RedirectView.as_view(
            url="/static/icons/favicon.ico",
            permanent=True,
        ),
        name="favicon_root",
    ),
    # Cron general
    path(
        "cron/",
        include(("notifications.urls", "notifications"), namespace="cron_general"),
    ),
    # Cable installation
    path(
        "cable-installation/",
        include(
            ("cable_installation.urls", "cable_installation"),
            namespace="cable_installation",
        ),
    ),
    # Onboarding
    path(
        "onboarding/",
        include(("ombording.urls", "ombording"), namespace="ombording"),
    ),
    path(
        "ombording/",
        include(("ombording.urls", "ombording_legacy"), namespace="ombording_legacy"),
    ),
    # Access control
    path(
        "access-control/",
        include(("access_control.urls", "access_control"), namespace="access_control"),
    ),
]


# Archivos estáticos y media en desarrollo
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

    if (
        getattr(settings, "DEFAULT_FILE_STORAGE", "")
        == "django.core.files.storage.FileSystemStorage"
    ):
        urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
