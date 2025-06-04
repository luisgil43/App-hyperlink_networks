from django.urls import path
from .views import (
    login_tecnico, login_view, dashboard_view,
    mis_cursos_view, dashboard_detalle_view,
    registrar_firma,
)

urlpatterns = [
    path('', dashboard_view, name='dashboard'),
    # ← ESTA LÍNEA AGREGA LA URL QUE FALTA
    path('dashboard/', dashboard_view, name='dashboard_redirect'),
    path('login/', login_tecnico, name='login_tecnico'),
    path('logout/', login_view, name='logout_tecnico'),
    path('dashboard/mis-cursos/', mis_cursos_view, name='mis_cursos'),
    path('detalle/<int:produccion_id>/',
         dashboard_detalle_view, name='dashboard_detalle'),
    path('registrar-firma/', registrar_firma, name='registrar_firma'),
    path('dashboard/', views.dashboard, name='dashboard'),
]
