# Register your models here.
from django.contrib import admin

from .models import RemoteBrowserAction, RemoteBrowserSession


@admin.register(RemoteBrowserSession)
class RemoteBrowserSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "submission",
        "status",
        "captcha_status",
        "stage",
        "round_number",
        "screenshot_version",
        "worker_identifier",
        "last_worker_activity_at",
        "created_at",
    )

    list_filter = (
        "status",
        "captcha_status",
        "stage",
        "created_at",
    )

    search_fields = (
        "submission__project_id",
        "submission__public_id",
        "public_id",
        "worker_identifier",
        "browser_session_key",
    )

    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
        "started_at",
        "closed_at",
        "last_worker_activity_at",
        "screenshot_captured_at",
        "last_action_at",
    )

    ordering = ("-created_at",)


@admin.register(RemoteBrowserAction)
class RemoteBrowserActionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "action_type",
        "status",
        "requested_by",
        "screenshot_version",
        "requested_at",
        "processed_at",
    )

    list_filter = (
        "action_type",
        "status",
        "requested_at",
    )

    search_fields = (
        "public_id",
        "session__public_id",
        "session__submission__project_id",
        "requested_by__username",
    )

    readonly_fields = (
        "public_id",
        "requested_at",
        "processing_started_at",
        "processed_at",
        "created_at",
        "updated_at",
    )

    ordering = ("-requested_at",)
