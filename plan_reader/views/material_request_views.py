from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from plan_reader.forms.material_request_forms import (
    PlanReaderMaterialRequestForm, PlanReaderMaterialRequestItemFormSet)
from plan_reader.models import (PlanReaderJob, PlanReaderMaterialRequest,
                                PlanReaderMaterialRequestItem)
from plan_reader.services.material_request_builder import (
    get_or_create_material_request, recalculate_material_request,
    synchronize_material_request_catalog, validate_request_type)
from plan_reader.views.job_views import (can_access_plan_reader,
                                         deny_plan_reader_access)

# =============================================================================
# GENERAL HELPERS
# =============================================================================


def _get_material_request_or_404(
    *,
    job_id: int,
    material_request_id: int,
    for_update: bool = False,
) -> PlanReaderMaterialRequest:
    """
    Obtiene una solicitud de material asegurando que pertenece al Job
    indicado en la URL.

    Cuando ``for_update=True`` bloquea la fila hasta finalizar la
    transacción actual.
    """

    queryset = PlanReaderMaterialRequest.objects.select_related(
        "job",
        "created_by",
        "updated_by",
    )

    if for_update:
        queryset = queryset.select_for_update()

    return get_object_or_404(
        queryset,
        id=material_request_id,
        job_id=job_id,
    )


def _get_material_request_items_queryset(
    material_request: PlanReaderMaterialRequest,
):
    """
    Devuelve las filas activas de una solicitud en el orden configurado
    por el catálogo.
    """

    return (
        material_request.items.select_related(
            "catalog_item",
        )
        .filter(
            is_active=True,
        )
        .order_by(
            "display_order",
            "id",
        )
    )


def _build_material_rows(
    item_formset,
) -> list[dict[str, Any]]:
    """
    Construye las filas que utilizará la plantilla web.
    """

    rows: list[dict[str, Any]] = []

    for item_form in item_formset.forms:
        item = item_form.instance

        rows.append(
            {
                "form": item_form,
                "item": item,
                "is_manual": (
                    item.source == PlanReaderMaterialRequestItem.SOURCE_MANUAL
                ),
                "is_automatic": (
                    item.source == PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC
                ),
                "is_automatic_edited": (
                    item.source == PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC_EDITED
                ),
                "was_automatically_modified": (
                    item.was_automatically_modified if item.pk else False
                ),
            }
        )

    return rows


def _build_material_request_summary(
    material_request: PlanReaderMaterialRequest,
) -> dict[str, Any]:
    """
    Calcula el resumen mostrado en la pantalla del Material Request.
    """

    request_items = list(
        _get_material_request_items_queryset(
            material_request,
        )
    )

    requested_items_count = 0
    total_requested_quantity = Decimal("0")
    automatic_items_count = 0
    automatic_edited_items_count = 0
    manual_items_count = 0

    for item in request_items:
        quantity_requested = item.quantity_requested or Decimal("0")

        if quantity_requested > 0:
            requested_items_count += 1
            total_requested_quantity += quantity_requested

        if item.source == PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC:
            automatic_items_count += 1

        elif item.source == PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC_EDITED:
            automatic_edited_items_count += 1

        else:
            manual_items_count += 1

    return {
        "total_rows_count": len(request_items),
        "requested_items_count": requested_items_count,
        "total_requested_quantity": total_requested_quantity,
        "automatic_items_count": automatic_items_count,
        "automatic_edited_items_count": automatic_edited_items_count,
        "manual_items_count": manual_items_count,
    }


def _render_material_request_editor(
    request: HttpRequest,
    *,
    material_request: PlanReaderMaterialRequest,
    material_request_form: PlanReaderMaterialRequestForm | None = None,
    item_formset=None,
    status: int = 200,
) -> HttpResponse:
    """
    Renderiza el editor web moderno del Material Request.
    """

    if material_request_form is None:
        material_request_form = PlanReaderMaterialRequestForm(
            instance=material_request,
        )

    if item_formset is None:
        item_formset = PlanReaderMaterialRequestItemFormSet(
            instance=material_request,
            queryset=_get_material_request_items_queryset(
                material_request,
            ),
        )

    material_rows = _build_material_rows(
        item_formset,
    )

    summary = _build_material_request_summary(
        material_request,
    )

    generated_pdf_url = ""

    if material_request.generated_pdf:
        try:
            generated_pdf_url = material_request.generated_pdf.url
        except ValueError:
            generated_pdf_url = ""

    context = {
        "job": material_request.job,
        "material_request": material_request,
        "material_request_form": material_request_form,
        "item_formset": item_formset,
        "material_rows": material_rows,
        "summary": summary,
        "is_splicing_request": (material_request.is_splicing_request),
        "is_cable_request": (material_request.is_cable_request),
        "generated_pdf_url": generated_pdf_url,
    }

    return render(
        request,
        "plan_reader/material_request.html",
        context,
        status=status,
    )


def _get_job_co_dfn(
    material_request: PlanReaderMaterialRequest,
) -> str:
    """
    Obtiene el CO_DFN oficial que debe mostrarse en el Material Request.

    Prioridad:

    1. Campo o propiedad ``co_dfn`` del Job.
    2. Combinación ``co`` + ``dfn`` del Job.
    3. DFN almacenado actualmente en la solicitud.

    Ejemplo:

        co = "0913QA"
        dfn = "09"

        resultado = "0913QA_09"
    """

    job = material_request.job

    direct_co_dfn = str(
        getattr(
            job,
            "co_dfn",
            "",
        )
        or ""
    ).strip()

    if direct_co_dfn:
        return direct_co_dfn

    co_value = str(
        getattr(
            job,
            "co",
            "",
        )
        or ""
    ).strip()

    dfn_value = str(
        getattr(
            job,
            "dfn",
            "",
        )
        or ""
    ).strip()

    if co_value and dfn_value:
        normalized_co = co_value.rstrip(
            "_- ",
        )

        normalized_dfn = dfn_value.lstrip(
            "_- ",
        )

        return f"{normalized_co}_" f"{normalized_dfn}"

    return str(material_request.dfn or "").strip()


def _synchronize_material_request_dfn(
    material_request: PlanReaderMaterialRequest,
) -> bool:
    """
    Sincroniza el campo DFN de la solicitud con el CO_DFN del Job.

    Retorna ``True`` cuando fue necesario actualizar la solicitud.
    """

    co_dfn = _get_job_co_dfn(
        material_request,
    )

    if not co_dfn:
        return False

    current_dfn = str(material_request.dfn or "").strip()

    if current_dfn == co_dfn:
        return False

    material_request.dfn = co_dfn

    material_request.save(
        update_fields=[
            "dfn",
            "updated_at",
        ]
    )

    return True


# =============================================================================
# PDF HELPERS
# =============================================================================


def _format_pdf_quantity(
    value: Decimal | None,
) -> str:
    """
    Convierte una cantidad Decimal a un texto limpio para el PDF.

    Ejemplos:

        Decimal("2.00") -> "2"
        Decimal("2.50") -> "2.5"
        None            -> ""
        Decimal("0")    -> ""
    """

    if value is None:
        return ""

    decimal_value = Decimal(value)

    if decimal_value == 0:
        return ""

    if decimal_value == decimal_value.to_integral():
        return str(int(decimal_value))

    normalized_value = decimal_value.normalize()

    return format(
        normalized_value,
        "f",
    )


def _truncate_pdf_text(
    text: str | None,
    *,
    max_length: int,
) -> str:
    """
    Limita un texto para evitar que invada otra celda del formulario.
    """

    normalized_text = str(text or "").strip()

    if len(normalized_text) <= max_length:
        return normalized_text

    return normalized_text[: max_length - 3].rstrip() + "..."


def _resolve_itg_logo_path() -> str:
    """
    Obtiene la ruta física del logo ITG guardado en static/images.

    Funciona tanto en desarrollo como después de collectstatic, siempre
    que Django pueda localizar el archivo estático.
    """

    logo_path = finders.find(
        "images/itg_logo.png",
    )

    if logo_path:
        return str(logo_path)

    raise FileNotFoundError(
        "The official ITG logo was not found at " "static/images/itg_logo.png."
    )


def _draw_pdf_text(
    canvas,
    *,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    font_name: str = "Helvetica",
    font_size: float = 6,
    horizontal_padding: float = 2,
    alignment: str = "left",
) -> None:
    """
    Dibuja texto dentro de una celda respetando su alineación.
    """

    canvas.setFont(
        font_name,
        font_size,
    )

    text_value = str(text or "")

    baseline_y = y + ((height - font_size) / 2) + 1

    if alignment == "center":
        canvas.drawCentredString(
            x + (width / 2),
            baseline_y,
            text_value,
        )

    elif alignment == "right":
        canvas.drawRightString(
            x + width - horizontal_padding,
            baseline_y,
            text_value,
        )

    else:
        canvas.drawString(
            x + horizontal_padding,
            baseline_y,
            text_value,
        )


def _draw_pdf_cell(
    canvas,
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    text: str = "",
    fill_color=None,
    stroke_width: float = 0.8,
    font_name: str = "Helvetica",
    font_size: float = 6,
    alignment: str = "left",
    horizontal_padding: float = 2,
) -> None:
    """
    Dibuja una celda completa del documento oficial.
    """

    from reportlab.lib import colors

    canvas.saveState()

    if fill_color is not None:
        canvas.setFillColor(
            fill_color,
        )

        canvas.rect(
            x,
            y,
            width,
            height,
            stroke=0,
            fill=1,
        )

    canvas.setStrokeColor(
        colors.black,
    )

    canvas.setLineWidth(
        stroke_width,
    )

    canvas.rect(
        x,
        y,
        width,
        height,
        stroke=1,
        fill=0,
    )

    canvas.setFillColor(
        colors.black,
    )

    _draw_pdf_text(
        canvas,
        text=text,
        x=x,
        y=y,
        width=width,
        height=height,
        font_name=font_name,
        font_size=font_size,
        horizontal_padding=horizontal_padding,
        alignment=alignment,
    )

    canvas.restoreState()


def _draw_official_pdf_header(
    canvas,
    *,
    material_request: PlanReaderMaterialRequest,
    page_width: float,
    top_y: float,
    left_margin: float,
    right_margin: float,
) -> float:
    """
    Dibuja el encabezado oficial del formulario ITG.

    La sección Contractor Employee Signature queda vacía para que el
    documento sea firmado manualmente. Incluye la línea horizontal
    interior del formulario oficial.
    """

    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader

    usable_width = page_width - left_margin - right_margin

    title_height = 22
    subcontractor_height = 24
    detail_row_height = 18

    label_width = 70
    value_width = 110
    logo_width = 135

    information_width = usable_width - logo_width

    signature_width = information_width - label_width - value_width

    current_y = top_y

    # =========================================================================
    # TITLE
    # =========================================================================

    current_y -= title_height

    _draw_pdf_cell(
        canvas,
        x=left_margin,
        y=current_y,
        width=usable_width,
        height=title_height,
        text="UNDERGROUND MATERIAL REQUEST FORM",
        font_name="Helvetica-Bold",
        font_size=9,
        alignment="center",
        stroke_width=1,
    )

    header_body_height = subcontractor_height + (detail_row_height * 3)

    logo_x = left_margin + information_width
    logo_y = current_y - header_body_height

    # =========================================================================
    # LOGO
    # =========================================================================

    _draw_pdf_cell(
        canvas,
        x=logo_x,
        y=logo_y,
        width=logo_width,
        height=header_body_height,
        stroke_width=1,
    )

    logo_path = _resolve_itg_logo_path()

    logo_reader = ImageReader(
        logo_path,
    )

    logo_padding = 16

    canvas.drawImage(
        logo_reader,
        logo_x + logo_padding,
        logo_y + logo_padding,
        width=logo_width - (logo_padding * 2),
        height=header_body_height - (logo_padding * 2),
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )

    # =========================================================================
    # SUBCONTRACTOR
    # =========================================================================

    current_y -= subcontractor_height

    _draw_pdf_cell(
        canvas,
        x=left_margin,
        y=current_y,
        width=label_width,
        height=subcontractor_height,
        text="SUBCONTRACTOR:",
        font_name="Helvetica-Bold",
        font_size=6.5,
        stroke_width=1,
    )

    subcontractor_text = _truncate_pdf_text(
        material_request.subcontractor,
        max_length=46,
    )

    _draw_pdf_cell(
        canvas,
        x=left_margin + label_width,
        y=current_y,
        width=information_width - label_width,
        height=subcontractor_height,
        text=subcontractor_text,
        font_name="Helvetica",
        font_size=9,
        stroke_width=1,
        horizontal_padding=7,
    )

    # =========================================================================
    # DATE
    # =========================================================================

    current_y -= detail_row_height

    _draw_pdf_cell(
        canvas,
        x=left_margin,
        y=current_y,
        width=label_width,
        height=detail_row_height,
        text="DATE",
        font_name="Helvetica-Bold",
        font_size=6.5,
        alignment="center",
        stroke_width=1,
    )

    request_date_text = ""

    if material_request.request_date:
        request_date_text = material_request.request_date.strftime(
            "%m-%d-%Y",
        )

    _draw_pdf_cell(
        canvas,
        x=left_margin + label_width,
        y=current_y,
        width=value_width,
        height=detail_row_height,
        text=request_date_text,
        font_size=7,
        alignment="center",
        stroke_width=1,
    )

    # =========================================================================
    # CONTRACTOR EMPLOYEE SIGNATURE
    # =========================================================================

    signature_x = left_margin + label_width + value_width

    signature_y = current_y - (detail_row_height * 2)

    signature_height = detail_row_height * 3

    _draw_pdf_cell(
        canvas,
        x=signature_x,
        y=signature_y,
        width=signature_width,
        height=signature_height,
        stroke_width=1,
    )

    canvas.saveState()

    canvas.setFillColor(
        colors.black,
    )

    canvas.setStrokeColor(
        colors.black,
    )

    canvas.setFont(
        "Helvetica-Bold",
        5.5,
    )

    canvas.drawString(
        signature_x + 3,
        signature_y + signature_height - 8,
        "Contractor Employee Signature:",
    )

    # Línea horizontal interior para la firma.
    # Se extiende de borde a borde, igual que en el formulario oficial.
    signature_line_y = signature_y + (signature_height * 0.48)

    canvas.setLineWidth(
        0.8,
    )

    canvas.line(
        signature_x,
        signature_line_y,
        signature_x + signature_width,
        signature_line_y,
    )

    canvas.restoreState()

    # =========================================================================
    # MARKET
    # =========================================================================

    current_y -= detail_row_height

    _draw_pdf_cell(
        canvas,
        x=left_margin,
        y=current_y,
        width=label_width,
        height=detail_row_height,
        text="MARKET",
        font_name="Helvetica-Bold",
        font_size=6.5,
        alignment="center",
        stroke_width=1,
    )

    _draw_pdf_cell(
        canvas,
        x=left_margin + label_width,
        y=current_y,
        width=value_width,
        height=detail_row_height,
        text=_truncate_pdf_text(
            material_request.market,
            max_length=24,
        ),
        font_size=7,
        alignment="center",
        stroke_width=1,
    )

    # =========================================================================
    # DFN / CO_DFN
    # =========================================================================

    current_y -= detail_row_height

    _draw_pdf_cell(
        canvas,
        x=left_margin,
        y=current_y,
        width=label_width,
        height=detail_row_height,
        text="DFN",
        font_name="Helvetica-Bold",
        font_size=6.5,
        alignment="center",
        stroke_width=1,
    )

    co_dfn = _get_job_co_dfn(
        material_request,
    )

    _draw_pdf_cell(
        canvas,
        x=left_margin + label_width,
        y=current_y,
        width=value_width,
        height=detail_row_height,
        text=_truncate_pdf_text(
            co_dfn,
            max_length=24,
        ),
        font_name="Helvetica-Bold",
        font_size=7,
        alignment="center",
        stroke_width=1,
    )

    return current_y


def _generate_official_material_request_pdf(
    material_request: PlanReaderMaterialRequest,
) -> bytes:
    """
    Genera el PDF oficial del cliente usando ReportLab.

    La interfaz web no participa en la generación. El documento se dibuja
    de forma independiente para mantener el formato rígido del cliente.
    """

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError(
            "ReportLab is required to generate the Material Request PDF. "
            "Install it with: pip install reportlab"
        ) from exc

    items = list(
        _get_material_request_items_queryset(
            material_request,
        )
    )

    if not items:
        raise ValueError("The Material Request does not contain active materials.")

    buffer = BytesIO()

    page_width, page_height = letter

    pdf_canvas = canvas.Canvas(
        buffer,
        pagesize=letter,
        pageCompression=1,
    )

    pdf_canvas.setTitle(
        (
            "Underground Material Request - "
            f"{material_request.dfn or material_request.id}"
        )
    )

    pdf_canvas.setAuthor(
        "Hyperlink Networks LLC",
    )

    pdf_canvas.setSubject(
        "Underground Material Request Form",
    )

    left_margin = 30
    right_margin = 30
    top_margin = 28
    bottom_margin = 24

    top_y = page_height - top_margin

    table_start_y = _draw_official_pdf_header(
        pdf_canvas,
        material_request=material_request,
        page_width=page_width,
        top_y=top_y,
        left_margin=left_margin,
        right_margin=right_margin,
    )

    usable_width = page_width - left_margin - right_margin

    column_widths = [
        65,
        75,
        278,
        32,
        66,
        66,
    ]

    calculated_width = sum(column_widths)

    width_difference = usable_width - calculated_width

    column_widths[2] += width_difference

    header_row_height = 13

    available_table_height = table_start_y - bottom_margin

    material_rows_height = available_table_height - header_row_height

    row_height = material_rows_height / len(items)

    row_height = min(
        row_height,
        11.3,
    )

    table_height = header_row_height + (row_height * len(items))

    current_y = table_start_y - table_height

    header_y = table_start_y - header_row_height

    blue_header = colors.HexColor(
        "#D9E8F7",
    )

    blue_received = colors.HexColor(
        "#D9E8F7",
    )

    headers = [
        "Type",
        "Category",
        "Material",
        "UOM",
        "QTY REQUESTED",
        "QTY RECEIVED",
    ]

    current_x = left_margin

    for index, header in enumerate(headers):
        alignment = "left"

        if index >= 3:
            alignment = "center"

        _draw_pdf_cell(
            pdf_canvas,
            x=current_x,
            y=header_y,
            width=column_widths[index],
            height=header_row_height,
            text=header,
            fill_color=blue_header,
            font_name="Helvetica-Bold",
            font_size=5.6,
            alignment=alignment,
            stroke_width=0.8,
            horizontal_padding=1.5,
        )

        current_x += column_widths[index]

    row_y = header_y

    for item in items:
        row_y -= row_height
        current_x = left_margin

        row_values = [
            item.material_type or "UNDERGROUND",
            item.category or "",
            item.material_name or "",
            item.uom or "",
            _format_pdf_quantity(
                item.quantity_requested,
            ),
            _format_pdf_quantity(
                item.quantity_received,
            ),
        ]

        for column_index, value in enumerate(row_values):
            fill_color = None
            alignment = "left"
            font_size = 5.1
            horizontal_padding = 1.5

            if column_index == 0:
                value = _truncate_pdf_text(
                    value,
                    max_length=18,
                )

            elif column_index == 1:
                value = _truncate_pdf_text(
                    value,
                    max_length=18,
                )

            elif column_index == 2:
                value = _truncate_pdf_text(
                    value,
                    max_length=94,
                )

                font_size = 4.9

            elif column_index == 3:
                alignment = "center"
                font_size = 5.3

            elif column_index == 4:
                alignment = "left"
                font_size = 5.3
                horizontal_padding = 4

            elif column_index == 5:
                fill_color = blue_received
                alignment = "left"
                font_size = 5.3
                horizontal_padding = 4

            _draw_pdf_cell(
                pdf_canvas,
                x=current_x,
                y=row_y,
                width=column_widths[column_index],
                height=row_height,
                text=value,
                fill_color=fill_color,
                font_name="Helvetica",
                font_size=font_size,
                alignment=alignment,
                stroke_width=0.7,
                horizontal_padding=horizontal_padding,
            )

            current_x += column_widths[column_index]

    pdf_canvas.showPage()
    pdf_canvas.save()

    pdf_bytes = buffer.getvalue()

    buffer.close()

    return pdf_bytes


def _build_material_request_pdf_storage_filename(
    material_request: PlanReaderMaterialRequest,
) -> str:
    """
    Construye un nombre físico único para cada PDF generado.

    Esto evita que el navegador, Wasabi o cualquier CDN reutilice una
    versión anterior del archivo.
    """

    request_type = (material_request.request_type or "material").strip().lower()

    safe_dfn = (
        _get_job_co_dfn(material_request)
        or material_request.dfn
        or f"job-{material_request.job_id}"
    )

    safe_dfn = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in safe_dfn
    )

    safe_dfn = safe_dfn.strip("-_")

    if not safe_dfn:
        safe_dfn = f"job-{material_request.job_id}"

    generation_token = timezone.now().strftime("%Y%m%d_%H%M%S_%f")

    return f"{safe_dfn}_" f"{request_type}_" f"material_request_{generation_token}.pdf"


def _build_material_request_pdf_storage_filename(
    material_request: PlanReaderMaterialRequest,
) -> str:
    """
    Construye un nombre único para almacenar cada nueva generación.

    El identificador temporal evita que el navegador, CDN o storage
    reutilicen una versión anterior del PDF.
    """

    request_type = (material_request.request_type or "material").strip().lower()

    safe_dfn = (
        _get_job_co_dfn(material_request)
        or material_request.dfn
        or f"job-{material_request.job_id}"
    )

    safe_dfn = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in safe_dfn
    )

    safe_dfn = safe_dfn.strip("-_")

    if not safe_dfn:
        safe_dfn = f"job-{material_request.job_id}"

    generation_token = timezone.now().strftime("%Y%m%d_%H%M%S_%f")

    return f"{safe_dfn}_" f"{request_type}_" f"material_request_{generation_token}.pdf"


# =============================================================================
# OPEN OR CREATE MATERIAL REQUEST
# =============================================================================


@login_required
@require_GET
def material_request_open(
    request: HttpRequest,
    job_id: int,
    request_type: str,
) -> HttpResponse:
    """
    Abre o crea la solicitud correspondiente al Job y tipo indicado.
    """

    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    try:
        normalized_request_type = validate_request_type(
            request_type,
        )
    except ValueError as exc:
        raise Http404("Invalid material request type.") from exc

    job = get_object_or_404(
        PlanReaderJob.objects.select_related(
            "uploaded_by",
        ),
        id=job_id,
    )

    material_request, created = get_or_create_material_request(
        job=job,
        user=request.user,
        request_type=normalized_request_type,
    )

    if created:
        messages.success(
            request,
            (
                f"{material_request.get_request_type_display()} "
                "Material Request created successfully."
            ),
        )

    return redirect(
        "plan_reader:material_request_edit",
        job_id=job.id,
        material_request_id=material_request.id,
    )


# =============================================================================
# EDIT MATERIAL REQUEST
# =============================================================================


@login_required
@require_GET
def material_request_edit(
    request: HttpRequest,
    job_id: int,
    material_request_id: int,
) -> HttpResponse:
    """
    Muestra el editor de una solicitud existente.

    Antes de renderizar, sincroniza:

    - catálogo activo;
    - DFN de la solicitud con el CO_DFN del Job.
    """

    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    material_request = _get_material_request_or_404(
        job_id=job_id,
        material_request_id=material_request_id,
    )

    synchronize_material_request_catalog(
        material_request=material_request,
    )

    _synchronize_material_request_dfn(
        material_request,
    )

    material_request.refresh_from_db()

    return _render_material_request_editor(
        request,
        material_request=material_request,
    )


# =============================================================================
# SAVE MATERIAL REQUEST
# =============================================================================


@login_required
@require_POST
def material_request_save(
    request: HttpRequest,
    job_id: int,
    material_request_id: int,
) -> HttpResponse:
    """
    Guarda el encabezado y las cantidades de todas las filas.
    """

    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    with transaction.atomic():
        material_request = _get_material_request_or_404(
            job_id=job_id,
            material_request_id=material_request_id,
            for_update=True,
        )

        synchronize_material_request_catalog(
            material_request=material_request,
        )

        item_queryset = _get_material_request_items_queryset(
            material_request,
        )

        material_request_form = PlanReaderMaterialRequestForm(
            request.POST,
            instance=material_request,
        )

        item_formset = PlanReaderMaterialRequestItemFormSet(
            request.POST,
            instance=material_request,
            queryset=item_queryset,
        )

        header_is_valid = material_request_form.is_valid()

        items_are_valid = item_formset.is_valid()

        if header_is_valid and items_are_valid:
            saved_material_request = material_request_form.save(
                commit=False,
            )

            saved_material_request.status = PlanReaderMaterialRequest.STATUS_DRAFT

            saved_material_request.updated_by = request.user

            saved_material_request.save(
                update_fields=[
                    "subcontractor",
                    "request_date",
                    "market",
                    "dfn",
                    "contractor_employee_name",
                    "contractor_employee_signature",
                    "notes",
                    "status",
                    "updated_by",
                    "updated_at",
                ]
            )

            item_formset.instance = saved_material_request

            item_formset.save()

            messages.success(
                request,
                (
                    f"{saved_material_request.get_request_type_display()} "
                    "Material Request saved successfully."
                ),
            )

            return redirect(
                "plan_reader:material_request_edit",
                job_id=saved_material_request.job_id,
                material_request_id=saved_material_request.id,
            )

    messages.error(
        request,
        (
            "The Material Request could not be saved. "
            "Please review the fields marked below."
        ),
    )

    return _render_material_request_editor(
        request,
        material_request=material_request,
        material_request_form=material_request_form,
        item_formset=item_formset,
        status=400,
    )


# =============================================================================
# RECALCULATE SPLICING MATERIAL REQUEST
# =============================================================================


@login_required
@require_POST
def material_request_recalculate(
    request: HttpRequest,
    job_id: int,
    material_request_id: int,
) -> HttpResponse:
    """
    Recalcula las cantidades automáticas de una solicitud Splicing.
    """

    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    material_request = _get_material_request_or_404(
        job_id=job_id,
        material_request_id=material_request_id,
    )

    if material_request.is_cable_request:
        messages.info(
            request,
            (
                "Cable Material Requests use manual quantities and "
                "do not require automatic recalculation."
            ),
        )

        return redirect(
            "plan_reader:material_request_edit",
            job_id=material_request.job_id,
            material_request_id=material_request.id,
        )

    recalculate_material_request(
        material_request=material_request,
        user=request.user,
        overwrite_user_edits=False,
    )

    messages.success(
        request,
        (
            "Automatic quantities were recalculated successfully. "
            "Values previously edited by the user were preserved."
        ),
    )

    return redirect(
        "plan_reader:material_request_edit",
        job_id=material_request.job_id,
        material_request_id=material_request.id,
    )


# =============================================================================
# GENERATE MATERIAL REQUEST PDF
# =============================================================================


@login_required
@require_POST
def material_request_generate_pdf(
    request: HttpRequest,
    job_id: int,
    material_request_id: int,
) -> HttpResponse:
    """
    Genera y guarda una nueva versión del PDF oficial.

    Cada generación utiliza un nombre físico único para evitar que el
    navegador o el storage devuelvan una versión anterior del documento.
    """

    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    old_pdf_name = ""
    requested_values_count = 0
    received_values_count = 0

    try:
        with transaction.atomic():
            material_request = _get_material_request_or_404(
                job_id=job_id,
                material_request_id=material_request_id,
                for_update=True,
            )

            synchronize_material_request_catalog(
                material_request=material_request,
            )

            _synchronize_material_request_dfn(
                material_request,
            )

            material_request.refresh_from_db()

            material_items = list(
                _get_material_request_items_queryset(
                    material_request,
                )
            )

            requested_values_count = sum(
                1
                for item in material_items
                if (
                    item.quantity_requested is not None
                    and item.quantity_requested != Decimal("0")
                )
            )

            received_values_count = sum(
                1
                for item in material_items
                if (
                    item.quantity_received is not None
                    and item.quantity_received != Decimal("0")
                )
            )

            pdf_bytes = _generate_official_material_request_pdf(
                material_request,
            )

            storage_filename = _build_material_request_pdf_storage_filename(
                material_request,
            )

            if material_request.generated_pdf:
                old_pdf_name = material_request.generated_pdf.name or ""

            material_request.generated_pdf.save(
                storage_filename,
                ContentFile(
                    pdf_bytes,
                ),
                save=False,
            )

            material_request.status = PlanReaderMaterialRequest.STATUS_GENERATED

            material_request.generated_at = timezone.now()
            material_request.updated_by = request.user

            material_request.save(
                update_fields=[
                    "generated_pdf",
                    "status",
                    "generated_at",
                    "updated_by",
                    "updated_at",
                ]
            )

        if old_pdf_name:
            try:
                storage = material_request.generated_pdf.storage

                if storage.exists(old_pdf_name):
                    storage.delete(old_pdf_name)

            except Exception:
                pass

    except FileNotFoundError as exc:
        messages.error(
            request,
            str(exc),
        )

    except RuntimeError as exc:
        messages.error(
            request,
            str(exc),
        )

    except ValueError as exc:
        messages.error(
            request,
            str(exc),
        )

    except Exception as exc:
        messages.error(
            request,
            ("The Material Request PDF could not be generated. " f"Error: {exc}"),
        )

    else:
        messages.success(
            request,
            (
                "The official client Material Request PDF was generated "
                f"successfully with {requested_values_count} requested "
                f"value(s) and {received_values_count} received value(s)."
            ),
        )

    return redirect(
        "plan_reader:material_request_edit",
        job_id=job_id,
        material_request_id=material_request_id,
    )


@login_required
@require_GET
def material_request_download_pdf(
    request: HttpRequest,
    job_id: int,
    material_request_id: int,
) -> HttpResponse:
    """
    Descarga el PDF que actualmente está asociado a la solicitud.

    El archivo no se vuelve a generar en esta vista. Se abre directamente
    desde el storage y se entrega con el mismo nombre físico que fue guardado.
    """

    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    material_request = _get_material_request_or_404(
        job_id=job_id,
        material_request_id=material_request_id,
    )

    if not material_request.generated_pdf:
        messages.warning(
            request,
            ("Generate the Material Request PDF before " "attempting to download it."),
        )

        return redirect(
            "plan_reader:material_request_edit",
            job_id=material_request.job_id,
            material_request_id=material_request.id,
        )

    stored_pdf_name = str(material_request.generated_pdf.name or "").strip()

    if not stored_pdf_name:
        messages.warning(
            request,
            ("The Material Request does not have a valid " "generated PDF file."),
        )

        return redirect(
            "plan_reader:material_request_edit",
            job_id=material_request.job_id,
            material_request_id=material_request.id,
        )

    filename = Path(
        stored_pdf_name,
    ).name

    try:
        pdf_file = material_request.generated_pdf.open(
            "rb",
        )

        response = FileResponse(
            pdf_file,
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )

        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"

        response["Pragma"] = "no-cache"
        response["Expires"] = "0"

        return response

    except Exception as exc:
        messages.error(
            request,
            ("The generated PDF could not be downloaded. " f"Error: {exc}"),
        )

        return redirect(
            "plan_reader:material_request_edit",
            job_id=material_request.job_id,
            material_request_id=material_request.id,
        )
