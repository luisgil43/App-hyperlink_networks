from django.db.models import Q

from facturacion.models import Proyecto

try:
    from usuarios.models import ProyectoAsignacion
except Exception:
    ProyectoAsignacion = None


def user_is_admin_general(user):
    return bool(
        user
        and user.is_authenticated
        and (
            getattr(user, "is_superuser", False)
            or getattr(user, "es_admin_general", False)
            or getattr(user, "is_staff", False)
        )
    )


def user_can_view_legacy_history(user):
    return bool(
        user_is_admin_general(user) or getattr(user, "es_usuario_historial", False)
    )


def user_is_pm(user):
    return bool(user and user.is_authenticated and getattr(user, "es_pm", False))


def user_is_supervisor(user):
    return bool(
        user and user.is_authenticated and getattr(user, "es_supervisor", False)
    )


def user_is_facturacion(user):
    return bool(
        user and user.is_authenticated and getattr(user, "es_facturacion", False)
    )


def user_is_client(user):
    return bool(
        user
        and user.is_authenticated
        and (
            getattr(user, "es_cliente", False) or getattr(user, "rol", "") == "cliente"
        )
    )


def can_manage_deliverables(user):
    return bool(
        user_is_admin_general(user)
        or user_is_pm(user)
        or user_is_supervisor(user)
        or user_is_facturacion(user)
    )


def can_publish_deliverables(user):
    return bool(user_is_admin_general(user) or user_is_pm(user))


def can_revoke_deliverables(user):
    return bool(user_is_admin_general(user) or user_is_pm(user))


def can_view_client_portal(user):
    return user_is_client(user)


def get_user_allowed_project_keys(user):
    """
    Devuelve las llaves de proyecto que el usuario puede ver.

    Incluye:
    - Proyecto.id
    - Proyecto.nombre
    - Proyecto.codigo

    Esto permite comparar contra DeliveryPackageFile.project_id,
    que puede venir como Project ID/código/nombre según el origen.
    """
    if not user or not user.is_authenticated:
        return set()

    if user_can_view_legacy_history(user):
        return None

    try:
        from core.permissions import filter_queryset_by_access

        proyectos_user = filter_queryset_by_access(
            Proyecto.objects.all(),
            user,
            "id",
        )
    except Exception:
        proyectos_user = Proyecto.objects.none()

    allowed_keys = set()

    for p in proyectos_user:
        nombre = (getattr(p, "nombre", "") or "").strip()
        codigo = (getattr(p, "codigo", "") or "").strip()

        if nombre:
            allowed_keys.add(nombre)
            allowed_keys.add(nombre.lower())

        if codigo:
            allowed_keys.add(str(codigo).strip())
            allowed_keys.add(str(codigo).strip().lower())

        allowed_keys.add(str(p.id).strip())

    return allowed_keys


def user_can_access_project_id(user, project_id):
    """
    Valida si el usuario puede acceder a un Project ID específico.
    """
    project_id = str(project_id or "").strip()

    if not project_id:
        return False

    allowed_keys = get_user_allowed_project_keys(user)

    if allowed_keys is None:
        return True

    return project_id in allowed_keys or project_id.lower() in allowed_keys


def user_can_access_delivery_package(user, package):
    """
    Valida acceso a un paquete completo.

    Regla importante:
    Si el paquete tiene varios proyectos, el usuario debe tener acceso
    a TODOS los Project IDs activos del paquete.
    """
    if not user or not user.is_authenticated:
        return False

    if user_can_view_legacy_history(user):
        return True

    if not can_manage_deliverables(user):
        return False

    allowed_keys = get_user_allowed_project_keys(user)

    if not allowed_keys:
        return False

    files = list(package.files.filter(is_active=True).only("project_id"))

    # Si todavía es draft sin archivos, solo lo puede ver quien lo creó.
    if not files:
        return package.created_by_id == user.id

    for f in files:
        project_id = str(getattr(f, "project_id", "") or "").strip()

        if not project_id:
            return False

        if project_id not in allowed_keys and project_id.lower() not in allowed_keys:
            return False

    return True


def filter_delivery_packages_by_user(qs, user):
    """
    Filtra la lista de paquetes según acceso por proyecto.

    Admin/historial: ve todo.
    Usuario normal: ve paquetes con archivos de sus proyectos.
    Draft sin archivos: solo si lo creó él.
    """
    if not user or not user.is_authenticated:
        return qs.none()

    if user_can_view_legacy_history(user):
        return qs

    if not can_manage_deliverables(user):
        return qs.none()

    allowed_keys = get_user_allowed_project_keys(user)

    if not allowed_keys:
        return qs.filter(created_by=user, files__isnull=True)

    allowed_variants = set()

    for key in allowed_keys:
        key = str(key or "").strip()
        if key:
            allowed_variants.add(key)
            allowed_variants.add(key.lower())

    return qs.filter(
        Q(files__project_id__in=allowed_variants)
        | Q(created_by=user, files__isnull=True)
    ).distinct()
