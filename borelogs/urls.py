# borelogs/urls.py
from django.urls import path

from . import views

app_name = "borelogs"

urlpatterns = [
    # ✅ NUEVO: por Billing (SesionBilling)
    path("billing/<int:sesion_id>/", views.borelog_list_for_billing, name="borelog_list_for_billing"),
    path("billing/<int:sesion_id>/create/", views.borelog_create_for_billing, name="borelog_create_for_billing"),

    # (Opcional) vistas globales que ya tenías
    path("", views.borelog_list, name="borelog_list"),
    path("create/", views.borelog_create, name="borelog_create"),

    path("<int:pk>/edit/", views.borelog_edit, name="borelog_edit"),
    path("<int:pk>/delete/", views.borelog_delete, name="borelog_delete"),
    path("<int:pk>/", views.borelog_detail, name="borelog_detail"),

    # ✅ download único (auto-cachea mapas)
    path("<int:pk>/download/", views.borelog_download_docx, name="borelog_download_docx"),
]