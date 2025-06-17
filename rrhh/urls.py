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
         views.editar_solicitud_vacaciones, name='editar_solicitud'),
    path('vacaciones/eliminar/<int:pk>/',
         views.eliminar_solicitud_vacaciones, name='eliminar_solicitud'),
    # Rutas para revisión por cada rol

    path('vacaciones/revisar/supervisor/',
         views.revisar_solicitudes_supervisor, name='revisar_supervisor'),
    path('vacaciones/revisar/pm/', views.revisar_solicitudes_pm, name='revisar_pm'),
    path('vacaciones/revisar/rrhh/',
         views.revisar_solicitudes_rrhh, name='revisar_rrhh'),
    path('vacaciones/revisar/<int:solicitud_id>/',
         views.revisar_solicitud, name='revisar_solicitud'),
    path('vacaciones/todas/', views.revisar_todas_vacaciones,
         name='revisar_todas_vacaciones'),
    path('vacaciones/rechazar/', views.rechazar_solicitud_vacaciones,
         name='rechazar_solicitud'),
    path('vacaciones/aprobar/supervisor/<int:pk>/',
         views.aprobar_vacacion_supervisor, name='aprobar_supervisor'),
    path('vacaciones/aprobar/pm/<int:pk>/',
         views.aprobar_vacacion_pm, name='aprobar_pm'),
    path('vacaciones/aprobar/rrhh/<int:pk>/',
         views.aprobar_vacacion_rrhh, name='aprobar_rrhh'),
    path('vacaciones/eliminar-admin/<int:pk>/',
         views.eliminar_solicitud_vacaciones_admin, name='eliminar_solicitud_admin'),
    path('vacaciones/revisar/rrhh/', views.revisar_solicitudes_rrhh,
         name='revisar_solicitudes_rrhh'),

]
