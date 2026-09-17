from django.urls import path

from . import views

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
]
