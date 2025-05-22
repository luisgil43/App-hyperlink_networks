from django.urls import path, include
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('dashboard/mis-cursos/', views.mis_cursos_view, name='mis_cursos'),

    path('detalle/<int:produccion_id>/',
         views.dashboard_detalle_view, name='dashboard_detalle'),

    path('produccion/', views.produccion_tecnicos, name='produccion_tecnicos'),
    path('produccion/pdf/', views.produccion_tecnicos_pdf,
         name='produccion_tecnicos_pdf'),

    # 🔐 Ruta para cerrar sesión técnico
    path('logout/', views.logout_view, name='logout_tecnico'),

    path('rrhh/liquidaciones/', include(('liquidaciones.urls',
         'liquidaciones'), namespace='liquidaciones')),


]
