from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# ============================================================
# Excepciones
# ============================================================


class BillingMappingError(ValueError):
    """
    Error específico al convertir los Items de un Billing
    en los campos requeridos por el formulario del cliente.
    """

    pass


# ============================================================
# Nombres internos de campos del formulario
# ============================================================


FORM_FIELD_SPLICE_CASE_UG_QTY = "splice_case_ug_quantity"

FORM_FIELD_SPLICE_CASE_AER_QTY = "splice_case_aer_quantity"

FORM_FIELD_FUSION_SPLICE_QTY = "fusion_splice_quantity"

FORM_FIELD_DS_SPLITTER_1X2_QTY = "ds_splitter_1x2_quantity"

FORM_FIELD_DS_SPLITTER_1X4_QTY = "ds_splitter_1x4_quantity"

FORM_FIELD_DS_SPLITTER_1X8_QTY = "ds_splitter_1x8_quantity"

FORM_FIELD_DS_SPLITTER_1X16_QTY = "ds_splitter_1x16_quantity"


# ============================================================
# Labels esperados en Smartsheet
# ============================================================


FORM_FIELD_LABELS = {
    FORM_FIELD_SPLICE_CASE_UG_QTY: ("C-108-UG - Splice Case Quantity"),
    FORM_FIELD_SPLICE_CASE_AER_QTY: ("C-108-AER - Splice Case Quantity"),
    FORM_FIELD_FUSION_SPLICE_QTY: ("C-109 - HO-1 Fusion Splice Quantity"),
    FORM_FIELD_DS_SPLITTER_1X2_QTY: ("C-110 - DS Splitter Add - 1x2"),
    FORM_FIELD_DS_SPLITTER_1X4_QTY: ("C-110 - DS Splitter Add - 1x4"),
    FORM_FIELD_DS_SPLITTER_1X8_QTY: ("C-110 - DS Splitter Add - 1x8"),
    FORM_FIELD_DS_SPLITTER_1X16_QTY: ("C-110 - DS Splitter Add - 1x16"),
}


# ============================================================
# Resultado estructurado
# ============================================================


@dataclass
class BillingItemSnapshot:
    """
    Snapshot de un ItemBilling.

    Se guarda antes de iniciar el proceso para que el envío no
    cambie silenciosamente si alguien modifica el Billing después.
    """

    item_id: int | None

    codigo_trabajo: str

    tipo_trabajo: str

    descripcion: str

    unidad_medida: str

    cantidad: Decimal

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "codigo_trabajo": self.codigo_trabajo,
            "tipo_trabajo": self.tipo_trabajo,
            "descripcion": self.descripcion,
            "unidad_medida": self.unidad_medida,
            "cantidad": decimal_to_json_value(
                self.cantidad,
            ),
        }


@dataclass
class BillingMappingResult:
    """
    Resultado final del mapeo de un Billing.

    Además de las cantidades del formulario, detecta
    automáticamente configuración individual derivada
    directamente de los códigos reales del Billing.

    Reglas:

        C-108-AER
            -> Aerial Case = YES

        C-108.1
            -> Re-Entry = YES
    """

    fields: dict[str, Decimal] = field(
        default_factory=dict,
    )

    items_snapshot: list[BillingItemSnapshot] = field(
        default_factory=list,
    )

    unmapped_items: list[BillingItemSnapshot] = field(
        default_factory=list,
    )

    aerial_case_detected: bool = False

    re_entry_detected: bool = False

    warnings: list[str] = field(
        default_factory=list,
    )

    errors: list[str] = field(
        default_factory=list,
    )

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "fields": {
                key: decimal_to_json_value(
                    value,
                )
                for key, value in self.fields.items()
            },
            "field_labels": {
                key: FORM_FIELD_LABELS.get(
                    key,
                    key,
                )
                for key in self.fields.keys()
            },
            "detected_configuration": {
                "aerial_case": bool(
                    self.aerial_case_detected,
                ),
                "re_entry": bool(
                    self.re_entry_detected,
                ),
            },
            "items_snapshot": [item.as_dict() for item in self.items_snapshot],
            "unmapped_items": [item.as_dict() for item in self.unmapped_items],
            "warnings": list(
                self.warnings,
            ),
            "errors": list(
                self.errors,
            ),
        }


# ============================================================
# Helpers generales
# ============================================================


def clean_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    return str(
        value,
    ).strip()


def normalize_text(
    value: Any,
) -> str:
    """
    Normaliza textos para comparación robusta.

    Ejemplos:

        "B8G TYPE 2"
        "b8g type 2"
        "B8G   TYPE-2"

    Se mantienen números y letras.
    """

    value = clean_text(
        value,
    ).lower()

    if not value:
        return ""

    value = value.replace(
        "×",
        "x",
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    value = re.sub(
        r"\s+",
        " ",
        value,
    )

    return value.strip()


def normalize_job_code(
    value: Any,
) -> str:
    """
    Normaliza los códigos reales usados en Billing.

    Soporta, entre otros:

        C109
        C-109
        c_109

            -> C-109

        C108UG
        C-108-UG
        c_108_ug

            -> C-108-UG

        C108AER
        C-108-AER
        c_108_aer

            -> C-108-AER

        C108.1
        C-108.1
        C_108.1

            -> C-108.1

        C110.2
        C-110.2

            -> C-110.2

        C110.4
        C-110.4

            -> C-110.4

        C110.8
        C-110.8

            -> C-110.8

        C110.16
        C-110.16

            -> C-110.16

    Importante:

    No elimina los sufijos decimales .1, .2, .4, .8 y .16,
    porque forman parte del código real del Billing.
    """

    value = clean_text(
        value,
    ).upper()

    if not value:
        return ""

    # ========================================================
    # Eliminar espacios
    # ========================================================

    value = re.sub(
        r"\s+",
        "",
        value,
    )

    # ========================================================
    # Normalizar separadores comunes
    # ========================================================

    value = value.replace(
        "_",
        "-",
    )

    # ========================================================
    # Códigos C-108 especiales
    # ========================================================

    compact_letters = re.sub(
        r"[^A-Z0-9]",
        "",
        value,
    )

    if compact_letters == "C108UG":
        return "C-108-UG"

    if compact_letters == "C108AER":
        return "C-108-AER"

    # ========================================================
    # Códigos con sufijo decimal
    #
    # Ejemplos:
    #
    #     C108.1
    #     C-108.1
    #     C_108.1
    #
    #     C110.2
    #     C110.16
    #
    # ========================================================

    decimal_candidate = value.replace(
        "-",
        "",
    )

    decimal_match = re.fullmatch(
        r"([A-Z]+)(\d+)\.(\d+)",
        decimal_candidate,
    )

    if decimal_match:
        prefix = decimal_match.group(
            1,
        )

        number = decimal_match.group(
            2,
        )

        suffix = decimal_match.group(
            3,
        )

        return f"{prefix}-" f"{number}." f"{suffix}"

    # ========================================================
    # Código alfanumérico con sufijo de letras
    #
    # Ejemplo:
    #
    #     C108UG
    #     C108AER
    #
    # ========================================================

    letter_suffix_match = re.fullmatch(
        r"([A-Z]+)(\d+)([A-Z]+)",
        compact_letters,
    )

    if letter_suffix_match:
        prefix = letter_suffix_match.group(
            1,
        )

        number = letter_suffix_match.group(
            2,
        )

        suffix = letter_suffix_match.group(
            3,
        )

        return f"{prefix}-" f"{number}-" f"{suffix}"

    # ========================================================
    # Código estándar
    #
    # Ejemplo:
    #
    #     C109
    #     C-109
    #
    # ========================================================

    standard_match = re.fullmatch(
        r"([A-Z]+)(\d+)",
        compact_letters,
    )

    if standard_match:
        prefix = standard_match.group(
            1,
        )

        number = standard_match.group(
            2,
        )

        return f"{prefix}-" f"{number}"

    return value


def to_decimal(
    value: Any,
    default: Decimal = Decimal(
        "0",
    ),
) -> Decimal:
    if value in (
        None,
        "",
    ):
        return default

    if isinstance(
        value,
        Decimal,
    ):
        return value

    try:
        return Decimal(
            str(
                value,
            )
            .strip()
            .replace(
                ",",
                ".",
            )
        )

    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return default


def decimal_to_json_value(
    value: Decimal,
):
    """
    Convierte Decimal en int cuando es entero.

    Ejemplos:

        Decimal("24")
            -> 24

        Decimal("24.5")
            -> 24.5
    """

    if value == value.to_integral():
        return int(
            value,
        )

    return float(
        value,
    )


def positive_quantity(
    value: Any,
) -> Decimal:
    """
    Los formularios del cliente esperan cantidades positivas.

    Los Direct Discounts no deberían llegar a este flujo,
    pero protegemos igualmente el mapper.
    """

    quantity = to_decimal(
        value,
    )

    if quantity < 0:
        return abs(
            quantity,
        )

    return quantity


# ============================================================
# Construcción de snapshots
# ============================================================


def build_item_snapshot(
    item,
) -> BillingItemSnapshot:
    return BillingItemSnapshot(
        item_id=getattr(
            item,
            "id",
            None,
        ),
        codigo_trabajo=clean_text(
            getattr(
                item,
                "codigo_trabajo",
                "",
            )
        ),
        tipo_trabajo=clean_text(
            getattr(
                item,
                "tipo_trabajo",
                "",
            )
        ),
        descripcion=clean_text(
            getattr(
                item,
                "descripcion",
                "",
            )
        ),
        unidad_medida=clean_text(
            getattr(
                item,
                "unidad_medida",
                "",
            )
        ),
        cantidad=positive_quantity(
            getattr(
                item,
                "cantidad",
                0,
            )
        ),
    )


def get_billing_items(
    billing_session,
) -> list:
    """
    Obtiene los items de forma compatible tanto con:

        prefetch_related(...)

    como con:

        consulta normal
    """

    manager = getattr(
        billing_session,
        "items",
        None,
    )

    if manager is None:
        return []

    try:
        return list(
            manager.all(),
        )

    except Exception:
        return []


# ============================================================
# Detección de variantes de splitter
# ============================================================


def detect_splitter_size(
    item: BillingItemSnapshot,
) -> str:
    """
    Mantiene compatibilidad con códigos históricos C-110.

    Busca tamaños de splitter en:

    - tipo_trabajo
    - descripcion
    - unidad_medida

    Soporta:

        1x2
        1 x 2
        1×2
        1:2
        1/2

    Retorna:

        "1x2"
        "1x4"
        "1x8"
        "1x16"
        ""

    Los códigos nuevos:

        C-110.2
        C-110.4
        C-110.8
        C-110.16

    se procesan directamente sin depender de esta función.
    """

    source = " ".join(
        [
            item.tipo_trabajo,
            item.descripcion,
            item.unidad_medida,
        ]
    ).lower()

    source = source.replace(
        "×",
        "x",
    )

    patterns = {
        "1x2": [
            r"\b1\s*x\s*2\b",
            r"\b1\s*:\s*2\b",
            r"\b1\s*/\s*2\b",
        ],
        "1x4": [
            r"\b1\s*x\s*4\b",
            r"\b1\s*:\s*4\b",
            r"\b1\s*/\s*4\b",
        ],
        "1x8": [
            r"\b1\s*x\s*8\b",
            r"\b1\s*:\s*8\b",
            r"\b1\s*/\s*8\b",
        ],
        "1x16": [
            r"\b1\s*x\s*16\b",
            r"\b1\s*:\s*16\b",
            r"\b1\s*/\s*16\b",
        ],
    }

    for (
        splitter_size,
        splitter_patterns,
    ) in patterns.items():
        for pattern in splitter_patterns:
            if re.search(
                pattern,
                source,
                flags=re.IGNORECASE,
            ):
                return splitter_size

    return ""


# ============================================================
# Detección de configuración por códigos
# ============================================================


def item_detects_aerial_case(
    item: BillingItemSnapshot,
) -> bool:
    """
    C-108-AER significa:

        Aerial Case = YES
    """

    code = normalize_job_code(
        item.codigo_trabajo,
    )

    return code == "C-108-AER"


def item_detects_re_entry(
    item: BillingItemSnapshot,
) -> bool:
    """
    La existencia de C-108.1 significa:

        Re-Entry = YES

    La cantidad del código no se envía al formulario.

    Solo importa la existencia del código.
    """

    code = normalize_job_code(
        item.codigo_trabajo,
    )

    return code == "C-108.1"


# ============================================================
# Mapeo de Items a campos del formulario
# ============================================================


def map_item_to_form_field(
    item: BillingItemSnapshot,
) -> str:
    """
    Determina qué campo del formulario corresponde al Item.

    Reglas actuales confirmadas:

        C-108-UG
            -> C-108-UG - Splice Case Quantity

        C-108-AER
            -> C-108-AER - Splice Case Quantity

        C-108.1
            -> No envía cantidad.
               Activa Re-Entry = YES.

        C-109
            -> C-109 - HO-1 Fusion Splice Quantity

        C-110.2
            -> C-110 - DS Splitter Add - 1x2

        C-110.4
            -> C-110 - DS Splitter Add - 1x4

        C-110.8
            -> C-110 - DS Splitter Add - 1x8

        C-110.16
            -> C-110 - DS Splitter Add - 1x16

    Compatibilidad histórica:

        C-110 + descripción 1x2 / 1x4 / 1x8 / 1x16

    continúa siendo reconocido.
    """

    code = normalize_job_code(
        item.codigo_trabajo,
    )

    # ========================================================
    # Splice Case
    # ========================================================

    if code == "C-108-UG":
        return FORM_FIELD_SPLICE_CASE_UG_QTY

    if code == "C-108-AER":
        return FORM_FIELD_SPLICE_CASE_AER_QTY

    # ========================================================
    # C-108.1 solo activa Re-Entry
    # ========================================================

    if code == "C-108.1":
        return ""

    # ========================================================
    # Fusion Splice
    # ========================================================

    if code == "C-109":
        return FORM_FIELD_FUSION_SPLICE_QTY

    # ========================================================
    # Splitter exactos
    # ========================================================

    if code == "C-110.2":
        return FORM_FIELD_DS_SPLITTER_1X2_QTY

    if code == "C-110.4":
        return FORM_FIELD_DS_SPLITTER_1X4_QTY

    if code == "C-110.8":
        return FORM_FIELD_DS_SPLITTER_1X8_QTY

    if code == "C-110.16":
        return FORM_FIELD_DS_SPLITTER_1X16_QTY

    # ========================================================
    # Compatibilidad con Billing históricos C-110
    # ========================================================

    if code == "C-110":
        splitter_size = detect_splitter_size(
            item,
        )

        if splitter_size == "1x2":
            return FORM_FIELD_DS_SPLITTER_1X2_QTY

        if splitter_size == "1x4":
            return FORM_FIELD_DS_SPLITTER_1X4_QTY

        if splitter_size == "1x8":
            return FORM_FIELD_DS_SPLITTER_1X8_QTY

        if splitter_size == "1x16":
            return FORM_FIELD_DS_SPLITTER_1X16_QTY

        return ""

    return ""


# ============================================================
# Mapeo principal
# ============================================================


def map_billing_items(
    items: Iterable,
) -> BillingMappingResult:
    """
    Convierte una colección de ItemBilling en los campos
    requeridos por el formulario del cliente.

    También detecta automáticamente:

        C-108-AER
            -> Aerial Case = YES

        C-108.1
            -> Re-Entry = YES
    """

    result = BillingMappingResult(
        fields={
            FORM_FIELD_SPLICE_CASE_UG_QTY: Decimal(
                "0",
            ),
            FORM_FIELD_SPLICE_CASE_AER_QTY: Decimal(
                "0",
            ),
            FORM_FIELD_FUSION_SPLICE_QTY: Decimal(
                "0",
            ),
            FORM_FIELD_DS_SPLITTER_1X2_QTY: Decimal(
                "0",
            ),
            FORM_FIELD_DS_SPLITTER_1X4_QTY: Decimal(
                "0",
            ),
            FORM_FIELD_DS_SPLITTER_1X8_QTY: Decimal(
                "0",
            ),
            FORM_FIELD_DS_SPLITTER_1X16_QTY: Decimal(
                "0",
            ),
        }
    )

    for raw_item in items:
        item = build_item_snapshot(
            raw_item,
        )

        result.items_snapshot.append(
            item,
        )

        # ====================================================
        # Configuración detectada automáticamente
        # ====================================================

        if item_detects_aerial_case(
            item,
        ):
            result.aerial_case_detected = True

        if item_detects_re_entry(
            item,
        ):
            result.re_entry_detected = True

            # C-108.1 no tiene una cantidad que deba
            # enviarse al formulario.
            continue

        # ====================================================
        # Cantidades
        # ====================================================

        form_field = map_item_to_form_field(
            item,
        )

        if not form_field:
            result.unmapped_items.append(
                item,
            )

            continue

        result.fields[form_field] += item.cantidad

    # ========================================================
    # Validaciones de consistencia del Billing
    # ========================================================

    has_ug = result.fields[FORM_FIELD_SPLICE_CASE_UG_QTY] > 0

    has_aer = result.fields[FORM_FIELD_SPLICE_CASE_AER_QTY] > 0

    if has_ug and has_aer:
        result.warnings.append(
            (
                "The Billing contains both C-108-UG and "
                "C-108-AER. Aerial Case will be detected "
                "as YES because C-108-AER exists."
            )
        )

    return result


def map_billing_session(
    billing_session,
) -> BillingMappingResult:
    """
    Punto de entrada principal desde una SesionBilling.
    """

    if billing_session is None:
        raise BillingMappingError("Billing session is required.")

    items = get_billing_items(
        billing_session,
    )

    result = map_billing_items(
        items,
    )

    if not items:
        result.warnings.append("This Billing does not contain any items.")

    return result


# ============================================================
# Payload listo para el formulario
# ============================================================


def build_form_quantity_payload(
    billing_session,
) -> dict:
    """
    Devuelve solamente los valores que necesita el formulario.

    Mantiene las cantidades específicas:

        splice_case_ug_quantity
        splice_case_aer_quantity

    y además genera:

        splice_case_quantity

    que representa la cantidad real de C-108 que debe enviarse
    al campo dinámico visible en Smartsheet.

    Reglas:

        C-108-UG
            -> Aerial Case = NO
            -> splice_case_quantity usa C-108-UG

        C-108-AER
            -> Aerial Case = YES
            -> splice_case_quantity usa C-108-AER

    C-108.1:
        -> Re-Entry = YES
        -> no modifica splice_case_quantity
    """

    mapping = map_billing_session(
        billing_session,
    )

    fields = {
        key: decimal_to_json_value(
            value,
        )
        for key, value in mapping.fields.items()
    }

    # ========================================================
    # Cantidad dinámica de Splice Case
    #
    # Si existe C-108-AER, Aerial Case es YES y utilizamos
    # exclusivamente la cantidad AER.
    #
    # En caso contrario utilizamos la cantidad UG.
    # ========================================================

    if mapping.aerial_case_detected:
        splice_case_quantity = mapping.fields.get(
            FORM_FIELD_SPLICE_CASE_AER_QTY,
            Decimal(
                "0",
            ),
        )

        splice_case_source = "C-108-AER"

    else:
        splice_case_quantity = mapping.fields.get(
            FORM_FIELD_SPLICE_CASE_UG_QTY,
            Decimal(
                "0",
            ),
        )

        splice_case_source = "C-108-UG"

    fields["splice_case_quantity"] = decimal_to_json_value(
        splice_case_quantity,
    )

    print(
        "BILLING FORM QUANTITY PAYLOAD:",
        {
            "billing_session_id": getattr(
                billing_session,
                "pk",
                None,
            ),
            "aerial_case_detected": (mapping.aerial_case_detected),
            "splice_case_source": splice_case_source,
            "splice_case_quantity": (fields["splice_case_quantity"]),
            "splice_case_ug_quantity": fields.get(
                FORM_FIELD_SPLICE_CASE_UG_QTY,
                0,
            ),
            "splice_case_aer_quantity": fields.get(
                FORM_FIELD_SPLICE_CASE_AER_QTY,
                0,
            ),
        },
    )

    return fields

# ============================================================
# Snapshot completo para ClientSubmission
# ============================================================


def build_billing_submission_snapshot(
    billing_session,
) -> dict:
    """
    Construye el snapshot completo utilizado por
    ClientSubmission.

    El snapshot contiene:

    - Cantidades mapeadas desde Billing.
    - Items originales del Billing.
    - Códigos no mapeados.
    - Configuración detectada automáticamente.
    - Cantidad dinámica de Splice Case.

    Reglas dinámicas de C-108:

        C-108-UG
            -> Aerial Case = NO
            -> splice_case_quantity usa la cantidad C-108-UG

        C-108-AER
            -> Aerial Case = YES
            -> splice_case_quantity usa la cantidad C-108-AER

        C-108.1
            -> Re-Entry = YES
            -> no modifica la cantidad de Splice Case
    """

    mapping = map_billing_session(
        billing_session,
    )

    # ========================================================
    # Cantidades originales mapeadas
    # ========================================================

    fields = {
        key: decimal_to_json_value(
            value,
        )
        for key, value in mapping.fields.items()
    }

    # ========================================================
    # Resolver cantidad dinámica C-108
    #
    # C-108-AER tiene prioridad porque su existencia activa
    # Aerial Case = YES.
    #
    # Si no existe C-108-AER, utilizamos C-108-UG.
    # ========================================================

    if mapping.aerial_case_detected:
        splice_case_quantity = mapping.fields.get(
            FORM_FIELD_SPLICE_CASE_AER_QTY,
            Decimal(
                "0",
            ),
        )

        splice_case_source_code = "C-108-AER"

        splice_case_field_label = "C-108-AER - Splice Case Quantity"

    else:
        splice_case_quantity = mapping.fields.get(
            FORM_FIELD_SPLICE_CASE_UG_QTY,
            Decimal(
                "0",
            ),
        )

        splice_case_source_code = "C-108-UG"

        splice_case_field_label = "C-108-UG - Splice Case Quantity"

    # ========================================================
    # Campo dinámico consumido por Smartsheet
    # ========================================================

    fields["splice_case_quantity"] = decimal_to_json_value(
        splice_case_quantity,
    )

    # ========================================================
    # Labels
    # ========================================================

    field_labels = {
        key: FORM_FIELD_LABELS.get(
            key,
            key,
        )
        for key in mapping.fields.keys()
    }

    field_labels["splice_case_quantity"] = splice_case_field_label

    # ========================================================
    # Debug temporal
    # ========================================================

    print(
        "BILLING SUBMISSION SNAPSHOT C-108:",
        {
            "billing_session_id": getattr(
                billing_session,
                "pk",
                None,
            ),
            "project_id": clean_text(
                getattr(
                    billing_session,
                    "proyecto_id",
                    "",
                )
            ),
            "aerial_case_detected": (mapping.aerial_case_detected),
            "re_entry_detected": (mapping.re_entry_detected),
            "splice_case_source_code": (splice_case_source_code),
            "splice_case_quantity": (fields["splice_case_quantity"]),
            "splice_case_ug_quantity": fields.get(
                FORM_FIELD_SPLICE_CASE_UG_QTY,
                0,
            ),
            "splice_case_aer_quantity": fields.get(
                FORM_FIELD_SPLICE_CASE_AER_QTY,
                0,
            ),
        },
    )

    # ========================================================
    # Snapshot final
    # ========================================================

    return {
        "ok": mapping.ok,
        "billing_session_id": getattr(
            billing_session,
            "id",
            None,
        ),
        "project_id": clean_text(
            getattr(
                billing_session,
                "proyecto_id",
                "",
            )
        ),
        "fields": fields,
        "field_labels": field_labels,
        "detected_configuration": {
            "aerial_case": bool(
                mapping.aerial_case_detected,
            ),
            "re_entry": bool(
                mapping.re_entry_detected,
            ),
        },
        "splice_case": {
            "source_code": splice_case_source_code,
            "quantity": fields["splice_case_quantity"],
            "field_label": splice_case_field_label,
        },
        "items": [item.as_dict() for item in mapping.items_snapshot],
        "unmapped_items": [item.as_dict() for item in mapping.unmapped_items],
        "warnings": list(
            mapping.warnings,
        ),
        "errors": list(
            mapping.errors,
        ),
    }


# ============================================================
# Validación específica para el envío
# ============================================================


def validate_required_billing_mapping(
    billing_session,
    *,
    splicing: bool = False,
    testing: bool = False,
    fiber_placed: bool = False,
) -> dict:
    """
    Valida la información disponible antes de crear un envío.

    Reglas actuales:

    - C-108-UG se usa como Splice Case Quantity Underground.
    - C-108-AER se usa como Splice Case Quantity Aerial.
    - C-108-AER activa automáticamente Aerial Case = YES.
    - C-108.1 activa automáticamente Re-Entry = YES.
    - C-109 carga Fusion Splice Quantity.
    - C-110.2 carga DS Splitter Add - 1x2.
    - C-110.4 carga DS Splitter Add - 1x4.
    - C-110.8 carga DS Splitter Add - 1x8.
    - C-110.16 carga DS Splitter Add - 1x16.

    Los códigos no reconocidos se mantienen como warnings.
    """

    mapping = map_billing_session(
        billing_session,
    )

    errors = list(
        mapping.errors,
    )

    warnings = list(
        mapping.warnings,
    )

    if mapping.unmapped_items:
        unmapped_codes = sorted(
            {
                normalize_job_code(
                    item.codigo_trabajo,
                )
                or item.codigo_trabajo
                or "Unknown"
                for item in mapping.unmapped_items
            }
        )

        warnings.append(
            (
                "Some Billing items are not currently mapped "
                "to Smartsheet fields: "
                + ", ".join(
                    unmapped_codes,
                )
            )
        )

    if splicing:
        has_splice_case_quantity = any(
            mapping.fields[field_name] > 0
            for field_name in (
                FORM_FIELD_SPLICE_CASE_UG_QTY,
                FORM_FIELD_SPLICE_CASE_AER_QTY,
            )
        )

        has_splicing_quantity = mapping.fields[FORM_FIELD_FUSION_SPLICE_QTY] > 0

        has_splitter_quantity = any(
            mapping.fields[field_name] > 0
            for field_name in (
                FORM_FIELD_DS_SPLITTER_1X2_QTY,
                FORM_FIELD_DS_SPLITTER_1X4_QTY,
                FORM_FIELD_DS_SPLITTER_1X8_QTY,
                FORM_FIELD_DS_SPLITTER_1X16_QTY,
            )
        )

        if not (
            has_splice_case_quantity or has_splicing_quantity or has_splitter_quantity
        ):
            warnings.append(
                (
                    "Splicing is selected, but no mapped "
                    "C-108-UG, C-108-AER, C-109, "
                    "C-110.2, C-110.4, C-110.8 or "
                    "C-110.16 quantity was found."
                )
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "mapping": mapping.as_dict(),
        "detected_configuration": {
            "aerial_case": bool(
                mapping.aerial_case_detected,
            ),
            "re_entry": bool(
                mapping.re_entry_detected,
            ),
        },
        "work_types": {
            "splicing": bool(
                splicing,
            ),
            "testing": bool(
                testing,
            ),
            "fiber_placed": bool(
                fiber_placed,
            ),
        },
    }
