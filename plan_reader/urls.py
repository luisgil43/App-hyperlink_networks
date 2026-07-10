from django.urls import path

from .views import (download_excel, item_review, job_create, job_detail,
                    job_list, job_status_json, queue_job_processing,
                    recalculate_job_duplicates, toggle_item_duplicate)

app_name = "plan_reader"

urlpatterns = [
    path("", job_list, name="job_list"),
    path("new/", job_create, name="job_create"),
    path("<int:job_id>/", job_detail, name="job_detail"),
    path("<int:job_id>/status-json/", job_status_json, name="job_status_json"),
    path("<int:job_id>/download-excel/", download_excel, name="download_excel"),
    # Worker-ready actions
    path(
        "<int:job_id>/queue-processing/",
        queue_job_processing,
        name="queue_job_processing",
    ),
    path(
        "<int:job_id>/recalculate-duplicates/",
        recalculate_job_duplicates,
        name="recalculate_job_duplicates",
    ),
    path("items/<int:item_id>/review/", item_review, name="item_review"),
    path(
        "items/<int:item_id>/toggle-duplicate/",
        toggle_item_duplicate,
        name="toggle_item_duplicate",
    ),
]
