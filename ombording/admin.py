from django.contrib import admin

from .models import (Ombording, OmbordingAuditLog, OmbordingDocument,
                     OmbordingEmailLog, OmbordingFieldReview,
                     OmbordingSignature, Position)


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Ombording)
class OmbordingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "first_name",
        "last_name",
        "email",
        "position",
        "status",
        "current_step",
        "created_at",
    )
    list_filter = ("status", "current_step", "position")
    search_fields = ("first_name", "last_name", "email", "position__name")


@admin.register(OmbordingFieldReview)
class OmbordingFieldReviewAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "ombording",
        "field_key",
        "review_status",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("step", "review_status")
    search_fields = (
        "ombording__first_name",
        "ombording__last_name",
        "field_key",
        "field_label",
    )


@admin.register(OmbordingDocument)
class OmbordingDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "ombording",
        "document_key",
        "label",
        "review_status",
        "created_at",
    )
    list_filter = ("document_key", "review_status")
    search_fields = ("ombording__first_name", "ombording__last_name", "label")


@admin.register(OmbordingSignature)
class OmbordingSignatureAdmin(admin.ModelAdmin):
    list_display = ("id", "ombording", "signature_name", "initials", "updated_at")


@admin.register(OmbordingAuditLog)
class OmbordingAuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "ombording", "action", "performed_by", "created_at")
    list_filter = ("action",)


@admin.register(OmbordingEmailLog)
class OmbordingEmailLogAdmin(admin.ModelAdmin):
    list_display = ("id", "ombording", "email_type", "recipient", "success", "sent_at")
    list_filter = ("email_type", "success")
