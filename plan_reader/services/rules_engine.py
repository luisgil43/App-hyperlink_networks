import re

VALID_FINAL_BOX_TYPES = {
    "B8G",
    "B8G 1X4",
    "B8G 1X8",
    "A4 1X4",
    "A4 1X2",
    "UNKNOWN",
}

INTEGRATED_PC_SPLITTERS = {
    ("S", "1:8"),
    ("T", "1:4"),
    ("T", "1:2"),
}

SPLITTER_LINE_RE = re.compile(
    r"^\s*([PST])\s*[-–—]?\s*1\s*[:Xx\-]\s*([248])\b",
    re.IGNORECASE,
)


def _normalize_text(value):
    return str(value or "").strip().upper()


def _normalize_splitter(value):
    """
    Normaliza valores tipo:
    S-1:8
    1:8
    1-8
    1X8
    1x8

    Resultado:
    1:2
    1:4
    1:8
    """
    text = _normalize_text(value)

    if not text:
        return ""

    text = text.replace(" ", "")
    text = text.replace("X", ":")
    text = text.replace("–", "-")
    text = text.replace("—", "-")

    match = re.search(r"1\s*[:\-]?\s*([248])", text)

    if match:
        return f"1:{match.group(1)}"

    return ""


def _normalize_splitter_level(value):
    level = _normalize_text(value)

    if level in {"P", "S", "T"}:
        return level

    return ""


def _normalize_splitter_line(line):
    """
    Normaliza una línea individual de splitter.

    Entrada esperada:
    {
        "level": "S",
        "ratio": "1:2",
        "raw_text": "S-1:2(P0053,S5:T1)"
    }

    La fuente principal para determinar el nivel es el inicio de raw_text.
    Nunca se busca P/S/T dentro del paréntesis.
    """
    line = line or {}

    raw_text = str(line.get("raw_text") or "").strip()

    detected_level = ""
    detected_ratio = ""

    if raw_text:
        match = SPLITTER_LINE_RE.search(raw_text)

        if match:
            detected_level = _normalize_splitter_level(match.group(1))
            detected_ratio = f"1:{match.group(2)}"

    level = detected_level or _normalize_splitter_level(line.get("level"))
    ratio = detected_ratio or _normalize_splitter(line.get("ratio"))

    if not level or ratio not in {"1:2", "1:4", "1:8"}:
        return None

    return {
        "level": level,
        "ratio": ratio,
        "raw_text": raw_text,
    }


def _normalize_splitter_lines(value):
    """
    Limpia splitter_lines conservando el orden original.

    Solo quedan líneas válidas P/S/T.
    """
    if not isinstance(value, list):
        return []

    normalized = []

    for line in value:
        cleaned = _normalize_splitter_line(line)

        if cleaned:
            normalized.append(cleaned)

    return normalized


def _legacy_splitter_lines(item_data):
    """
    Compatibilidad con datos antiguos.

    Si splitter_lines no existe o viene vacío, reconstruye una representación
    mínima desde:
    - has_p
    - s_splitter
    - t_splitter

    Esto permite que los items antiguos sigan funcionando.
    """
    lines = []

    if bool(item_data.get("has_p")):
        lines.append(
            {
                "level": "P",
                "ratio": "1:8",
                "raw_text": "",
            }
        )

    s_splitter = _normalize_splitter(item_data.get("s_splitter"))

    if s_splitter:
        lines.append(
            {
                "level": "S",
                "ratio": s_splitter,
                "raw_text": "",
            }
        )

    t_splitter = _normalize_splitter(item_data.get("t_splitter"))

    if t_splitter:
        lines.append(
            {
                "level": "T",
                "ratio": t_splitter,
                "raw_text": "",
            }
        )

    return lines


def _get_effective_splitter_lines(item_data):
    """
    splitter_lines es la fuente principal.

    Si no existe o viene vacío, usa fallback legacy.
    """
    splitter_lines = _normalize_splitter_lines(item_data.get("splitter_lines"))

    if splitter_lines:
        return splitter_lines

    return _legacy_splitter_lines(item_data)


def _derive_legacy_splitter_fields(splitter_lines):
    """
    Deriva los campos antiguos desde splitter_lines.

    has_p:
    - True si existe al menos una línea P.

    s_splitter:
    - ratio de la última línea S.

    t_splitter:
    - ratio de la última línea T.
    """
    has_p = any(line["level"] == "P" for line in splitter_lines)

    s_splitter = ""
    t_splitter = ""

    for line in splitter_lines:
        if line["level"] == "S":
            s_splitter = line["ratio"]

        if line["level"] == "T":
            t_splitter = line["ratio"]

    return has_p, s_splitter, t_splitter


def _has_b8g(value):
    text = _normalize_text(value)

    variants = {
        "B8G",
        "BG8",
        "BGP",
        "BBP",
        "B6G",
        "B86",
        "B8C",
        "BBG",
    }

    return any(variant in text for variant in variants)


def _has_a4(value):
    return "A4" in _normalize_text(value)


def _declared_box_type(visible_type, detected_box_type):
    """
    Mapeo del tipo declarado por el ingeniero.

    Solo se usa como referencia/fallback.
    La última línea válida de splitter tiene prioridad.
    """
    combined = f"{visible_type} {detected_box_type}".upper()

    if "A4 TYPE 1" in combined:
        return "A4 1X4"

    if "A4 TYPE 2" in combined:
        return "A4 1X4"

    if "B8G TYPE 1" in combined:
        return "B8G"

    if "B8G TYPE 2" in combined:
        return "B8G 1X8"

    if "B8G TYPE 3" in combined:
        return "B8G 1X4"

    if _has_a4(combined):
        return "A4 1X4"

    if _has_b8g(combined):
        return "B8G"

    return ""


def _calculate_box_type(
    visible_type,
    detected_box_type,
    splitter_lines,
    project_name,
):
    """
    Prioridad:
    1. Última línea válida de splitter.
    2. Tipo declarado.
    3. Valor por defecto de la familia.
    """
    combined_type = f"{visible_type} {detected_box_type}".strip()

    is_b8g = _has_b8g(combined_type)
    is_a4 = _has_a4(combined_type)

    declared = _declared_box_type(
        visible_type=visible_type,
        detected_box_type=detected_box_type,
    )

    last_splitter = splitter_lines[-1] if splitter_lines else None

    calculated_box_type = ""

    if last_splitter:
        level = last_splitter["level"]
        ratio = last_splitter["ratio"]

        if is_b8g:
            if level == "S" and ratio == "1:8":
                calculated_box_type = "B8G 1X8"

            elif level == "T" and ratio == "1:4":
                calculated_box_type = "B8G 1X4"

            elif level in {"P", "S"} and ratio in {"1:2", "1:4", "1:8"}:
                calculated_box_type = "B8G"

        elif is_a4:
            if level == "T" and ratio == "1:2":
                calculated_box_type = "A4 1X2"

            elif level == "T" and ratio == "1:4":
                calculated_box_type = "A4 1X4"

            elif declared:
                calculated_box_type = declared

    if not calculated_box_type and declared:
        calculated_box_type = declared

    if not calculated_box_type:
        if is_b8g:
            calculated_box_type = "B8G"

        elif is_a4:
            calculated_box_type = "A4 1X4"

        elif project_name:
            calculated_box_type = "B8G"

        else:
            calculated_box_type = "UNKNOWN"

    calculated_box_type = calculated_box_type.upper()

    if calculated_box_type not in VALID_FINAL_BOX_TYPES:
        calculated_box_type = "UNKNOWN"

    return calculated_box_type, declared


def _calculate_c110(splitter_lines):
    """
    Regla general:

    Se cuentan todas las líneas válidas de splitter.

    Se resta la última línea únicamente cuando esa última línea corresponde
    a un splitter PC integrado en la caja.

    Splitters PC integrados:
    - S-1:8
    - T-1:4
    - T-1:2
    """
    total = len(splitter_lines)

    if total == 0:
        return 0

    last = splitter_lines[-1]

    last_key = (
        last.get("level"),
        last.get("ratio"),
    )

    if last_key in INTEGRATED_PC_SPLITTERS:
        total -= 1

    return max(0, total)


def apply_box_rules(item_data):
    """
    Aplica reglas actuales de negocio DFN.

    Fuente principal:
    - splitter_lines

    Compatibilidad:
    - has_p
    - s_splitter
    - t_splitter

    Reglas BOX TYPE:
    - Última línea válida manda.
    - Tipo declarado es fallback.
    - Familia es último fallback.

    Reglas C-110:
    - Cuenta todas las líneas válidas.
    - Resta la última si es un splitter PC integrado:
      S-1:8
      T-1:4
      T-1:2

    Observaciones:
    - Vacías en casos normales.
    - Solo se agregan cuando hay algo realmente relevante.
    - Los mensajes visibles al usuario se generan en inglés.
    """
    project_name = str(item_data.get("project_name") or "").strip()

    primary_feed = str(item_data.get("primary_feed") or "").strip()

    visible_type = _normalize_text(item_data.get("visible_type"))

    detected_box_type = _normalize_text(item_data.get("detected_box_type"))

    splitter_lines = _get_effective_splitter_lines(item_data)

    has_p, s_splitter, t_splitter = _derive_legacy_splitter_fields(splitter_lines)

    try:
        splice_count = int(item_data.get("splice_count") or 0)
    except Exception:
        splice_count = 0

    splice_count = max(0, splice_count)

    calculated_box_type, declared_box_type = _calculate_box_type(
        visible_type=visible_type,
        detected_box_type=detected_box_type,
        splitter_lines=splitter_lines,
        project_name=project_name,
    )

    c108_ug = 1
    c109_splices = splice_count
    c110_splitters = _calculate_c110(splitter_lines)

    observation_parts = []
    needs_review = False

    # ======================================================
    # Solo número de caja
    # ======================================================
    has_visible_family = bool(visible_type or detected_box_type)

    if project_name and not has_visible_family and not splitter_lines:
        needs_review = True
        observation_parts.append(
            "Box type is not visible; provisionally classified as B8G."
        )

    # ======================================================
    # Project name faltante
    # ======================================================
    if not project_name:
        needs_review = True
        observation_parts.append("Project name is incomplete or unreadable.")

    # ======================================================
    # Primary feed faltante
    # ======================================================
    if not primary_feed:
        needs_review = True
        observation_parts.append("Primary feed is missing or uncertain.")

    # ======================================================
    # Familia no identificada
    # ======================================================
    if calculated_box_type == "UNKNOWN":
        needs_review = True
        observation_parts.append("Box family could not be identified.")

    # ======================================================
    # Contradicción entre tipo declarado y última línea
    # ======================================================
    if (
        declared_box_type
        and calculated_box_type
        and declared_box_type != calculated_box_type
        and splitter_lines
    ):
        needs_review = True
        observation_parts.append(
            (
                f"Declared box type {declared_box_type} conflicts "
                f"with the last splitter line; "
                f"{calculated_box_type} was applied."
            )
        )

    # ======================================================
    # Splitter incompleto
    # ======================================================
    raw_splitter_lines = item_data.get("splitter_lines")

    if isinstance(raw_splitter_lines, list):
        valid_count = len(splitter_lines)
        raw_count = len(raw_splitter_lines)

        if raw_count > valid_count:
            needs_review = True
            observation_parts.append("Splitter information is incomplete or invalid.")

    result = {
        **item_data,
        "splitter_lines": splitter_lines,
        "has_p": has_p,
        "s_splitter": s_splitter,
        "t_splitter": t_splitter,
        "splice_count": c109_splices,
        "calculated_box_type": calculated_box_type,
        "c108_ug": c108_ug,
        "c109_splices": c109_splices,
        "c110_splitters": c110_splitters,
        "needs_review": needs_review,
        "observation": " ".join(observation_parts).strip(),
    }

    return result
