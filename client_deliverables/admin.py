from django.contrib import admin

from .models import (ClientProjectAssignment, DeliveryAccessLog,
                     DeliveryPackage, DeliveryPackageFile)


class DeliveryPackageFileInline(admin.TabularInline):
    model = DeliveryPackageFile
    extra = 0
    fields = (
        "project_id",
        "billing_session",
        "file_type",
        "display_name",
        "file",
        "source_url",
        "order",
        "is_active",
    )


@admin.register(ClientProjectAssignment)
class ClientProjectAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "project_id",
        "client_name",
        "is_active",
        "assigned_by",
        "created_at",
    )
    list_filter = ("is_active", "created_at")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__email",
        "project_id",
        "client_name",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(DeliveryPackage)
class DeliveryPackageAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "status",
        "expiration_mode",
        "expires_at",
        "requires_access_key",
        "created_by",
        "published_at",
        "created_at",
    )
    list_filter = (
        "status",
        "expiration_mode",
        "requires_access_key",
        "created_at",
        "published_at",
    )
    search_fields = ("name", "token", "message")
    readonly_fields = (
        "id",
        "token",
        "published_at",
        "revoked_at",
        "created_at",
        "updated_at",
        "failed_attempts",
        "locked_until",
    )
    inlines = [DeliveryPackageFileInline]


@admin.register(DeliveryPackageFile)
class DeliveryPackageFileAdmin(admin.ModelAdmin):
    list_display = (
        "package",
        "project_id",
        "file_type",
        "display_name",
        "is_active",
        "created_at",
    )
    list_filter = ("file_type", "is_active", "created_at")
    search_fields = (
        "package__name",
        "project_id",
        "display_name",
        "source_url",
        "source_key",
    )


@admin.register(DeliveryAccessLog)
class DeliveryAccessLogAdmin(admin.ModelAdmin):
    list_display = (
        "package",
        "file",
        "user",
        "action",
        "ip_address",
        "created_at",
    )
    list_filter = ("action", "created_at")
    search_fields = (
        "package__name",
        "file__display_name",
        "file__project_id",
        "user__username",
        "user__email",
        "ip_address",
    )
    readonly_fields = (
        "package",
        "file",
        "user",
        "action",
        "ip_address",
        "user_agent",
        "extra",
        "created_at",
    )

    def has_add_permission(self, request):
        return False
