from __future__ import annotations

import re
from collections import Counter
from decimal import ROUND_CEILING, Decimal
from typing import Iterable

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from plan_reader.models import (MaterialCatalogItem, PlanReaderItem,
                                PlanReaderJob, PlanReaderMaterialRequest,
                                PlanReaderMaterialRequestItem)

# =============================================================================
# CONFIGURACIÓN GENERAL
# =============================================================================


TEN_PERCENT_MULTIPLIER = Decimal("1.10")
GENERIC_TDS_LABEL_NAME = "TDS LABEL"
GENERIC_TDS_LABEL_CALCULATION_KEY = "__tds_label_total__"


VALID_REQUEST_TYPES = {
    PlanReaderMaterialRequest.REQUEST_TYPE_SPLICING,
    PlanReaderMaterialRequest.REQUEST_TYPE_CABLE,
}


BOX_RULES = {
    "a4": MaterialCatalogItem.RULE_SPLICE_CASE_A4,
    "b8g_empty": MaterialCatalogItem.RULE_SPLICE_CASE_B8G_EMPTY,
    "b8g_1x2": MaterialCatalogItem.RULE_SPLICE_CASE_B8G_1X2,
    "b8g_1x4": MaterialCatalogItem.RULE_SPLICE_CASE_B8G_1X4,
    "b8g_1x8": MaterialCatalogItem.RULE_SPLICE_CASE_B8G_1X8,
    "c12": MaterialCatalogItem.RULE_SPLICE_CASE_C12,
}


TDS_LABEL_RULES = {
    "a4": MaterialCatalogItem.RULE_TDS_LABEL_A4,
    "b8g_empty": MaterialCatalogItem.RULE_TDS_LABEL_B8G_EMPTY,
    "b8g_1x2": MaterialCatalogItem.RULE_TDS_LABEL_B8G_1X2,
    "b8g_1x4": MaterialCatalogItem.RULE_TDS_LABEL_B8G_1X4,
    "b8g_1x8": MaterialCatalogItem.RULE_TDS_LABEL_B8G_1X8,
    "c12": MaterialCatalogItem.RULE_TDS_LABEL_C12,
}


SPLITTER_RULES = {
    "1x2": MaterialCatalogItem.RULE_SPLITTER_1X2,
    "1x4": MaterialCatalogItem.RULE_SPLITTER_1X4,
    "1x6": MaterialCatalogItem.RULE_SPLITTER_1X6,
    "1x8": MaterialCatalogItem.RULE_SPLITTER_1X8,
}


SPLICING_AUTOMATIC_RULES = {
    *BOX_RULES.values(),
    *TDS_LABEL_RULES.values(),
    *SPLITTER_RULES.values(),
    MaterialCatalogItem.RULE_SPLICE_SLEEVE_40MM,
    MaterialCatalogItem.RULE_SPLICE_SLEEVE_60MM,
}


# =============================================================================
# VALIDACIONES
# =============================================================================


def validate_request_type(
    request_type: str,
) -> str:
    normalized_request_type = str(request_type or "").strip().lower()

    if normalized_request_type not in VALID_REQUEST_TYPES:
        valid_values = ", ".join(sorted(VALID_REQUEST_TYPES))

        raise ValueError(
            "Invalid material request type. " f"Expected one of: {valid_values}."
        )

    return normalized_request_type


# =============================================================================
# NORMALIZACIÓN
# =============================================================================


def normalize_text(
    value: object,
) -> str:
    text = str(value or "").strip().upper()

    text = text.replace(
        "×",
        "X",
    )

    text = text.replace(
        ":",
        "X",
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    text = re.sub(
        r"\b1\s*X\s*(2|4|6|8)\b",
        r"1X\1",
        text,
    )

    return text.strip()


def normalize_splitter_ratio(
    value: object,
) -> str:
    text = normalize_text(value)

    match = re.search(
        r"\b1X(2|4|6|8)\b",
        text,
    )

    if not match:
        return ""

    return f"1x{match.group(1)}"


def quantity_with_ten_percent(
    quantity: int | Decimal,
) -> Decimal:
    decimal_quantity = Decimal(str(quantity or 0))

    if decimal_quantity <= 0:
        return Decimal("0")

    return (decimal_quantity * TEN_PERCENT_MULTIPLIER).quantize(
        Decimal("1"),
        rounding=ROUND_CEILING,
    )


# =============================================================================
# DETECCIÓN DE SPLITTERS
# =============================================================================


def get_item_splitter_ratios(
    item: PlanReaderItem,
) -> list[str]:
    """
    Devuelve todos los splitters detectados para un PlanReaderItem.

    Primero intenta leer splitter_lines. Si no encuentra datos allí,
    utiliza los campos heredados s_splitter y t_splitter.
    """

    ratios: list[str] = []

    splitter_lines = item.splitter_lines or []

    if isinstance(
        splitter_lines,
        list,
    ):
        for line in splitter_lines:
            if not isinstance(
                line,
                dict,
            ):
                continue

            candidate_values = [
                line.get("ratio"),
                line.get("splitter_ratio"),
                line.get("type"),
                line.get("raw_text"),
                line.get("text"),
            ]

            ratio = ""

            for candidate_value in candidate_values:
                ratio = normalize_splitter_ratio(candidate_value)

                if ratio:
                    break

            if ratio in SPLITTER_RULES:
                ratios.append(ratio)

    if ratios:
        return ratios

    for legacy_value in (
        item.s_splitter,
        item.t_splitter,
    ):
        ratio = normalize_splitter_ratio(legacy_value)

        if ratio in SPLITTER_RULES:
            ratios.append(ratio)

    return ratios


def get_box_splitter_ratio(
    item: PlanReaderItem,
) -> str:
    """
    Determina el splitter integrado usado para escoger el código
    de splice case B8G.
    """

    candidate_values = [
        item.calculated_box_type,
        item.detected_box_type,
        item.visible_type,
    ]

    for candidate_value in candidate_values:
        ratio = normalize_splitter_ratio(candidate_value)

        if ratio in {
            "1x2",
            "1x4",
            "1x8",
        }:
            return ratio

    return ""


# =============================================================================
# CLASIFICACIÓN DE CAJAS
# =============================================================================


def classify_box_type(
    item: PlanReaderItem,
) -> str:
    calculated = normalize_text(item.calculated_box_type)

    detected = normalize_text(item.detected_box_type)

    visible = normalize_text(item.visible_type)

    combined = " ".join(
        value
        for value in (
            calculated,
            detected,
            visible,
        )
        if value
    )

    if "C12" in combined:
        return "c12"

    if "A4" in combined:
        return "a4"

    if "B8G" not in combined:
        return ""

    integrated_splitter = get_box_splitter_ratio(item)

    if integrated_splitter == "1x2":
        return "b8g_1x2"

    if integrated_splitter == "1x4":
        return "b8g_1x4"

    if integrated_splitter == "1x8":
        return "b8g_1x8"

    return "b8g_empty"


# =============================================================================
# CÁLCULO DE SPLICING
# =============================================================================


def calculate_material_rule_quantities(
    items: Iterable[PlanReaderItem],
) -> tuple[
    dict[str, Decimal],
    dict[str, str],
]:
    """
    Calcula las cantidades automáticas para una solicitud Splicing.

    Reglas:

    - Splice cases:
        cantidad exacta de cajas detectadas por tipo.

    - TDS LABEL genérico:
        cantidad total de cajas detectadas + 10 %,
        redondeada hacia arriba.

    - Reglas antiguas de TDS por tipo:
        se mantienen calculadas por compatibilidad con solicitudes
        históricas, aunque las filas correspondientes estén inactivas.

    - Splitters:
        cantidad detectada por tipo + 10 %, redondeada hacia arriba.

    - Splice Sleeve 40MM:
        total de splices + 10 %, redondeado hacia arriba.

    - Splice Sleeve 60MM:
        siempre 0.

    Los PlanReaderItem marcados como duplicados se ignoran.
    """

    box_counts: Counter[str] = Counter()
    splitter_counts: Counter[str] = Counter()

    total_splices = 0

    for item in items:
        if item.is_duplicate:
            continue

        box_type = classify_box_type(
            item,
        )

        if box_type:
            box_counts[box_type] += 1

        for ratio in get_item_splitter_ratios(
            item,
        ):
            splitter_counts[ratio] += 1

        try:
            item_splice_count = int(
                item.splice_count or 0,
            )
        except (
            TypeError,
            ValueError,
        ):
            item_splice_count = 0

        total_splices += max(
            item_splice_count,
            0,
        )

    quantities: dict[str, Decimal] = {}
    notes: dict[str, str] = {}

    # -------------------------------------------------------------------------
    # Splice cases: cantidad exacta por tipo
    # -------------------------------------------------------------------------

    for box_type, rule in BOX_RULES.items():
        detected_quantity = int(
            box_counts.get(
                box_type,
                0,
            )
        )

        requested_quantity = Decimal(
            detected_quantity,
        )

        quantities[rule] = requested_quantity

        notes[rule] = (
            f"{detected_quantity} splice case(s) detected. "
            "No additional percentage applied."
        )

    # -------------------------------------------------------------------------
    # TDS LABEL genérico: total de cajas + 10 %
    # -------------------------------------------------------------------------

    total_detected_boxes = sum(int(quantity) for quantity in box_counts.values())

    total_tds_labels = quantity_with_ten_percent(
        total_detected_boxes,
    )

    quantities[GENERIC_TDS_LABEL_CALCULATION_KEY] = total_tds_labels

    notes[GENERIC_TDS_LABEL_CALCULATION_KEY] = (
        f"{total_detected_boxes} total splice case(s) detected. "
        f"10% additional applied. Requested: "
        f"{total_tds_labels} TDS label(s)."
    )

    # -------------------------------------------------------------------------
    # Reglas antiguas de TDS Labels por tipo
    # -------------------------------------------------------------------------

    for box_type, rule in TDS_LABEL_RULES.items():
        detected_quantity = int(
            box_counts.get(
                box_type,
                0,
            )
        )

        requested_quantity = quantity_with_ten_percent(
            detected_quantity,
        )

        quantities[rule] = requested_quantity

        notes[rule] = (
            f"{detected_quantity} splice case(s) of this type detected. "
            f"10% additional applied. Requested: "
            f"{requested_quantity} TDS label(s)."
        )

    # -------------------------------------------------------------------------
    # Splitters sin conectores: detectados + 10 %
    # -------------------------------------------------------------------------

    for ratio, rule in SPLITTER_RULES.items():
        detected_quantity = int(
            splitter_counts.get(
                ratio,
                0,
            )
        )

        requested_quantity = quantity_with_ten_percent(
            detected_quantity,
        )

        quantities[rule] = requested_quantity

        notes[rule] = (
            f"{detected_quantity} splitter(s) {ratio} detected. "
            f"10% additional applied. Requested: "
            f"{requested_quantity}. Without connectors."
        )

    # -------------------------------------------------------------------------
    # Splice Sleeve 40MM
    # -------------------------------------------------------------------------

    sleeves_40mm = quantity_with_ten_percent(
        total_splices,
    )

    quantities[MaterialCatalogItem.RULE_SPLICE_SLEEVE_40MM] = sleeves_40mm

    notes[MaterialCatalogItem.RULE_SPLICE_SLEEVE_40MM] = (
        f"{total_splices} splice(s) detected. "
        f"10% additional applied. Requested: "
        f"{sleeves_40mm} sleeve(s)."
    )

    # -------------------------------------------------------------------------
    # Splice Sleeve 60MM
    # -------------------------------------------------------------------------

    quantities[MaterialCatalogItem.RULE_SPLICE_SLEEVE_60MM] = Decimal("0")

    notes[MaterialCatalogItem.RULE_SPLICE_SLEEVE_60MM] = (
        "Automatic quantity configured as 0 for 60MM splice sleeves."
    )

    return quantities, notes


# =============================================================================
# CREACIÓN DE FILAS DEL CATÁLOGO
# =============================================================================


def get_initial_item_source(
    *,
    material_request: PlanReaderMaterialRequest,
    catalog_item: MaterialCatalogItem,
) -> str:
    """
    Define si una fila nace automática o manual.

    Splicing:
        las reglas configuradas para cajas, labels, splitters y sleeves
        son automáticas.

        La fila genérica llamada TDS LABEL también es automática, aunque
        actualmente conserve auto_rule='manual' en el catálogo.

    Cable:
        todas las filas son manuales.
    """

    if material_request.is_cable_request:
        return PlanReaderMaterialRequestItem.SOURCE_MANUAL

    normalized_material_name = normalize_text(
        catalog_item.material_name,
    )

    if normalized_material_name == GENERIC_TDS_LABEL_NAME:
        return PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC

    if catalog_item.auto_rule in SPLICING_AUTOMATIC_RULES:
        return PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC

    return PlanReaderMaterialRequestItem.SOURCE_MANUAL


@transaction.atomic
def synchronize_material_request_catalog(
    *,
    material_request: PlanReaderMaterialRequest,
) -> int:
    """
    Copia a la solicitud todos los materiales activos del catálogo.

    No elimina filas existentes y no crea duplicados.
    Esto permite ampliar el catálogo posteriormente sin perder
    cantidades ya introducidas en solicitudes anteriores.
    """

    catalog_items = MaterialCatalogItem.objects.filter(
        is_active=True,
    ).order_by(
        "display_order",
        "id",
    )

    existing_catalog_ids = set(
        material_request.items.filter(
            catalog_item__isnull=False,
        ).values_list(
            "catalog_item_id",
            flat=True,
        )
    )

    request_items_to_create: list[PlanReaderMaterialRequestItem] = []

    for catalog_item in catalog_items:
        if catalog_item.id in existing_catalog_ids:
            continue

        source = get_initial_item_source(
            material_request=material_request,
            catalog_item=catalog_item,
        )

        request_items_to_create.append(
            PlanReaderMaterialRequestItem(
                material_request=material_request,
                catalog_item=catalog_item,
                material_type=catalog_item.material_type,
                category=catalog_item.category,
                material_name=catalog_item.material_name,
                uom=catalog_item.uom,
                quantity_requested=Decimal("0"),
                quantity_received=None,
                source=source,
                auto_rule=catalog_item.auto_rule,
                automatic_quantity=Decimal("0"),
                calculation_note="",
                display_order=catalog_item.display_order,
                is_active=True,
            )
        )

    if request_items_to_create:
        PlanReaderMaterialRequestItem.objects.bulk_create(request_items_to_create)

    return len(request_items_to_create)


# =============================================================================
# CREACIÓN DE SOLICITUD
# =============================================================================


@transaction.atomic
def get_or_create_material_request(
    *,
    job: PlanReaderJob,
    user: AbstractBaseUser,
    request_type: str,
) -> tuple[
    PlanReaderMaterialRequest,
    bool,
]:
    """
    Obtiene o crea la solicitud correspondiente al Job y tipo indicado.

    Se permite como máximo:

    - una solicitud Splicing por Job;
    - una solicitud Cable por Job.
    """

    normalized_request_type = validate_request_type(request_type)

    (
        material_request,
        created,
    ) = PlanReaderMaterialRequest.objects.select_for_update().get_or_create(
        job=job,
        request_type=normalized_request_type,
        defaults={
            "status": PlanReaderMaterialRequest.STATUS_DRAFT,
            "subcontractor": "Hyperlink Networks LLC",
            "request_date": timezone.localdate(),
            "market": job.city or "",
            "dfn": job.dfn or "",
            "contractor_employee_name": (
                user.get_full_name() or user.get_username() or ""
            ),
            "contractor_employee_signature": "",
            "notes": "",
            "created_by": user,
            "updated_by": user,
        },
    )

    synchronize_material_request_catalog(
        material_request=material_request,
    )

    if created and material_request.is_splicing_request:
        recalculate_material_request(
            material_request=material_request,
            user=user,
            overwrite_user_edits=True,
        )

    return material_request, created


# =============================================================================
# RECÁLCULO DE SOLICITUD
# =============================================================================


@transaction.atomic
def recalculate_material_request(
    *,
    material_request: PlanReaderMaterialRequest,
    user: AbstractBaseUser,
    overwrite_user_edits: bool = False,
) -> PlanReaderMaterialRequest:
    """
    Recalcula una solicitud Splicing.

    En solicitudes Cable no se modifica ninguna cantidad porque todas
    las cantidades son manuales.

    En Splicing:

    - automatic_quantity siempre se actualiza;
    - quantity_requested solo se reemplaza cuando:
        * overwrite_user_edits=True; o
        * el usuario no había modificado la cantidad automática.

    La fila genérica TDS LABEL se calcula como:

        total de cajas detectadas + 10 %

    redondeando hacia arriba.
    """

    material_request = (
        PlanReaderMaterialRequest.objects.select_for_update()
        .select_related(
            "job",
        )
        .get(
            pk=material_request.pk,
        )
    )

    synchronize_material_request_catalog(
        material_request=material_request,
    )

    if material_request.is_cable_request:
        material_request.updated_by = user

        material_request.save(
            update_fields=[
                "updated_by",
                "updated_at",
            ]
        )

        return material_request

    plan_items = material_request.job.items.all().order_by(
        "sheet",
        "project_name",
        "primary_feed",
        "id",
    )

    quantities, notes = calculate_material_rule_quantities(
        plan_items,
    )

    request_items = material_request.items.filter(
        Q(
            auto_rule__in=SPLICING_AUTOMATIC_RULES,
        )
        | Q(
            material_name__iexact=GENERIC_TDS_LABEL_NAME,
        ),
        is_active=True,
    ).order_by(
        "display_order",
        "id",
    )

    for request_item in request_items:
        is_generic_tds_label = (
            normalize_text(
                request_item.material_name,
            )
            == GENERIC_TDS_LABEL_NAME
        )

        if is_generic_tds_label:
            calculation_key = GENERIC_TDS_LABEL_CALCULATION_KEY
        else:
            calculation_key = request_item.auto_rule

        calculated_quantity = quantities.get(
            calculation_key,
            Decimal("0"),
        )

        previous_automatic_quantity = request_item.automatic_quantity or Decimal("0")

        current_requested_quantity = request_item.quantity_requested or Decimal("0")

        explicitly_marked_as_edited = (
            request_item.source == PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC_EDITED
        )

        quantity_differs_from_previous_calculation = (
            request_item.source == PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC
            and current_requested_quantity != previous_automatic_quantity
        )

        user_modified_quantity = (
            explicitly_marked_as_edited or quantity_differs_from_previous_calculation
        )

        request_item.automatic_quantity = calculated_quantity

        request_item.calculation_note = notes.get(
            calculation_key,
            "",
        )

        if overwrite_user_edits or not user_modified_quantity:
            request_item.quantity_requested = calculated_quantity

            request_item.source = PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC
        else:
            request_item.source = PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC_EDITED

        request_item.save(
            update_fields=[
                "quantity_requested",
                "automatic_quantity",
                "source",
                "calculation_note",
                "updated_at",
            ]
        )

    material_request.status = PlanReaderMaterialRequest.STATUS_DRAFT

    material_request.updated_by = user

    material_request.save(
        update_fields=[
            "status",
            "updated_by",
            "updated_at",
        ]
    )

    return material_request
