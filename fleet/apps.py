# fleet/apps.py
from django.apps import AppConfig


class FleetConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fleet"

    def ready(self):
        from .signals import connect_fleet_signals
        connect_fleet_signals()