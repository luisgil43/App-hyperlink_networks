from django.contrib import admin

from .models import PlanReaderItem, PlanReaderJob, PlanReaderPage


@admin.register(PlanReaderJob)
class PlanReaderJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_filename",
        "client",
        "co",
        "dfn",
        "status",
        "total_pages",
        "processed_pages",
        "failed_pages",
        "uploaded_by",
        "created_at",
    )
    list_filter = ("status", "client", "co", "dfn", "created_at")
    search_fields = (
        "original_filename",
        "client",
        "co",
        "dfn",
        "uploaded_by__username",
        "uploaded_by__first_name",
        "uploaded_by__last_name",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    )


@admin.register(PlanReaderPage)
class PlanReaderPageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "page_number",
        "sheet_name",
        "status",
        "confidence",
        "processed_at",
    )
    list_filter = ("status", "sheet_name")
    search_fields = (
        "job__original_filename",
        "sheet_name",
    )


@admin.register(PlanReaderItem)
class PlanReaderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "sheet",
        "project_name",
        "primary_feed",
        "calculated_box_type",
        "c108_ug",
        "c109_splices",
        "c110_splitters",
        "needs_review",
        "is_duplicate",
    )
    list_filter = (
        "needs_review",
        "is_duplicate",
        "calculated_box_type",
        "sheet",
    )
    search_fields = (
        "project_name",
        "primary_feed",
        "sheet",
        "co",
        "dfn",
    )
