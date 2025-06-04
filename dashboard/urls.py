from django.urls import path, include
from . import views
from .views import dashboard_view
from django.contrib.auth.views import LogoutView


app_name = 'dashboard'

urlpatterns = [
    # Esta será la página inicial de /dashboard/
    path('', views.inicio, name='inicio'),
    path('inicio/', views.inicio_tecnico, name='inicio_tecnico'),
    path('mis-cursos/', views.mis_cursos_view, name='mis_cursos'),
    path('detalle/<int:produccion_id>/',
         views.dashboard_detalle_view, name='dashboard_detalle'),
    path('produccion/', views.produccion_tecnicos_view,
         name='produccion_tecnicos'),
    path('produccion/pdf/', views.produccion_tecnicos_pdf,
         name='produccion_tecnicos_pdf'),


    # Subsección de recursos humanos
    path('rrhh/liquidaciones/', include(('liquidaciones.urls',
         'liquidaciones'), namespace='liquidaciones')),
    # path('login/', UsuarioLoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='dashboard:login'), name='logout'),
    path('dashboard/', dashboard_view, name='home'),
]
