# usuarios/apps.py
from django.apps import AppConfig
import sys
import os


class UsuariosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'usuarios'

    def ready(self):
        # PRAGMAs para SQLite
        from . import sqlite_pragmas  # noqa: F401

        # Señales existentes
        import usuarios.signals  # noqa: F401

        # Iniciar el scheduler SOLO una vez
        if 'runserver' in sys.argv:
            # Evita doble arranque con el autoreloader
            if os.environ.get('RUN_MAIN') == 'true':
                from . import schedulers
                schedulers.iniciar_scheduler()
        elif 'gunicorn' in sys.argv:
            from . import schedulers
            schedulers.iniciar_scheduler()
