from django.urls import path
from . import views


app_name = 'facturacion'

urlpatterns = [

    path('cartola/', views.listar_cartola, name='listar_cartola'),
    path('cartola/registrar/', views.registrar_abono, name='registrar_abono'),
    path('cartola/crear-tipo/', views.crear_tipo, name='crear_tipo'),
    path('cartola/editar-tipo/<int:pk>/',
         views.editar_tipo, name='editar_tipo'),
    path('cartola/eliminar-tipo/<int:pk>/',
         views.eliminar_tipo, name='eliminar_tipo'),
    path('proyectos/', views.crear_proyecto, name='crear_proyecto'),
    path('proyectos/editar/<int:pk>/',
         views.editar_proyecto, name='editar_proyecto'),
    path('proyectos/eliminar/<int:pk>/',
         views.eliminar_proyecto, name='eliminar_proyecto'),
    path('cartola/aprobar/<int:pk>/',
         views.aprobar_movimiento, name='aprobar_movimiento'),
    path('cartola/rechazar/<int:pk>/',
         views.rechazar_movimiento, name='rechazar_movimiento'),
    path('cartola/editar/<int:pk>/',
         views.editar_movimiento, name='editar_movimiento'),
    path('cartola/eliminar/<int:pk>/',
         views.eliminar_movimiento, name='eliminar_movimiento'),
    path('saldos-usuarios/', views.listar_saldos_usuarios,
         name='listar_saldos_usuarios'),
    path('cartola/exportar/', views.exportar_cartola, name='exportar_cartola'),
    path('balances/exportar/', views.exportar_saldos, name='exportar_saldos'),

]
