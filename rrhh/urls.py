from django.urls import path
from django.urls import include
from . import views

app_name = 'rrhh'

urlpatterns = [
    # Contratos de trabajo
    path('contratos-trabajo/', views.listar_contratos_admin,
         name='contratos_trabajo'),
    path('mis-contratos/', views.listar_contratos_usuario, name='mis_contratos'),
    path('contratos-trabajo/crear/', views.crear_contrato, name='crear_contrato'),
    path('contratos-trabajo/editar/<int:contrato_id>/',
         views.editar_contrato, name='editar_contrato'),
    path('contratos-trabajo/eliminar/<int:contrato_id>/',
         views.eliminar_contrato, name='eliminar_contrato'),
    path('contratos-trabajo/ver/<int:contrato_id>/',
         views.ver_contrato, name='ver_contrato'),

    # Fichas de ingreso (reutilizando modelo y formulario)
    path('fichas/', views.listar_fichas_ingreso_admin,
         name='listar_fichas_ingreso_admin'),
    path('fichas/crear/', views.crear_ficha_ingreso, name='crear_ficha'),
    path('fichas/editar/<int:pk>/',
         views.editar_ficha_ingreso, name='editar_ficha'),
    path('fichas/eliminar/<int:pk>/',
         views.eliminar_ficha_ingreso, name='eliminar_ficha'),
    path('fichas/ver/<int:pk>/', views.ver_ficha_ingreso, name='ver_ficha'),
    path('mis-fichas/', views.listar_fichas_ingreso_usuario,
         name='mis_fichas_ingreso'),
    path('vacaciones/', views.mis_vacaciones, name='mis_vacaciones'),
    path('vacaciones/editar/<int:pk>/',
         views.editar_solicitud, name='editar_solicitud'),
    path('vacaciones/eliminar/<int:pk>/',
         views.eliminar_solicitud, name='eliminar_solicitud'),
]
