from django.urls import path

from . import views

app_name = "underground"

urlpatterns = [
    path("", views.route_list, name="route_list"),
    path("new/", views.route_create, name="route_create"),
    path("<int:route_id>/", views.route_detail, name="route_detail"),
    path("<int:route_id>/update-segment/", views.update_segment_progress, name="update_segment_progress"),
    path("<int:route_id>/regenerate/", views.route_regenerate_segments, name="route_regenerate_segments"),
]