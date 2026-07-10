import re


def _normalize_text(value):
    return (str(value or "").strip()).upper()


def _normalize_splitter(value):
    """
    Normaliza valores tipo:
    S-1:8, 1:8, 1-8, 1X8, 1x8
    """
    text = _normalize_text(value)
    if not text:
        return ""

    text = text.replace(" ", "")
    text = text.replace("X", ":")
    text = text.replace("-", ":")

    match = re.search(r"1[:\-]?([248])", text)
    if match:
        return f"1:{match.group(1)}"

    return text


def _has_b8g(value):
    text = _normalize_text(value)

    return (
        "B8G" in text
        or "BG8" in text
        or "BGP" in text
        or "BBP" in text
        or "BGP TYPE" in text
        or "BBP TYPE" in text
    )


def _has_a4(value):
    text = _normalize_text(value)
    return "A4" in text


def apply_box_rules(item_data):
    """
    Aplica reglas de negocio Hyperlink.

    Reglas principales:
    - Si solo existe el número de caja y no hay P/S/T/tipo visible => B8G sola.
    - BGP / BBP se interpretan como B8G.
    - B8G + P => B8G sola.
    - B8G + P + S 1:2/1:4 => B8G sola.
    - B8G sin P + S 1:2/1:4 => B8G.
    - B8G + T => B8G 1x4.
    - B8G + S 1:8 => B8G 1x8.
    - A4 => A4 1x4, excepto T 1:2 => A4 1x2.
    - T no cobra.
    - P cobra siempre.
    - S cobra siempre excepto B8G con S 1:8.
    """

    project_name = str(item_data.get("project_name") or "").strip()

    visible_type = _normalize_text(item_data.get("visible_type"))
    detected_box_type = _normalize_text(item_data.get("detected_box_type"))

    combined_type = f"{visible_type} {detected_box_type}".strip()

    has_p = bool(item_data.get("has_p"))
    s_splitter = _normalize_splitter(item_data.get("s_splitter"))
    t_splitter = _normalize_splitter(item_data.get("t_splitter"))

    try:
        splice_count = int(item_data.get("splice_count") or 0)
    except Exception:
        splice_count = 0

    calculated_box_type = ""
    observation_parts = []

    is_b8g = _has_b8g(combined_type)
    is_a4 = _has_a4(combined_type)

    # ======================================================
    # Regla nueva:
    # Solo número de caja, sin tipo, sin P/S/T => B8G sola
    # ======================================================
    only_box_number = (
        bool(project_name)
        and not visible_type
        and not detected_box_type
        and not has_p
        and not s_splitter
        and not t_splitter
    )

    if only_box_number:
        calculated_box_type = "B8G"
        is_b8g = True
        observation_parts.append(
            "Only box number detected with no P/S/T/type. Classified as standard B8G."
        )

    elif is_b8g:
        if "BGP" in combined_type or "BBP" in combined_type:
            observation_parts.append(
                "BGP/BBP text detected. Classified as B8G because BGP/BBP is not a valid final box type."
            )

        if t_splitter:
            calculated_box_type = "B8G 1x4"
            observation_parts.append("B8G with T splitter classified as B8G 1x4.")

        elif s_splitter == "1:8":
            calculated_box_type = "B8G 1x8"
            observation_parts.append("B8G with S 1:8 classified as B8G 1x8.")

        elif s_splitter in ["1:2", "1:4"] and not has_p:
            calculated_box_type = "B8G"
            observation_parts.append(
                "B8G/BGP with only S 1:2/1:4 and no P classified as standard B8G."
            )

        else:
            calculated_box_type = "B8G"
            observation_parts.append("B8G classified as standard B8G.")

    elif is_a4:
        if t_splitter == "1:2":
            calculated_box_type = "A4 1x2"
            observation_parts.append("A4 with T 1:2 classified as A4 1x2.")
        else:
            calculated_box_type = "A4 1x4"
            observation_parts.append("A4 classified as A4 1x4.")

    else:
        calculated_box_type = detected_box_type or visible_type or "UNKNOWN"
        observation_parts.append("Box type could not be classified confidently.")

    # ======================================================
    # Cobros
    # ======================================================
    c108_ug = 1
    c109_splices = max(0, splice_count)

    c110_splitters = 0

    # P cobra siempre
    if has_p:
        c110_splitters += 1

    # S cobra siempre excepto B8G con S 1:8
    if s_splitter:
        if is_b8g and s_splitter == "1:8":
            observation_parts.append("S 1:8 in B8G is not billed.")
        else:
            c110_splitters += 1

    # T nunca cobra
    if t_splitter:
        observation_parts.append(
            "T splitter is not billed because it goes inside the box."
        )

    needs_review = False

    if not project_name:
        needs_review = True
        observation_parts.append("Missing project name.")

    if not calculated_box_type or calculated_box_type == "UNKNOWN":
        needs_review = True

    result = {
        **item_data,
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
