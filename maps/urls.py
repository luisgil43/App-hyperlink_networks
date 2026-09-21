from django.urls import path

from . import views, views_technician

app_name = "maps"


urlpatterns = [
    path(
        "",
        views.map_home,
        name="home",
    ),
    path(
        "project-location/save/",
        views.save_project_location,
        name="save_project_location",
    ),
    path(
        "project-location/remove/",
        views.remove_project_location,
        name="remove_project_location",
    ),
    path(
        "technician/location-check/<int:assignment_id>/",
        views_technician.technician_location_check,
        name="technician_location_check",
    ),
    path(
        "technician/location-verify/<int:assignment_id>/",
        views_technician.technician_location_verify,
        name="technician_location_verify",
    ),
    path(
        "project-location/toggle-validation/",
        views.toggle_location_validation,
        name="toggle_location_validation",
    ),
]
