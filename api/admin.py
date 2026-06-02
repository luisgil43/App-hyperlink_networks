# api/admin.py

from django.contrib import admin

from .models import ApiFeature


@admin.register(ApiFeature)
class ApiFeatureAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "is_enabled",
        "only_superusers",
        "updated_by",
        "updated_at",
    )
    list_filter = ("is_enabled", "only_superusers")
    search_fields = ("code", "name", "description")
    readonly_fields = ("created_at", "updated_at")
