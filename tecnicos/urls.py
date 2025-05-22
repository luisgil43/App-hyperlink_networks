from django.urls import path
from .views import login_tecnico, logout_tecnico

urlpatterns = [
    path('login/', login_tecnico, name='login_tecnico'),
    path('logout/', logout_tecnico, name='logout_tecnico'),
]
