# fleet/urls.py
from django.urls import path

from . import views

app_name = "fleet"

urlpatterns = [
    path("", views.fleet_home, name="fleet_home"),

    # Vehicles
    path("vehicles/", views.vehicles_list, name="vehicles_list"),
    path("vehicles/new/", views.vehicle_create, name="vehicle_create"),
    path("vehicles/<int:pk>/", views.vehicle_detail, name="vehicle_detail"),
    path("vehicles/<int:pk>/edit/", views.vehicle_edit, name="vehicle_edit"),

    # Assignments
    path("assignments/", views.assignments_list, name="assignments_list"),
    path("assignments/new/", views.assignment_create, name="assignment_create"),
    path("assignments/<int:pk>/edit/", views.assignment_edit, name="assignment_edit"),
    path("assignments/<int:pk>/end/", views.assignment_end, name="assignment_end"),

    # Odometer logs
    path("odometer/", views.odometer_logs_list, name="odometer_logs_list"),
    path("odometer/new/", views.odometer_log_create, name="odometer_log_create"),
    path("odometer/export/", views.odometer_logs_export_csv, name="odometer_logs_export_csv"),
]