from django.apps import AppConfig
import sys


class UsuariosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'usuarios'

    def ready(self):
        # Importar señales (no accede a la BD aquí)
        import usuarios.signals

        # Iniciar el scheduler SOLO si el servidor está corriendo
        if 'runserver' in sys.argv or 'gunicorn' in sys.argv:
            from . import schedulers
            schedulers.iniciar_scheduler()
