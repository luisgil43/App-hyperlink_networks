from django.urls import path

from . import views, views_cron

app_name = "notifications"

urlpatterns = [
    path("", views.notification_center, name="center"),
    # ✅ cron general
    path("diario/", views_cron.cron_daily_general, name="cron_daily_general"),
]
