import json
import logging
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from io import BytesIO

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from access_control.services import user_can as access_user_can
from facturacion.models import Proyecto
from usuarios.models import CustomUser

from .forms_billing_masivo import BillingMasivoUploadForm
from .models import (BillingPayWeekSnapshot, BulkBillingPreview, ItemBilling,
                     ItemBillingTecnico, PrecioActividadTecnico,
                     RequirementList, RequisitoFotoBilling,
                     RequisitoFotoBillingPlantilla, SesionBilling,
                     SesionBillingTecnico)

logger = logging.getLogger(__name__)

try:
    from usuarios.decoradores import rol_requerido
except Exception:

    def rol_requerido(*roles):
        def decorator(fn):
            return fn

        return decorator


def _bulk_billing_price_permissions(user):
    """
    Permisos visuales para preview de Billing Masivo.

    Usa la misma Access Matrix que Billing List:
    - billing.view_technical_amounts
    - billing.view_company_amounts
    """
    return {
        "can_view_tech_prices": access_user_can(
            user,
            "billing.view_technical_amounts",
        ),
        "can_view_company_prices": access_user_can(
            user,
            "billing.view_company_amounts",
        ),
    }


def _norm_text(value) -> str:
    """
    Normalización controlada para comparar textos:
    - Limpia espacios al inicio/final.
    - Convierte a minúsculas.
    - Quita acentos.
    - Colapsa espacios internos múltiples.
    - NO cambia guiones, puntos ni underscores.
    """
    raw = _clean_cell(value)

    raw = " ".join(raw.split())

    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))

    return raw.lower()


def _same_text(a, b) -> bool:
    return _norm_text(a) == _norm_text(b)


def _find_price_robust(*, tech_id, project, client, city, office, job_code):
    """
    Busca precio de forma robusta para letras/mayúsculas/espacios,
    pero sin transformar símbolos como -, _, .
    """

    qs = PrecioActividadTecnico.objects.filter(
        tecnico_id=tech_id,
        proyecto=project,
    )

    matches = []

    for price in qs:
        if (
            _same_text(price.cliente, client)
            and _same_text(price.ciudad, city)
            and _same_text(price.oficina, office)
            and _same_text(price.codigo_trabajo, job_code)
        ):
            matches.append(price)

    if len(matches) == 1:
        return matches[0], None

    if len(matches) > 1:
        return None, (
            f"More than one price matches Job Code '{job_code}' for this technician/project. "
            "Please clean duplicate prices before importing."
        )

    return None, None


# =============================================================================
# CONFIGURACIÓN DEL TEMPLATE
# =============================================================================

SHEET_BILLINGS = "Billings"
SHEET_TECHNICIANS = "Technicians"
SHEET_ITEMS = "Items"

BILLINGS_HEADERS = [
    "bulk_key",
    "project_id",
    "client",
    "city",
    "project",
    "office",
    "project_address",
    "projected_week",
    "tech_payment_mode",
    "direct_discount",
    "show_immediately",
    "cable_installation",
    "requirement_type",
    "requirement_list",
]

TECHNICIANS_HEADERS = [
    "bulk_key",
    "technician_username",
    "priority",
]

ITEMS_HEADERS = [
    "bulk_key",
    "job_code",
    "quantity",
]

YES_VALUES = {"yes", "y", "true", "1", "si", "sí"}
NO_VALUES = {"no", "n", "false", "0", ""}

VALID_PAYMENT_MODES = {"full", "split"}

VALID_REQUIREMENT_TYPES = {"none", "fiber", "cable", ""}


# =============================================================================
# DATACLASSES DE PREVIEW
# =============================================================================


@dataclass
class CellError:
    sheet: str
    row: int
    field: str
    message: str


@dataclass
class PreviewBilling:

    bulk_key: str

    source_row: int

    project_id: str = ""

    client: str = ""

    city: str = ""

    project: str = ""

    office: str = ""

    project_address: str = ""

    projected_week: str = ""

    tech_payment_mode: str = "full"

    direct_discount: bool = False

    show_immediately: bool = False

    cable_installation: bool = False

    requirement_type: str = "none"

    requirement_list: str = ""

    requirement_list_id: int | None = None

    requirement_list_label: str = ""

    requirement_count: int = 0

    technicians: list = field(default_factory=list)
    items: list = field(default_factory=list)

    subtotal_tecnico: Decimal = Decimal("0.00")
    subtotal_empresa: Decimal = Decimal("0.00")

    errors: list = field(default_factory=list)


@dataclass
class PreviewTechnician:
    source_row: int
    username: str
    user_id: int | None = None
    display_name: str = ""

    # Valores admitidos desde Excel:
    #
    # número positivo:
    #     orden dentro del lote nuevo.
    #
    # AUTO:
    #     entra a cola y el Preview calcula el orden.
    #
    # vacío / SHOW NOW:
    #     el Billing debe mostrarse inmediatamente
    #     y no participa en la cola numérica.
    requested_priority: int | None = None

    priority_mode: str = "show_now"


@dataclass
class PreviewItem:
    source_row: int
    job_code: str
    quantity_raw: str
    quantity: Decimal | None = None

    tipo_trabajo: str = ""
    descripcion: str = ""
    unidad_medida: str = ""
    precio_empresa: Decimal = Decimal("0.00")

    subtotal_tecnico: Decimal = Decimal("0.00")
    subtotal_empresa: Decimal = Decimal("0.00")

    desglose_tecnico: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# =============================================================================
# HELPERS GENERALES
# =============================================================================


def _clean_cell(value) -> str:
    """
    Regla acordada:
    - Se permite limpiar espacios al inicio/final.
    - No se corrigen códigos.
    - No se cambia punto por guion.
    - No se hace fuzzy match.
    """
    if value is None:
        return ""
    return str(value).strip()


def _normalize_header(value) -> str:
    return _clean_cell(value).lower()


def _to_decimal(value):
    raw = _clean_cell(value)

    if raw == "":
        return None, raw

    try:
        dec = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return dec, raw
    except (InvalidOperation, ValueError):
        return None, raw


def _parse_bool(value):
    raw = _clean_cell(value).lower()

    if raw in YES_VALUES:
        return True, None

    if raw in NO_VALUES:
        return False, None

    return False, "Use YES or NO."


def _cell_error(sheet, row, field, message):
    return {
        "sheet": sheet,
        "row": row,
        "field": field,
        "message": message,
    }


def _read_sheet_rows(wb, sheet_name, expected_headers):
    """
    Retorna:
    - rows: lista de dicts con __rownum
    - errors: errores de estructura de hoja/header
    """
    errors = []

    if sheet_name not in wb.sheetnames:
        errors.append(
            _cell_error(
                sheet_name,
                1,
                "sheet",
                f"Missing sheet '{sheet_name}'.",
            )
        )
        return [], errors

    ws = wb[sheet_name]
    header_values = [_normalize_header(c.value) for c in ws[1]]
    expected_normalized = [_normalize_header(h) for h in expected_headers]

    for idx, expected in enumerate(expected_normalized, start=1):
        actual = header_values[idx - 1] if idx - 1 < len(header_values) else ""

        if actual != expected:
            col = get_column_letter(idx)
            errors.append(
                _cell_error(
                    sheet_name,
                    1,
                    expected_headers[idx - 1],
                    f"Invalid header in cell {col}1. Expected '{expected_headers[idx - 1]}'.",
                )
            )

    if errors:
        return [], errors

    rows = []

    for row_idx in range(2, ws.max_row + 1):
        values = {}
        is_empty = True

        for col_idx, header in enumerate(expected_headers, start=1):
            val = _clean_cell(ws.cell(row=row_idx, column=col_idx).value)
            values[header] = val

            if val != "":
                is_empty = False

        if is_empty:
            continue

        values["__rownum"] = row_idx
        rows.append(values)

    return rows, []


def _format_money(value):
    try:
        return f"{Decimal(value or 0).quantize(Decimal('0.01'))}"
    except Exception:
        return "0.00"


def _display_user(user):
    if not user:
        return ""
    full_name = (user.get_full_name() or "").strip()
    return full_name or user.username


def _iso_week_is_valid(value):
    value = _clean_cell(value).upper()

    if not value:
        return False

    if len(value) != 8:
        return False

    if value[4:6] != "-W":
        return False

    year = value[:4]
    week = value[6:]

    if not year.isdigit() or not week.isdigit():
        return False

    week_num = int(week)

    return 1 <= week_num <= 53


# =============================================================================
# TEMPLATE EXCEL
# =============================================================================


# =============================================================================
# TEMPLATE EXCEL
# =============================================================================


def billing_masivo_template(request):
    wb = Workbook()

    ws_b = wb.active
    ws_b.title = SHEET_BILLINGS

    ws_t = wb.create_sheet(SHEET_TECHNICIANS)
    ws_i = wb.create_sheet(SHEET_ITEMS)
    ws_help = wb.create_sheet("Instructions")

    _write_sheet_header(ws_b, BILLINGS_HEADERS)
    _write_sheet_header(ws_t, TECHNICIANS_HEADERS)
    _write_sheet_header(ws_i, ITEMS_HEADERS)

    # ==========================================================
    # Examples
    # ==========================================================

    # ----------------------------------------------------------
    # BILL-001
    #
    # Normal queued Billing.
    # Technician priorities define order inside the new import.
    # ----------------------------------------------------------

    ws_b.append(
        [
            "BILL-001",
            "0913UA_02_1000-012",
            "ITG",
            "Chile",
            "Underground",
            "PC676",
            "123 Main St",
            "2026-W20",
            "full",
            "NO",
            "NO",
            "NO",
            "fiber",
            "B8G Fiber / Photo",
        ]
    )

    # ----------------------------------------------------------
    # BILL-002
    #
    # Show Immediately from Billings sheet.
    # Technician priority is ignored.
    # ----------------------------------------------------------

    ws_b.append(
        [
            "BILL-002",
            "0913UA_02_1000-013",
            "ITG",
            "Chile",
            "Underground",
            "PC676",
            "456 Main St",
            "2026-W20",
            "full",
            "NO",
            "YES",
            "NO",
            "none",
            "",
        ]
    )

    # ----------------------------------------------------------
    # BILL-003
    #
    # Normal Billing using AUTO queue placement.
    # ----------------------------------------------------------

    ws_b.append(
        [
            "BILL-003",
            "0913UA_02_1000-014",
            "ITG",
            "Chile",
            "Underground",
            "PC676",
            "789 Main St",
            "2026-W20",
            "full",
            "NO",
            "NO",
            "NO",
            "none",
            "",
        ]
    )

    # ==========================================================
    # Technician examples
    # ==========================================================

    # Explicit order inside this import batch.
    #
    # Columns:
    # A = bulk_key
    # B = technician_username
    # C = priority
    # D = primary_feed
    #
    # primary_feed is informational and remains in column D.

    ws_t.append(
        [
            "BILL-001",
            "tech1",
            "1",
            "P0049",
        ]
    )

    ws_t.append(
        [
            "BILL-001",
            "tech2",
            "2",
            "P0049",
        ]
    )

    # The same Billing can have several technicians.
    # Each technician has an independent real queue.
    ws_t.append(
        [
            "BILL-001",
            "tech3",
            "AUTO",
            "P0049",
        ]
    )

    # BILL-002 is already Show Immediately at Billing level.
    # Priority is therefore not required.
    ws_t.append(
        [
            "BILL-002",
            "tech4, tech5",
            "",
            "P0050",
        ]
    )

    # Blank priority means AUTO.
    # AUTO preserves the current real queue and appends
    # this imported work according to the generated plan.
    ws_t.append(
        [
            "BILL-003",
            "tech6",
            "",
            "P0051",
        ]
    )

    # ==========================================================
    # Items
    # ==========================================================

    ws_i.append(
        [
            "BILL-001",
            "C-123",
            "1",
        ]
    )

    ws_i.append(
        [
            "BILL-002",
            "C-123",
            "1",
        ]
    )

    ws_i.append(
        [
            "BILL-003",
            "C-123",
            "1",
        ]
    )

    # ==========================================================
    # Instructions sheet - visual
    # ==========================================================

    ws_help.sheet_view.showGridLines = False

    dark_fill = PatternFill("solid", fgColor="1F2937")
    blue_fill = PatternFill("solid", fgColor="DBEAFE")
    green_fill = PatternFill("solid", fgColor="DCFCE7")
    amber_fill = PatternFill("solid", fgColor="FEF3C7")
    red_fill = PatternFill("solid", fgColor="FEE2E2")
    gray_fill = PatternFill("solid", fgColor="F3F4F6")

    title_font = Font(
        color="FFFFFF",
        bold=True,
        size=16,
    )

    section_font = Font(
        color="111827",
        bold=True,
        size=12,
    )

    bold_font = Font(
        bold=True,
    )

    normal_font = Font(
        color="374151",
        size=11,
    )

    error_font = Font(
        color="991B1B",
        bold=True,
    )

    ws_help.merge_cells("A1:F1")

    ws_help["A1"] = "Bulk Billing Import Guide"

    ws_help["A1"].fill = dark_fill

    ws_help["A1"].font = title_font

    ws_help["A1"].alignment = Alignment(
        horizontal="center",
        vertical="center",
    )

    ws_help.row_dimensions[1].height = 28

    row = 3

    def section(title, fill):
        nonlocal row

        ws_help.merge_cells(
            start_row=row,
            start_column=1,
            end_row=row,
            end_column=6,
        )

        cell = ws_help.cell(
            row=row,
            column=1,
        )

        cell.value = title
        cell.fill = fill
        cell.font = section_font

        cell.alignment = Alignment(
            horizontal="left",
            vertical="center",
        )

        ws_help.row_dimensions[row].height = 22

        row += 1

    def line(label, value="", note=""):
        nonlocal row

        ws_help.cell(
            row=row,
            column=1,
        ).value = label

        ws_help.cell(
            row=row,
            column=1,
        ).font = bold_font

        ws_help.cell(
            row=row,
            column=2,
        ).value = value

        ws_help.cell(
            row=row,
            column=2,
        ).font = normal_font

        if note:
            ws_help.merge_cells(
                start_row=row,
                start_column=3,
                end_row=row,
                end_column=6,
            )

            ws_help.cell(
                row=row,
                column=3,
            ).value = note

            ws_help.cell(
                row=row,
                column=3,
            ).font = normal_font

        for col in range(
            1,
            7,
        ):
            ws_help.cell(
                row=row,
                column=col,
            ).alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )

        row += 1

    def blank():
        nonlocal row
        row += 1

    # ==========================================================
    # 1. GENERAL WORKFLOW
    # ==========================================================

    section(
        "1. General workflow",
        blue_fill,
    )

    line(
        "Step 1",
        "Fill the Billings sheet.",
        "One row per Billing.",
    )

    line(
        "Step 2",
        "Choose execution behavior.",
        (
            "Use show_immediately in Billings to decide whether "
            "the entire Billing is Show Now or participates "
            "in the normal execution queue."
        ),
    )

    line(
        "Step 3",
        "Fill the Technicians sheet.",
        (
            "Use priority to define the relative order of new "
            "projects for each technician. Blank priority means AUTO."
        ),
    )

    line(
        "Step 4",
        "Review primary_feed.",
        (
            "primary_feed is stored in column D of Technicians. "
            "Plan Reader can populate it automatically."
        ),
    )

    line(
        "Step 5",
        "Fill the Items sheet.",
        ("Each item must use a valid Job Code " "and quantity."),
    )

    line(
        "Step 6",
        "Upload the file.",
        (
            "Preview checks Billing data, technician queues "
            "and execution decisions before creating anything."
        ),
    )

    line(
        "Preview",
        "Queue conflicts can be resolved there.",
        (
            "You do not need to rebuild the Excel only because "
            "two imported priorities conflict."
        ),
    )

    line(
        "Important",
        "Business/data errors still block creation.",
        (
            "Examples: invalid technician, invalid Job Code, "
            "missing price or invalid requirement list."
        ),
    )

    blank()

    # ==========================================================
    # 2. BILLINGS SHEET
    # ==========================================================

    section(
        "2. Billings sheet",
        green_fill,
    )

    line(
        "bulk_key",
        "Required",
        ("Unique key inside the file. " "Example: BILL-001."),
    )

    line(
        "project_id",
        "Required",
        "Final Project ID visible in Billing List.",
    )

    line(
        "client",
        "Required",
        "Must match Technician Prices.",
    )

    line(
        "city",
        "Required",
        "Must match Technician Prices.",
    )

    line(
        "project",
        "Required",
        ("Must match the Project value used " "in Technician Prices."),
    )

    line(
        "office",
        "Required",
        "Must match Technician Prices.",
    )

    line(
        "project_address",
        "Optional",
        "Address or Google Maps link.",
    )

    line(
        "projected_week",
        "Required",
        ("ISO format YYYY-W##. " "Example: 2026-W20."),
    )

    line(
        "tech_payment_mode",
        "Required",
        "Only full or split.",
    )

    line(
        "direct_discount",
        "Required",
        ("YES or NO. Direct Discount bypasses " "the execution priority queue."),
    )

    line(
        "show_immediately",
        "Required",
        ("YES or NO. This is the Billing-level " "Show Now decision."),
    )

    line(
        "show_immediately = NO",
        "Normal queue",
        (
            "The Billing participates in execution planning. "
            "Each technician's priority is read from "
            "the Technicians sheet."
        ),
    )

    line(
        "show_immediately = YES",
        "SHOW NOW",
        (
            "The entire Billing becomes immediately visible "
            "to all assigned technicians and receives no "
            "numbered queue position."
        ),
    )

    line(
        "Show Immediately scope",
        "Entire Billing",
        (
            "Show Immediately is not technician-specific. "
            "If YES, it applies to every active technician "
            "assigned to that Billing."
        ),
    )

    line(
        "Show Immediately effect",
        "Queue preserved",
        ("Existing numbered projects remain in their " "current relative order."),
    )

    line(
        "Timer behavior",
        "No timer change",
        ("Show Immediately does not start, pause or stop " "a technician work timer."),
    )

    line(
        "Direct Discount rule",
        "Do not use Show Immediately",
        (
            "Direct Discount has its own execution flow "
            "and does not participate in the queue."
        ),
    )

    line(
        "cable_installation",
        "Required",
        "YES or NO.",
    )

    line(
        "requirement_type",
        "Optional",
        "Use none, fiber or cable.",
    )

    line(
        "requirement_list",
        "Optional",
        ("Exact active Requirement List name for " "the selected project and type."),
    )

    blank()

    # ==========================================================
    # 3. TECHNICIANS SHEET
    # ==========================================================

    section(
        "3. Technicians sheet",
        amber_fill,
    )

    line(
        "Column A",
        "bulk_key",
        ("Must match the corresponding bulk_key " "from the Billings sheet."),
    )

    line(
        "Column B",
        "technician_username",
        (
            "Existing technician username. "
            "Comma or semicolon separated usernames "
            "are supported when the same execution "
            "setting applies to all of them."
        ),
    )

    line(
        "Column C",
        "priority",
        (
            "Defines execution order for this technician "
            "inside the new imported batch."
        ),
    )

    line(
        "Column D",
        "primary_feed",
        (
            "Fiber / primary feed information. "
            "Plan Reader can populate this value automatically. "
            "It does not define queue position."
        ),
    )

    line(
        "Recommended",
        "One technician per row",
        (
            "Use separate rows when technicians require "
            "independent imported priorities."
        ),
    )

    line(
        "priority = 1, 2, 3...",
        "Imported batch order",
        (
            "The number orders NEW projects for that technician. "
            "It is not the technician's absolute database position."
        ),
    )

    line(
        "Example",
        "BILL-A | tech1 | 1 | P0049",
        ("This Billing is first inside the new imported batch " "for tech1."),
    )

    line(
        "Current queue",
        "Checked automatically",
        (
            "The person preparing the Excel does not need to know "
            "what the technician already has."
        ),
    )

    line(
        "Example",
        "Current #1 + import 1, 2, 3",
        ("The imported projects normally become " "#2, #3 and #4."),
    )

    line(
        "priority = AUTO",
        "Automatic queue",
        (
            "The Billing participates in the queue and the "
            "system determines its order inside the imported batch."
        ),
    )

    line(
        "priority blank",
        "AUTO",
        (
            "A blank priority has the same execution meaning "
            "as AUTO. It does not mean Show Now."
        ),
    )

    line(
        "Show Now",
        "Use Billings.show_immediately",
        (
            "To make a Billing immediately visible, set "
            "show_immediately = YES in the Billings sheet."
        ),
    )

    line(
        "Show Now is Billing-level",
        "All assigned technicians",
        (
            "A Billing cannot be Show Now for one technician "
            "and numbered for another."
        ),
    )

    line(
        "Show Immediately priority",
        "Ignored",
        (
            "When show_immediately = YES, technician priorities "
            "do not create numbered queue positions for that Billing."
        ),
    )

    line(
        "Existing work",
        "Preserved",
        (
            "Normal imports never silently replace projects "
            "already in a technician's execution queue."
        ),
    )

    line(
        "Multiple technicians",
        "Supported",
        (
            "Example: BILL-001 | tech1, tech2, tech3 | AUTO | P0049. "
            "The same imported priority behavior applies to "
            "all usernames in that row."
        ),
    )

    line(
        "Multiple technicians + different priorities",
        "Use separate rows",
        (
            "If each technician requires a different imported "
            "priority, put one technician per row."
        ),
    )

    line(
        "Accepted separators",
        "Comma or semicolon",
        ("Examples: tech1, tech2, tech3 " "OR tech1; tech2; tech3."),
    )

    line(
        "Username rule",
        "Must match an existing user",
        ("The user must exist and must have " "technician/user role."),
    )

    line(
        "Do not duplicate",
        "Same technician in one Billing",
        ("The same technician cannot be assigned twice " "to the same Billing."),
    )

    blank()

    # ==========================================================
    # 4. PREVIEW ACTIONS
    # ==========================================================

    section(
        "4. Preview execution controls",
        blue_fill,
    )

    line(
        "Organize Queue Automatically",
        "One click",
        (
            "Resolves imported order conflicts for all technicians "
            "while preserving their current database queues."
        ),
    )

    line(
        "Example conflict",
        "1, 1, 1",
        ("Preview can normalize conflicting imported " "projects to 1, 2, 3."),
    )

    line(
        "Show All Now",
        "One click",
        (
            "Marks all eligible normal imported Billings "
            "as Show Now without changing existing technician "
            "queue positions."
        ),
    )

    line(
        "Individual Billing",
        "Queue or Show Now",
        (
            "Preview can change the execution decision "
            "for one Billing without re-uploading the Excel."
        ),
    )

    line(
        "Timer behavior",
        "No automatic timer change",
        (
            "Queue reordering and Show Now do not start, pause "
            "or stop technician timers."
        ),
    )

    blank()

    # ==========================================================
    # 5. ITEMS
    # ==========================================================

    section(
        "5. Items sheet",
        blue_fill,
    )

    line(
        "bulk_key",
        "Required",
        "Must match one Billing from the Billings sheet.",
    )

    line(
        "job_code",
        "Required",
        ("Must match exactly what exists " "in Technician Prices."),
    )

    line(
        "quantity",
        "Required",
        "Cannot be zero.",
    )

    line(
        "Normal billing",
        "Positive quantity",
        "Example: 1, 2, 3.",
    )

    line(
        "Direct discount",
        "Negative quantity",
        "Example: -1, -2.",
    )

    blank()

    # ==========================================================
    # 6. TECHNICIAN PAYMENT MODE
    # ==========================================================

    section(
        "6. Technician payment mode",
        green_fill,
    )

    line(
        "full",
        "Full amount for each technician",
        (
            "Example: 2 technicians, qty 1, rate 100. "
            "Each technician receives 100. Tech total = 200."
        ),
    )

    line(
        "split",
        "Split between technicians",
        (
            "Example: 2 technicians, qty 1, rate 100. "
            "Each technician receives 50. Tech total = 100."
        ),
    )

    line(
        "Do not write",
        "Full amount / Split between technicians / yes / no",
        "Only full or split are valid.",
    )

    blank()

    # ==========================================================
    # 7. REQUIREMENT LISTS
    # ==========================================================

    section(
        "7. Requirement lists",
        amber_fill,
    )

    line(
        "none",
        "No requirements loaded",
        "Leave requirement_list empty.",
    )

    line(
        "fiber",
        "Loads Fiber / Photo requirements",
        ("requirement_list must match an active " "Fiber / Photo Requirement List."),
    )

    line(
        "cable",
        "Loads Cable requirements",
        (
            "cable_installation must be YES and requirement_list "
            "must match an active Cable Requirement List."
        ),
    )

    line(
        "Exact name",
        "Requirement list name must match exactly.",
        ("The preview validates the list before " "creating Billings."),
    )

    blank()

    # ==========================================================
    # 8. COMMON ERRORS
    # ==========================================================

    section(
        "8. Common errors",
        red_fill,
    )

    line(
        "Invalid Job Code",
        "Job Code does not match Technician Prices.",
        "Example: C-123 is not the same as C.123.",
    )

    line(
        "Missing technician",
        "The username does not exist.",
        "Check spelling and spaces.",
    )

    line(
        "Invalid priority",
        "Use 1, 2, 3..., AUTO or blank.",
        (
            "Numeric priorities must be positive whole numbers. "
            "Blank is interpreted as AUTO."
        ),
    )

    line(
        "Duplicate imported priority",
        "Resolved in Preview",
        (
            "Example: the same technician has priority 1 "
            "for several imported Billings. This is a planning "
            "conflict, not a reason to rebuild the Excel."
        ),
    )

    line(
        "Missing price",
        "Technician has no matching price.",
        ("Client, City, Project, Office and Job Code " "must match."),
    )

    line(
        "Wrong quantity",
        "Quantity cannot be zero.",
        "Discounts require negative quantities.",
    )

    line(
        "Wrong requirement list",
        "List does not exist or is inactive.",
        ("Check Project, requirement_type " "and requirement_list."),
    )

    line(
        "Direct Discount + Show Immediately",
        "Invalid combination",
        (
            "Direct Discount bypasses execution priority "
            "and should not be marked Show Immediately."
        ),
    )

    blank()

    # ==========================================================
    # Bottom warning box
    # ==========================================================

    ws_help.merge_cells(
        start_row=row,
        start_column=1,
        end_row=row,
        end_column=6,
    )

    ws_help.cell(
        row=row,
        column=1,
    ).value = (
        "IMPORTANT: Do not rename sheets or headers. "
        "Billings.show_immediately controls Show Now. "
        "Technicians.priority controls imported queue order, "
        "and blank priority means AUTO. "
        "Planning conflicts such as duplicated imported priorities "
        "can be resolved in Preview. Data/business validation errors "
        "must be corrected before creation."
    )

    ws_help.cell(
        row=row,
        column=1,
    ).fill = red_fill

    ws_help.cell(
        row=row,
        column=1,
    ).font = error_font

    ws_help.cell(
        row=row,
        column=1,
    ).alignment = Alignment(
        horizontal="center",
        vertical="center",
        wrap_text=True,
    )

    ws_help.row_dimensions[row].height = 60

    # ==========================================================
    # Sheet formatting
    # ==========================================================

    for ws in [
        ws_b,
        ws_t,
        ws_i,
        ws_help,
    ]:
        _autosize_sheet(ws)

    ws_help.column_dimensions["A"].width = 27
    ws_help.column_dimensions["B"].width = 36
    ws_help.column_dimensions["C"].width = 26
    ws_help.column_dimensions["D"].width = 26
    ws_help.column_dimensions["E"].width = 26
    ws_help.column_dimensions["F"].width = 26

    # ==========================================================
    # Highlight example rows
    # ==========================================================

    for ws in [
        ws_b,
        ws_t,
        ws_i,
    ]:

        for cell in ws[2]:
            cell.fill = gray_fill

            cell.alignment = Alignment(
                wrap_text=True,
            )

        if ws.max_row >= 3:
            for cell in ws[3]:
                cell.fill = gray_fill

                cell.alignment = Alignment(
                    wrap_text=True,
                )

        if ws.max_row >= 4:
            for cell in ws[4]:
                cell.fill = gray_fill

                cell.alignment = Alignment(
                    wrap_text=True,
                )

    # ==========================================================
    # Output
    # ==========================================================

    output = BytesIO()

    wb.save(output)

    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        'attachment; filename="bulk_billing_template.xlsx"'
    )

    return response


def _write_sheet_header(ws, headers):
    ws.append(headers)

    fill = PatternFill("solid", fgColor="1F2937")
    font = Font(color="FFFFFF", bold=True)

    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = "A2"


def _autosize_sheet(ws):
    for column_cells in ws.columns:
        max_len = 0
        column_letter = get_column_letter(column_cells[0].column)

        for cell in column_cells:
            value = _clean_cell(cell.value)
            max_len = max(max_len, len(value))

        ws.column_dimensions[column_letter].width = min(max(max_len + 2, 14), 45)


# =============================================================================
# PREVIEW TEMPORAL PERSISTENTE PARA BILLING MASIVO
# =============================================================================

BULK_BILLING_PREVIEW_TIMEOUT = 60 * 60  # 1 hora


def _save_bulk_billing_preview(request, payload):
    """
    Guarda el Preview completo en base de datos y deja en la sesión
    únicamente un token liviano.

    La base de datos es compartida por todos los procesos/worker, por lo
    que Upload, Preview y Confirm pueden ser atendidos por procesos
    distintos sin perder el payload.
    """

    token = uuid.uuid4().hex
    now = timezone.now()

    try:
        # Limpieza oportunista de previews vencidos.
        BulkBillingPreview.objects.filter(
            expires_at__lte=now,
        ).delete()

        BulkBillingPreview.objects.create(
            token=token,
            user_id=request.user.id,
            payload=payload,
            expires_at=(
                now
                + timedelta(
                    seconds=BULK_BILLING_PREVIEW_TIMEOUT
                )
            ),
        )

    except Exception:
        logger.exception(
            "[BULK BILLING] Failed to save Preview "
            "user_id=%s token=%s",
            request.user.id,
            token[:8],
        )
        raise

    request.session[
        "billing_masivo_preview_token"
    ] = token

    request.session.modified = True


def _update_bulk_billing_preview(request, payload):
    """
    Actualiza el Preview existente conservando el mismo token.

    Si la sesión ya no posee token o el Preview dejó de existir,
    crea un Preview nuevo de forma segura.
    """

    token = request.session.get(
        "billing_masivo_preview_token"
    )

    if not token:
        _save_bulk_billing_preview(
            request,
            payload,
        )
        return

    now = timezone.now()

    try:
        updated = (
            BulkBillingPreview.objects
            .filter(
                token=token,
                user_id=request.user.id,
                expires_at__gt=now,
            )
            .update(
                payload=payload,
                updated_at=now,
                expires_at=(
                    now
                    + timedelta(
                        seconds=BULK_BILLING_PREVIEW_TIMEOUT
                    )
                ),
            )
        )

    except Exception:
        logger.exception(
            "[BULK BILLING] Failed to update Preview "
            "user_id=%s token=%s",
            request.user.id,
            token[:8],
        )
        raise

    if updated:
        return

    logger.error(
        "[BULK BILLING] Preview disappeared while updating "
        "user_id=%s token=%s. Creating a new Preview.",
        request.user.id,
        token[:8],
    )

    request.session.pop(
        "billing_masivo_preview_token",
        None,
    )

    request.session.modified = True

    _save_bulk_billing_preview(
        request,
        payload,
    )

def _rebuild_bulk_billing_execution_plan(payload):
    """
    Recalcula completamente la planificación de ejecución del Preview.

    IMPORTANTE:

    - No crea Billing.
    - No modifica BillingAssignmentQueue.
    - No modifica BillingWorkSession.
    - No modifica timers.
    - No modifica SesionBilling.
    - Sólo consulta el estado REAL actual de las colas y reconstruye
      payload["queue_plans"].

    Reglas:

    Direct Discount
        -> bypass, no queue.

    Show Now
        -> Billing completo fuera de queue.

    explicit
        -> prioridad relativa dentro del lote importado.

    auto
        -> entra después de prioridades explícitas,
           preservando orden estable del Excel.

    La cola existente siempre permanece delante del lote importado.
    """

    from operaciones.services.billing_technician_queue import (
        _project_label, _running_work, _technician_queue)

    billings = payload.get(
        "billings"
    ) or []

    planning_conflicts = []

    # =====================================================================
    # RESET CALCULATED TECHNICIAN FIELDS
    # =====================================================================

    for billing in billings:

        direct_discount = bool(
            billing.get(
                "direct_discount"
            )
        )

        show_immediately = bool(
            billing.get(
                "show_immediately"
            )
        )

        if direct_discount:
            billing[
                "execution_mode"
            ] = "direct_discount"

            billing[
                "execution_mode_label"
            ] = "Direct Discount"

        elif show_immediately:
            billing[
                "execution_mode"
            ] = "show_now"

            billing[
                "execution_mode_label"
            ] = "Show Now"

        else:
            billing[
                "execution_mode"
            ] = "queue"

            billing[
                "execution_mode_label"
            ] = "Queue"

        for technician in billing.get(
            "technicians",
            [],
        ):
            technician[
                "resulting_priority"
            ] = None

            technician[
                "existing_queue_count"
            ] = 0

            technician[
                "queue_has_existing_work"
            ] = False

            technician[
                "current_running_project"
            ] = ""

            technician[
                "current_running_priority"
            ] = None

            technician[
                "priority_warning"
            ] = ""

            technician[
                "has_planning_conflict"
            ] = False

            technician[
                "planning_conflicts"
            ] = []

            if direct_discount:
                technician[
                    "requested_priority"
                ] = None

            elif show_immediately:
                if technician.get(
                    "priority_mode"
                ) != "invalid":
                    technician[
                        "priority_mode"
                    ] = "show_now"

                    technician[
                        "requested_priority"
                    ] = None

    # =====================================================================
    # BUILD INCOMING QUEUE WORK BY TECHNICIAN
    # =====================================================================

    incoming_by_technician = defaultdict(
        list
    )

    for billing in billings:

        if billing.get(
            "direct_discount"
        ):
            continue

        if billing.get(
            "execution_mode"
        ) == "show_now":
            continue

        for technician in billing.get(
            "technicians",
            [],
        ):
            technician_id = technician.get(
                "user_id"
            )

            if not technician_id:
                continue

            priority_mode = (
                technician.get(
                    "priority_mode"
                )
                or "auto"
            )

            if priority_mode not in {
                "explicit",
                "auto",
            }:
                continue

            incoming_by_technician[
                technician_id
            ].append(
                {
                    "billing": billing,
                    "technician": technician,
                }
            )

    queue_plans = []

    # =====================================================================
    # SIMULATE EACH TECHNICIAN
    # =====================================================================

    for technician_id, incoming_entries in incoming_by_technician.items():

        # -----------------------------------------------------------------
        # Detect duplicate imported priorities.
        #
        # These are planning conflicts, NOT validation errors.
        # -----------------------------------------------------------------

        explicit_priorities = defaultdict(
            list
        )

        for incoming in incoming_entries:
            technician = incoming[
                "technician"
            ]

            if technician.get(
                "priority_mode"
            ) != "explicit":
                continue

            requested_priority = technician.get(
                "requested_priority"
            )

            if requested_priority is None:
                continue

            explicit_priorities[
                requested_priority
            ].append(
                incoming
            )

        technician_conflicts = []

        for requested_priority, duplicates in explicit_priorities.items():

            if len(
                duplicates
            ) <= 1:
                continue

            first_technician = duplicates[
                0
            ][
                "technician"
            ]

            projects = [
                (
                    duplicate[
                        "billing"
                    ].get(
                        "project_id"
                    )
                    or duplicate[
                        "billing"
                    ].get(
                        "bulk_key"
                    )
                )
                for duplicate in duplicates
            ]

            bulk_keys = [
                duplicate[
                    "billing"
                ].get(
                    "bulk_key"
                )
                for duplicate in duplicates
            ]

            conflict = {
                "type": (
                    "duplicate_import_priority"
                ),
                "technician_id": (
                    technician_id
                ),
                "technician_name": (
                    first_technician.get(
                        "display_name"
                    )
                    or first_technician.get(
                        "username"
                    )
                    or f"Technician #{technician_id}"
                ),
                "username": (
                    first_technician.get(
                        "username"
                    )
                    or ""
                ),
                "requested_priority": (
                    requested_priority
                ),
                "bulk_keys": (
                    bulk_keys
                ),
                "projects": (
                    projects
                ),
                "message": (
                    f"Priority {requested_priority} is used by "
                    f"{len(duplicates)} imported Billings for "
                    f"{first_technician.get('display_name') or first_technician.get('username') or technician_id}. "
                    "Use Organize Queue Automatically, change an imported "
                    "order manually, or mark a Billing as Show Now."
                ),
            }

            technician_conflicts.append(
                conflict
            )

            planning_conflicts.append(
                conflict
            )

        # -----------------------------------------------------------------
        # Real current numbered queue
        # -----------------------------------------------------------------

        current_queue_objects = list(
            _technician_queue(
                technician_id
            )
        )

        current_queue = []

        for queue_entry in current_queue_objects:
            assignment = queue_entry.assignment

            current_queue.append(
                {
                    "assignment_id": assignment.id,
                    "billing_id": assignment.sesion_id,
                    "project_id": _project_label(
                        assignment.sesion
                    ),
                    "priority": (
                        queue_entry.queue_position
                    ),
                    "state": assignment.estado,
                    "is_running": False,
                }
            )

        # -----------------------------------------------------------------
        # Current open timer
        # -----------------------------------------------------------------

        running_work = _running_work(
            technician_id
        )

        running_assignment_id = None
        running_project = ""
        running_priority = None

        if running_work:
            running_assignment_id = (
                running_work.assignment_id
            )

            running_project = _project_label(
                running_work.assignment.sesion
            )

            for current in current_queue:
                if (
                    current.get(
                        "assignment_id"
                    )
                    == running_assignment_id
                ):
                    current[
                        "is_running"
                    ] = True

                    running_priority = current.get(
                        "priority"
                    )

                    break

        # -----------------------------------------------------------------
        # Sort imported batch
        #
        # explicit first
        # AUTO after
        # stable Excel order inside equal groups
        # -----------------------------------------------------------------

        incoming_entries = sorted(
            incoming_entries,
            key=lambda entry: (
                (
                    0
                    if entry[
                        "technician"
                    ].get(
                        "priority_mode"
                    ) == "explicit"
                    else 1
                ),
                (
                    entry[
                        "technician"
                    ].get(
                        "requested_priority"
                    )
                    if (
                        entry[
                            "technician"
                        ].get(
                            "priority_mode"
                        ) == "explicit"
                        and entry[
                            "technician"
                        ].get(
                            "requested_priority"
                        ) is not None
                    )
                    else 10**9
                ),
                entry[
                    "billing"
                ].get(
                    "source_row"
                )
                or 0,
                entry[
                    "technician"
                ].get(
                    "source_row"
                )
                or 0,
                entry[
                    "billing"
                ].get(
                    "bulk_key"
                )
                or "",
            ),
        )

        existing_queue_count = len(
            current_queue
        )

        resulting_incoming = []

        # -----------------------------------------------------------------
        # Simulate final appended positions
        # -----------------------------------------------------------------

        for index, incoming in enumerate(
            incoming_entries,
            start=1,
        ):
            billing = incoming[
                "billing"
            ]

            technician = incoming[
                "technician"
            ]

            resulting_priority = (
                existing_queue_count
                + index
            )

            if technician.get(
                "priority_mode"
            ) == "explicit":
                requested_priority_label = str(
                    technician.get(
                        "requested_priority"
                    )
                )
            else:
                requested_priority_label = "Auto"

            if existing_queue_count:
                priority_warning = (
                    f"This technician already has "
                    f"{existing_queue_count} project"
                    f"{'' if existing_queue_count == 1 else 's'} "
                    f"in the execution queue. "
                    f"Imported order {requested_priority_label} "
                    f"will normally become queue position "
                    f"#{resulting_priority}."
                )
            else:
                priority_warning = ""

            entry_conflicts = [
                conflict
                for conflict in technician_conflicts
                if billing.get(
                    "bulk_key"
                )
                in conflict.get(
                    "bulk_keys",
                    [],
                )
            ]

            technician[
                "resulting_priority"
            ] = resulting_priority

            technician[
                "existing_queue_count"
            ] = existing_queue_count

            technician[
                "queue_has_existing_work"
            ] = bool(
                existing_queue_count
            )

            technician[
                "current_running_project"
            ] = running_project

            technician[
                "current_running_priority"
            ] = running_priority

            technician[
                "priority_warning"
            ] = priority_warning

            technician[
                "has_planning_conflict"
            ] = bool(
                entry_conflicts
            )

            technician[
                "planning_conflicts"
            ] = entry_conflicts

            resulting_incoming.append(
                {
                    "bulk_key": billing.get(
                        "bulk_key"
                    ),
                    "project_id": (
                        billing.get(
                            "project_id"
                        )
                        or billing.get(
                            "bulk_key"
                        )
                    ),
                    "source_row": technician.get(
                        "source_row"
                    ),
                    "username": technician.get(
                        "username"
                    ),
                    "priority_mode": technician.get(
                        "priority_mode"
                    ),
                    "requested_priority": technician.get(
                        "requested_priority"
                    ),
                    "requested_priority_label": (
                        requested_priority_label
                    ),
                    "resulting_priority": (
                        resulting_priority
                    ),
                    "existing_queue_count": (
                        existing_queue_count
                    ),
                    "priority_warning": (
                        priority_warning
                    ),
                    "has_planning_conflict": bool(
                        entry_conflicts
                    ),
                    "planning_conflicts": (
                        entry_conflicts
                    ),
                }
            )

        first_technician = incoming_entries[
            0
        ][
            "technician"
        ]

        technician_name = (
            first_technician.get(
                "display_name"
            )
            or first_technician.get(
                "username"
            )
            or f"Technician #{technician_id}"
        )

        if existing_queue_count:
            queue_notice = (
                f"{technician_name} already has "
                f"{existing_queue_count} project"
                f"{'' if existing_queue_count == 1 else 's'} "
                "in the current execution queue. "
                "The imported projects will be added after "
                "the existing queue."
            )
        else:
            queue_notice = (
                f"{technician_name} has no numbered projects "
                "in the current execution queue. "
                "The imported order can start at #1."
            )

        # -----------------------------------------------------------------
        # Snapshot.
        #
        # Later Confirm will use this to detect queue drift between
        # Preview and creation.
        # -----------------------------------------------------------------

        queue_snapshot = [
            {
                "assignment_id": current.get(
                    "assignment_id"
                ),
                "priority": current.get(
                    "priority"
                ),
            }
            for current in current_queue
        ]

        queue_plans.append(
            {
                "technician_id": technician_id,
                "technician_name": technician_name,
                "username": first_technician.get(
                    "username"
                )
                or "",
                "existing_queue_count": (
                    existing_queue_count
                ),
                "has_existing_queue": bool(
                    existing_queue_count
                ),
                "queue_notice": queue_notice,
                "running_assignment_id": (
                    running_assignment_id
                ),
                "running_project": (
                    running_project
                ),
                "running_priority": (
                    running_priority
                ),
                "has_running_work": bool(
                    running_work
                ),
                "has_planning_conflicts": bool(
                    technician_conflicts
                ),
                "planning_conflicts": (
                    technician_conflicts
                ),
                "current_queue": current_queue,
                "queue_snapshot": queue_snapshot,
                "incoming": resulting_incoming,
            }
        )

    # =====================================================================
    # STABLE DISPLAY ORDER
    # =====================================================================

    queue_plans.sort(
        key=lambda plan: (
            (
                plan.get(
                    "technician_name"
                )
                or ""
            ).lower(),
            plan.get(
                "technician_id"
            )
            or 0,
        )
    )

    payload[
        "queue_plans"
    ] = queue_plans

    payload[
        "planning_conflicts"
    ] = planning_conflicts

    payload[
        "has_planning_conflicts"
    ] = bool(
        planning_conflicts
    )

    return payload


def _get_bulk_billing_preview(request):
    """
    Recupera el Preview persistido utilizando el token guardado
    en la sesión del usuario.

    El Preview pertenece obligatoriamente al mismo usuario y debe
    permanecer dentro de su período de validez.
    """

    token = request.session.get("billing_masivo_preview_token")

    if not token:
        logger.error(
            "[BULK BILLING] Preview token missing " "user_id=%s",
            request.user.id,
        )
        return None

    try:
        preview = BulkBillingPreview.objects.filter(
            token=token,
            user_id=request.user.id,
        ).first()

    except Exception:
        logger.exception(
            "[BULK BILLING] Database error retrieving Preview " "user_id=%s token=%s",
            request.user.id,
            token[:8],
        )
        raise

    if preview is None:
        logger.error(
            "[BULK BILLING] Preview not found " "user_id=%s token=%s",
            request.user.id,
            token[:8],
        )
        return None

    if preview.expires_at <= timezone.now():
        logger.error(
            "[BULK BILLING] Preview expired " "user_id=%s token=%s expires_at=%s",
            request.user.id,
            token[:8],
            preview.expires_at,
        )

        preview.delete()

        request.session.pop(
            "billing_masivo_preview_token",
            None,
        )

        request.session.modified = True

        return None

    return preview.payload


def _clear_bulk_billing_preview(request):
    """
    Elimina el Preview temporal después de una confirmación exitosa
    y limpia el token de la sesión.
    """

    token = request.session.get("billing_masivo_preview_token")

    if token:
        try:
            BulkBillingPreview.objects.filter(
                token=token,
                user_id=request.user.id,
            ).delete()

        except Exception:
            logger.exception(
                "[BULK BILLING] Failed to clear Preview " "user_id=%s token=%s",
                request.user.id,
                token[:8],
            )
            raise

    request.session.pop(
        "billing_masivo_preview_token",
        None,
    )

    request.session.modified = True


# =============================================================================
# UPLOAD + PREVIEW
# =============================================================================


@login_required
@rol_requerido("admin", "pm", "supervisor", "facturacion", "emision_facturacion")
def billing_masivo_upload(request):
    if request.method == "POST":
        form = BillingMasivoUploadForm(request.POST, request.FILES)

        if form.is_valid():
            archivo = form.cleaned_data["archivo"]
            preview_payload = _build_preview_from_excel(archivo, request.user)

            _save_bulk_billing_preview(request, preview_payload)

            return redirect("operaciones:billing_masivo_preview")

        messages.error(
            request,
            "The uploaded file is not valid. Please choose a valid .xlsx file.",
        )
    else:
        form = BillingMasivoUploadForm()

    return render(
        request,
        "operaciones/billing_masivo/upload.html",
        {
            "form": form,
            "template_url": reverse("operaciones:billing_masivo_template"),
        },
    )


def billing_masivo_preview(request):
    payload = _get_bulk_billing_preview(request)

    if not payload:
        messages.warning(
            request,
            "Please upload a bulk billing file first.",
        )
        return redirect("operaciones:billing_masivo_upload")

    # =====================================================================
    # POST — EDIT EXECUTION PLAN
    #
    # None of these actions modify real Billing queues.
    # They only change the cached Preview payload.
    # =====================================================================

    if request.method == "POST":

        action = (request.POST.get("action") or "").strip()

        billings = payload.get("billings") or []

        # =================================================================
        # ORGANIZE ALL
        #
        # Uses the current simulated order as the definitive relative
        # sequence and converts it to 1..N for every technician.
        #
        # Existing database queue stays untouched.
        # =================================================================

        if action == "organize_all":

            payload = _rebuild_bulk_billing_execution_plan(payload)

            billing_by_key = {billing.get("bulk_key"): billing for billing in billings}

            for plan in payload.get(
                "queue_plans",
                [],
            ):
                for index, incoming in enumerate(
                    plan.get(
                        "incoming",
                        [],
                    ),
                    start=1,
                ):
                    billing = billing_by_key.get(incoming.get("bulk_key"))

                    if not billing:
                        continue

                    for technician in billing.get(
                        "technicians",
                        [],
                    ):
                        if technician.get("user_id") == plan.get(
                            "technician_id"
                        ) and technician.get("source_row") == incoming.get(
                            "source_row"
                        ):
                            technician["priority_mode"] = "explicit"

                            technician["requested_priority"] = index

                            break

            payload = _rebuild_bulk_billing_execution_plan(payload)

            _update_bulk_billing_preview(
                request,
                payload,
            )

            messages.success(
                request,
                (
                    "Imported execution order was organized "
                    "automatically. Existing technician queues "
                    "were not modified."
                ),
            )

            return redirect("operaciones:billing_masivo_preview")

        # =================================================================
        # SHOW ALL NOW
        # =================================================================

        elif action == "show_all_now":

            for billing in billings:

                if billing.get("direct_discount"):
                    continue

                billing["show_immediately"] = True

                billing["execution_mode"] = "show_now"

                billing["execution_mode_label"] = "Show Now"

                for technician in billing.get(
                    "technicians",
                    [],
                ):
                    if technician.get("priority_mode") == "invalid":
                        continue

                    technician["priority_mode"] = "show_now"

                    technician["requested_priority"] = None

            payload = _rebuild_bulk_billing_execution_plan(payload)

            _update_bulk_billing_preview(
                request,
                payload,
            )

            messages.success(
                request,
                (
                    "All normal imported Billings are now planned "
                    "as Show Now. Direct Discounts were left unchanged."
                ),
            )

            return redirect("operaciones:billing_masivo_preview")

        # =================================================================
        # SET ONE BILLING EXECUTION MODE
        # =================================================================

        elif action == "set_billing_execution":

            bulk_key = (request.POST.get("bulk_key") or "").strip()

            execution_mode = (request.POST.get("execution_mode") or "").strip().lower()

            billing = next(
                (
                    billing
                    for billing in billings
                    if billing.get("bulk_key") == bulk_key
                ),
                None,
            )

            if billing is None:
                messages.error(
                    request,
                    "Billing was not found in the current Preview.",
                )

                return redirect("operaciones:billing_masivo_preview")

            if billing.get("direct_discount"):
                messages.error(
                    request,
                    (
                        "Direct Discount does not participate "
                        "in execution queue planning."
                    ),
                )

                return redirect("operaciones:billing_masivo_preview")

            if execution_mode == "show_now":

                billing["show_immediately"] = True

                billing["execution_mode"] = "show_now"

                billing["execution_mode_label"] = "Show Now"

                for technician in billing.get(
                    "technicians",
                    [],
                ):
                    if technician.get("priority_mode") == "invalid":
                        continue

                    technician["priority_mode"] = "show_now"

                    technician["requested_priority"] = None

            elif execution_mode == "queue":

                billing["show_immediately"] = False

                billing["execution_mode"] = "queue"

                billing["execution_mode_label"] = "Queue"

                # Returning from Show Now no longer has a meaningful
                # previous number because the Billing was removed from
                # numbered planning. Re-enter safely as AUTO.
                for technician in billing.get(
                    "technicians",
                    [],
                ):
                    if technician.get("priority_mode") == "invalid":
                        continue

                    if technician.get("user_id"):
                        technician["priority_mode"] = "auto"

                        technician["requested_priority"] = None

            else:
                messages.error(
                    request,
                    "Invalid Billing execution mode.",
                )

                return redirect("operaciones:billing_masivo_preview")

            payload = _rebuild_bulk_billing_execution_plan(payload)

            _update_bulk_billing_preview(
                request,
                payload,
            )

            messages.success(
                request,
                (f"Execution planning for {bulk_key} " "was updated."),
            )

            return redirect("operaciones:billing_masivo_preview")

        # =================================================================
        # CHANGE ONE TECHNICIAN IMPORTED PRIORITY
        # =================================================================

        elif action == "set_technician_priority":

            bulk_key = (request.POST.get("bulk_key") or "").strip()

            technician_id_raw = (request.POST.get("technician_id") or "").strip()

            source_row_raw = (request.POST.get("source_row") or "").strip()

            raw_priority = (request.POST.get("priority") or "").strip()

            try:
                technician_id = int(technician_id_raw)
            except (
                TypeError,
                ValueError,
            ):
                messages.error(
                    request,
                    "Invalid technician.",
                )

                return redirect("operaciones:billing_masivo_preview")

            try:
                source_row = int(source_row_raw)
            except (
                TypeError,
                ValueError,
            ):
                messages.error(
                    request,
                    "Invalid technician source row.",
                )

                return redirect("operaciones:billing_masivo_preview")

            billing = next(
                (
                    billing
                    for billing in billings
                    if billing.get("bulk_key") == bulk_key
                ),
                None,
            )

            if billing is None:
                messages.error(
                    request,
                    "Billing was not found in the current Preview.",
                )

                return redirect("operaciones:billing_masivo_preview")

            if billing.get("direct_discount"):
                messages.error(
                    request,
                    ("Direct Discount does not use " "execution priority."),
                )

                return redirect("operaciones:billing_masivo_preview")

            technician = next(
                (
                    technician
                    for technician in billing.get(
                        "technicians",
                        [],
                    )
                    if (
                        technician.get("user_id") == technician_id
                        and technician.get("source_row") == source_row
                    )
                ),
                None,
            )

            if technician is None:
                messages.error(
                    request,
                    ("Technician assignment was not found " "in the current Preview."),
                )

                return redirect("operaciones:billing_masivo_preview")

            normalized_priority = raw_priority.upper()

            # -------------------------------------------------------------
            # Blank / AUTO
            # -------------------------------------------------------------

            if normalized_priority in {
                "",
                "AUTO",
            }:
                billing["show_immediately"] = False

                billing["execution_mode"] = "queue"

                billing["execution_mode_label"] = "Queue"

                technician["priority_mode"] = "auto"

                technician["requested_priority"] = None

            # -------------------------------------------------------------
            # SHOW NOW
            #
            # This is Billing-level, therefore all technicians are changed.
            # -------------------------------------------------------------

            elif normalized_priority in {
                "SHOW NOW",
                "SHOW_NOW",
                "SHOWNOW",
            }:
                billing["show_immediately"] = True

                billing["execution_mode"] = "show_now"

                billing["execution_mode_label"] = "Show Now"

                for billing_technician in billing.get(
                    "technicians",
                    [],
                ):
                    if billing_technician.get("priority_mode") == "invalid":
                        continue

                    billing_technician["priority_mode"] = "show_now"

                    billing_technician["requested_priority"] = None

            # -------------------------------------------------------------
            # Positive whole number
            # -------------------------------------------------------------

            else:

                try:
                    priority_decimal = Decimal(raw_priority)

                    if (
                        priority_decimal <= 0
                        or priority_decimal != priority_decimal.to_integral_value()
                    ):
                        raise ValueError

                    requested_priority = int(priority_decimal)

                except (
                    InvalidOperation,
                    TypeError,
                    ValueError,
                ):
                    messages.error(
                        request,
                        (
                            "Priority must be a positive whole number, "
                            "AUTO, SHOW NOW, or blank."
                        ),
                    )

                    return redirect("operaciones:billing_masivo_preview")

                billing["show_immediately"] = False

                billing["execution_mode"] = "queue"

                billing["execution_mode_label"] = "Queue"

                technician["priority_mode"] = "explicit"

                technician["requested_priority"] = requested_priority

            payload = _rebuild_bulk_billing_execution_plan(payload)

            _update_bulk_billing_preview(
                request,
                payload,
            )

            messages.success(
                request,
                (f"Execution order for {bulk_key} " "was updated."),
            )

            return redirect("operaciones:billing_masivo_preview")

        # =================================================================
        # UNKNOWN ACTION
        # =================================================================

        else:
            messages.error(
                request,
                "Invalid Preview action.",
            )

            return redirect("operaciones:billing_masivo_preview")

    # =====================================================================
    # GET
    # =====================================================================

    return render(
        request,
        "operaciones/billing_masivo/preview.html",
        {
            "payload": payload,
            "has_errors": bool(payload.get("has_errors")),
            "template_url": reverse("operaciones:billing_masivo_template"),
        },
    )


# =============================================================================
# VALIDACIÓN PRINCIPAL
# =============================================================================


def _build_preview_from_excel(archivo, user=None):
    from operaciones.services.billing_technician_queue import (
        _project_label, _running_work, _technician_queue)

    global_errors = []
    planning_conflicts = []

    price_perms = _bulk_billing_price_permissions(user)

    # =====================================================================
    # LOAD WORKBOOK
    # =====================================================================

    try:
        wb = load_workbook(
            archivo,
            data_only=True,
        )
    except Exception:
        return {
            "ok": False,
            "has_errors": True,
            "has_planning_conflicts": False,
            "permissions": price_perms,
            "global_errors": [
                _cell_error(
                    "Workbook",
                    1,
                    "archivo",
                    "The file could not be read. Please upload a valid .xlsx file.",
                )
            ],
            "planning_conflicts": [],
            "billings": [],
            "queue_plans": [],
            "summary": {
                "billing_count": 0,
                "item_count": 0,
                "technician_count": 0,
                "subtotal_tecnico": "0.00",
                "subtotal_empresa": "0.00",
            },
        }

    # =====================================================================
    # READ SHEETS
    # =====================================================================

    billing_rows, errors_b = _read_sheet_rows(
        wb,
        SHEET_BILLINGS,
        BILLINGS_HEADERS,
    )

    tech_rows, errors_t = _read_sheet_rows(
        wb,
        SHEET_TECHNICIANS,
        TECHNICIANS_HEADERS,
    )

    item_rows, errors_i = _read_sheet_rows(
        wb,
        SHEET_ITEMS,
        ITEMS_HEADERS,
    )

    global_errors.extend(errors_b)
    global_errors.extend(errors_t)
    global_errors.extend(errors_i)

    if global_errors:
        return {
            "ok": False,
            "has_errors": True,
            "has_planning_conflicts": False,
            "permissions": price_perms,
            "global_errors": global_errors,
            "planning_conflicts": [],
            "billings": [],
            "queue_plans": [],
            "summary": {
                "billing_count": 0,
                "item_count": 0,
                "technician_count": 0,
                "subtotal_tecnico": "0.00",
                "subtotal_empresa": "0.00",
            },
        }

    billings_by_key = {}

    # =====================================================================
    # BILLINGS
    # =====================================================================

    for row in billing_rows:
        bulk_key = _clean_cell(row.get("bulk_key"))

        if not bulk_key:
            global_errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "bulk_key",
                    "bulk_key is required.",
                )
            )
            continue

        if bulk_key in billings_by_key:
            global_errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "bulk_key",
                    (
                        f"Duplicate bulk_key '{bulk_key}'. "
                        "Each billing must have a unique bulk_key."
                    ),
                )
            )
            continue

        direct_discount, dd_error = _parse_bool(row.get("direct_discount"))

        show_immediately, show_error = _parse_bool(row.get("show_immediately"))

        cable_installation, cable_error = _parse_bool(row.get("cable_installation"))

        payment_mode = _clean_cell(row.get("tech_payment_mode")).lower() or "full"

        requirement_type = _clean_cell(row.get("requirement_type")).lower() or "none"

        requirement_list_name = _clean_cell(row.get("requirement_list"))

        preview = PreviewBilling(
            bulk_key=bulk_key,
            source_row=row["__rownum"],
            project_id=_clean_cell(row.get("project_id")),
            client=_clean_cell(row.get("client")),
            city=_clean_cell(row.get("city")),
            project=_clean_cell(row.get("project")),
            office=_clean_cell(row.get("office")),
            project_address=_clean_cell(row.get("project_address")),
            projected_week=_clean_cell(row.get("projected_week")).upper(),
            tech_payment_mode=payment_mode,
            direct_discount=direct_discount,
            show_immediately=show_immediately,
            cable_installation=cable_installation,
            requirement_type=requirement_type,
            requirement_list=requirement_list_name,
        )

        # -----------------------------------------------------------------
        # Required fields
        # -----------------------------------------------------------------

        required_fields = [
            "project_id",
            "client",
            "city",
            "project",
            "office",
            "projected_week",
            "tech_payment_mode",
        ]

        for field_name in required_fields:
            if not _clean_cell(row.get(field_name)):
                preview.errors.append(
                    _cell_error(
                        SHEET_BILLINGS,
                        row["__rownum"],
                        field_name,
                        "This field is required.",
                    )
                )

        # -----------------------------------------------------------------
        # Payment mode
        # -----------------------------------------------------------------

        if payment_mode not in VALID_PAYMENT_MODES:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "tech_payment_mode",
                    "Use full or split.",
                )
            )

        # -----------------------------------------------------------------
        # Boolean fields
        # -----------------------------------------------------------------

        if dd_error:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "direct_discount",
                    dd_error,
                )
            )

        if show_error:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "show_immediately",
                    show_error,
                )
            )

        if cable_error:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "cable_installation",
                    cable_error,
                )
            )

        # -----------------------------------------------------------------
        # Direct Discount cannot use Show Now.
        #
        # Direct Discount already bypasses the normal queue by design.
        # -----------------------------------------------------------------

        if direct_discount and show_immediately:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "show_immediately",
                    (
                        "Direct Discount cannot use Show Immediately. "
                        "Direct Discount already bypasses the execution queue."
                    ),
                )
            )

        # -----------------------------------------------------------------
        # Requirement type
        # -----------------------------------------------------------------

        if requirement_type not in VALID_REQUIREMENT_TYPES:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "requirement_type",
                    "Use none, fiber or cable.",
                )
            )

        if requirement_type in ("", "none"):
            preview.requirement_type = "none"
            preview.requirement_list = ""

        elif not requirement_list_name:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "requirement_list",
                    (
                        "Requirement list is required when "
                        "requirement_type is fiber or cable."
                    ),
                )
            )

        if requirement_type == "cable" and not cable_installation:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "cable_installation",
                    ("Cable requirement lists require " "cable_installation = YES."),
                )
            )

        # -----------------------------------------------------------------
        # ISO week
        # -----------------------------------------------------------------

        if preview.projected_week and not _iso_week_is_valid(preview.projected_week):
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "projected_week",
                    ("Use ISO week format YYYY-W##. " "Example: 2026-W20."),
                )
            )

        billings_by_key[bulk_key] = preview

    # =====================================================================
    # GROUP TECHNICIANS / ITEMS BY BULK KEY
    # =====================================================================

    tech_rows_by_key = defaultdict(list)
    item_rows_by_key = defaultdict(list)

    for row in tech_rows:
        key = _clean_cell(row.get("bulk_key"))

        tech_rows_by_key[key].append(row)

    for row in item_rows:
        key = _clean_cell(row.get("bulk_key"))

        item_rows_by_key[key].append(row)

    # =====================================================================
    # VALIDATE FOREIGN BULK KEYS
    # =====================================================================

    for row in tech_rows:
        key = _clean_cell(row.get("bulk_key"))

        if not key:
            global_errors.append(
                _cell_error(
                    SHEET_TECHNICIANS,
                    row["__rownum"],
                    "bulk_key",
                    "bulk_key is required.",
                )
            )
            continue

        if key not in billings_by_key:
            global_errors.append(
                _cell_error(
                    SHEET_TECHNICIANS,
                    row["__rownum"],
                    "bulk_key",
                    (f"bulk_key '{key}' does not exist " "in Billings sheet."),
                )
            )

    for row in item_rows:
        key = _clean_cell(row.get("bulk_key"))

        if not key:
            global_errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "bulk_key",
                    "bulk_key is required.",
                )
            )
            continue

        if key not in billings_by_key:
            global_errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "bulk_key",
                    (f"bulk_key '{key}' does not exist " "in Billings sheet."),
                )
            )

    # =====================================================================
    # BILLING VALIDATION
    # =====================================================================

    for bulk_key, preview in billings_by_key.items():
        _attach_and_validate_technicians(
            preview,
            tech_rows_by_key.get(
                bulk_key,
                [],
            ),
        )

        # --------------------------------------------------------------
        # A technician row may have requested SHOW NOW.
        #
        # _attach_and_validate_technicians() normalizes that decision
        # to Billing-level show_immediately.
        #
        # Direct Discount cannot use this mode.
        # --------------------------------------------------------------

        if (
            preview.direct_discount
            and preview.show_immediately
            and not any(
                getattr(error, "field", "") == "show_immediately"
                for error in preview.errors
            )
        ):
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    preview.source_row,
                    "show_immediately",
                    (
                        "Direct Discount cannot use Show Immediately "
                        "or SHOW NOW priority. Direct Discount already "
                        "bypasses the execution queue."
                    ),
                )
            )

        _attach_and_validate_items(
            preview,
            item_rows_by_key.get(
                bulk_key,
                [],
            ),
        )

        _validate_project_and_prices(preview)

    billings_preview = list(billings_by_key.values())

    # =====================================================================
    # BUILD INCOMING PLAN BY TECHNICIAN
    #
    # Rules:
    #
    # Direct Discount
    #     -> no queue
    #
    # SHOW NOW
    #     -> no numbered queue
    #
    # explicit priority 1,2,3...
    #     -> relative order inside imported batch
    #
    # AUTO / blank
    #     -> participate in queue automatically
    # =====================================================================

    incoming_by_technician = defaultdict(list)

    for billing in billings_preview:

        if billing.direct_discount:
            continue

        if billing.show_immediately:
            continue

        for technician in billing.technicians:
            if not technician.user_id:
                continue

            if technician.priority_mode not in {
                "explicit",
                "auto",
            }:
                continue

            incoming_by_technician[technician.user_id].append(
                {
                    "billing": billing,
                    "technician": technician,
                }
            )

    queue_plans = []

    # =====================================================================
    # SIMULATE EACH TECHNICIAN INDEPENDENTLY
    # =====================================================================

    for technician_id, incoming_entries in incoming_by_technician.items():

        # -----------------------------------------------------------------
        # Duplicate explicit priority is a PLANNING conflict.
        #
        # It does NOT become billing.errors.
        #
        # Preview will allow:
        #
        # - automatic organization;
        # - manual change;
        # - Show Now.
        # -----------------------------------------------------------------

        explicit_priorities = defaultdict(list)

        for incoming in incoming_entries:
            technician = incoming["technician"]

            if technician.priority_mode != "explicit":
                continue

            requested_priority = technician.requested_priority

            if requested_priority is None:
                continue

            explicit_priorities[requested_priority].append(incoming)

        technician_conflicts = []

        for requested_priority, duplicates in explicit_priorities.items():
            if len(duplicates) <= 1:
                continue

            projects = [
                (duplicate["billing"].project_id or duplicate["billing"].bulk_key)
                for duplicate in duplicates
            ]

            bulk_keys = [duplicate["billing"].bulk_key for duplicate in duplicates]

            technician = duplicates[0]["technician"]

            conflict = {
                "type": "duplicate_import_priority",
                "technician_id": technician_id,
                "technician_name": (technician.display_name or technician.username),
                "username": technician.username,
                "requested_priority": requested_priority,
                "bulk_keys": bulk_keys,
                "projects": projects,
                "message": (
                    f"Priority {requested_priority} is used by "
                    f"{len(duplicates)} imported Billings for "
                    f"{technician.display_name or technician.username}. "
                    "Use Organize Queue Automatically, change the order "
                    "manually, or mark a Billing as Show Now."
                ),
            }

            technician_conflicts.append(conflict)

            planning_conflicts.append(conflict)

        # -----------------------------------------------------------------
        # Current numbered queue
        # -----------------------------------------------------------------

        current_queue_objects = list(_technician_queue(technician_id))

        current_queue = []

        for queue_entry in current_queue_objects:
            assignment = queue_entry.assignment

            current_queue.append(
                {
                    "assignment_id": assignment.id,
                    "billing_id": assignment.sesion_id,
                    "project_id": _project_label(assignment.sesion),
                    "priority": queue_entry.queue_position,
                    "state": assignment.estado,
                    "is_running": False,
                }
            )

        # -----------------------------------------------------------------
        # Current actual running work / timer
        # -----------------------------------------------------------------

        running_work = _running_work(technician_id)

        running_assignment_id = None
        running_project = ""
        running_priority = None

        if running_work:
            running_assignment_id = running_work.assignment_id

            running_project = _project_label(running_work.assignment.sesion)

            for current in current_queue:
                if current["assignment_id"] == running_assignment_id:
                    current["is_running"] = True

                    running_priority = current["priority"]

                    break

        # -----------------------------------------------------------------
        # Sort imported work.
        #
        # Explicit priorities first.
        #
        # When duplicated, stable Excel order is used ONLY for the
        # simulation display. The conflict remains unresolved until
        # Preview modifies the planning decision.
        #
        # AUTO comes after explicit priorities preserving Excel order.
        # -----------------------------------------------------------------

        incoming_entries = sorted(
            incoming_entries,
            key=lambda entry: (
                (0 if entry["technician"].priority_mode == "explicit" else 1),
                (
                    entry["technician"].requested_priority
                    if (
                        entry["technician"].priority_mode == "explicit"
                        and entry["technician"].requested_priority is not None
                    )
                    else 10**9
                ),
                entry["billing"].source_row,
                entry["technician"].source_row,
                entry["billing"].bulk_key,
            ),
        )

        existing_queue_count = len(current_queue)

        resulting_incoming = []

        # -----------------------------------------------------------------
        # Existing queue remains before imported work.
        # -----------------------------------------------------------------

        for index, incoming in enumerate(
            incoming_entries,
            start=1,
        ):
            billing = incoming["billing"]

            technician = incoming["technician"]

            resulting_priority = existing_queue_count + index

            if technician.priority_mode == "explicit":
                requested_priority_label = str(technician.requested_priority)
            else:
                requested_priority_label = "Auto"

            if existing_queue_count:
                priority_warning = (
                    f"This technician already has "
                    f"{existing_queue_count} project"
                    f"{'' if existing_queue_count == 1 else 's'} "
                    f"in the execution queue. "
                    f"Imported order {requested_priority_label} "
                    f"will normally become queue position "
                    f"#{resulting_priority}."
                )
            else:
                priority_warning = ""

            entry_conflicts = [
                conflict
                for conflict in technician_conflicts
                if (
                    billing.bulk_key
                    in conflict.get(
                        "bulk_keys",
                        [],
                    )
                )
            ]

            resulting_incoming.append(
                {
                    "bulk_key": billing.bulk_key,
                    "project_id": (billing.project_id or billing.bulk_key),
                    "source_row": technician.source_row,
                    "username": technician.username,
                    "priority_mode": technician.priority_mode,
                    "requested_priority": technician.requested_priority,
                    "requested_priority_label": requested_priority_label,
                    "resulting_priority": resulting_priority,
                    "existing_queue_count": existing_queue_count,
                    "priority_warning": priority_warning,
                    "has_planning_conflict": bool(entry_conflicts),
                    "planning_conflicts": entry_conflicts,
                }
            )

        first_technician = incoming_entries[0]["technician"]

        technician_name = first_technician.display_name or first_technician.username

        # -----------------------------------------------------------------
        # Human-readable queue notice
        # -----------------------------------------------------------------

        if existing_queue_count:
            queue_notice = (
                f"{technician_name} already has "
                f"{existing_queue_count} project"
                f"{'' if existing_queue_count == 1 else 's'} "
                f"in the current execution queue. "
                f"The imported projects will be added after "
                f"the existing queue unless the plan is explicitly "
                f"changed in Preview."
            )
        else:
            queue_notice = (
                f"{technician_name} has no numbered projects "
                f"in the current execution queue. "
                f"The imported order can start at #1."
            )

        queue_plans.append(
            {
                "technician_id": technician_id,
                "technician_name": technician_name,
                "username": first_technician.username,
                "existing_queue_count": existing_queue_count,
                "has_existing_queue": bool(existing_queue_count),
                "queue_notice": queue_notice,
                "running_assignment_id": running_assignment_id,
                "running_project": running_project,
                "running_priority": running_priority,
                "has_running_work": bool(running_work),
                "has_planning_conflicts": bool(technician_conflicts),
                "planning_conflicts": technician_conflicts,
                "current_queue": current_queue,
                "incoming": resulting_incoming,
            }
        )

    # =====================================================================
    # Stable ordering for display
    # =====================================================================

    queue_plans.sort(
        key=lambda plan: (
            (plan.get("technician_name") or "").lower(),
            plan.get("technician_id") or 0,
        )
    )

    # =====================================================================
    # SERIALIZE BILLINGS
    # =====================================================================

    serialized_billings = [_billing_to_dict(billing) for billing in billings_preview]

    serialized_by_key = {
        billing["bulk_key"]: billing for billing in serialized_billings
    }

    # =====================================================================
    # Attach calculated queue data back into technician rows
    # =====================================================================

    for plan in queue_plans:
        for incoming in plan["incoming"]:
            billing_dict = serialized_by_key.get(incoming["bulk_key"])

            if not billing_dict:
                continue

            matching_technician = None

            for technician_dict in billing_dict.get(
                "technicians",
                [],
            ):
                if (
                    technician_dict.get("user_id") == plan["technician_id"]
                    and technician_dict.get("source_row") == incoming["source_row"]
                ):
                    matching_technician = technician_dict
                    break

            if not matching_technician:
                continue

            matching_technician["resulting_priority"] = incoming["resulting_priority"]

            matching_technician["existing_queue_count"] = incoming[
                "existing_queue_count"
            ]

            matching_technician["queue_has_existing_work"] = bool(
                incoming["existing_queue_count"]
            )

            matching_technician["current_running_project"] = plan["running_project"]

            matching_technician["current_running_priority"] = plan["running_priority"]

            matching_technician["priority_warning"] = incoming["priority_warning"]

            matching_technician["has_planning_conflict"] = incoming[
                "has_planning_conflict"
            ]

            matching_technician["planning_conflicts"] = incoming["planning_conflicts"]

    # =====================================================================
    # Attach execution information to SHOW NOW / DD Billings
    # =====================================================================

    for billing_dict in serialized_billings:
        if billing_dict.get("direct_discount"):
            billing_dict["execution_mode"] = "direct_discount"

            billing_dict["execution_mode_label"] = "Direct Discount"

        elif billing_dict.get("show_immediately"):
            billing_dict["execution_mode"] = "show_now"

            billing_dict["execution_mode_label"] = "Show Now"

        else:
            billing_dict["execution_mode"] = "queue"

            billing_dict["execution_mode_label"] = "Queue"

    # =====================================================================
    # TOTALS
    # =====================================================================

    total_tecnico = Decimal("0.00")

    total_empresa = Decimal("0.00")

    total_items = 0
    total_tech_rows = 0

    for billing in billings_preview:
        total_tecnico += billing.subtotal_tecnico or Decimal("0.00")

        total_empresa += billing.subtotal_empresa or Decimal("0.00")

        total_items += len(billing.items)

        total_tech_rows += len(billing.technicians)

    # =====================================================================
    # REAL VALIDATION ERRORS
    #
    # Planning conflicts deliberately DO NOT participate here.
    # =====================================================================

    has_errors = bool(
        global_errors
        or any(
            billing.errors or any(item.errors for item in billing.items)
            for billing in billings_preview
        )
    )

    # =====================================================================
    # FINAL PAYLOAD
    # =====================================================================

    payload = {
        "ok": not has_errors,
        "has_errors": has_errors,
        "has_planning_conflicts": bool(planning_conflicts),
        "permissions": price_perms,
        "global_errors": global_errors,
        "planning_conflicts": planning_conflicts,
        "billings": serialized_billings,
        "queue_plans": queue_plans,
        "summary": {
            "billing_count": len(billings_preview),
            "item_count": total_items,
            "technician_count": total_tech_rows,
            "subtotal_tecnico": _format_money(total_tecnico),
            "subtotal_empresa": _format_money(total_empresa),
        },
    }

    return payload


def _split_technician_usernames(value):
    """
    Permite cargar técnicos de dos formas:

    1) Una fila por técnico:
       BILL-001 | tech1

    2) Varios técnicos en el mismo campo:
       BILL-001 | tech1, tech2, tech3
       BILL-001 | tech1; tech2; tech3

    No modifica usernames internamente, solo separa por coma, punto y coma o salto de línea.
    """
    raw = _clean_cell(value)

    if not raw:
        return []

    parts = []

    for chunk in raw.replace("\n", ",").replace(";", ",").split(","):
        username = _clean_cell(chunk)

        if username:
            parts.append(username)

    return parts


def _attach_and_validate_technicians(preview: PreviewBilling, rows):
    if not rows:
        preview.errors.append(
            _cell_error(
                SHEET_TECHNICIANS,
                preview.source_row,
                "technician_username",
                "At least one technician is required for this billing.",
            )
        )
        return

    seen = set()

    for row in rows:
        raw_usernames = _clean_cell(row.get("technician_username"))
        raw_priority = _clean_cell(row.get("priority"))

        if not raw_usernames:
            preview.errors.append(
                _cell_error(
                    SHEET_TECHNICIANS,
                    row["__rownum"],
                    "technician_username",
                    "technician_username is required.",
                )
            )

            preview.technicians.append(
                PreviewTechnician(
                    source_row=row["__rownum"],
                    username="",
                    requested_priority=None,
                    priority_mode="auto",
                )
            )

            continue

        usernames = (
            raw_usernames.replace(";", ",")
            .replace("\n", ",")
            .replace("\r", ",")
            .split(",")
        )

        usernames = [
            _clean_cell(username) for username in usernames if _clean_cell(username)
        ]

        if not usernames:
            preview.errors.append(
                _cell_error(
                    SHEET_TECHNICIANS,
                    row["__rownum"],
                    "technician_username",
                    "technician_username is required.",
                )
            )
            continue

        # ======================================================
        # Interpretar priority
        #
        # vacío / AUTO:
        #     cola automática
        #
        # SHOW NOW:
        #     Billing completo inmediatamente visible
        #
        # número:
        #     orden relativo dentro del lote
        # ======================================================

        normalized_priority = raw_priority.strip().upper()

        requested_priority = None
        priority_mode = "auto"
        priority_error = None

        if normalized_priority in {
            "",
            "AUTO",
        }:
            priority_mode = "auto"

        elif normalized_priority in {
            "SHOW NOW",
            "SHOW_NOW",
            "SHOWNOW",
        }:
            priority_mode = "show_now"

        else:
            try:
                requested_decimal = Decimal(raw_priority)

                if (
                    requested_decimal <= 0
                    or requested_decimal != requested_decimal.to_integral_value()
                ):
                    raise ValueError

                requested_priority = int(requested_decimal)
                priority_mode = "explicit"

            except (InvalidOperation, TypeError, ValueError):
                priority_error = (
                    "Priority must be a positive whole number, "
                    "AUTO, SHOW NOW, or blank."
                )

        # ======================================================
        # Prioridad numérica + múltiples técnicos
        #
        # Cada técnico tiene una cola independiente, por lo que
        # una prioridad explícita requiere una fila por técnico.
        # ======================================================

        if len(usernames) > 1 and priority_mode == "explicit":
            priority_error = (
                "A numeric priority cannot be used with multiple "
                "technicians in the same cell. Use one technician "
                "per row when defining an explicit priority."
            )

        if priority_error:
            preview.errors.append(
                _cell_error(
                    SHEET_TECHNICIANS,
                    row["__rownum"],
                    "priority",
                    priority_error,
                )
            )

        # ======================================================
        # Resolver técnicos
        # ======================================================

        for username in usernames:
            tech = PreviewTechnician(
                source_row=row["__rownum"],
                username=username,
                requested_priority=(requested_priority if not priority_error else None),
                priority_mode=(priority_mode if not priority_error else "invalid"),
            )

            normalized_username = username.strip().lower()

            if normalized_username in seen:
                preview.errors.append(
                    _cell_error(
                        SHEET_TECHNICIANS,
                        row["__rownum"],
                        "technician_username",
                        f"Duplicate technician '{username}' in this billing.",
                    )
                )

                preview.technicians.append(tech)
                continue

            seen.add(normalized_username)

            user = CustomUser.objects.filter(username__iexact=username).first()

            if not user:
                preview.errors.append(
                    _cell_error(
                        SHEET_TECHNICIANS,
                        row["__rownum"],
                        "technician_username",
                        f"Technician username '{username}' does not exist.",
                    )
                )

                preview.technicians.append(tech)
                continue

            try:
                is_user_role = user.roles.filter(nombre__iexact="usuario").exists()
            except Exception:
                is_user_role = True

            if not is_user_role:
                preview.errors.append(
                    _cell_error(
                        SHEET_TECHNICIANS,
                        row["__rownum"],
                        "technician_username",
                        f"User '{username}' is not a technician user.",
                    )
                )

            tech.user_id = user.id
            tech.display_name = _display_user(user)

            preview.technicians.append(tech)

    # ==========================================================
    # SHOW NOW es Billing-level.
    #
    # Puede venir de:
    #
    # - Billings.show_immediately = YES
    # - Technicians.priority = SHOW NOW
    #
    # Si cualquiera lo solicita, todo el Billing queda Show Now.
    # ==========================================================

    technician_requested_show_now = any(
        technician.priority_mode == "show_now"
        for technician in preview.technicians
        if technician.username
    )

    if preview.show_immediately or technician_requested_show_now:
        preview.show_immediately = True

        for technician in preview.technicians:
            if technician.priority_mode == "invalid":
                continue

            technician.requested_priority = None
            technician.priority_mode = "show_now"


def _attach_and_validate_items(preview: PreviewBilling, rows):
    if not rows:
        preview.errors.append(
            _cell_error(
                SHEET_ITEMS,
                preview.source_row,
                "job_code",
                "At least one item is required for this billing.",
            )
        )
        return

    for row in rows:
        job_code = _clean_cell(row.get("job_code"))
        quantity, quantity_raw = _to_decimal(row.get("quantity"))

        item = PreviewItem(
            source_row=row["__rownum"],
            job_code=job_code,
            quantity_raw=quantity_raw,
            quantity=quantity,
        )

        if not job_code:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "job_code",
                    "job_code is required.",
                )
            )

        if quantity is None:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "quantity",
                    "Quantity must be a valid number.",
                )
            )
        elif quantity == 0:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "quantity",
                    "Quantity cannot be zero.",
                )
            )
        elif preview.direct_discount and quantity >= 0:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "quantity",
                    "Direct discount requires a negative quantity.",
                )
            )
        elif not preview.direct_discount and quantity <= 0:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    row["__rownum"],
                    "quantity",
                    "Normal billing requires a positive quantity.",
                )
            )

        preview.items.append(item)


def _validate_requirement_list_for_preview(preview: PreviewBilling, project):
    requirement_type = (preview.requirement_type or "none").strip().lower()
    requirement_list_name = (preview.requirement_list or "").strip()

    if requirement_type in ("", "none"):
        preview.requirement_type = "none"
        preview.requirement_list = ""
        preview.requirement_list_id = None
        preview.requirement_list_label = ""
        preview.requirement_count = 0
        return

    if requirement_type not in (
        RequirementList.LIST_TYPE_FIBER,
        RequirementList.LIST_TYPE_CABLE,
    ):
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "requirement_type",
                "Use none, fiber or cable.",
            )
        )
        return

    if not requirement_list_name:
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "requirement_list",
                "Requirement list is required when requirement_type is fiber or cable.",
            )
        )
        return

    qs = (
        RequirementList.objects.filter(
            project=project,
            list_type=requirement_type,
            is_active=True,
            name__iexact=requirement_list_name,
        )
        .prefetch_related("items")
    )

    count = qs.count()

    if count == 0:
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "requirement_list",
                (
                    f"Active Requirement List '{requirement_list_name}' was not found "
                    f"for Project '{project.nombre}' and type '{requirement_type}'."
                ),
            )
        )
        return

    if count > 1:
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "requirement_list",
                (
                    f"More than one active Requirement List named '{requirement_list_name}' "
                    f"was found for Project '{project.nombre}' and type '{requirement_type}'."
                ),
            )
        )
        return

    req_list = qs.first()
    req_count = req_list.items.count()

    if req_count <= 0:
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "requirement_list",
                f"Requirement List '{req_list.name}' has no requirements.",
            )
        )
        return

    preview.requirement_type = requirement_type
    preview.requirement_list = req_list.name
    preview.requirement_list_id = req_list.id
    preview.requirement_list_label = f"{req_list.name} ({req_count} item(s))"
    preview.requirement_count = req_count


def _validate_project_and_prices(preview: PreviewBilling):
    """
    Valida:
    - que exista el Proyecto indicado en project
    - que existan precios para:
      Technician + Client + City + Proyecto FK + Office + Job Code
    - que exista Requirement List si el Excel trae requirement_type/list.
    """

    if preview.errors:
        return

    project, project_error = _find_project_robust(preview)

    if not project:
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "project",
                project_error,
            )
        )
        return

    preview.project = project.nombre

    _validate_requirement_list_for_preview(preview, project)

    if preview.errors:
        return

    valid_techs = [t for t in preview.technicians if t.user_id]

    if not valid_techs:
        return

    for item in preview.items:
        if item.errors:
            continue

        _hydrate_item_prices(preview, item, project, valid_techs)


def _hydrate_item_prices(
    preview: PreviewBilling, item: PreviewItem, project, valid_techs
):
    qty = item.quantity or Decimal("0.00")

    prices = []

    # Estos valores vienen del Excel y deben calzar contra PrecioActividadTecnico
    client = _clean_cell(preview.client)
    city = _clean_cell(preview.city)
    office = _clean_cell(preview.office)

    for tech in valid_techs:
        price, price_error = _find_price_robust(
            tech_id=tech.user_id,
            project=project,
            client=client,
            city=city,
            office=office,
            job_code=item.job_code,
        )

        if price_error:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    item.source_row,
                    "job_code",
                    price_error,
                )
            )
            continue

        if not price:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    item.source_row,
                    "job_code",
                    (
                        f"Technician '{tech.username}' does not have a matching price for "
                        f"Client '{client}', City '{city}', Project '{project.nombre}', "
                        f"Office '{office}', Job Code '{item.job_code}'."
                    ),
                )
            )
            continue

        prices.append((tech, price))

    if item.errors:
        return

    if not prices:
        item.errors.append(
            _cell_error(
                SHEET_ITEMS,
                item.source_row,
                "job_code",
                f"Job Code '{item.job_code}' does not exist for the selected configuration.",
            )
        )
        return

    first_price = prices[0][1]

    # Canonicalizar header desde la tabla de precios
    preview.client = first_price.cliente
    preview.city = first_price.ciudad
    preview.office = first_price.oficina
    preview.project = project.nombre

    item.tipo_trabajo = first_price.tipo_trabajo
    item.descripcion = first_price.descripcion
    item.unidad_medida = first_price.unidad_medida
    item.precio_empresa = first_price.precio_empresa or Decimal("0.00")

    for tech, price in prices:
        if not _same_text(price.tipo_trabajo, first_price.tipo_trabajo):
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    item.source_row,
                    "job_code",
                    f"Job Code '{item.job_code}' has different Work Type for technician '{tech.username}'.",
                )
            )

        if not _same_text(price.descripcion, first_price.descripcion):
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    item.source_row,
                    "job_code",
                    f"Job Code '{item.job_code}' has different Description for technician '{tech.username}'.",
                )
            )

        if not _same_text(price.unidad_medida, first_price.unidad_medida):
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    item.source_row,
                    "job_code",
                    f"Job Code '{item.job_code}' has different UOM for technician '{tech.username}'.",
                )
            )

        if price.precio_empresa != first_price.precio_empresa:
            item.errors.append(
                _cell_error(
                    SHEET_ITEMS,
                    item.source_row,
                    "job_code",
                    f"Job Code '{item.job_code}' has different Company Price for technician '{tech.username}'.",
                )
            )

    if item.errors:
        return

    # Canonicalizar Job Code desde la tabla de precios.
    item.job_code = first_price.codigo_trabajo

    percentage = Decimal("100.00")

    if preview.tech_payment_mode == "split":
        percentage = (Decimal("100.00") / Decimal(len(prices))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    total_tech = Decimal("0.00")

    for tech, price in prices:
        tarifa_base = price.precio_tecnico or Decimal("0.00")

        if preview.tech_payment_mode == "full":
            tarifa_efectiva = tarifa_base
            pct = Decimal("100.00")
        else:
            pct = percentage
            tarifa_efectiva = (tarifa_base * pct / Decimal("100.00")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        subtotal = (qty * tarifa_efectiva).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        total_tech += subtotal

        item.desglose_tecnico.append(
            {
                "tecnico_id": tech.user_id,
                "tecnico_username": tech.username,
                "tecnico_nombre": tech.display_name,
                "tarifa_base": _format_money(tarifa_base),
                "porcentaje": _format_money(pct),
                "tarifa_efectiva": _format_money(tarifa_efectiva),
                "subtotal": _format_money(subtotal),
                "payment_weeks": int(price.payment_weeks or 0),
            }
        )

    item.subtotal_tecnico = total_tech.quantize(Decimal("0.01"))
    item.subtotal_empresa = (qty * item.precio_empresa).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    preview.subtotal_tecnico += item.subtotal_tecnico
    preview.subtotal_empresa += item.subtotal_empresa


def _billing_to_dict(preview: PreviewBilling):
    if preview.direct_discount:
        execution_mode = "direct_discount"
        execution_mode_label = "Direct Discount"

    elif preview.show_immediately:
        execution_mode = "show_now"
        execution_mode_label = "Show Now"

    else:
        execution_mode = "queue"
        execution_mode_label = "Queue"

    return {
        "bulk_key": preview.bulk_key,
        "source_row": preview.source_row,
        "project_id": preview.project_id,
        "client": preview.client,
        "city": preview.city,
        "project": preview.project,
        "office": preview.office,
        "project_address": preview.project_address,
        "projected_week": preview.projected_week,
        "tech_payment_mode": preview.tech_payment_mode,
        "direct_discount": preview.direct_discount,
        "show_immediately": preview.show_immediately,
        "execution_mode": execution_mode,
        "execution_mode_label": execution_mode_label,
        "cable_installation": preview.cable_installation,
        "requirement_type": preview.requirement_type,
        "requirement_list": preview.requirement_list,
        "requirement_list_id": preview.requirement_list_id,
        "requirement_list_label": preview.requirement_list_label,
        "requirement_count": preview.requirement_count,
        "subtotal_tecnico": _format_money(preview.subtotal_tecnico),
        "subtotal_empresa": _format_money(preview.subtotal_empresa),
        "errors": preview.errors,
        "technicians": [
            {
                "source_row": t.source_row,
                "username": t.username,
                "user_id": t.user_id,
                "display_name": t.display_name,
                "requested_priority": t.requested_priority,
                "priority_mode": t.priority_mode,
                "requested_priority_label": (
                    str(t.requested_priority)
                    if (
                        t.priority_mode == "explicit"
                        and t.requested_priority is not None
                    )
                    else (
                        "Show Now"
                        if t.priority_mode == "show_now"
                        else ("Invalid" if t.priority_mode == "invalid" else "Auto")
                    )
                ),
                "resulting_priority": None,
                "existing_queue_count": 0,
                "queue_has_existing_work": False,
                "current_running_project": "",
                "current_running_priority": None,
                "priority_warning": "",
                "has_planning_conflict": False,
                "planning_conflicts": [],
            }
            for t in preview.technicians
        ],
        "items": [
            {
                "source_row": item.source_row,
                "job_code": item.job_code,
                "quantity_raw": item.quantity_raw,
                "quantity": (
                    _format_money(item.quantity or Decimal("0.00"))
                    if item.quantity is not None
                    else ""
                ),
                "tipo_trabajo": item.tipo_trabajo,
                "descripcion": item.descripcion,
                "unidad_medida": item.unidad_medida,
                "precio_empresa": _format_money(item.precio_empresa),
                "subtotal_tecnico": _format_money(item.subtotal_tecnico),
                "subtotal_empresa": _format_money(item.subtotal_empresa),
                "desglose_tecnico": item.desglose_tecnico,
                "errors": item.errors,
            }
            for item in preview.items
        ],
    }


# =============================================================================
# CONFIRMAR CREACIÓN
# =============================================================================


@login_required
@rol_requerido("admin", "pm", "supervisor", "facturacion", "emision_facturacion")
@transaction.atomic
def billing_masivo_confirm(request):
    if request.method != "POST":
        return redirect("operaciones:billing_masivo_upload")

    payload = _get_bulk_billing_preview(request)

    if not payload:
        token = request.session.get("billing_masivo_preview_token")

        logger.error(
            "[BULK BILLING] Confirm aborted because Preview payload is missing "
            "user_id=%s token=%s",
            request.user.id,
            token[:8] if token else "missing",
        )

        messages.warning(
            request,
            "Please upload a bulk billing file first.",
        )
        return redirect("operaciones:billing_masivo_upload")

    # =====================================================================
    # PREVIEW MUST BE FULLY VALID BEFORE CREATION
    # =====================================================================

    if payload.get("has_errors"):
        logger.error(
            "[BULK BILLING] Confirm aborted because Preview has validation errors "
            "user_id=%s billing_count=%s",
            request.user.id,
            len(payload.get("billings") or []),
        )

        messages.error(
            request,
            ("The import still has validation errors. " "No billing was created."),
        )
        return redirect("operaciones:billing_masivo_preview")

    if payload.get("has_planning_conflicts"):
        logger.error(
            "[BULK BILLING] Confirm aborted because Preview has planning conflicts "
            "user_id=%s billing_count=%s",
            request.user.id,
            len(payload.get("billings") or []),
        )

        messages.error(
            request,
            (
                "Execution planning still has unresolved conflicts. "
                "Resolve them in Preview before creating the Billings."
            ),
        )
        return redirect("operaciones:billing_masivo_preview")

    billings = payload.get("billings") or []

    if not billings:
        logger.error(
            "[BULK BILLING] Confirm aborted because Preview contains no Billings "
            "user_id=%s",
            request.user.id,
        )

        messages.error(
            request,
            "There are no billings to create.",
        )
        return redirect("operaciones:billing_masivo_upload")

    queue_plans = payload.get("queue_plans") or []

    # =====================================================================
    # LOCAL IMPORTS
    # =====================================================================

    from operaciones.models_billing_queue import BillingAssignmentQueue
    from operaciones.services.billing_assignment_queue import \
        activate_assignment_in_managed_queue
    from operaciones.services.billing_technician_queue import (
        _lock_technician_assignments, show_now_project)

    # =====================================================================
    # BASIC PAYLOAD CONSISTENCY VALIDATION
    # =====================================================================

    billing_by_key = {billing.get("bulk_key"): billing for billing in billings}

    for billing in billings:
        execution_mode = billing.get("execution_mode")

        direct_discount = bool(billing.get("direct_discount"))

        if direct_discount:
            if execution_mode not in {
                "direct_discount",
                None,
                "",
            }:
                logger.error(
                    "[BULK BILLING] Invalid Direct Discount execution plan "
                    "user_id=%s bulk_key=%s execution_mode=%s",
                    request.user.id,
                    billing.get("bulk_key"),
                    execution_mode,
                )

                messages.error(
                    request,
                    (
                        f"Billing {billing.get('bulk_key')} has an invalid "
                        "execution plan for Direct Discount."
                    ),
                )
                return redirect("operaciones:billing_masivo_preview")

            continue

        if execution_mode not in {
            "queue",
            "show_now",
        }:
            logger.error(
                "[BULK BILLING] Invalid execution plan "
                "user_id=%s bulk_key=%s execution_mode=%s",
                request.user.id,
                billing.get("bulk_key"),
                execution_mode,
            )

            messages.error(
                request,
                (
                    f"Billing {billing.get('bulk_key')} does not have "
                    "a valid execution plan."
                ),
            )
            return redirect("operaciones:billing_masivo_preview")

    # =====================================================================
    # TECHNICIANS WHOSE REAL QUEUES MUST REMAIN STABLE
    # =====================================================================

    managed_technician_ids = sorted(
        {
            int(plan.get("technician_id"))
            for plan in queue_plans
            if plan.get("technician_id")
        }
    )

    created_ids = []

    # Maps:
    #
    #   (bulk_key, technician_id, source_row)
    #       -> SesionBillingTecnico
    #
    created_assignment_map = {}

    # Billing objects that must become Show Now after assignments exist.
    show_now_sessions = []

    # Detect queue drift without creating anything.
    queue_drift = False

    try:

        with transaction.atomic():

            # =============================================================
            # SERIALIZE QUEUE OPERATIONS PER TECHNICIAN
            # =============================================================

            if managed_technician_ids:
                _lock_technician_assignments(managed_technician_ids)

            # =============================================================
            # REVALIDATE CURRENT QUEUE AGAINST PREVIEW SNAPSHOT
            #
            # Nothing has been created yet.
            # =============================================================

            for plan in queue_plans:

                technician_id = plan.get("technician_id")

                if not technician_id:
                    continue

                preview_snapshot = [
                    (
                        int(item.get("assignment_id")),
                        int(item.get("priority")),
                    )
                    for item in (plan.get("queue_snapshot") or [])
                    if (item.get("assignment_id") and item.get("priority"))
                ]

                current_entries = list(
                    BillingAssignmentQueue.objects.select_for_update()
                    .filter(
                        technician_id=technician_id,
                        queue_position__isnull=False,
                    )
                    .order_by(
                        "queue_position",
                        "id",
                    )
                    .values_list(
                        "assignment_id",
                        "queue_position",
                    )
                )

                current_snapshot = [
                    (
                        int(assignment_id),
                        int(position),
                    )
                    for (
                        assignment_id,
                        position,
                    ) in current_entries
                ]

                if current_snapshot != preview_snapshot:
                    logger.error(
                        "[BULK BILLING] QUEUE DRIFT detected "
                        "user_id=%s technician_id=%s "
                        "preview_snapshot=%s current_snapshot=%s "
                        "incoming=%s",
                        request.user.id,
                        technician_id,
                        preview_snapshot,
                        current_snapshot,
                        plan.get("incoming") or [],
                    )

                    queue_drift = True
                    break

            # =============================================================
            # IF QUEUE CHANGED, DO NOT CREATE ANYTHING
            # =============================================================

            if queue_drift:
                pass

            else:

                # =========================================================
                # CREATE BILLINGS AND TECHNICIAN ASSIGNMENTS
                #
                # IMPORTANT:
                #
                # We deliberately DO NOT activate queue entries here.
                #
                # First we create all Billings and assignments.
                # After that, we apply the complete execution plan.
                # =========================================================

                for b in billings:

                    direct_discount = bool(b.get("direct_discount"))

                    execution_mode = b.get("execution_mode") or (
                        "direct_discount" if direct_discount else "queue"
                    )

                    sesion = SesionBilling.objects.create(
                        creado_en=timezone.now(),
                        is_direct_discount=direct_discount,
                        is_cable_installation=bool(b.get("cable_installation")),
                        tech_payment_mode=(b.get("tech_payment_mode") or "full"),
                        proyecto_id=(b.get("project_id") or ""),
                        cliente=(b.get("client") or ""),
                        ciudad=(b.get("city") or ""),
                        proyecto=(b.get("project") or ""),
                        oficina=(b.get("office") or ""),
                        direccion_proyecto=(b.get("project_address") or ""),
                        semana_pago_proyectada=(b.get("projected_week") or ""),
                        estado="asignado",
                        subtotal_tecnico=Decimal(
                            str(b.get("subtotal_tecnico") or "0.00")
                        ),
                        subtotal_empresa=Decimal(
                            str(b.get("subtotal_empresa") or "0.00")
                        ),
                    )

                    sesion.cliente = b.get("client") or ""

                    sesion.ciudad = b.get("city") or ""

                    sesion.proyecto = b.get("project") or ""

                    sesion.oficina = b.get("office") or ""

                    sesion.subtotal_tecnico = Decimal(
                        str(b.get("subtotal_tecnico") or "0.00")
                    )

                    sesion.subtotal_empresa = Decimal(
                        str(b.get("subtotal_empresa") or "0.00")
                    )

                    if sesion.is_direct_discount:
                        sesion.finance_status = "review_discount"

                    sesion.save(
                        update_fields=[
                            "cliente",
                            "ciudad",
                            "proyecto",
                            "oficina",
                            "subtotal_tecnico",
                            "subtotal_empresa",
                            "finance_status",
                        ]
                    )

                    created_ids.append(sesion.id)

                    technicians = b.get("technicians") or []

                    valid_technicians = [
                        technician
                        for technician in technicians
                        if technician.get("user_id")
                    ]

                    tech_count = max(
                        len(valid_technicians),
                        1,
                    )

                    created_tech_sessions = []

                    # =====================================================
                    # TECHNICIAN ASSIGNMENTS
                    # =====================================================

                    for t in valid_technicians:

                        user_id = int(t.get("user_id"))

                        porcentaje = Decimal("100.00")

                        if sesion.tech_payment_mode == "split":
                            porcentaje = (
                                Decimal("100.00") / Decimal(tech_count)
                            ).quantize(
                                Decimal("0.01"),
                                rounding=ROUND_HALF_UP,
                            )

                        tecnico_sesion = SesionBillingTecnico.objects.create(
                            sesion=sesion,
                            tecnico_id=user_id,
                            porcentaje=porcentaje,
                            estado="asignado",
                            is_active=True,
                        )

                        created_tech_sessions.append(tecnico_sesion)

                        assignment_key = (
                            b.get("bulk_key"),
                            user_id,
                            int(t.get("source_row") or 0),
                        )

                        created_assignment_map[assignment_key] = tecnico_sesion

                    # =====================================================
                    # REQUIREMENTS
                    # =====================================================

                    _apply_requirement_list_to_sesion(
                        sesion=sesion,
                        requirement_list_id=b.get("requirement_list_id"),
                        requirement_type=b.get("requirement_type"),
                        tecnico_sesiones=(created_tech_sessions),
                    )

                    # =====================================================
                    # ITEMS
                    # =====================================================

                    for item_data in b.get("items") or []:

                        item = ItemBilling.objects.create(
                            sesion=sesion,
                            codigo_trabajo=(item_data.get("job_code") or ""),
                            tipo_trabajo=(item_data.get("tipo_trabajo") or ""),
                            descripcion=(item_data.get("descripcion") or ""),
                            unidad_medida=(item_data.get("unidad_medida") or ""),
                            cantidad=Decimal(str(item_data.get("quantity") or "0.00")),
                            precio_empresa=Decimal(
                                str(item_data.get("precio_empresa") or "0.00")
                            ),
                            subtotal_empresa=Decimal(
                                str(item_data.get("subtotal_empresa") or "0.00")
                            ),
                            subtotal_tecnico=Decimal(
                                str(item_data.get("subtotal_tecnico") or "0.00")
                            ),
                        )

                        for d in item_data.get("desglose_tecnico") or []:

                            ItemBillingTecnico.objects.create(
                                item=item,
                                tecnico_id=d.get("tecnico_id"),
                                tarifa_base=Decimal(
                                    str(d.get("tarifa_base") or "0.00")
                                ),
                                porcentaje=Decimal(str(d.get("porcentaje") or "0.00")),
                                tarifa_efectiva=Decimal(
                                    str(d.get("tarifa_efectiva") or "0.00")
                                ),
                                subtotal=Decimal(str(d.get("subtotal") or "0.00")),
                            )

                            _create_pay_week_snapshot(
                                sesion=sesion,
                                item=item,
                                tecnico_id=d.get("tecnico_id"),
                                codigo_trabajo=(item.codigo_trabajo),
                                tipo_trabajo=(item.tipo_trabajo),
                                payment_weeks=int(d.get("payment_weeks") or 0),
                                semana_base=(sesion.semana_pago_proyectada),
                                tarifa_base=Decimal(
                                    str(d.get("tarifa_base") or "0.00")
                                ),
                                porcentaje=Decimal(str(d.get("porcentaje") or "0.00")),
                                tarifa_efectiva=Decimal(
                                    str(d.get("tarifa_efectiva") or "0.00")
                                ),
                                subtotal=Decimal(str(d.get("subtotal") or "0.00")),
                            )

                    # =====================================================
                    # SAVE SHOW NOW TARGETS
                    # =====================================================

                    if not sesion.is_direct_discount and execution_mode == "show_now":
                        show_now_sessions.append(sesion)

                # =========================================================
                # APPLY SHOW NOW
                #
                # This creates/updates each assignment queue_state with:
                #
                # queue_position = NULL
                # is_released = True
                # released_manually = True
                #
                # It does NOT touch timers or operational state.
                # =========================================================

                for sesion in show_now_sessions:
                    show_now_project(
                        sesion,
                        request.user,
                    )

                # =========================================================
                # APPLY NUMBERED QUEUES
                #
                # queue_plans["incoming"] is already the definitive order
                # for each technician.
                #
                # Existing work stays untouched.
                #
                # New assignments are appended one by one in exactly this
                # sequence, therefore:
                #
                # existing:
                #   #1 A
                #   #2 B
                #
                # preview incoming:
                #   C
                #   D
                #   E
                #
                # becomes:
                #   #1 A
                #   #2 B
                #   #3 C
                #   #4 D
                #   #5 E
                # =========================================================

                queued_assignment_ids = set()

                for plan in queue_plans:

                    technician_id = int(plan.get("technician_id"))

                    for incoming in plan.get("incoming") or []:

                        bulk_key = incoming.get("bulk_key")

                        source_row = int(incoming.get("source_row") or 0)

                        assignment_key = (
                            bulk_key,
                            technician_id,
                            source_row,
                        )

                        assignment = created_assignment_map.get(assignment_key)

                        if assignment is None:
                            raise ValueError(
                                (
                                    "The execution plan could not be "
                                    "matched to a created technician "
                                    f"assignment: {bulk_key}, "
                                    f"technician #{technician_id}, "
                                    f"row {source_row}."
                                )
                            )

                        queue_state, _ = activate_assignment_in_managed_queue(
                            assignment
                        )

                        if queue_state is None or queue_state.queue_position is None:
                            raise ValueError(
                                (
                                    "A queued technician assignment "
                                    "could not be added to the managed "
                                    f"execution queue: {bulk_key}, "
                                    f"technician #{technician_id}."
                                )
                            )

                        queued_assignment_ids.add(assignment.id)

                # =========================================================
                # FINAL SAFETY CHECK
                #
                # Every normal Queue Billing assignment must have been
                # materialized into one technician queue.
                #
                # Direct Discount and Show Now are intentionally excluded.
                # =========================================================

                for b in billings:

                    if b.get("direct_discount"):
                        continue

                    if b.get("execution_mode") != "queue":
                        continue

                    bulk_key = b.get("bulk_key")

                    for t in b.get("technicians") or []:

                        user_id = t.get("user_id")

                        if not user_id:
                            continue

                        assignment_key = (
                            bulk_key,
                            int(user_id),
                            int(t.get("source_row") or 0),
                        )

                        assignment = created_assignment_map.get(assignment_key)

                        if assignment is None:
                            raise ValueError(
                                (
                                    "A technician assignment is "
                                    "missing from the creation plan."
                                )
                            )

                        if assignment.id not in queued_assignment_ids:
                            raise ValueError(
                                (
                                    "A queued Billing technician "
                                    "assignment was not materialized "
                                    "in the execution queue."
                                )
                            )

    except Exception as exc:

        logger.exception(
            "[BULK BILLING] Confirm failed and transaction was rolled back "
            "user_id=%s billing_count=%s managed_technician_ids=%s error=%s",
            request.user.id,
            len(billings),
            managed_technician_ids,
            exc,
        )

        messages.error(
            request,
            (
                "No billing was created because the bulk operation "
                f"could not be completed safely: {exc}"
            ),
        )

        return redirect("operaciones:billing_masivo_preview")

    # =====================================================================
    # QUEUE CHANGED AFTER PREVIEW
    #
    # Transaction made no writes, so nothing was created.
    #
    # Rebuild Preview from current database state so user sees the new
    # positions immediately.
    # =====================================================================

    if queue_drift:

        logger.error(
            "[BULK BILLING] Confirm stopped because technician queue changed. "
            "Rebuilding Preview. user_id=%s billing_count=%s "
            "managed_technician_ids=%s",
            request.user.id,
            len(billings),
            managed_technician_ids,
        )

        payload = _rebuild_bulk_billing_execution_plan(payload)

        _update_bulk_billing_preview(
            request,
            payload,
        )

        messages.warning(
            request,
            (
                "A technician execution queue changed after this "
                "Preview was calculated. No billing was created. "
                "The Preview has been recalculated with the current queue."
            ),
        )

        return redirect("operaciones:billing_masivo_preview")

    # =====================================================================
    # SUCCESS
    # =====================================================================

    _clear_bulk_billing_preview(request)

    messages.success(
        request,
        (
            f"{len(created_ids)} billing(s) created successfully "
            "with the reviewed execution plan."
        ),
    )

    return redirect("operaciones:listar_billing")


def _create_pay_week_snapshot(
    sesion,
    item,
    tecnico_id,
    codigo_trabajo,
    tipo_trabajo,
    payment_weeks,
    semana_base,
    tarifa_base,
    porcentaje,
    tarifa_efectiva,
    subtotal,
):
    semana_resultado = _add_weeks_to_iso_week(semana_base, payment_weeks)

    BillingPayWeekSnapshot.objects.create(
        sesion=sesion,
        tecnico_id=tecnico_id,
        item=item,
        codigo_trabajo=codigo_trabajo or "",
        tipo_trabajo=tipo_trabajo or "",
        payment_weeks=payment_weeks or 0,
        semana_base=semana_base or "",
        semana_resultado=semana_resultado or semana_base or "",
        tarifa_base=tarifa_base or Decimal("0.00"),
        porcentaje=porcentaje or Decimal("0.00"),
        tarifa_efectiva=tarifa_efectiva or Decimal("0.00"),
        subtotal=subtotal or Decimal("0.00"),
        payment_status="pending",
    )


def _add_weeks_to_iso_week(iso_week, weeks_to_add):
    iso_week = _clean_cell(iso_week).upper()

    if not _iso_week_is_valid(iso_week):
        return iso_week

    try:
        import datetime

        year = int(iso_week[:4])
        week = int(iso_week[6:])

        monday = datetime.date.fromisocalendar(year, week, 1)
        result = monday + datetime.timedelta(weeks=int(weeks_to_add or 0))
        y, w, _ = result.isocalendar()

        return f"{y}-W{int(w):02d}"
    except Exception:
        return iso_week


def _find_project_robust(preview: PreviewBilling):
    """
    Busca Proyecto de forma robusta pero segura.

    En el import masivo, project puede venir como:
    - Proyecto.nombre
    - Proyecto.codigo
    - Proyecto.id

    Para mantener simetría con Technician Prices, lo normal es usar
    el nombre visible del proyecto, ejemplo: Underground.
    """

    project_value = _clean_cell(preview.project)

    if not project_value:
        return None, "Project is required."

    candidates = list(Proyecto.objects.all())

    if project_value.isdigit():
        by_id = [p for p in candidates if str(p.id) == project_value]

        if len(by_id) == 1:
            return by_id[0], None

        if len(by_id) > 1:
            return (
                None,
                f"Project ID '{preview.project}' matched more than one project.",
            )

    by_name = [
        p for p in candidates if _same_text(getattr(p, "nombre", ""), project_value)
    ]

    if len(by_name) == 1:
        return by_name[0], None

    if len(by_name) > 1:
        return None, (
            f"Project name '{preview.project}' matched more than one project. "
            "Use Proyecto.codigo or Proyecto.id to avoid ambiguity."
        )

    by_code = [
        p for p in candidates if _same_text(getattr(p, "codigo", ""), project_value)
    ]

    if len(by_code) == 1:
        return by_code[0], None

    if len(by_code) > 1:
        return None, (
            f"Project code '{preview.project}' matched more than one project. "
            "Use the exact Project name from the system."
        )

    return None, (
        f"Project '{preview.project}' does not exist. "
        "Use the Project value shown in Technician Prices."
    )


def _apply_requirement_list_to_sesion(
    *,
    sesion,
    requirement_list_id,
    requirement_type,
    tecnico_sesiones,
):
    requirement_type = (requirement_type or "none").strip().lower()

    if not requirement_list_id or requirement_type in ("", "none"):
        return

    req_list = (
        RequirementList.objects.filter(
            id=requirement_list_id,
            is_active=True,
        )
        .prefetch_related("items")
        .first()
    )

    if not req_list:
        return

    if req_list.list_type == RequirementList.LIST_TYPE_FIBER:
        _apply_fiber_requirement_list_to_sesion(
            sesion=sesion,
            req_list=req_list,
            tecnico_sesiones=tecnico_sesiones,
        )
        return

    if req_list.list_type == RequirementList.LIST_TYPE_CABLE:
        _apply_cable_requirement_list_to_sesion(
            sesion=sesion,
            req_list=req_list,
            tecnico_sesiones=tecnico_sesiones,
        )
        return


def _apply_fiber_requirement_list_to_sesion(
    *,
    sesion,
    req_list,
    tecnico_sesiones,
):
    items = list(req_list.items.all().order_by("order", "id"))

    for item in items:
        title = (item.title or "").strip()

        if not title:
            continue

        plantilla, _ = RequisitoFotoBillingPlantilla.objects.update_or_create(
            sesion=sesion,
            slug=slugify(title),
            defaults={
                "titulo": title,
                "descripcion": item.description or "",
                "obligatorio": bool(item.required),
                "orden": item.order or 0,
                "needs_power_reading": bool(item.needs_power_reading),
                "needs_light_source_reading": bool(item.needs_light_source_reading),
                "power_port_no": item.power_port_no,
            },
        )

        for tecnico_sesion in tecnico_sesiones:
            RequisitoFotoBilling.objects.update_or_create(
                tecnico_sesion=tecnico_sesion,
                titulo=plantilla.titulo,
                defaults={
                    "descripcion": plantilla.descripcion or "",
                    "obligatorio": bool(plantilla.obligatorio),
                    "orden": plantilla.orden or 0,
                    "needs_power_reading": bool(plantilla.needs_power_reading),
                    "needs_light_source_reading": bool(
                        plantilla.needs_light_source_reading
                    ),
                    "power_port_no": plantilla.power_port_no,
                },
            )


def _apply_cable_requirement_list_to_sesion(
    *,
    sesion,
    req_list,
    tecnico_sesiones,
):
    """
    Crea los Cable Requirements del billing y los asigna a cada técnico.

    Fuente:
    - RequirementListItem.handhole
    - RequirementListItem.planned_reserve_ft
    - RequirementListItem.required
    - RequirementListItem.warning
    - RequirementListItem.order

    Destino:
    - cable_installation.CableRequirement
    - cable_installation.CableAssignmentRequirement
    """

    from cable_installation.models import (CableAssignmentRequirement,
                                           CableRequirement)

    items = list(req_list.items.all().order_by("order", "id"))

    if not items:
        return

    next_sequence = CableRequirement.next_sequence_for_billing(sesion)

    for idx, item in enumerate(items):
        handhole = (item.handhole or item.title or "").strip()

        if not handhole:
            continue

        sequence_no = next_sequence + idx

        cable_requirement, _ = CableRequirement.objects.update_or_create(
            billing=sesion,
            sequence_no=sequence_no,
            defaults={
                "handhole": handhole,
                "planned_reserve_ft": item.planned_reserve_ft or Decimal("0.00"),
                "warning": item.warning or "",
                "required": bool(item.required),
                "order": item.order or 0,
            },
        )

        for tecnico_sesion in tecnico_sesiones:
            CableAssignmentRequirement.objects.update_or_create(
                assignment=tecnico_sesion,
                requirement=cable_requirement,
                defaults={
                    "status": CableAssignmentRequirement.STATUS_PENDING,
                    "note": "",
                    "supervisor_note": "",
                },
            )
