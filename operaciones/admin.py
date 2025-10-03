# operations/admin.py
from django.contrib import admin
from .models import AdjustmentEntry


@admin.register(AdjustmentEntry)
class AdjustmentEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "technician", "week",
                    "adjustment_type", "amount", "created_at")
    list_filter = ("adjustment_type", "week")
    search_fields = ("technician__username", "technician__first_name",
                     "technician__last_name", "project_id", "project", "client")
