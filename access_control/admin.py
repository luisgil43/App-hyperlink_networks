from django.contrib import admin

from access_control.models import AccessPermission, RoleAccessPermission


@admin.register(AccessPermission)
class AccessPermissionAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "module", "is_active", "order")
    list_filter = ("module", "is_active")
    search_fields = ("key", "label", "description")
    ordering = ("module", "order", "label")


@admin.register(RoleAccessPermission)
class RoleAccessPermissionAdmin(admin.ModelAdmin):
    list_display = ("permission", "role_name", "enabled")
    list_filter = ("role_name", "enabled", "permission__module")
    search_fields = ("permission__key", "permission__label", "role_name")
    ordering = ("permission__module", "permission__order", "role_name")
