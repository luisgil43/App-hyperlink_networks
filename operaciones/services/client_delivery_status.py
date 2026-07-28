from __future__ import annotations

from collections.abc import Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from operaciones.models import SesionBilling


def _normalize_billing_ids(
    billing_sessions,
) -> list[int]:
    """
    Convierte una colección de SesionBilling, IDs o QuerySets
    en una lista única de IDs válidos.

    Acepta:

        [SesionBilling(...), SesionBilling(...)]
        [1, 2, 3]
        QuerySet de SesionBilling
        Un único SesionBilling
        Un único ID
    """

    if billing_sessions is None:
        return []

    if isinstance(
        billing_sessions,
        SesionBilling,
    ):
        billing_sessions = [
            billing_sessions,
        ]

    elif isinstance(
        billing_sessions,
        int,
    ):
        billing_sessions = [
            billing_sessions,
        ]

    elif not isinstance(
        billing_sessions,
        Iterable,
    ):
        billing_sessions = [
            billing_sessions,
        ]

    billing_ids = []
    seen = set()

    for billing in billing_sessions:
        if isinstance(
            billing,
            SesionBilling,
        ):
            billing_id = billing.pk

        else:
            try:
                billing_id = int(
                    billing,
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

        if not billing_id:
            continue

        if billing_id in seen:
            continue

        seen.add(
            billing_id,
        )

        billing_ids.append(
            billing_id,
        )

    return billing_ids


@transaction.atomic
def mark_billing_sessions_sent_to_client(
    billing_sessions,
) -> dict:
    """
    Marca uno o varios SesionBilling como enviados al cliente.

    Reglas compartidas por:

        Client Submissions
        Client Deliverables

    Solamente modifica Billings que cumplan:

        finance_status == "sent"
        is_direct_discount == False

    No modifica estados posteriores como:

        sent_to_client
        pending
        in_review
        rejected
        paid

    Devuelve:

        {
            "requested": int,
            "updated": int,
            "skipped": int,
            "updated_ids": list[int],
        }
    """

    billing_ids = _normalize_billing_ids(
        billing_sessions,
    )

    if not billing_ids:
        return {
            "requested": 0,
            "updated": 0,
            "skipped": 0,
            "updated_ids": [],
        }

    locked_billings = list(
        SesionBilling.objects.select_for_update()
        .filter(
            id__in=billing_ids,
        )
        .order_by(
            "id",
        )
    )

    now = timezone.now()

    updated_ids = []

    for billing in locked_billings:
        if bool(
            getattr(
                billing,
                "is_direct_discount",
                False,
            )
        ):
            continue

        current_status = str(
            getattr(
                billing,
                "finance_status",
                "",
            )
            or ""
        ).strip()

        if current_status != "sent":
            continue

        billing.finance_status = "sent_to_client"

        update_fields = [
            "finance_status",
        ]

        if hasattr(
            billing,
            "finance_updated_at",
        ):
            billing.finance_updated_at = now

            update_fields.append(
                "finance_updated_at",
            )

        if hasattr(
            billing,
            "updated_at",
        ):
            update_fields.append(
                "updated_at",
            )

        billing.save(
            update_fields=list(
                dict.fromkeys(
                    update_fields,
                )
            )
        )

        updated_ids.append(
            billing.pk,
        )

    return {
        "requested": len(
            billing_ids,
        ),
        "updated": len(
            updated_ids,
        ),
        "skipped": (
            len(
                billing_ids,
            )
            - len(
                updated_ids,
            )
        ),
        "updated_ids": updated_ids,
    }


def resolve_delivery_package_billings(
    package,
):
    """
    Recupera los SesionBilling relacionados con un DeliveryPackage.

    Primero utiliza DeliveryPackageFile.billing_session.

    También busca por project_id para soportar archivos agregados desde
    el flujo manual, donde DeliveryPackageFile puede no tener asignado
    billing_session.

    La búsqueda por Project ID solo considera Billings pendientes de
    envío al cliente:

        finance_status == "sent"

    Esto evita modificar estados financieros posteriores.
    """

    linked_billing_ids = list(
        package.files.filter(
            is_active=True,
            billing_session_id__isnull=False,
        )
        .values_list(
            "billing_session_id",
            flat=True,
        )
        .distinct()
    )

    project_ids = []

    for project_id in (
        package.files.filter(
            is_active=True,
        )
        .exclude(
            project_id="",
        )
        .values_list(
            "project_id",
            flat=True,
        )
        .distinct()
    ):
        clean_project_id = str(
            project_id or "",
        ).strip()

        if not clean_project_id:
            continue

        if clean_project_id in project_ids:
            continue

        project_ids.append(
            clean_project_id,
        )

    query = Q()

    if linked_billing_ids:
        query |= Q(
            id__in=linked_billing_ids,
        )

    project_query = Q()

    for project_id in project_ids:
        project_query |= Q(
            proyecto_id__iexact=project_id,
        )

        project_query |= Q(
            proyecto__iexact=project_id,
        )

    if project_ids:
        query |= project_query

    if not query:
        return SesionBilling.objects.none()

    return (
        SesionBilling.objects.filter(
            query,
        )
        .filter(
            finance_status="sent",
            is_direct_discount=False,
        )
        .distinct()
        .order_by(
            "id",
        )
    )


@transaction.atomic
def mark_delivery_package_billings_sent_to_client(
    package,
    *,
    billing_sessions=None,
) -> dict:
    """
    Marca como enviados al cliente los Billings de un package publicado.

    Si billing_sessions es proporcionado, utiliza exactamente esa selección.
    Esto se usa en el flujo desde Invoices para no afectar otros Billings
    que pudieran compartir el mismo Project ID.

    Si billing_sessions no se proporciona, resuelve los Billings mediante:

        DeliveryPackageFile.billing_session
        DeliveryPackageFile.project_id

    El package debe encontrarse publicado.
    """

    package_status = str(
        getattr(
            package,
            "status",
            "",
        )
        or ""
    ).strip()

    published_status = str(
        getattr(
            package,
            "STATUS_PUBLISHED",
            "published",
        )
    ).strip()

    if package_status != published_status:
        return {
            "requested": 0,
            "updated": 0,
            "skipped": 0,
            "updated_ids": [],
            "package_not_published": True,
        }

    if billing_sessions is None:
        billing_sessions = resolve_delivery_package_billings(
            package,
        )

    result = mark_billing_sessions_sent_to_client(
        billing_sessions,
    )

    result["package_not_published"] = False

    return result
