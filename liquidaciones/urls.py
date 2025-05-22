from django.urls import path
from . import views

app_name = 'liquidaciones'

urlpatterns = [
    path('', views.listar_liquidaciones, name='listar'),
    path('firmar/<int:pk>/', views.firmar_liquidacion, name='firmar_liquidacion'),
    path('ver-pdf/<int:pk>/', views.ver_pdf_liquidacion, name='ver_pdf'),
    path('registrar-firma/', views.registrar_firma, name='registrar_firma'),
    path('pdf/', views.liquidaciones_pdf, name='liquidaciones_pdf'),
    path('descargar-pdf/', views.descargar_pdf, name='descargar_pdf'),
    path('liquidaciones/confirmar-firma/<int:pk>/',
         views.confirmar_firma, name='confirmar_firma'),

]
