from django.urls import path

from .views import (download_excel, item_review, job_create, job_delete,
                    job_detail, job_edit, job_excel_options, job_list,
                    job_status_json, queue_job_processing,
                    recalculate_job_duplicates, stop_job_processing,
                    toggle_item_duplicate)

app_name = "plan_reader"

urlpatterns = [
    path(
        "",
        job_list,
        name="job_list",
    ),
    path(
        "excel-options/",
        job_excel_options,
        name="job_excel_options",
    ),
    path(
        "new/",
        job_create,
        name="job_create",
    ),
    path(
        "<int:job_id>/",
        job_detail,
        name="job_detail",
    ),
    path(
        "<int:job_id>/edit/",
        job_edit,
        name="job_edit",
    ),
    path(
        "<int:job_id>/delete/",
        job_delete,
        name="job_delete",
    ),
    path(
        "<int:job_id>/status-json/",
        job_status_json,
        name="job_status_json",
    ),
    path(
        "<int:job_id>/queue/",
        queue_job_processing,
        name="queue_job_processing",
    ),
    path(
        "<int:job_id>/stop/",
        stop_job_processing,
        name="stop_job_processing",
    ),
    path(
        "<int:job_id>/recalculate-duplicates/",
        recalculate_job_duplicates,
        name="recalculate_job_duplicates",
    ),
    path(
        "<int:job_id>/download-excel/",
        download_excel,
        name="download_excel",
    ),
    path(
        "items/<int:item_id>/review/",
        item_review,
        name="item_review",
    ),
    path(
        "items/<int:item_id>/toggle-duplicate/",
        toggle_item_duplicate,
        name="toggle_item_duplicate",
    ),
]
