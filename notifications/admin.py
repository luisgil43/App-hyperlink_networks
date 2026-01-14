from django.contrib import admin

from .models import CronDailyRun


@admin.register(CronDailyRun)
class CronDailyRunAdmin(admin.ModelAdmin):
    list_display = ("name", "run_date", "ok", "created_at")
    list_filter = ("name", "ok", "run_date")
    search_fields = ("name", "log")