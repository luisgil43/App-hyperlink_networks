from django.urls import path

from . import views

app_name = "planificacion"


urlpatterns = [
    path(
        "",
        views.master_plan,
        name="master_plan",
    ),
    path(
        "assignments/",
        views.assignment_list,
        name="assignment_list",
    ),
    path(
        "assignments/new/",
        views.assignment_create,
        name="assignment_create",
    ),
    path(
        "assignments/<int:pk>/",
        views.assignment_detail,
        name="assignment_detail",
    ),
    path(
        "assignments/<int:pk>/edit/",
        views.assignment_edit,
        name="assignment_edit",
    ),
    path(
        "assignments/<int:pk>/delete/",
        views.assignment_delete,
        name="assignment_delete",
    ),
    path(
        "productivity/",
        views.productivity_list,
        name="productivity_list",
    ),
    path(
        "productivity/new/",
        views.productivity_create,
        name="productivity_create",
    ),
    path(
        "productivity/<int:pk>/edit/",
        views.productivity_edit,
        name="productivity_edit",
    ),
    path(
        "productivity/<int:pk>/delete/",
        views.productivity_delete,
        name="productivity_delete",
    ),
]
