# usuarios/sqlite_pragmas.py
from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def configure_sqlite(sender, connection, **kwargs):
    if connection.vendor != 'sqlite':
        return
    cur = connection.cursor()
    cur.execute('PRAGMA journal_mode=WAL;')
    cur.execute('PRAGMA synchronous=NORMAL;')
    cur.execute('PRAGMA busy_timeout=5000;')  # ms
