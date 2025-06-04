# usuarios/urls.py

from django.urls import path
from .views import UsuarioLoginView, AdminLoginView, logout_view, grupos_view, usuarios_view
from .views import crear_usuario

app_name = 'usuarios'

urlpatterns = [
    # Login para usuarios normales
    path('login/', UsuarioLoginView.as_view(), name='login'),
    path('admin/login/', AdminLoginView.as_view(),
         name='admin_login'),   # Login para admins
    path('logout/', logout_view, name='logout'),
    path('grupos/', grupos_view, name='grupos'),
    path('usuarios/', usuarios_view, name='usuarios'),
    path('crear/', crear_usuario, name='crear_usuario'),
]
