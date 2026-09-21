from django.contrib import admin

from maps.models import GeographicBox


@admin.register(GeographicBox)
class GeographicBoxAdmin(admin.ModelAdmin):
    list_display = (
        "identifier",
        "dfn",
        "has_location",
        "validation_radius_m",
        "location_validation_enabled",
        "active",
    )

    list_filter = (
        "location_validation_enabled",
        "active",
        "dfn",
    )

    search_fields = (
        "identifier",
        "dfn__code",
        "description",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    list_editable = (
        "validation_radius_m",
        "location_validation_enabled",
    )

    list_select_related = ("dfn",)

    ordering = ("identifier",)

    @admin.display(
        boolean=True,
        description="Official location",
    )
    def has_location(self, obj):
        return obj.has_official_location
