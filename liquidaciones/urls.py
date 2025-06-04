from django.urls import path
from . import views
from .views import UsuarioAutocomplete


app_name = 'liquidaciones'

urlpatterns = [
    path('', views.listar_liquidaciones, name='listar'),
    path('admin/', views.admin_lista_liquidaciones, name='admin_lista'),
    path('pdf/<int:pk>/', views.ver_pdf_liquidacion, name='ver_pdf'),
    path('firmar/<int:pk>/', views.firmar_liquidacion, name='firmar'),
    path('registrar-firma/', views.registrar_firma, name='registrar_firma'),
    path('pdfs/', views.liquidaciones_pdf, name='pdfs'),
    path('descargar-pdf/', views.descargar_pdf, name='descargar_pdf'),
    path('confirmar-firma/<int:pk>/',
         views.confirmar_firma, name='confirmar_firma'),
    path('confirmar-reemplazo/', views.confirmar_reemplazo,
         name='confirmar_reemplazo'),
    path('carga-masiva/', views.carga_masiva_view, name='carga_masiva'),
    path('crear/', views.crear_liquidacion, name='crear'),
    path('usuario-autocomplete/', UsuarioAutocomplete.as_view(),
         name='usuario-autocomplete'),
    path('firmar/<int:pk>/', views.firmar_liquidacion, name='firmar_liquidacion'),
    path('editar/<int:pk>/', views.editar_liquidacion, name='editar'),
    path('eliminar/<int:pk>/', views.eliminar_liquidacion, name='eliminar'),


]
