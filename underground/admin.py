from django.contrib import admin

from .models import Route, RouteSegment, SegmentStageProgress, Stage


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "start_ft", "end_ft", "segment_length_ft", "updated_at")
    search_fields = ("name",)


@admin.register(RouteSegment)
class RouteSegmentAdmin(admin.ModelAdmin):
    list_display = ("id", "route", "index", "from_ft", "to_ft")
    list_filter = ("route",)
    ordering = ("route", "index")


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "order", "is_active", "requires_prev_stage")
    list_filter = ("is_active",)
    ordering = ("order", "id")


@admin.register(SegmentStageProgress)
class SegmentStageProgressAdmin(admin.ModelAdmin):
    list_display = ("id", "segment", "stage", "status", "updated_by", "updated_at")
    list_filter = ("stage", "status")
    search_fields = ("segment__route__name", "notes")