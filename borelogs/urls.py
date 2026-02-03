# borelogs/urls.py
from django.urls import path

from . import views

app_name = "borelogs"

urlpatterns = [
    # ✅ por Billing (SesionBilling)
    path("billing/<int:sesion_id>/", views.borelog_list_for_billing, name="borelog_list_for_billing"),

    # ✅ listas explícitas (NO dependen de ?ui=)
    path("billing/<int:sesion_id>/user/", views.borelog_list_for_billing_user, name="borelog_list_for_billing_user"),
    path("billing/<int:sesion_id>/admin/", views.borelog_list_for_billing_admin, name="borelog_list_for_billing_admin"),

    # ✅ create para billing (si quieres, puedes crear 2 rutas separadas también)
    path("billing/<int:sesion_id>/create/", views.borelog_create_for_billing, name="borelog_create_for_billing"),

    # (Opcional) vistas globales
    path("", views.borelog_list, name="borelog_list"),
    path("create/", views.borelog_create, name="borelog_create"),

    # ✅ DETALLE SEPARADO (lo que pediste)
    path("<int:pk>/user/", views.borelog_detail_user, name="borelog_detail_user"),
    path("<int:pk>/admin/", views.borelog_detail_admin, name="borelog_detail_admin"),

    # ✅ Router opcional: si alguien entra a /<pk>/ lo mandas a user o admin según permisos
    path("<int:pk>/", views.borelog_detail_router, name="borelog_detail"),

    # header edit / delete (admin)
    path("<int:pk>/edit/", views.borelog_edit, name="borelog_edit"),
    path("<int:pk>/delete/", views.borelog_delete, name="borelog_delete"),

    # ✅ download único
    path("<int:pk>/download/", views.borelog_download_docx, name="borelog_download_docx"),
]