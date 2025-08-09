# usuarios/signals.py
from django.apps import apps
from django.db import connection
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

User = get_user_model()


def table_exists(name: str) -> bool:
    try:
        return name in connection.introspection.table_names()
    except Exception:
        return False

# ======= POST SAVE (crear/actualizar usuario) =======


@receiver(post_save, sender=User)
def asociar_ficha_al_crear_usuario(sender, instance, created, **kwargs):
    # No ejecutar durante migraciones/loaddata
    if kwargs.get('raw', False):
        return

    # Sin identidad no hay nada que asociar
    if not getattr(instance, "identidad", None):
        return

    # Si la tabla de RRHH aún no existe, salimos silenciosamente
    if not table_exists('rrhh_fichaingreso'):
        return

    # Import diferido para evitar errores al cargar el módulo
    FichaIngreso = apps.get_model('rrhh', 'FichaIngreso')

    ficha = FichaIngreso.objects.filter(
        usuario__isnull=True,
        rut=instance.identidad
    ).first()
    if ficha:
        ficha.usuario = instance
        ficha.save()

# ======= POST MIGRATE (solo cuando migra 'usuarios') =======


@receiver(post_migrate, sender=apps.get_app_config('usuarios'))
def crear_roles_y_admin(sender, **kwargs):
    # Asegurar que las tablas mínimas existan
    if not (table_exists('usuarios_rol') and table_exists('usuarios_customuser')):
        return

    Rol = apps.get_model('usuarios', 'Rol')

    roles = [
        'admin', 'rrhh', 'pm', 'usuario', 'supervisor',
        'flota', 'prevencion', 'subcontrato', 'logistica', 'facturacion',
    ]
    for nombre in roles:
        Rol.objects.get_or_create(nombre=nombre)

    if not User.objects.filter(username='admin').exists():
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='admin123',
            first_name='Admin',
            last_name='General',
            is_active=True,
            is_staff=True,
            is_superuser=True,
            identidad='99999999-9',
        )
        rol_admin, _ = Rol.objects.get_or_create(nombre='admin')
        admin.roles.add(rol_admin)
        admin.save()
