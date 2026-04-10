from django.contrib import admin

from .models import CableAssignmentRequirement, CableEvidence, CableRequirement


@admin.register(CableRequirement)
class CableRequirementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "billing",
        "sequence_no",
        "handhole",
        "planned_reserve_ft",
        "order",
        "updated_at",
    )
    list_filter = ("billing",)
    search_fields = (
        "billing__proyecto_id",
        "billing__cliente",
        "handhole",
    )
    ordering = ("billing", "order", "sequence_no", "id")


@admin.register(CableAssignmentRequirement)
class CableAssignmentRequirementAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "assignment",
        "requirement",
        "status",
        "start_ft",
        "end_ft",
        "installed_ft",
        "end_ft_overridden",
        "updated_at",
    )
    list_filter = (
        "status",
        "end_ft_overridden",
        "assignment__sesion",
    )
    search_fields = (
        "assignment__sesion__proyecto_id",
        "assignment__tecnico__username",
        "assignment__tecnico__first_name",
        "assignment__tecnico__last_name",
        "requirement__handhole",
    )
    ordering = (
        "assignment__sesion",
        "requirement__order",
        "requirement__sequence_no",
        "id",
    )


@admin.register(CableEvidence)
class CableEvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "assignment_requirement",
        "review_status",
        "taken_at",
        "reviewed_at",
        "created_at",
    )
    list_filter = (
        "review_status",
        "taken_at",
        "reviewed_at",
    )
    search_fields = (
        "assignment_requirement__assignment__sesion__proyecto_id",
        "assignment_requirement__requirement__handhole",
        "note",
        "review_comment",
    )
    ordering = ("-created_at",)
