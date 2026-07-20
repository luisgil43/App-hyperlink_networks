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
    Extrae un Project ID válido y completo desde texto crudo.

    Estructura obligatoria:
    - cuatro dígitos;
    - guion;
    - tres dígitos;
    - cero o más sufijos numéricos unidos por guiones.

    Casos válidos:
    - 7020-001
    - 7020-001-1
    - 5005-009-7
    - 5000-039-1-1
    - 5000-039-1-3
    - 7022-007-1-2

    Casos inválidos:
    - 0913RA_P0043:1-4;
    - 0913RA,P0045;1-3;
    - P0045
    - 1-12XD
    - 16-24XD

    No inventa ni conserva textos que no tengan el patrón numérico
    válido de Project ID.
    """
    text = _clean_read_text(value)

    if not text:
        return ""

    text = text.replace("–", "-").replace("—", "-").replace("−", "-")

    match = re.search(
        r"(?<![A-Z0-9])\d{4}-\d{3}(?:-\d+)*(?![A-Z0-9])",
        text,
        re.IGNORECASE,
    )

    if not match:
        return ""

    return match.group(0).strip()


def _extract_primary_feed_from_text(value):
    """
    Extrae un Primary Feed con formato P + 4 dígitos.

    Ejemplos válidos:
    - P0018
    - P0049
    - P1234

    Si no encuentra uno, devuelve "".
    """
    text = _clean_read_text(value).upper()

    if not text:
        return ""

    match = re.search(r"\bP\d{4}\b", text)

    if not match:
        return ""

    return match.group(0).strip()


def _normalize_primary_feed_from_raw(raw_item):
    """
    Decide el Primary Feed final.

    Prioridad:
    1. primary_feed entregado por OpenAI.
    2. raw_text completo del item.
    3. raw_text de splitter_lines, respetando su orden.

    Si no existe un patrón P#### válido, devuelve "".
    """
    raw_item = raw_item or {}

    primary_feed = _extract_primary_feed_from_text(raw_item.get("primary_feed"))

    if primary_feed:
        return primary_feed

    primary_feed = _extract_primary_feed_from_text(raw_item.get("raw_text"))

    if primary_feed:
        return primary_feed

    splitter_lines = raw_item.get("splitter_lines")

    if isinstance(splitter_lines, list):
        for line in splitter_lines:
            if not isinstance(line, dict):
                continue

            primary_feed = _extract_primary_feed_from_text(line.get("raw_text"))

            if primary_feed:
                return primary_feed

    return ""


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
    - BBG

    Todo eso se interpreta como B8G.
    """
    text = _clean_read_text(value)

    if not text:
        return ""

    text = text.upper().strip()

    variants_pattern = r"\b(BGP|BBP|BG8|B6G|B86|B8C|BBG)\b"

    if re.search(variants_pattern, text):
        text = re.sub(
            variants_pattern,
            "B8G",
            text,
        )

    return text


def _normalize_final_box_type(value):
    """
    Última defensa para que nunca quede una variante OCR inválida
    como Final Box Type.

    Valores como:
    - BGP
    - BBP
    - BG8
    - B6G
    - B86
    - B8C
    - BBG

    se normalizan a B8G.

    No modifica la lógica de rules_engine.py.
    Solo protege el valor final antes de guardar en DB.
    """
    text = _clean_read_text(value)

    if not text:
        return ""

    upper = text.upper().strip()

    invalid_b8g_variants = {
        "BGP",
        "BBP",
        "BG8",
        "B6G",
        "B86",
        "B8C",
        "BBG",
    }

    if upper in invalid_b8g_variants:
        return "B8G"

    replacements = {
        "BGP": "B8G",
        "BBP": "B8G",
        "BG8": "B8G",
        "B6G": "B8G",
        "B86": "B8G",
        "B8C": "B8G",
        "BBG": "B8G",
    }

    normalized = upper

    for old_value, new_value in replacements.items():
        normalized = normalized.replace(
            old_value,
            new_value,
        )

    return normalized


def _normalize_project_name_from_raw(raw_item):
    """
    Decide el project_name final usando exclusivamente Project IDs
    que cumplan el formato numérico válido.

    Compara:
    - project_name entregado por OpenAI;
    - Project ID encontrado dentro de raw_text.

    Formato válido:
    - ####-###
    - ####-###-N
    - ####-###-N-N
    - y otros sufijos numéricos visiblemente conectados por guiones.

    Cuando ambos candidatos pertenecen a la misma caja base,
    conserva siempre la versión más completa.

    Ejemplos:

    project_name = 5000-039-1
    raw_text     = 5000-039-1-3
    resultado    = 5000-039-1-3

    project_name = 5000-039-1-1
    raw_text     = 5000-039-1
    resultado    = 5000-039-1-1

    project_name = 0913RA_P0043:1-4;
    raw_text     = 0913RA_P0043:1-4;
    resultado    = ""

    Si OpenAI entrega un texto que no contiene un Project ID válido,
    no se conserva como project_name.
    """
    raw_item = raw_item or {}

    raw_project_name = _clean_read_text(raw_item.get("project_name"))

    raw_text = _clean_read_text(raw_item.get("raw_text"))

    box_from_project = _extract_box_from_text(raw_project_name)

    box_from_raw = _extract_box_from_text(raw_text)

    candidates = []

    if box_from_project:
        candidates.append(box_from_project)

    if box_from_raw:
        candidates.append(box_from_raw)

    # No se conserva el texto original si no cumple el patrón válido.
    # Esto elimina falsos Project IDs como:
    # 0913RA_P0043:1-4;
    if not candidates:
        return ""

    unique_candidates = []

    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    if len(unique_candidates) == 1:
        return unique_candidates[0]

    def base_without_suffix(value):
        """
        Obtiene la base ####-### sin importar cuántos sufijos tenga.

        Ejemplos:
        5000-039         -> 5000-039
        5000-039-1       -> 5000-039
        5000-039-1-1     -> 5000-039
        5000-039-1-3     -> 5000-039
        """
        match = re.fullmatch(
            r"(?P<base>\d{4}-\d{3})(?:-\d+)*",
            value,
        )

        if not match:
            return value

        return match.group("base")

    candidate_bases = {
        base_without_suffix(candidate) for candidate in unique_candidates
    }

    # Cuando pertenecen a la misma base, conserva el valor
    # con mayor cantidad de sufijos numéricos.
    if len(candidate_bases) == 1:
        return max(
            unique_candidates,
            key=lambda value: (
                value.count("-"),
                len(value),
            ),
        )

    # Si OpenAI y raw_text contienen dos Project IDs realmente
    # diferentes, se prioriza el campo específico project_name.
    if box_from_project:
        return box_from_project

    return box_from_raw


def _clean_ai_item(raw_item):
    """
    Limpia el item de OpenAI antes de aplicar reglas.

    Corrige:
    - project_name incompleto.
    - primary_feed P####.
    - BGP/BBP/BG8/B6G/B86/B8C/BBG como B8G.

    splitter_lines se conserva como lista completa y ordenada.
    La validación final de sus líneas la realiza rules_engine.py.
    """
    raw_item = raw_item or {}

    cleaned = dict(raw_item)

    cleaned["project_name"] = _normalize_project_name_from_raw(raw_item)

    cleaned["primary_feed"] = _normalize_primary_feed_from_raw(raw_item)

    cleaned["visible_type"] = _normalize_ai_box_family(raw_item.get("visible_type"))

    cleaned["detected_box_type"] = _normalize_ai_box_family(
        raw_item.get("detected_box_type")
    )

    splitter_lines = raw_item.get("splitter_lines")

    if isinstance(splitter_lines, list):
        cleaned["splitter_lines"] = splitter_lines
    else:
        cleaned["splitter_lines"] = []

    return cleaned


def create_item_from_extraction(
    job,
    page_obj,
    page_data,
    raw_item,
):
    """
    Crea un PlanReaderItem desde la extracción de OpenAI.

    Fuente principal de splitters:
    - splitter_lines

    Compatibilidad legacy:
    - has_p
    - s_splitter
    - t_splitter

    El rules_engine:
    - normaliza splitter_lines;
    - deriva los campos legacy;
    - calcula BOX TYPE;
    - calcula C-108, C-109 y C-110;
    - decide needs_review y observation.

    Importante:
    - No modifica la lógica de duplicados.
    - No modifica la exportación.
    """
    raw_item = _clean_ai_item(raw_item)

    sheet = (
        raw_item.get("sheet")
        or page_data.get("sheet_name")
        or page_obj.sheet_name
        or ""
    )

    raw_splitter_lines = raw_item.get("splitter_lines")

    if not isinstance(raw_splitter_lines, list):
        raw_splitter_lines = []

    base_data = {
        "sheet": sheet,
        "co": job.co,
        "dfn": job.dfn,
        "project_name": str(raw_item.get("project_name") or "").strip(),
        "primary_feed": str(raw_item.get("primary_feed") or "").strip(),
        "visible_type": str(raw_item.get("visible_type") or "").strip(),
        "detected_box_type": str(raw_item.get("detected_box_type") or "").strip(),
        # Nueva fuente principal.
        "splitter_lines": raw_splitter_lines,
        # Campos legacy.
        # Se siguen enviando como fallback para compatibilidad.
        "has_p": bool(raw_item.get("has_p")),
        "s_splitter": str(raw_item.get("s_splitter") or "").strip(),
        "t_splitter": str(raw_item.get("t_splitter") or "").strip(),
        "splice_count": safe_int(
            raw_item.get("splice_count"),
            0,
        ),
    }

    calculated = apply_box_rules(base_data)

    calculated["calculated_box_type"] = _normalize_final_box_type(
        calculated.get(
            "calculated_box_type",
            "",
        )
    )

    calculated["detected_box_type"] = _normalize_ai_box_family(
        calculated.get(
            "detected_box_type",
            "",
        )
    )

    calculated["visible_type"] = _normalize_ai_box_family(
        calculated.get(
            "visible_type",
            "",
        )
    )

    calculated_splitter_lines = calculated.get(
        "splitter_lines",
        [],
    )

    if not isinstance(calculated_splitter_lines, list):
        calculated_splitter_lines = []

    item_confidence = safe_decimal(raw_item.get("confidence"))

    return PlanReaderItem.objects.create(
        job=job,
        page=page_obj,
        sheet=calculated.get(
            "sheet",
            "",
        ),
        co=calculated.get(
            "co",
            "",
        ),
        dfn=calculated.get(
            "dfn",
            "",
        ),
        project_name=calculated.get(
            "project_name",
            "",
        ),
        primary_feed=calculated.get(
            "primary_feed",
            "",
        ),
        visible_type=calculated.get(
            "visible_type",
            "",
        ),
        detected_box_type=calculated.get(
            "detected_box_type",
            "",
        ),
        # Nueva fuente principal.
        splitter_lines=calculated_splitter_lines,
        # Compatibilidad con todo lo existente.
        has_p=bool(calculated.get("has_p")),
        s_splitter=calculated.get(
            "s_splitter",
            "",
        ),
        t_splitter=calculated.get(
            "t_splitter",
            "",
        ),
        splice_count=safe_int(
            calculated.get("splice_count"),
            0,
        ),
        calculated_box_type=calculated.get(
            "calculated_box_type",
            "",
        ),
        c108_ug=safe_int(
            calculated.get("c108_ug"),
            0,
        ),
        c109_splices=safe_int(
            calculated.get("c109_splices"),
            0,
        ),
        c110_splitters=safe_int(
            calculated.get("c110_splitters"),
            0,
        ),
        observation=calculated.get(
            "observation",
            "",
        ),
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


class PlanReaderJobCancelled(Exception):
    """
    Excepción interna para terminar ordenadamente un PlanReaderJob
    cuando el usuario solicita detenerlo.
    """

    pass


def _plan_reader_cancel_requested(job_id):
    """
    Consulta directamente la base de datos para saber si el usuario
    solicitó detener el PlanReaderJob.

    No utiliza el objeto job que ya está en memoria porque ese objeto
    puede tener un estado desactualizado.
    """
    current_status = (
        PlanReaderJob.objects.filter(id=job_id).values_list("status", flat=True).first()
    )

    if current_status is None:
        raise PlanReaderJobCancelled(f"PlanReaderJob #{job_id} no longer exists.")

    return current_status == PlanReaderJob.STATUS_CANCELLED


def _raise_if_plan_reader_cancelled(job_id):
    """
    Detiene el flujo cuando el job está marcado como CANCELLED.
    """
    if _plan_reader_cancel_requested(job_id):
        raise PlanReaderJobCancelled(
            f"PlanReaderJob #{job_id} was stopped by the user."
        )


def _finish_cancelled_plan_reader_job(job_id):
    """
    Confirma en la base de datos que el proceso fue detenido.

    completed_at deja de ser None, por lo que la interfaz cambia de:

        Stopping...

    a:

        Cancelled / Reprocess
    """
    now = timezone.now()

    PlanReaderJob.objects.filter(id=job_id).update(
        status=PlanReaderJob.STATUS_CANCELLED,
        error_message="Processing stopped by user.",
        completed_at=now,
        updated_at=now,
    )

    return PlanReaderJob.objects.get(id=job_id)


def process_plan_reader_job(job_id, allow_processing=False):
    """
    Procesa un PlanReaderJob.

    Si PLAN_READER_USE_OPENAI=False:
        registra páginas solamente.

    Si PLAN_READER_USE_OPENAI=True:
        renderiza cada página como PNG, manda a OpenAI, crea items
        y aplica reglas.

    allow_processing=True:
        se utiliza desde el worker porque el worker principal reclama
        primero el job y lo marca como PROCESSING.

    Cancelación cooperativa:

    - Revisa CANCELLED antes de comenzar cada página.
    - Revisa CANCELLED después de operaciones costosas.
    - Revisa CANCELLED durante la creación de items.
    - No convierte una cancelación en FAILED.
    - No convierte una cancelación en NEEDS_REVIEW.
    - Completa completed_at cuando confirma que el proceso se detuvo.

    Nota:
        Una solicitud que ya fue enviada a OpenAI no se puede interrumpir
        a mitad de la respuesta. En ese caso, el proceso se detendrá
        inmediatamente después de que termine la llamada actual.
    """

    run_openai = use_openai()
    zoom = render_zoom()

    # =========================================================
    # Preparación inicial protegida
    # =========================================================

    with transaction.atomic():
        job = PlanReaderJob.objects.select_for_update().get(
            id=job_id,
        )

        # Puede ocurrir que el worker haya reclamado el job y que el
        # usuario presione Stop antes de que el subproceso termine
        # de iniciar. No debemos devolverlo a PROCESSING.
        if job.status == PlanReaderJob.STATUS_CANCELLED:
            job.completed_at = timezone.now()
            job.error_message = "Processing stopped by user."

            job.save(
                update_fields=[
                    "completed_at",
                    "error_message",
                    "updated_at",
                ]
            )

            return job

        if job.status == PlanReaderJob.STATUS_PROCESSING and not allow_processing:
            raise RuntimeError(f"PlanReaderJob #{job.id} is already processing.")

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

    processed_pages = 0
    failed_pages = 0

    try:
        _raise_if_plan_reader_cancelled(job_id)

        with get_pdf_temp_path(job.pdf_file) as pdf_path:
            _raise_if_plan_reader_cancelled(job_id)

            total_pages = count_pdf_pages(pdf_path)

            _raise_if_plan_reader_cancelled(job_id)

            PlanReaderJob.objects.filter(
                id=job_id,
            ).update(
                total_pages=total_pages,
                updated_at=timezone.now(),
            )

            with get_plan_reader_temp_dir(job.id) as temp_dir:
                for page_number in range(
                    1,
                    total_pages + 1,
                ):
                    # No iniciar una página nueva si el usuario
                    # ya solicitó detener el proceso.
                    _raise_if_plan_reader_cancelled(job_id)

                    page_obj = PlanReaderPage.objects.create(
                        job_id=job_id,
                        page_number=page_number,
                        status=PlanReaderPage.STATUS_PROCESSING,
                    )

                    try:
                        _raise_if_plan_reader_cancelled(job_id)

                        sheet_name = extract_sheet_name_from_page(
                            pdf_path=pdf_path,
                            page_number=page_number,
                        )

                        _raise_if_plan_reader_cancelled(job_id)

                        page_obj.sheet_name = sheet_name

                        if run_openai:
                            image_path = render_pdf_page_to_image(
                                pdf_path=pdf_path,
                                page_number=page_number,
                                output_dir=temp_dir,
                                zoom=zoom,
                            )

                            _raise_if_plan_reader_cancelled(job_id)

                            page_data, raw_response, confidence_decimal = (
                                extract_plan_page_with_openai(
                                    image_path=image_path,
                                    page_number=page_number,
                                    known_sheet_name=sheet_name,
                                )
                            )

                            # Si Stop fue presionado mientras OpenAI
                            # respondía, se detiene aquí y no guarda
                            # resultados parciales de esta página.
                            _raise_if_plan_reader_cancelled(job_id)

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

                            for raw_item in page_data.get(
                                "items",
                                [],
                            ):
                                _raise_if_plan_reader_cancelled(job_id)

                                create_item_from_extraction(
                                    job=job,
                                    page_obj=page_obj,
                                    page_data=page_data,
                                    raw_item=raw_item,
                                )

                            _raise_if_plan_reader_cancelled(job_id)

                        else:
                            _raise_if_plan_reader_cancelled(job_id)

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

                    except PlanReaderJobCancelled:
                        # Elimina solamente los resultados parciales
                        # de la página que estaba en ejecución.
                        PlanReaderItem.objects.filter(
                            job_id=job_id,
                            page_id=page_obj.id,
                        ).delete()

                        page_obj.delete()

                        raise

                    except Exception as exc:
                        # Antes de registrar un error debemos verificar
                        # que realmente no haya sido una cancelación.
                        if _plan_reader_cancel_requested(job_id):
                            PlanReaderItem.objects.filter(
                                job_id=job_id,
                                page_id=page_obj.id,
                            ).delete()

                            page_obj.delete()

                            raise PlanReaderJobCancelled(
                                (f"PlanReaderJob #{job_id} " "was stopped by the user.")
                            ) from exc

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

                    # Solo actualiza contadores; nunca toca status,
                    # para no sobrescribir CANCELLED.
                    PlanReaderJob.objects.filter(
                        id=job_id,
                    ).update(
                        processed_pages=processed_pages,
                        failed_pages=failed_pages,
                        updated_at=timezone.now(),
                    )

                    _raise_if_plan_reader_cancelled(job_id)

        # =====================================================
        # Finalización normal
        # =====================================================

        _raise_if_plan_reader_cancelled(job_id)

        job = PlanReaderJob.objects.get(
            id=job_id,
        )

        mark_duplicates(job)

        _raise_if_plan_reader_cancelled(job_id)

        with transaction.atomic():
            job = PlanReaderJob.objects.select_for_update().get(
                id=job_id,
            )

            # Última defensa para no reemplazar CANCELLED por
            # FAILED o NEEDS_REVIEW.
            if job.status == PlanReaderJob.STATUS_CANCELLED:
                job.completed_at = timezone.now()
                job.error_message = "Processing stopped by user."

                job.save(
                    update_fields=[
                        "completed_at",
                        "error_message",
                        "updated_at",
                    ]
                )

                return job

            if failed_pages:
                job.status = PlanReaderJob.STATUS_FAILED
                job.error_message = f"{failed_pages} page(s) failed during processing."
            else:
                job.status = PlanReaderJob.STATUS_NEEDS_REVIEW
                job.error_message = ""

            job.processed_pages = processed_pages
            job.failed_pages = failed_pages
            job.completed_at = timezone.now()

            job.save(
                update_fields=[
                    "status",
                    "processed_pages",
                    "failed_pages",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )

        return job

    except PlanReaderJobCancelled:
        return _finish_cancelled_plan_reader_job(job_id)

    except Exception as exc:
        # Si el usuario solicitó detenerlo mientras se producía
        # otra excepción, la cancelación tiene prioridad.
        if _plan_reader_cancel_requested(job_id):
            return _finish_cancelled_plan_reader_job(job_id)

        now = timezone.now()

        PlanReaderJob.objects.filter(
            id=job_id,
        ).exclude(
            status=PlanReaderJob.STATUS_CANCELLED,
        ).update(
            status=PlanReaderJob.STATUS_FAILED,
            error_message=str(exc),
            completed_at=now,
            updated_at=now,
        )

        raise
