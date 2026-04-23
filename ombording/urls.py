from django.urls import path

from . import views

app_name = "ombording"

urlpatterns = [
    path("", views.ombording_list, name="ombording_list"),
    path("positions/", views.position_list, name="position_list"),
    path("positions/new/", views.position_create, name="position_create"),
    path("positions/<int:pk>/edit/", views.position_edit, name="position_edit"),
    path("new/", views.ombording_create, name="ombording_create"),
    path("<int:pk>/edit/", views.ombording_edit, name="ombording_edit"),
    path("<int:pk>/review/", views.ombording_review, name="ombording_review"),
    path(
        "<int:pk>/field-review/<int:review_id>/update/",
        views.ombording_field_review_update,
        name="ombording_field_review_update",
    ),
    path(
        "<int:pk>/document-review/<int:document_id>/update/",
        views.ombording_document_review_update,
        name="ombording_document_review_update",
    ),
    path(
        "<int:pk>/send-email/", views.ombording_send_email, name="ombording_send_email"
    ),
    path(
        "<int:pk>/reactivate/", views.ombording_reactivate, name="ombording_reactivate"
    ),
    path("public/<str:token>/", views.public_start, name="public_start"),
    path(
        "public/<str:token>/document/<str:document_key>/",
        views.public_document_download,
        name="public_document_download",
    ),
    path("<int:pk>/pause/", views.ombording_pause, name="ombording_pause"),
    path("review/<int:pk>/approve/", views.ombording_approve, name="ombording_approve"),
    path("review/<int:pk>/reject/", views.ombording_reject, name="ombording_reject"),
    path("<int:pk>/delete/", views.ombording_delete, name="ombording_delete"),
]
