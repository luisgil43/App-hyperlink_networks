# fleet/urls.py
from django.urls import path

from . import views, views_cron, views_user

app_name = "fleet"

urlpatterns = [
    # Home
    path("", views.fleet_home, name="fleet_home"),
    # Vehicles
    path("vehicles/", views.vehicle_list, name="vehicles_list"),
    path("vehicles/new/", views.vehicle_create, name="vehicle_create"),
    path("vehicles/<int:pk>/", views.vehicle_detail, name="vehicle_detail"),
    path("vehicles/<int:pk>/edit/", views.vehicle_edit, name="vehicle_edit"),
    path("vehicles/<int:pk>/delete/", views.vehicle_delete, name="vehicle_delete"),
    path(
        "vehicles/<int:pk>/status/",
        views.vehicle_change_status,
        name="vehicle_change_status",
    ),
    # Statuses
    path("statuses/", views.status_manage, name="status_manage"),
    path("statuses/<int:pk>/edit/", views.status_edit, name="status_edit"),
    path("statuses/<int:pk>/toggle/", views.status_toggle, name="status_toggle"),
    path("statuses/<int:pk>/delete/", views.status_delete, name="status_delete"),
    # Assignments
    path("assignments/", views.assignments_list, name="assignments_list"),
    path("assignments/new/", views.assignment_create, name="assignment_create"),
    path("assignments/<int:pk>/edit/", views.assignment_edit, name="assignment_edit"),
    path(
        "assignments/<int:pk>/toggle/",
        views.assignment_toggle,
        name="assignment_toggle",
    ),
    path(
        "assignments/<int:pk>/delete/",
        views.assignment_delete,
        name="assignment_delete",
    ),
    # Odometer logs
    path("odometer/", views.odometer_logs_list, name="odometer_logs_list"),
    path("odometer/new/", views.odometer_log_create, name="odometer_log_create"),
    path("odometer/export/", views.export_odometer_logs, name="odometer_logs_export"),
    # Notifications
    path("notifications/", views.notification_list, name="notification_list"),
    path(
        "notifications/<int:vehicle_id>/",
        views.notification_edit,
        name="notification_edit",
    ),
    # Services
    path("services/", views.service_list, name="service_list"),
    path("services/new/", views.service_create, name="service_create"),
    path("services/<int:pk>/edit/", views.service_edit, name="service_edit"),
    path("services/<int:pk>/delete/", views.service_delete, name="service_delete"),
    # Service Types
    path("service-types/", views.service_type_manage, name="service_type_manage"),
    path(
        "service-types/<int:pk>/edit/",
        views.service_type_edit,
        name="service_type_edit",
    ),
    path(
        "service-types/<int:pk>/toggle/",
        views.service_type_toggle,
        name="service_type_toggle",
    ),
    path(
        "service-types/<int:pk>/delete/",
        views.service_type_delete,
        name="service_type_delete",
    ),
    # User
    path("my-vehicles/", views_user.my_vehicle_dashboard, name="my_vehicle_dashboard"),
    path(
        "my-vehicles/history.xlsx",
        views_user.my_vehicle_history_excel,
        name="my_vehicle_history_excel",
    ),
    # Cron
    path(
        "cron/mantenciones/",
        views_cron.cron_fleet_maintenances,
        name="cron_fleet_maintenances",
    ),
]
