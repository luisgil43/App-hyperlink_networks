import json
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
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
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from access_control.services import user_can as access_user_can
from facturacion.models import Proyecto
from usuarios.models import CustomUser

from .forms_billing_masivo import BillingMasivoUploadForm
from .models import (BillingPayWeekSnapshot, ItemBilling, ItemBillingTecnico,
                     PrecioActividadTecnico, SesionBilling,
                     SesionBillingTecnico)

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
    "cable_installation",
]

TECHNICIANS_HEADERS = [
    "bulk_key",
    "technician_username",
]

ITEMS_HEADERS = [
    "bulk_key",
    "job_code",
    "quantity",
]

YES_VALUES = {"yes", "y", "true", "1", "si", "sí"}
NO_VALUES = {"no", "n", "false", "0", ""}

VALID_PAYMENT_MODES = {"full", "split"}


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

    cable_installation: bool = False

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


@login_required
@rol_requerido("admin", "pm", "supervisor", "facturacion", "emision_facturacion")
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

    # Examples
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
        ]
    )

    ws_t.append(["BILL-001", "technician.username"])
    ws_i.append(["BILL-001", "C-123", "1"])

    instructions = [
        ["Bulk Billing Import Instructions"],
        [""],
        ["General rules"],
        ["1. Do not rename sheets."],
        ["2. Do not rename headers."],
        ["3. bulk_key links rows across the 3 sheets."],
        ["4. Job Code must match exactly what exists in the database."],
        ["5. Only leading/trailing spaces are cleaned."],
        ["6. Quantity cannot be zero."],
        ["7. If direct_discount is YES, all quantities must be negative."],
        ["8. If direct_discount is NO, all quantities must be positive."],
        ["9. projected_week must use ISO format: YYYY-W##. Example: 2026-W20."],
        ["10. tech_payment_mode must be full or split."],
        [""],
        ["Technician payment mode"],
        ["The tech_payment_mode column controls how technician totals are calculated."],
        ["The value must be exactly full or split."],
        [""],
        ["full = Full amount for each technician."],
        [
            "Use full when every selected technician must receive their full technical rate."
        ],
        ["Example: 2 technicians, quantity 1, each technician rate 100."],
        ["Result: each technician receives 100. Technical total = 200."],
        [""],
        ["split = Split between technicians."],
        [
            "Use split when the technical amount must be divided between the selected technicians."
        ],
        ["Example: 2 technicians, quantity 1, technician rate 100."],
        ["Result: each technician receives 50%. Technical total = 100."],
        [""],
        [
            "Important: do not write Full amount, Split, yes, no, or any other text in tech_payment_mode."
        ],
        ["Only these two exact values are valid: full or split."],
        [""],
        ["Billings sheet"],
        ["bulk_key: unique key for each billing inside the file."],
        ["project_id: final billing Project ID visible in the billing list."],
        ["client: must match the Client column used in Technician Prices."],
        ["city: must match the City column used in Technician Prices."],
        ["project: must match the Project column used in Technician Prices."],
        ["office: must match the Office column used in Technician Prices."],
        ["project_address: optional address or Google Maps link."],
        ["projected_week: ISO week format YYYY-W##. Example: 2026-W20."],
        ["tech_payment_mode: full or split."],
        ["direct_discount: YES or NO."],
        ["cable_installation: YES or NO."],
        [""],
        ["Technicians sheet"],
        ["bulk_key: must match one bulk_key from the Billings sheet."],
        ["technician_username: must match an existing technician username exactly."],
        ["Each billing must have at least one technician."],
        ["Do not repeat the same technician username inside the same billing."],
        [""],
        ["Items sheet"],
        ["bulk_key: must match one bulk_key from the Billings sheet."],
        ["job_code: must match exactly what exists in the price table."],
        ["Example: C-123 is not the same as C.123."],
        ["quantity: must be numeric and different from zero."],
        ["For normal billings, quantity must be positive."],
        ["For direct discounts, quantity must be negative."],
        [""],
        ["Validation rule"],
        ["If one row has an error, no billing will be created."],
        ["The preview will show the specific field, row, and correction needed."],
    ]

    for row in instructions:
        ws_help.append(row)

    for ws in [ws_b, ws_t, ws_i, ws_help]:
        _autosize_sheet(ws)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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

            request.session["billing_masivo_preview"] = preview_payload
            request.session.modified = True

            return redirect("operaciones:billing_masivo_preview")
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


@login_required
@rol_requerido("admin", "pm", "supervisor", "facturacion", "emision_facturacion")
def billing_masivo_preview(request):
    payload = request.session.get("billing_masivo_preview")

    if not payload:
        messages.warning(request, "Please upload a bulk billing file first.")
        return redirect("operaciones:billing_masivo_upload")

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
    global_errors = []
    billings_preview = []

    price_perms = _bulk_billing_price_permissions(user)

    try:
        wb = load_workbook(archivo, data_only=True)
    except Exception:
        return {
            "ok": False,
            "has_errors": True,
            "permissions": price_perms,
            "global_errors": [
                _cell_error(
                    "Workbook",
                    1,
                    "archivo",
                    "The file could not be read. Please upload a valid .xlsx file.",
                )
            ],
            "billings": [],
            "summary": {
                "billing_count": 0,
                "item_count": 0,
                "technician_count": 0,
                "subtotal_tecnico": "0.00",
                "subtotal_empresa": "0.00",
            },
        }

    billing_rows, errors_b = _read_sheet_rows(wb, SHEET_BILLINGS, BILLINGS_HEADERS)
    tech_rows, errors_t = _read_sheet_rows(wb, SHEET_TECHNICIANS, TECHNICIANS_HEADERS)
    item_rows, errors_i = _read_sheet_rows(wb, SHEET_ITEMS, ITEMS_HEADERS)

    global_errors.extend(errors_b)
    global_errors.extend(errors_t)
    global_errors.extend(errors_i)

    if global_errors:
        return {
            "ok": False,
            "has_errors": True,
            "permissions": price_perms,
            "global_errors": global_errors,
            "billings": [],
            "summary": {
                "billing_count": 0,
                "item_count": 0,
                "technician_count": 0,
                "subtotal_tecnico": "0.00",
                "subtotal_empresa": "0.00",
            },
        }

    billings_by_key = {}
    duplicate_keys = set()

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
            duplicate_keys.add(bulk_key)
            global_errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "bulk_key",
                    f"Duplicate bulk_key '{bulk_key}'. Each billing must have a unique bulk_key.",
                )
            )
            continue

        direct_discount, dd_error = _parse_bool(row.get("direct_discount"))
        cable_installation, cable_error = _parse_bool(row.get("cable_installation"))

        payment_mode = _clean_cell(row.get("tech_payment_mode")).lower() or "full"

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
            cable_installation=cable_installation,
        )

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

        if payment_mode not in VALID_PAYMENT_MODES:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "tech_payment_mode",
                    "Use full or split.",
                )
            )

        if dd_error:
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "direct_discount",
                    dd_error,
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

        if preview.projected_week and not _iso_week_is_valid(preview.projected_week):
            preview.errors.append(
                _cell_error(
                    SHEET_BILLINGS,
                    row["__rownum"],
                    "projected_week",
                    "Use ISO week format YYYY-W##. Example: 2026-W20.",
                )
            )

        billings_by_key[bulk_key] = preview

    tech_rows_by_key = defaultdict(list)
    item_rows_by_key = defaultdict(list)

    for row in tech_rows:
        key = _clean_cell(row.get("bulk_key"))
        tech_rows_by_key[key].append(row)

    for row in item_rows:
        key = _clean_cell(row.get("bulk_key"))
        item_rows_by_key[key].append(row)

    # Validar referencias a bulk_key inexistente
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
                    f"bulk_key '{key}' does not exist in Billings sheet.",
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
                    f"bulk_key '{key}' does not exist in Billings sheet.",
                )
            )

    # Validar detalle por billing
    for bulk_key, preview in billings_by_key.items():
        _attach_and_validate_technicians(preview, tech_rows_by_key.get(bulk_key, []))
        _attach_and_validate_items(preview, item_rows_by_key.get(bulk_key, []))
        _validate_project_and_prices(preview)

    billings_preview = list(billings_by_key.values())

    total_tecnico = Decimal("0.00")
    total_empresa = Decimal("0.00")
    total_items = 0
    total_tech_rows = 0

    for b in billings_preview:
        total_tecnico += b.subtotal_tecnico or Decimal("0.00")
        total_empresa += b.subtotal_empresa or Decimal("0.00")
        total_items += len(b.items)
        total_tech_rows += len(b.technicians)

    payload = {
        "ok": True,
        "has_errors": bool(
            global_errors
            or any(b.errors or any(i.errors for i in b.items) for b in billings_preview)
        ),
        "permissions": price_perms,
        "global_errors": global_errors,
        "billings": [_billing_to_dict(b) for b in billings_preview],
        "summary": {
            "billing_count": len(billings_preview),
            "item_count": total_items,
            "technician_count": total_tech_rows,
            "subtotal_tecnico": _format_money(total_tecnico),
            "subtotal_empresa": _format_money(total_empresa),
        },
    }

    return payload


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
        username = _clean_cell(row.get("technician_username"))

        tech = PreviewTechnician(
            source_row=row["__rownum"],
            username=username,
        )

        if not username:
            preview.errors.append(
                _cell_error(
                    SHEET_TECHNICIANS,
                    row["__rownum"],
                    "technician_username",
                    "technician_username is required.",
                )
            )
            preview.technicians.append(tech)
            continue

        if username in seen:
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

        seen.add(username)

        user = CustomUser.objects.filter(username=username).first()

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
            is_user_role = user.roles.filter(nombre="usuario").exists()
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


def _validate_project_and_prices(preview: PreviewBilling):
    """
    Valida:
    - que exista el Proyecto indicado en project_code
    - que existan precios para:
      Technician + Client + City + Proyecto FK + Office + Job Code

    Importante:
    NO compara Client/City/Office contra facturacion.Proyecto.
    Esa información pertenece a PrecioActividadTecnico.
    """

    if preview.errors:
        return

    project, project_error = _find_project_robust(preview)

    if not project:
        preview.errors.append(
            _cell_error(
                SHEET_BILLINGS,
                preview.source_row,
                "project_code",
                project_error,
            )
        )
        return

    # Guardamos el nombre real del proyecto, igual que _guardar_billing()
    preview.project = project.nombre

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
        "cable_installation": preview.cable_installation,
        "subtotal_tecnico": _format_money(preview.subtotal_tecnico),
        "subtotal_empresa": _format_money(preview.subtotal_empresa),
        "errors": preview.errors,
        "technicians": [
            {
                "source_row": t.source_row,
                "username": t.username,
                "user_id": t.user_id,
                "display_name": t.display_name,
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

    payload = request.session.get("billing_masivo_preview")

    if not payload:
        messages.warning(request, "Please upload a bulk billing file first.")
        return redirect("operaciones:billing_masivo_upload")

    if payload.get("has_errors"):
        messages.error(
            request, "The file still has validation errors. No billing was created."
        )
        return redirect("operaciones:billing_masivo_preview")

    billings = payload.get("billings") or []

    if not billings:
        messages.error(request, "There are no billings to create.")
        return redirect("operaciones:billing_masivo_upload")

    created_ids = []

    for b in billings:
        sesion = SesionBilling.objects.create(
            creado_en=timezone.now(),
            is_direct_discount=bool(b.get("direct_discount")),
            is_cable_installation=bool(b.get("cable_installation")),
            tech_payment_mode=b.get("tech_payment_mode") or "full",
            proyecto_id=b.get("project_id") or "",
            cliente=b.get("client") or "",
            ciudad=b.get("city") or "",
            proyecto=b.get("project") or "",
            oficina=b.get("office") or "",
            direccion_proyecto=b.get("project_address") or "",
            semana_pago_proyectada=b.get("projected_week") or "",
            estado="asignado",
            subtotal_tecnico=Decimal(str(b.get("subtotal_tecnico") or "0.00")),
            subtotal_empresa=Decimal(str(b.get("subtotal_empresa") or "0.00")),
        )

        # Reforzar campos por si save() sincroniza con Proyecto.
        sesion.cliente = b.get("client") or ""
        sesion.ciudad = b.get("city") or ""
        sesion.proyecto = b.get("project") or ""
        sesion.oficina = b.get("office") or ""
        sesion.subtotal_tecnico = Decimal(str(b.get("subtotal_tecnico") or "0.00"))
        sesion.subtotal_empresa = Decimal(str(b.get("subtotal_empresa") or "0.00"))

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
        tech_count = max(len(technicians), 1)

        for t in technicians:
            user_id = t.get("user_id")
            if not user_id:
                continue

            porcentaje = Decimal("100.00")

            if sesion.tech_payment_mode == "split":
                porcentaje = (Decimal("100.00") / Decimal(tech_count)).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )

            SesionBillingTecnico.objects.create(
                sesion=sesion,
                tecnico_id=user_id,
                porcentaje=porcentaje,
                estado="asignado",
                is_active=True,
            )

        for item_data in b.get("items") or []:
            item = ItemBilling.objects.create(
                sesion=sesion,
                codigo_trabajo=item_data.get("job_code") or "",
                tipo_trabajo=item_data.get("tipo_trabajo") or "",
                descripcion=item_data.get("descripcion") or "",
                unidad_medida=item_data.get("unidad_medida") or "",
                cantidad=Decimal(str(item_data.get("quantity") or "0.00")),
                precio_empresa=Decimal(str(item_data.get("precio_empresa") or "0.00")),
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
                    tarifa_base=Decimal(str(d.get("tarifa_base") or "0.00")),
                    porcentaje=Decimal(str(d.get("porcentaje") or "0.00")),
                    tarifa_efectiva=Decimal(str(d.get("tarifa_efectiva") or "0.00")),
                    subtotal=Decimal(str(d.get("subtotal") or "0.00")),
                )

                _create_pay_week_snapshot(
                    sesion=sesion,
                    item=item,
                    tecnico_id=d.get("tecnico_id"),
                    codigo_trabajo=item.codigo_trabajo,
                    tipo_trabajo=item.tipo_trabajo,
                    payment_weeks=int(d.get("payment_weeks") or 0),
                    semana_base=sesion.semana_pago_proyectada,
                    tarifa_base=Decimal(str(d.get("tarifa_base") or "0.00")),
                    porcentaje=Decimal(str(d.get("porcentaje") or "0.00")),
                    tarifa_efectiva=Decimal(str(d.get("tarifa_efectiva") or "0.00")),
                    subtotal=Decimal(str(d.get("subtotal") or "0.00")),
                )

    request.session.pop("billing_masivo_preview", None)
    request.session.modified = True

    messages.success(
        request,
        f"{len(created_ids)} billing(s) created successfully.",
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
