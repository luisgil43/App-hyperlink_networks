from django.core.cache import cache

from access_control.models import RoleAccessPermission

CACHE_SECONDS = 60 * 5

ACCESS_CONTROL_VERSION_KEY = "access_control:version"


def normalize_role_name(role_name):
    return str(role_name or "").strip().lower()


def get_access_control_version():
    """
    Versión global del cache de permisos.

    Cuando cambia la Matrix, subimos esta versión.
    Así las llaves viejas dejan de usarse sin hacer cache.clear().
    """
    version = cache.get(ACCESS_CONTROL_VERSION_KEY)

    if version is None:
        version = 1
        cache.set(ACCESS_CONTROL_VERSION_KEY, version, None)

    return int(version)


def bump_access_control_version():
    """
    Invalida solo el cache lógico de Access Control.
    No borra otros caches del sistema.
    """
    version = get_access_control_version() + 1
    cache.set(ACCESS_CONTROL_VERSION_KEY, version, None)
    return version


def get_user_role_names(user):
    """
    Devuelve los roles reales del usuario en minúscula.
    Superuser/admin general se maneja aparte en user_can().
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []

    roles = []

    try:
        if hasattr(user, "roles"):
            roles = list(user.roles.values_list("nombre", flat=True))
    except Exception:
        roles = []

    return [normalize_role_name(r) for r in roles if normalize_role_name(r)]


def user_can(user, permission_key):
    """
    Consulta central de permisos.

    Regla:
    - Superuser siempre puede.
    - Admin general siempre puede.
    - Si algún rol activo del usuario tiene el permiso enabled=True, puede.
    - Usa cache versionado para que los cambios de Matrix apliquen inmediato.
    """

    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    if getattr(user, "es_admin_general", False):
        return True

    permission_key = str(permission_key or "").strip()
    if not permission_key:
        return False

    role_names = get_user_role_names(user)
    if not role_names:
        return False

    version = get_access_control_version()

    cache_key = "access_control:v{}:user_can:{}:{}:{}".format(
        version,
        getattr(user, "pk", "anon"),
        permission_key,
        ",".join(sorted(role_names)),
    )

    cached = cache.get(cache_key)
    if cached is not None:
        return bool(cached)

    allowed = RoleAccessPermission.objects.filter(
        permission__key=permission_key,
        permission__is_active=True,
        role_name__in=role_names,
        enabled=True,
    ).exists()

    cache.set(cache_key, bool(allowed), CACHE_SECONDS)

    return bool(allowed)


def clear_access_control_cache():
    """
    Mantiene el nombre de función que ya estás usando,
    pero ahora NO borra todo el cache del proyecto.

    Solo invalida el cache de Access Control cambiando la versión.
    """
    return bump_access_control_version()
