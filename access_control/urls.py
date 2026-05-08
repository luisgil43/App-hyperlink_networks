# access_control/urls.py
from django.urls import path

from . import views

app_name = "access_control"

urlpatterns = [
    path("matrix/", views.matrix, name="matrix"),
]
