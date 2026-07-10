from django.urls import path

from . import views

app_name = "plan_reader"

urlpatterns = [
    path("", views.job_list, name="job_list"),
    path("new/", views.job_create, name="job_create"),
    path("<int:job_id>/", views.job_detail, name="job_detail"),
]
