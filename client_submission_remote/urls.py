# client_submission_remote/urls.py

from django.urls import path

from client_submission_remote import views

app_name = "client_submission_remote"


urlpatterns = [
    path(
        "session/<uuid:public_id>/",
        views.remote_browser_console,
        name="console",
    ),
    path(
        "session/<uuid:public_id>/state/",
        views.remote_browser_state,
        name="state",
    ),
    path(
        "session/<uuid:public_id>/action/",
        views.remote_browser_action,
        name="action",
    ),
]
