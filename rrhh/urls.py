from django.urls import path
from django.urls import include
from .views import (listar_contratos_admin, listar_contratos_usuario,
                    crear_contrato, editar_contrato, eliminar_contrato, ver_contrato)


app_name = 'rrhh'

urlpatterns = [
    path('contratos-trabajo/', listar_contratos_admin, name='contratos_trabajo'),
    path('mis-contratos/', listar_contratos_usuario, name='mis_contratos'),
    path('contratos-trabajo/crear/', crear_contrato, name='crear_contrato'),
    path('contratos-trabajo/eliminar/<int:contrato_id>/',
         eliminar_contrato, name='eliminar_contrato'),
    path('contratos-trabajo/editar/<int:contrato_id>/',
         editar_contrato, name='editar_contrato'),
    path('contratos-trabajo/ver/<int:contrato_id>/',
         ver_contrato, name='ver_contrato'),
]
