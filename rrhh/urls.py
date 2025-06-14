from django.urls import path
from django.urls import include
from .views import (listar_contratos_admin, listar_contratos_usuario,
                    crear_contrato, editar_contrato, eliminar_contrato, ver_contrato,
                    listar_fichas_ingreso_admin, crear_ficha_ingreso,
                    editar_ficha_ingreso, eliminar_ficha_ingreso, ver_ficha_ingreso, listar_fichas_ingreso_usuario)


app_name = 'rrhh'

urlpatterns = [
    # Contratos de trabajo
    path('contratos-trabajo/', listar_contratos_admin, name='contratos_trabajo'),
    path('mis-contratos/', listar_contratos_usuario, name='mis_contratos'),
    path('contratos-trabajo/crear/', crear_contrato, name='crear_contrato'),
    path('contratos-trabajo/editar/<int:contrato_id>/',
         editar_contrato, name='editar_contrato'),
    path('contratos-trabajo/eliminar/<int:contrato_id>/',
         eliminar_contrato, name='eliminar_contrato'),
    path('contratos-trabajo/ver/<int:contrato_id>/',
         ver_contrato, name='ver_contrato'),

    # Fichas de ingreso (reutilizando modelo y formulario)
    path('fichas/', listar_fichas_ingreso_admin,
         name='listar_fichas_ingreso_admin'),
    path('fichas/crear/', crear_ficha_ingreso, name='crear_ficha'),
    path('fichas/editar/<int:pk>/', editar_ficha_ingreso, name='editar_ficha'),
    path('fichas/eliminar/<int:pk>/',
         eliminar_ficha_ingreso, name='eliminar_ficha'),
    path('fichas/ver/<int:pk>/', ver_ficha_ingreso, name='ver_ficha'),
    path('mis-fichas/', listar_fichas_ingreso_usuario, name='mis_fichas_ingreso'),
]
