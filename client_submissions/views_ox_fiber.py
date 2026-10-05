from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET

from usuarios.decoradores import rol_requerido

from .views import (_assert_manage_permission, _get_selected_billings,
                    _parse_ids)


def _derive_dfn(project_id: str) -> str:
    """
    Deriva el DFN utilizado por OX-FIBER desde el Project ID.

    Ejemplo:
        0913RA_06_5011-006-9 -> 0913RA_06
    """

    normalized = (project_id or "").strip()

    parts = normalized.split("_")

    if len(parts) < 2:
        raise ValueError(f"Cannot derive OX-FIBER DFN from Project ID: {normalized!r}")

    first = parts[0].strip()
    second = parts[1].strip()

    if not first or not second:
        raise ValueError(f"Cannot derive OX-FIBER DFN from Project ID: {normalized!r}")

    return f"{first}_{second}"


@login_required
@rol_requerido(
    "admin"
)
@require_GET
def start_from_invoices(
    request: HttpRequest,
) -> HttpResponse:
    """
    Entrada independiente de OX-FIBER desde Invoice List.

    PHASE 1:
    - Recibe los invoice IDs seleccionados.
    - Recupera los billings autorizados.
    - Obtiene Project ID.
    - Deriva DFN.
    - NO ejecuta Playwright todavía.
    - NO modifica estados.
    - NO toca el flujo Smartsheet.
    """

    _assert_manage_permission(request)

    ids = _parse_ids(request.GET.get("ids"))

    if not ids:
        messages.warning(
            request,
            "Select at least one invoice.",
        )

        return redirect("facturacion:invoices")

    billings = _get_selected_billings(ids)

    if not billings:
        messages.warning(
            request,
            "No valid invoices were found in the selection.",
        )

        return redirect("facturacion:invoices")

    found_ids = {billing.pk for billing in billings}

    missing_ids = [billing_id for billing_id in ids if billing_id not in found_ids]

    if missing_ids:
        messages.warning(
            request,
            (
                "Some selected invoices could not be loaded: "
                + ", ".join(str(value) for value in missing_ids)
            ),
        )

    results = []

    for billing in billings:
        project_id = (billing.proyecto_id or "").strip()

        try:
            dfn = _derive_dfn(project_id)
        except ValueError as exc:
            messages.error(
                request,
                str(exc),
            )

            return redirect("facturacion:invoices")

        results.append(
            {
                "invoice_id": billing.pk,
                "project_id": project_id,
                "dfn": dfn,
            }
        )

    print("=" * 60)
    print("OX-FIBER PHASE 1 — INVOICE INPUT")
    print("=" * 60)

    for result in results:
        print(f"PASS: Invoice ID = " f"{result['invoice_id']}")

        print(f"PASS: Project ID = " f"{result['project_id']}")

        print(f"PASS: DFN derived = " f"{result['dfn']}")

    print(
        "STOP: OX-FIBER input validation completed. "
        "Playwright has NOT been executed."
    )

    print("=" * 60)

    messages.success(
        request,
        (
            f"OX-FIBER Phase 1 input validated for "
            f"{len(results)} project(s). "
            "No portal submission was executed."
        ),
    )

    return redirect("facturacion:invoices")
