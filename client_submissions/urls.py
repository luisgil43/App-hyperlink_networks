from django.urls import path

from . import views, views_verification

app_name = "client_submissions"


urlpatterns = [
    # ========================================================
    # Entrada desde Ready to Invoice
    # ========================================================
    path(
        "from-invoices/",
        views.create_batch_from_invoices,
        name="create_batch_from_invoices",
    ),
    path(
        "from-invoices/create/",
        views.create_batch_submit,
        name="create_batch_submit",
    ),
    # ========================================================
    # Lista
    # ========================================================
    path(
        "",
        views.batch_list,
        name="batch_list",
    ),
    path(
        "batches/status/",
        views.batch_list_status_json,
        name="batch_list_status_json",
    ),
    # ========================================================
    # Batch
    # ========================================================
    path(
        "batch/<uuid:public_id>/",
        views.batch_detail,
        name="batch_detail",
    ),
    path(
        "batch/<uuid:public_id>/status/",
        views.batch_status_json,
        name="batch_status_json",
    ),
    path(
        "batch/<uuid:public_id>/revalidate/",
        views.batch_revalidate,
        name="batch_revalidate",
    ),
    path(
        "batch/<uuid:public_id>/start/",
        views.batch_start,
        name="batch_start",
    ),
    path(
        "batch/<uuid:public_id>/pause/",
        views.batch_pause,
        name="batch_pause",
    ),
    path(
        "batch/<uuid:public_id>/cancel/",
        views.batch_cancel,
        name="batch_cancel",
    ),
    path(
        "batch/<uuid:public_id>/delete/",
        views.batch_delete,
        name="batch_delete",
    ),
    # ========================================================
    # Submission individual
    # ========================================================
    path(
        "submission/<uuid:public_id>/revalidate/",
        views.submission_revalidate,
        name="submission_revalidate",
    ),
    # ========================================================
    # Verificación humana / CAPTCHA
    # ========================================================
    path(
        "submission/<uuid:public_id>/verification/",
        views_verification.verification_detail,
        name="verification_detail",
    ),
    path(
        "submission/<uuid:public_id>/verification/status/",
        views_verification.verification_status_json,
        name="verification_status_json",
    ),
    path(
        "submission/<uuid:public_id>/verification/retry/",
        views_verification.verification_retry,
        name="verification_retry",
    ),
    path(
        "submission/<uuid:public_id>/verification/continue/",
        views_verification.verification_continue,
        name="verification_continue",
    ),
    path(
        "submission/<uuid:public_id>/verification/cancel/",
        views_verification.verification_cancel,
        name="verification_cancel",
    ),
]
