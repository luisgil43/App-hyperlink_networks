import os
import re
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from plan_reader.models import PlanReaderItem, PlanReaderJob, PlanReaderPage
from plan_reader.services.openai_service import extract_plan_page_with_openai
from plan_reader.services.pdf_service import (count_pdf_pages,
                                              extract_sheet_name_from_page,
                                              get_pdf_temp_path,
                                              get_plan_reader_temp_dir,
                                              render_pdf_page_to_image)
from plan_reader.services.rules_engine import apply_box_rules


def use_openai():
    value = getattr(settings, "PLAN_READER_USE_OPENAI", None)

    if value is None:
        value = os.getenv("PLAN_READER_USE_OPENAI", "False")

    return str(value).lower() in ["1", "true", "yes", "y"]


def render_zoom():
    value = getattr(settings, "PLAN_READER_RENDER_ZOOM", None) or os.getenv(
        "PLAN_READER_RENDER_ZOOM",
        "3",
    )

    try:
        return float(value)
    except Exception:
        return 3.0


def safe_int(value, default=0):
    try:
        return int(value or default)
    except Exception:
        return default


def safe_decimal(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


# =============================================================================
# LIMPIEZA DEFENSIVA DE LECTURA IA
# =============================================================================


def _clean_read_text(value):
    return str(value or "").strip()


def _extract_box_from_text(value):
    """
    Extrae el número de caja completo desde texto crudo.

    Casos:
    - 7020-001
    - 7020-001-1
    - 7022-007-1

    Esto evita que la IA corte una caja como:
    7020-001-1 -> 7020-001
    """
    text = _clean_read_text(value)

    if not text:
        return ""

    text = text.replace("–", "-").replace("—", "-")

    matches = re.findall(r"\b\d{4}-\d{3}(?:-\d+)?\b", text)

    if not matches:
        return ""

    return matches[0].strip()


def _normalize_ai_box_family(value):
    """
    Corrige familias mal leídas por la IA antes de pasar a rules_engine.

    En este flujo no deben quedar como familia real:
    - BGP
    - BBP
    - BG8
    - B6G
    - B86
    - B8C

    Todo eso se interpreta como B8G.
    """
    text = _clean_read_text(value)

    if not text:
        return ""

    text = text.upper().strip()

    if re.search(r"\b(BGP|BBP|BG8|B6G|B86|B8C)\b", text):
        text = re.sub(r"\b(BGP|BBP|BG8|B6G|B86|B8C)\b", "B8G", text)

    return text


def _normalize_final_box_type(value):
    """
    Última defensa para que nunca quede BGP/BBP como Final Box Type.

    No modifica rules_engine.py.
    Solo corrige el resultado antes de guardar en DB.
    """
    text = _clean_read_text(value)

    if not text:
        return ""

    upper = text.upper().strip()

    if upper in {"BGP", "BBP", "BG8", "B6G", "B86", "B8C"}:
        return "B8G"

    if upper.startswith("BGP") or upper.startswith("BBP"):
        return text.replace("BGP", "B8G").replace("BBP", "B8G")

    return text


def _normalize_project_name_from_raw(raw_item):
    """
    Decide el project_name final.

    Prioridad:
    1) raw_text, porque normalmente trae la caja completa.
    2) project_name entregado por OpenAI.
    """
    raw_project_name = _clean_read_text(raw_item.get("project_name"))
    raw_text = _clean_read_text(raw_item.get("raw_text"))

    box_from_raw = _extract_box_from_text(raw_text)

    if box_from_raw:
        return box_from_raw

    box_from_project = _extract_box_from_text(raw_project_name)

    if box_from_project:
        return box_from_project

    return raw_project_name


def _clean_ai_item(raw_item):
    """
    Limpia el item de OpenAI antes de aplicar reglas.

    Corrige:
    - project_name incompleto.
    - BGP/BBP/BG8/etc. como B8G.
    """
    raw_item = raw_item or {}

    cleaned = dict(raw_item)

    cleaned["project_name"] = _normalize_project_name_from_raw(raw_item)
    cleaned["visible_type"] = _normalize_ai_box_family(raw_item.get("visible_type"))
    cleaned["detected_box_type"] = _normalize_ai_box_family(
        raw_item.get("detected_box_type")
    )

    return cleaned


def create_item_from_extraction(job, page_obj, page_data, raw_item):
    raw_item = _clean_ai_item(raw_item)

    sheet = (
        raw_item.get("sheet")
        or page_data.get("sheet_name")
        or page_obj.sheet_name
        or ""
    )

    base_data = {
        "sheet": sheet,
        "co": job.co,
        "dfn": job.dfn,
        "project_name": str(raw_item.get("project_name") or "").strip(),
        "primary_feed": str(raw_item.get("primary_feed") or "").strip(),
        "visible_type": str(raw_item.get("visible_type") or "").strip(),
        "detected_box_type": str(raw_item.get("detected_box_type") or "").strip(),
        "has_p": bool(raw_item.get("has_p")),
        "s_splitter": str(raw_item.get("s_splitter") or "").strip(),
        "t_splitter": str(raw_item.get("t_splitter") or "").strip(),
        "splice_count": safe_int(raw_item.get("splice_count"), 0),
    }

    calculated = apply_box_rules(base_data)

    calculated["calculated_box_type"] = _normalize_final_box_type(
        calculated.get("calculated_box_type", "")
    )

    calculated["detected_box_type"] = _normalize_ai_box_family(
        calculated.get("detected_box_type", "")
    )

    calculated["visible_type"] = _normalize_ai_box_family(
        calculated.get("visible_type", "")
    )

    raw_text = str(raw_item.get("raw_text") or "").strip()

    if raw_text:
        calculated["observation"] = (
            f"{calculated.get('observation', '')} Raw: {raw_text}"
        ).strip()

    item_confidence = safe_decimal(raw_item.get("confidence"))

    return PlanReaderItem.objects.create(
        job=job,
        page=page_obj,
        sheet=calculated.get("sheet", ""),
        co=calculated.get("co", ""),
        dfn=calculated.get("dfn", ""),
        project_name=calculated.get("project_name", ""),
        primary_feed=calculated.get("primary_feed", ""),
        visible_type=calculated.get("visible_type", ""),
        detected_box_type=calculated.get("detected_box_type", ""),
        has_p=bool(calculated.get("has_p")),
        s_splitter=calculated.get("s_splitter", ""),
        t_splitter=calculated.get("t_splitter", ""),
        splice_count=safe_int(calculated.get("splice_count"), 0),
        calculated_box_type=calculated.get("calculated_box_type", ""),
        c108_ug=safe_int(calculated.get("c108_ug"), 0),
        c109_splices=safe_int(calculated.get("c109_splices"), 0),
        c110_splitters=safe_int(calculated.get("c110_splitters"), 0),
        observation=calculated.get("observation", ""),
        confidence=item_confidence,
        needs_review=bool(calculated.get("needs_review")),
        is_duplicate=False,
    )


def _clean_key_text(value):
    text = str(value or "").strip().upper()

    if text in {"", "-", "—", "N/A", "NA", "NONE", "NULL"}:
        return ""

    return text


def _clean_bool(value):
    return bool(value)


def _item_score(item):
    """
    Sirve para decidir cuál duplicado conservar.
    Mayor score = mejor item.
    """
    score = 0

    if _clean_key_text(item.primary_feed):
        score += 20

    if item.visible_type:
        score += 8

    if item.detected_box_type:
        score += 6

    if item.calculated_box_type and item.calculated_box_type != "UNKNOWN":
        score += 10

    if item.has_p:
        score += 3

    if item.s_splitter:
        score += 3

    if item.t_splitter:
        score += 3

    if item.splice_count:
        score += 2

    if item.confidence is not None:
        try:
            score += int(float(item.confidence) / 10)
        except Exception:
            pass

    return score


def _append_observation(item, text):
    current = (item.observation or "").strip()
    text = (text or "").strip()

    if not text:
        return current

    if text in current:
        return current

    if current:
        return f"{current} {text}".strip()

    return text


def _letters_to_number(letters):
    letters = _clean_key_text(letters)

    if not letters:
        return None

    total = 0

    for char in letters:
        if not ("A" <= char <= "Z"):
            return None

        total = total * 26 + (ord(char) - ord("A") + 1)

    return total


def _sheet_coordinates(sheet):
    """
    Convierte un sheet name en coordenadas de plano.

    Ejemplos:
    B4       -> (2, 4)
    Sheet C2 -> (3, 2)
    AA10     -> (27, 10)
    """
    text = _clean_key_text(sheet)

    if not text:
        return None

    match = re.search(r"\b([A-Z]{1,3})\s*[-_ ]?\s*(\d{1,3})\b", text)

    if not match:
        return None

    col_letters = match.group(1)
    row_number = match.group(2)

    col = _letters_to_number(col_letters)

    try:
        row = int(row_number)
    except Exception:
        row = None

    if not col or not row:
        return None

    return col, row


def _same_sheet(item_a, item_b):
    sheet_a = _clean_key_text(item_a.sheet)
    sheet_b = _clean_key_text(item_b.sheet)

    if not sheet_a or not sheet_b:
        return False

    return sheet_a == sheet_b


def _are_border_neighbor_sheets(item_a, item_b):
    """
    Hojas colindantes reales por frontera:
    - izquierda / derecha
    - arriba / abajo

    No incluye diagonales.
    No depende del orden del PDF.
    """
    coords_a = _sheet_coordinates(item_a.sheet)
    coords_b = _sheet_coordinates(item_b.sheet)

    if not coords_a or not coords_b:
        return False

    col_a, row_a = coords_a
    col_b, row_b = coords_b

    col_diff = abs(col_a - col_b)
    row_diff = abs(row_a - row_b)

    if row_diff == 0 and col_diff == 1:
        return True

    if col_diff == 0 and row_diff == 1:
        return True

    return False


def _sort_for_keeper(item):
    coords = _sheet_coordinates(item.sheet)

    if coords:
        col, row = coords
    else:
        col, row = 999999, 999999

    page_number = item.page.page_number if item.page_id else 999999

    return (
        -_item_score(item),
        row,
        col,
        page_number,
        item.id,
    )


def _exact_duplicate_key(item):
    """
    Clave exacta para excluir automático.

    Se excluye automático solo si coincide TODO esto:
    - project_name
    - primary_feed
    - splice_count
    - calculated_box_type
    - s_splitter
    - t_splitter
    - has_p

    Además deben estar en hojas colindantes por frontera real.
    """
    project_name = _clean_key_text(item.project_name)

    if not project_name:
        return None

    return (
        project_name,
        _clean_key_text(item.primary_feed),
        safe_int(item.splice_count, 0),
        _clean_key_text(item.calculated_box_type),
        _clean_key_text(item.s_splitter),
        _clean_key_text(item.t_splitter),
        _clean_bool(item.has_p),
    )


def _find_neighbor_components(group_items):
    ids = [item.id for item in group_items]
    item_by_id = {item.id: item for item in group_items}
    graph = {item.id: set() for item in group_items}

    for i, item_a in enumerate(group_items):
        for item_b in group_items[i + 1 :]:
            if _are_border_neighbor_sheets(item_a, item_b):
                graph[item_a.id].add(item_b.id)
                graph[item_b.id].add(item_a.id)

    visited = set()
    components = []

    for item_id in ids:
        if item_id in visited:
            continue

        stack = [item_id]
        visited.add(item_id)
        component_ids = []

        while stack:
            current_id = stack.pop()
            component_ids.append(current_id)

            for next_id in graph[current_id]:
                if next_id not in visited:
                    visited.add(next_id)
                    stack.append(next_id)

        component = [item_by_id[x] for x in component_ids]
        components.append(component)

    return components


def _mark_item_as_auto_duplicate(item, keeper, reason):
    item.is_duplicate = True
    item.needs_review = False
    item.observation = _append_observation(
        item,
        (
            f"Automatic duplicate: {reason} "
            f"Kept item #{keeper.id}; this one is excluded from view and export."
        ),
    )
    item.save(update_fields=["is_duplicate", "needs_review", "observation"])


def _mark_item_as_review(item, text):
    item.needs_review = True
    item.observation = _append_observation(item, text)
    item.save(update_fields=["needs_review", "observation"])


def _mark_keeper_note(keeper, text):
    keeper.observation = _append_observation(keeper, text)
    keeper.save(update_fields=["observation"])


def mark_duplicates(job):
    """
    Reglas finales de duplicados:

    1) Duplicado de borde por lectura parcial:
       - mismo project_name
       - mismo primary_feed
       - hojas colindantes por frontera real
       => se considera duplicado automático aunque cambien splices/type.
       Se conserva la lectura más completa.

       Ejemplo:
       D1 7020-001 P0019 A4 1 splice
       D2 7020-001 P0019 B8G 13 splices
       => queda D2, D1 pasa a Duplicate detail.

    2) Duplicado automático normal:
       - mismo project_name
       - mismo splice_count
       => se considera duplicado automático.
       Se conserva una sola línea en la tabla principal.

    3) Mismo número de caja con distinta cantidad de fusiones,
       pero sin coincidir primary_feed o sin ser borde:
       => NO se elimina automático.
       => se marca needs_review=True para revisión manual.

    4) Si no tiene project_name:
       => no se evalúa como duplicado automático.
    """

    job.items.update(is_duplicate=False)

    items = list(
        job.items.select_related("page").order_by(
            "project_name",
            "primary_feed",
            "splice_count",
            "sheet",
            "id",
        )
    )

    def _border_keeper_sort(item):
        """
        Para duplicados de borde se conserva la lectura más completa.

        Prioridad:
        - mayor cantidad de fusiones
        - mejor score general
        - orden natural por hoja/página/id
        """
        coords = _sheet_coordinates(item.sheet)

        if coords:
            col, row = coords
        else:
            col, row = 999999, 999999

        page_number = item.page.page_number if item.page_id else 999999

        return (
            -safe_int(item.splice_count, 0),
            -_item_score(item),
            row,
            col,
            page_number,
            item.id,
        )

    # ==========================================================
    # PASO 1:
    # Duplicado de borde:
    # mismo project_name + mismo primary_feed + hojas vecinas.
    #
    # Esto corrige lecturas parciales de borde entre hojas.
    # Aunque discrepen las fusiones o el tipo de caja, si es la misma
    # caja y el mismo primary feed en hojas colindantes, se conserva
    # la lectura más completa.
    # ==========================================================
    groups_by_box_and_feed = {}

    for item in items:
        project_name = _clean_key_text(item.project_name)
        primary_feed = _clean_key_text(item.primary_feed)

        if not project_name or not primary_feed:
            continue

        key = (project_name, primary_feed)
        groups_by_box_and_feed.setdefault(key, []).append(item)

    for key, group_items in groups_by_box_and_feed.items():
        if len(group_items) <= 1:
            continue

        components = _find_neighbor_components(group_items)

        for component in components:
            if len(component) <= 1:
                continue

            unique_sheets = {
                _clean_key_text(item.sheet)
                for item in component
                if _clean_key_text(item.sheet)
            }

            # Si están en la misma hoja, no lo eliminamos aquí.
            # Eso normalmente es una doble lectura de la IA dentro de la misma página
            # y se maneja por splice_count igual o revisión manual.
            if len(unique_sheets) <= 1:
                continue

            keeper = sorted(component, key=_border_keeper_sort)[0]

            _mark_keeper_note(
                keeper,
                (
                    "Automatic border duplicate check: this item was kept because "
                    "another item with the same box number and primary feed was found "
                    "on a border-neighbor sheet. The system kept the most complete reading."
                ),
            )

            for item in component:
                if item.id == keeper.id:
                    continue

                _mark_item_as_auto_duplicate(
                    item,
                    keeper,
                    (
                        "same box number and same primary feed detected on a "
                        "border-neighbor sheet. Treated as partial/edge duplicate."
                    ),
                )

    # ==========================================================
    # PASO 2:
    # Duplicado automático normal:
    # mismo project_name + mismo splice_count.
    #
    # Se ejecuta después del borde, solo con items todavía incluidos.
    # ==========================================================
    current_items = list(
        job.items.select_related("page")
        .filter(is_duplicate=False)
        .order_by(
            "project_name",
            "splice_count",
            "sheet",
            "primary_feed",
            "id",
        )
    )

    groups_by_box_and_splices = {}

    for item in current_items:
        project_name = _clean_key_text(item.project_name)
        splice_count = safe_int(item.splice_count, 0)

        if not project_name:
            continue

        key = (project_name, splice_count)
        groups_by_box_and_splices.setdefault(key, []).append(item)

    for key, group_items in groups_by_box_and_splices.items():
        if len(group_items) <= 1:
            continue

        keeper = sorted(group_items, key=_sort_for_keeper)[0]

        _mark_keeper_note(
            keeper,
            (
                "Automatic duplicate check: this item was kept because another item "
                "had the same box number and the same splice count."
            ),
        )

        for item in group_items:
            if item.id == keeper.id:
                continue

            _mark_item_as_auto_duplicate(
                item,
                keeper,
                "same box number and same splice count detected.",
            )

    # ==========================================================
    # PASO 3:
    # Mismo número de caja con distinta cantidad de fusiones.
    #
    # Si llegó aquí, significa que:
    # - no fue eliminado por borde con mismo primary_feed
    # - no fue eliminado por mismo splice_count
    #
    # Entonces se deja visible en rojo para decisión manual.
    # ==========================================================
    remaining_items = list(
        job.items.select_related("page")
        .filter(is_duplicate=False)
        .order_by(
            "project_name",
            "sheet",
            "id",
        )
    )

    by_project = {}

    for item in remaining_items:
        project_name = _clean_key_text(item.project_name)

        if not project_name:
            continue

        by_project.setdefault(project_name, []).append(item)

    for project_name, group_items in by_project.items():
        if len(group_items) <= 1:
            continue

        for item in group_items:
            _mark_item_as_review(
                item,
                (
                    "Duplicate review: same box number appears more than once, "
                    "but splice count or feed information is different. Kept included "
                    "and visible for manual decision."
                ),
            )


def process_plan_reader_job(job_id):
    """
    Procesa un PlanReaderJob.

    Si PLAN_READER_USE_OPENAI=False:
        registra páginas solamente.

    Si PLAN_READER_USE_OPENAI=True:
        renderiza cada página como PNG, manda a OpenAI, crea items y aplica reglas.
    """

    job = PlanReaderJob.objects.get(id=job_id)

    if job.status == PlanReaderJob.STATUS_PROCESSING:
        raise RuntimeError(f"PlanReaderJob #{job.id} is already processing.")

    run_openai = use_openai()
    zoom = render_zoom()

    with transaction.atomic():
        job.status = PlanReaderJob.STATUS_PROCESSING
        job.started_at = job.started_at or timezone.now()
        job.completed_at = None
        job.error_message = ""
        job.processed_pages = 0
        job.failed_pages = 0
        job.save(
            update_fields=[
                "status",
                "started_at",
                "completed_at",
                "error_message",
                "processed_pages",
                "failed_pages",
                "updated_at",
            ]
        )

        job.pages.all().delete()
        job.items.all().delete()

    try:
        with get_pdf_temp_path(job.pdf_file) as pdf_path:
            total_pages = count_pdf_pages(pdf_path)

            job.total_pages = total_pages
            job.save(update_fields=["total_pages", "updated_at"])

            processed_pages = 0
            failed_pages = 0

            with get_plan_reader_temp_dir(job.id) as temp_dir:
                for page_number in range(1, total_pages + 1):
                    page_obj = PlanReaderPage.objects.create(
                        job=job,
                        page_number=page_number,
                        status=PlanReaderPage.STATUS_PROCESSING,
                    )

                    try:
                        sheet_name = extract_sheet_name_from_page(
                            pdf_path=pdf_path,
                            page_number=page_number,
                        )

                        page_obj.sheet_name = sheet_name

                        if run_openai:
                            image_path = render_pdf_page_to_image(
                                pdf_path=pdf_path,
                                page_number=page_number,
                                output_dir=temp_dir,
                                zoom=zoom,
                            )

                            page_data, raw_response, confidence_decimal = (
                                extract_plan_page_with_openai(
                                    image_path=image_path,
                                    page_number=page_number,
                                    known_sheet_name=sheet_name,
                                )
                            )

                            extracted_sheet = str(
                                page_data.get("sheet_name") or ""
                            ).strip()
                            if extracted_sheet:
                                page_obj.sheet_name = extracted_sheet

                            page_obj.extracted_json = page_data
                            page_obj.raw_ai_response = raw_response
                            page_obj.confidence = confidence_decimal
                            page_obj.status = PlanReaderPage.STATUS_COMPLETED
                            page_obj.processed_at = timezone.now()
                            page_obj.error_message = ""
                            page_obj.save(
                                update_fields=[
                                    "sheet_name",
                                    "extracted_json",
                                    "raw_ai_response",
                                    "confidence",
                                    "status",
                                    "processed_at",
                                    "error_message",
                                ]
                            )

                            for raw_item in page_data.get("items", []):
                                create_item_from_extraction(
                                    job=job,
                                    page_obj=page_obj,
                                    page_data=page_data,
                                    raw_item=raw_item,
                                )

                        else:
                            page_obj.status = PlanReaderPage.STATUS_COMPLETED
                            page_obj.processed_at = timezone.now()
                            page_obj.error_message = ""
                            page_obj.save(
                                update_fields=[
                                    "sheet_name",
                                    "status",
                                    "processed_at",
                                    "error_message",
                                ]
                            )

                        processed_pages += 1

                    except Exception as exc:
                        failed_pages += 1

                        page_obj.status = PlanReaderPage.STATUS_FAILED
                        page_obj.error_message = str(exc)
                        page_obj.processed_at = timezone.now()
                        page_obj.save(
                            update_fields=[
                                "status",
                                "error_message",
                                "processed_at",
                            ]
                        )

                    job.processed_pages = processed_pages
                    job.failed_pages = failed_pages
                    job.save(
                        update_fields=[
                            "processed_pages",
                            "failed_pages",
                            "updated_at",
                        ]
                    )

        mark_duplicates(job)

        if failed_pages:
            job.status = PlanReaderJob.STATUS_FAILED
            job.error_message = f"{failed_pages} page(s) failed during processing."
        else:
            job.status = PlanReaderJob.STATUS_NEEDS_REVIEW
            job.error_message = ""

        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )

        return job

    except Exception as exc:
        job.status = PlanReaderJob.STATUS_FAILED
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_message",
                "completed_at",
                "updated_at",
            ]
        )

        raise
