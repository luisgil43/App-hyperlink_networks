# operaciones/urls.py

from django.urls import path
from . import views

app_name = 'operaciones'  # <--- ESTA LÍNEA ES OBLIGATORIA PARA USAR NAMESPACES

urlpatterns = [


    path('mis-rendiciones/', views.mis_rendiciones, name='mis_rendiciones'),
    path('aprobar_abono/<int:pk>/', views.aprobar_abono, name='aprobar_abono'),
    path('rechazar_abono/<int:pk>/', views.rechazar_abono, name='rechazar_abono'),
    path('mis-rendiciones/editar/<int:pk>/',
         views.editar_rendicion, name='editar_rendicion'),
    path('mis-rendiciones/eliminar/<int:pk>/',
         views.eliminar_rendicion, name='eliminar_rendicion'),

    path('rendiciones/', views.vista_rendiciones, name='vista_rendiciones'),
    path('rendiciones/aprobar/<int:pk>/',
         views.aprobar_rendicion, name='aprobar_rendicion'),
    path('rendiciones/rechazar/<int:pk>/',
         views.rechazar_rendicion, name='rechazar_rendicion'),
    path('rendiciones/exportar/', views.exportar_rendiciones,
         name='exportar_rendiciones'),
    path('mis-rendiciones/exportar/', views.exportar_mis_rendiciones,
         name='exportar_mis_rendiciones'),



]
