from django.urls import path

from . import views

app_name = "client_deliverables"

urlpatterns = [
    path("", views.admin_package_list, name="admin_package_list"),
    path("new/", views.admin_package_create, name="admin_package_create"),
    path("<uuid:pk>/", views.admin_package_detail, name="admin_package_detail"),
    path(
        "<uuid:pk>/add-selected/",
        views.admin_package_add_selected,
        name="admin_package_add_selected",
    ),
    path(
        "<uuid:pk>/file/<int:file_id>/delete/",
        views.admin_package_delete_file,
        name="admin_package_delete_file",
    ),
    path(
        "<uuid:pk>/publish/", views.admin_package_publish, name="admin_package_publish"
    ),
    path("<uuid:pk>/revoke/", views.admin_package_revoke, name="admin_package_revoke"),
    path(
        "<uuid:pk>/outlook/", views.admin_package_outlook, name="admin_package_outlook"
    ),
    path("p/<str:token>/", views.public_package_detail, name="public_package_detail"),
    path(
        "p/<str:token>/unlock/",
        views.public_package_unlock,
        name="public_package_unlock",
    ),
    path(
        "p/<str:token>/download/<int:file_id>/",
        views.public_download_file,
        name="public_download_file",
    ),
    path(
        "p/<str:token>/download-all/",
        views.public_download_all,
        name="public_download_all",
    ),
    path(
        "portal/my-deliverables/",
        views.client_my_deliverables,
        name="client_my_deliverables",
    ),
    path("portal/project/", views.client_project_search, name="client_project_search"),
    path(
        "portal/project/<str:project_id>/",
        views.client_project_detail,
        name="client_project_detail",
    ),
    path(
        "portal/project/<str:project_id>/download-all/",
        views.client_project_download_all,
        name="client_project_download_all",
    ),
    path(
        "p/<str:token>/download-all/status/<uuid:job_id>/",
        views.public_download_all_status,
        name="public_download_all_status",
    ),
    path(
        "p/<str:token>/download-all/status/<uuid:job_id>/json/",
        views.public_download_all_status_json,
        name="public_download_all_status_json",
    ),
    path(
        "p/<str:token>/download-all/file/<uuid:job_id>/",
        views.public_download_all_file,
        name="public_download_all_file",
    ),
    path(
        "<uuid:pk>/reopen/",
        views.admin_package_reopen,
        name="admin_package_reopen",
    ),
    path("<uuid:pk>/edit/", views.admin_package_edit, name="admin_package_edit"),
]
