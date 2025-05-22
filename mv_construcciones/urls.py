from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LogoutView, PasswordResetView, PasswordResetDoneView, PasswordResetConfirmView, PasswordResetCompleteView
from tecnicos.views import login_tecnico
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse  # 👈 AÑADIDO para vista de salud

# 👇 AÑADIR esta función


def health_check(request):
    return HttpResponse("OK", status=200)


urlpatterns = [
    # Health check para Render
    path('healthz', health_check),  # 👈 AÑADIDO

    # Admin y logout de admin
    path('admin/', admin.site.urls),
    path('admin/logout/', LogoutView.as_view(next_page='/admin/login/'),
         name='admin_logout'),

    # Login/Logout para técnicos
    path('tecnicos/login/', login_tecnico, name='login_tecnico'),
    path('tecnicos/logout/', LogoutView.as_view(next_page='/tecnicos/login/'),
         name='logout_tecnico'),

    # Dashboard para técnicos
    path('tecnicos/dashboard/', include('dashboard.urls', namespace='dashboard')),

    # Liquidaciones
    path('liquidaciones/', include(('liquidaciones.urls',
         'liquidaciones'), namespace='liquidaciones')),

    # Recuperación de contraseña (opcional si no la estás usando aún)
    path('password_reset/', PasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', PasswordResetDoneView.as_view(),
         name='password_reset_done'),
    path('reset/<uidb64>/<token>/', PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('reset/done/', PasswordResetCompleteView.as_view(),
         name='password_reset_complete'),

    # Página raíz redirige a login técnico
    path('', login_tecnico),
]

# Archivos estáticos y media (solo si estás sirviendo en desarrollo)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
