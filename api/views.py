# api/views.py

from django.conf import settings
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from operaciones.models import SesionBilling, SesionBillingTecnico

from .security import is_api_feature_enabled

# ==============================
# Helpers API security
# ==============================


def _api_disabled_response(feature_code):
    return Response(
        {
            "detail": "This API feature is disabled.",
            "feature": feature_code,
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _require_api_feature(feature_code, user=None):
    return is_api_feature_enabled(feature_code, user=user)


class MobileTokenObtainPairView(TokenObtainPairView):
    """
    Login JWT para app móvil.

    Protegido por feature:
    - mobile_auth
    """

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        if not _require_api_feature("mobile_auth"):
            return _api_disabled_response("mobile_auth")

        return super().post(request, *args, **kwargs)


# ==============================
# Helpers usuario
# ==============================


def _user_roles(user):
    """
    Devuelve roles básicos del usuario autenticado para la app móvil.
    """
    roles = []

    if getattr(user, "is_superuser", False):
        roles.append("superuser")

    if getattr(user, "is_staff", False):
        roles.append("staff")

    rol = getattr(user, "rol", None)
    if rol:
        roles.append(str(rol))

    if hasattr(user, "roles"):
        try:
            for role in user.roles.all():
                roles.append(str(role))
        except Exception:
            pass

    return list(dict.fromkeys(roles))


def _is_admin_like(user):
    """
    Define si el usuario móvil puede ver más que sus propios billing.

    En desarrollo:
    - superuser
    - rol admin / supervisor / pm

    Después lo conectamos más fino a access_control/proyectos.
    """
    roles = set(_user_roles(user))

    if getattr(user, "is_superuser", False):
        return True

    if "admin" in roles or "supervisor" in roles or "pm" in roles:
        return True

    return False


# ==============================
# Helpers billing
# ==============================


def _billing_status_label(estado):
    labels = {
        "asignado": "Assigned",
        "en_proceso": "In progress",
        "en_revision_supervisor": "In supervisor review",
        "rechazado_supervisor": "Rejected by supervisor",
        "aprobado_supervisor": "Approved by supervisor",
        "rechazado_pm": "Rejected by PM",
        "aprobado_pm": "Approved by PM",
        "aprobado_finanzas": "Approved by finance",
    }
    return labels.get(estado, estado or "")


def _finance_status_label(finance_status):
    labels = {
        "none": "—",
        "review_discount": "Review discount",
        "discount_applied": "Discount applied",
        "sent": "Sent to Finance",
        "pending": "Pending payment",
        "in_review": "In review",
        "rejected": "Rejected",
        "paid": "Paid",
    }
    return labels.get(finance_status, finance_status or "—")


def _safe_str(value):
    if value is None:
        return ""
    return str(value)


def _serialize_technicians(sesion):
    technicians = []

    for st in sesion.tecnicos_sesion.all():
        tecnico = getattr(st, "tecnico", None)

        technicians.append(
            {
                "id": getattr(st, "tecnico_id", None),
                "name": str(tecnico) if tecnico else "",
                "status": getattr(st, "estado", ""),
                "percentage": str(getattr(st, "porcentaje", "")),
                "comments": getattr(st, "comentarios", "") or "",
            }
        )

    return technicians


def _serialize_billing_basic(sesion):
    """
    Serializador liviano para listado móvil.
    No exponemos precios en esta fase.
    """
    created = getattr(sesion, "creado_en", None)

    return {
        "id": sesion.id,
        "project_id": _safe_str(getattr(sesion, "proyecto_id", "")),
        "client": _safe_str(getattr(sesion, "cliente", "")),
        "city": _safe_str(getattr(sesion, "ciudad", "")),
        "project": _safe_str(getattr(sesion, "proyecto", "")),
        "office": _safe_str(getattr(sesion, "oficina", "")),
        "address": _safe_str(getattr(sesion, "direccion_proyecto", "")),
        "date": created.date().isoformat() if created else None,
        "status": _safe_str(getattr(sesion, "estado", "")),
        "status_label": _billing_status_label(getattr(sesion, "estado", "")),
        "finance_status": _safe_str(getattr(sesion, "finance_status", "")),
        "finance_status_label": _finance_status_label(
            getattr(sesion, "finance_status", "")
        ),
        "is_direct_discount": bool(getattr(sesion, "is_direct_discount", False)),
        "is_cable_installation": bool(getattr(sesion, "is_cable_installation", False)),
        "technicians": _serialize_technicians(sesion),
    }


def _serialize_item_basic(item):
    """
    Serializador de item para detalle móvil.
    No exponemos precios en esta fase.
    """
    return {
        "id": item.id,
        "job_code": _safe_str(getattr(item, "codigo_trabajo", "")),
        "work_type": _safe_str(getattr(item, "tipo_trabajo", "")),
        "description": _safe_str(getattr(item, "descripcion", "")),
        "uom": _safe_str(getattr(item, "unidad_medida", "")),
        "qty": str(getattr(item, "cantidad", "")),
    }


def _mobile_billing_queryset_for_user(user):
    """
    Queryset base de billing para móvil.
    """
    qs = SesionBilling.objects.all()

    if not _is_admin_like(user):
        sesion_ids = SesionBillingTecnico.objects.filter(tecnico=user).values_list(
            "sesion_id", flat=True
        )

        qs = qs.filter(id__in=sesion_ids)

    return qs


def _user_can_view_billing(user, sesion):
    if _is_admin_like(user):
        return True

    return SesionBillingTecnico.objects.filter(
        sesion=sesion,
        tecnico=user,
    ).exists()


# ==============================
# Auth API
# ==============================


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_me(request):
    """
    Endpoint base para la app móvil.
    """
    if not _require_api_feature("mobile_auth", user=request.user):
        return _api_disabled_response("mobile_auth")

    user = request.user

    return Response(
        {
            "id": user.id,
            "username": getattr(user, "username", ""),
            "email": getattr(user, "email", ""),
            "first_name": getattr(user, "first_name", ""),
            "last_name": getattr(user, "last_name", ""),
            "full_name": (
                user.get_full_name() if hasattr(user, "get_full_name") else str(user)
            ),
            "roles": _user_roles(user),
            "is_staff": bool(getattr(user, "is_staff", False)),
            "is_superuser": bool(getattr(user, "is_superuser", False)),
        }
    )


# ==============================
# Billing API móvil
# ==============================


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_my_billing_list(request):
    """
    Lista de billing para la app móvil.
    """
    if not _require_api_feature("mobile_billing", user=request.user):
        return _api_disabled_response("mobile_billing")

    user = request.user

    qs = _mobile_billing_queryset_for_user(user)

    q = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    limit_raw = request.GET.get("limit") or "50"

    if q:
        qs = qs.filter(
            Q(proyecto_id__icontains=q)
            | Q(cliente__icontains=q)
            | Q(ciudad__icontains=q)
            | Q(proyecto__icontains=q)
            | Q(oficina__icontains=q)
            | Q(direccion_proyecto__icontains=q)
        )

    if status_filter:
        qs = qs.filter(estado=status_filter)

    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 50

    limit = max(1, min(limit, 100))

    qs = qs.prefetch_related("tecnicos_sesion__tecnico").order_by("-creado_en")[:limit]

    results = [_serialize_billing_basic(sesion) for sesion in qs]

    return Response(
        {
            "count": len(results),
            "results": results,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_my_billing_detail(request, pk):
    """
    Detalle de billing para la app móvil.
    """
    if not _require_api_feature("mobile_billing", user=request.user):
        return _api_disabled_response("mobile_billing")

    user = request.user

    try:
        sesion = SesionBilling.objects.prefetch_related(
            "tecnicos_sesion__tecnico",
            "items",
        ).get(pk=pk)
    except SesionBilling.DoesNotExist:
        return Response(
            {"detail": "Billing not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not _user_can_view_billing(user, sesion):
        return Response(
            {"detail": "You do not have permission to view this billing."},
            status=status.HTTP_403_FORBIDDEN,
        )

    data = _serialize_billing_basic(sesion)

    items = []
    for item in sesion.items.all():
        items.append(_serialize_item_basic(item))

    estado = getattr(sesion, "estado", "")

    data["items"] = items
    data["actions"] = {
        "can_upload": estado in ["asignado", "en_proceso", "rechazado_supervisor"],
        "can_send_review": estado in ["asignado", "en_proceso", "rechazado_supervisor"],
        "can_approve": _is_admin_like(user) and estado == "en_revision_supervisor",
        "can_reject": _is_admin_like(user) and estado == "en_revision_supervisor",
    }

    return Response(data)
