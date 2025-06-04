from django.apps import AppConfig


class LiquidacionesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'liquidaciones'
    # <- ✅ Este es el nombre que verás en el menú del admin
    verbose_name = 'Gestión de Liquidaciones'
