from django.urls import path

from .views import (download_excel, item_review, job_create, job_delete,
                    job_detail, job_edit, job_excel_options, job_list,
                    job_status_json, material_request_download_pdf,
                    material_request_edit, material_request_generate_pdf,
                    material_request_open, material_request_recalculate,
                    material_request_save, queue_job_processing,
                    recalculate_job_duplicates, stop_job_processing,
                    toggle_item_duplicate)

app_name = "plan_reader"


urlpatterns = [
    # =========================================================================
    # JOB LIST AND CREATION
    # =========================================================================
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
    # =========================================================================
    # MATERIAL REQUESTS
    # =========================================================================
    path(
        "<int:job_id>/material-requests/<str:request_type>/open/",
        material_request_open,
        name="material_request_open",
    ),
    path(
        ("<int:job_id>/material-requests/" "<int:material_request_id>/"),
        material_request_edit,
        name="material_request_edit",
    ),
    path(
        ("<int:job_id>/material-requests/" "<int:material_request_id>/save/"),
        material_request_save,
        name="material_request_save",
    ),
    path(
        ("<int:job_id>/material-requests/" "<int:material_request_id>/recalculate/"),
        material_request_recalculate,
        name="material_request_recalculate",
    ),
    path(
        ("<int:job_id>/material-requests/" "<int:material_request_id>/generate-pdf/"),
        material_request_generate_pdf,
        name="material_request_generate_pdf",
    ),
    # =========================================================================
    # JOB DETAIL AND ACTIONS
    # =========================================================================
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
    # =========================================================================
    # ITEM REVIEW
    # =========================================================================
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
    path(
        ("<int:job_id>/material-requests/" "<int:material_request_id>/download-pdf/"),
        material_request_download_pdf,
        name="material_request_download_pdf",
    ),
]
