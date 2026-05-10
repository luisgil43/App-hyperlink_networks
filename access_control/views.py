from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from access_control.models import AccessPermission, RoleAccessPermission
from access_control.services import clear_access_control_cache
from usuarios.decoradores import rol_requerido

BILLING_PERMISSION_SEED = [
    {
        "key": "billing.create_billing",
        "label": "Create billing",
        "description": "Allows creating new billings.",
        "module": "Billing",
        "order": 1,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": True,
            "facturacion": True,
            "emision_facturacion": False,
        },
    },
    {
        "key": "billing.edit_billing",
        "label": "Edit billing",
        "description": "Allows editing existing billings.",
        "module": "Billing",
        "order": 2,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": True,
            "facturacion": True,
            "emision_facturacion": False,
        },
    },
    {
        "key": "billing.view_technical_amounts",
        "label": "View technical billing amounts",
        "description": "Allows viewing technician-related billing totals, technical rates and technical subtotals.",
        "module": "Billing",
        "order": 10,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": False,
            "facturacion": True,
            "emision_facturacion": True,
        },
    },
    {
        "key": "billing.view_company_amounts",
        "label": "View company billing amounts",
        "description": "Allows viewing company rates and company subtotals.",
        "module": "Billing",
        "order": 20,
        "defaults": {
            "admin": True,
            "pm": False,
            "supervisor": False,
            "facturacion": True,
            "emision_facturacion": True,
        },
    },
    {
        "key": "billing.view_real_company_billing",
        "label": "View real company billing",
        "description": "Allows viewing the real company billing value.",
        "module": "Billing",
        "order": 30,
        "defaults": {
            "admin": True,
            "pm": False,
            "supervisor": False,
            "facturacion": True,
            "emision_facturacion": True,
        },
    },
    {
        "key": "billing.view_billing_difference",
        "label": "View billing difference",
        "description": "Allows viewing the difference between company billing and real company billing.",
        "module": "Billing",
        "order": 40,
        "defaults": {
            "admin": True,
            "pm": False,
            "supervisor": False,
            "facturacion": True,
            "emision_facturacion": True,
        },
    },
    {
        "key": "billing.edit_real_week",
        "label": "Edit real pay week",
        "description": "Allows editing the real pay week / payment week lines.",
        "module": "Billing",
        "order": 50,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": False,
            "facturacion": True,
            "emision_facturacion": False,
        },
    },
    {
        "key": "billing.delete_billing",
        "label": "Delete billing",
        "description": "Allows deleting billings.",
        "module": "Billing",
        "order": 60,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": False,
            "facturacion": False,
            "emision_facturacion": False,
        },
    },
    {
        "key": "billing.send_finance",
        "label": "Send billing to Finance",
        "description": "Allows sending approved billings and direct discounts to Finance.",
        "module": "Billing",
        "order": 70,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": False,
            "facturacion": False,
            "emision_facturacion": False,
        },
    },
    {
        "key": "billing.export_billing",
        "label": "Export client billing",
        "description": "Allows exporting client billing records with company amounts.",
        "module": "Billing",
        "order": 80,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": False,
            "facturacion": True,
            "emision_facturacion": False,
        },
    },
    {
        "key": "billing.export_operational_billing",
        "label": "Export operational billing",
        "description": "Allows exporting operational billing records without prices or subtotals.",
        "module": "Billing",
        "order": 81,
        "defaults": {
            "admin": True,
            "pm": True,
            "supervisor": True,
            "facturacion": True,
            "emision_facturacion": False,
        },
    },
]


ROLES_FOR_MATRIX = [
    {
        "key": "admin",
        "label": "Admin",
    },
    {
        "key": "pm",
        "label": "PM",
    },
    {
        "key": "supervisor",
        "label": "Supervisor",
    },
    {
        "key": "facturacion",
        "label": "Billing / Finance",
    },
    {
        "key": "emision_facturacion",
        "label": "Invoice Issuance",
    },
]


def seed_access_matrix():
    """
    Crea permisos iniciales y valores por defecto si no existen.
    No pisa cambios existentes.
    """

    for item in BILLING_PERMISSION_SEED:
        permission, _created = AccessPermission.objects.get_or_create(
            key=item["key"],
            defaults={
                "label": item["label"],
                "description": item["description"],
                "module": item["module"],
                "order": item["order"],
                "is_active": True,
            },
        )

        changed = False

        if permission.label != item["label"]:
            permission.label = item["label"]
            changed = True

        if permission.description != item["description"]:
            permission.description = item["description"]
            changed = True

        if permission.module != item["module"]:
            permission.module = item["module"]
            changed = True

        if permission.order != item["order"]:
            permission.order = item["order"]
            changed = True

        if changed:
            permission.save(
                update_fields=[
                    "label",
                    "description",
                    "module",
                    "order",
                ]
            )

        defaults = item.get("defaults") or {}

        for role in ROLES_FOR_MATRIX:
            role_key = role["key"]
            RoleAccessPermission.objects.get_or_create(
                permission=permission,
                role_name=role_key,
                defaults={
                    "enabled": bool(defaults.get(role_key, False)),
                },
            )


@login_required
@rol_requerido("admin")
@require_http_methods(["GET", "POST"])
def matrix(request):
    """
    Access Matrix - Hyperlink Networks.
    Permite al admin configurar permisos visibles por rol.
    """

    seed_access_matrix()

    if request.method == "POST":
        with transaction.atomic():
            permissions = AccessPermission.objects.filter(is_active=True)

            for permission in permissions:
                for role in ROLES_FOR_MATRIX:
                    role_key = role["key"]
                    field_name = f"perm_{permission.id}_{role_key}"

                    enabled = request.POST.get(field_name) == "on"

                    obj, _created = RoleAccessPermission.objects.get_or_create(
                        permission=permission,
                        role_name=role_key,
                        defaults={
                            "enabled": enabled,
                        },
                    )

                    if obj.enabled != enabled:
                        obj.enabled = enabled
                        obj.save(update_fields=["enabled"])

        # ✅ limpiar / invalidar cache DESPUÉS de guardar los cambios
        clear_access_control_cache()

        messages.success(request, "Access Matrix updated successfully.")
        return redirect("access_control:matrix")

    permissions = list(
        AccessPermission.objects.filter(is_active=True)
        .prefetch_related("role_permissions")
        .order_by("module", "order", "label")
    )

    matrix_rows = []

    for permission in permissions:
        role_map = {
            rp.role_name: rp.enabled for rp in permission.role_permissions.all()
        }

        matrix_rows.append(
            {
                "permission": permission,
                "roles": [
                    {
                        "key": role["key"],
                        "label": role["label"],
                        "enabled": bool(role_map.get(role["key"], False)),
                    }
                    for role in ROLES_FOR_MATRIX
                ],
            }
        )

    return render(
        request,
        "access_control/matrix.html",
        {
            "roles": ROLES_FOR_MATRIX,
            "matrix_rows": matrix_rows,
        },
    )
