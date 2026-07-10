import base64
import json
import os
from decimal import Decimal

from django.conf import settings
from openai import OpenAI

PLAN_READER_JSON_SCHEMA = {
    "name": "plan_reader_page_extraction",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sheet_name": {
                "type": "string",
                "description": "Sheet name visible on the plan, for example A1, B1, C3, D2.",
            },
            "confidence": {
                "type": "number",
                "description": "Overall extraction confidence from 0 to 100.",
            },
            "items": {
                "type": "array",
                "description": "Detected boxes or project items in this page.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Box/project name such as 7021-005, 7020-001, 7000-016.",
                        },
                        "primary_feed": {
                            "type": "string",
                            "description": "Primary feed value such as P0018, P0019, P0015.",
                        },
                        "visible_type": {
                            "type": "string",
                            "description": "Visible type text from plan such as B8G Type 2, A4 Type 1, BGP Type 2, BBP Type 2.",
                        },
                        "detected_box_type": {
                            "type": "string",
                            "description": "Normalized box family detected from text. Use only B8G or A4 when possible. If the plan says BGP or BBP, return B8G.",
                        },
                        "has_p": {
                            "type": "boolean",
                            "description": "True if a P splitter/feed is visible for this item.",
                        },
                        "s_splitter": {
                            "type": "string",
                            "description": "S splitter size if visible, such as 1:2, 1:4, 1:8. Empty if none.",
                        },
                        "t_splitter": {
                            "type": "string",
                            "description": "T splitter size if visible, such as 1:2, 1:4. Empty if none.",
                        },
                        "splice_count": {
                            "type": "integer",
                            "description": "Number of splices near the item. Use 0 if not visible.",
                        },
                        "raw_text": {
                            "type": "string",
                            "description": "Short raw text block read from the plan for this item.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Item confidence from 0 to 100.",
                        },
                    },
                    "required": [
                        "project_name",
                        "primary_feed",
                        "visible_type",
                        "detected_box_type",
                        "has_p",
                        "s_splitter",
                        "t_splitter",
                        "splice_count",
                        "raw_text",
                        "confidence",
                    ],
                },
            },
        },
        "required": [
            "sheet_name",
            "confidence",
            "items",
        ],
    },
    "strict": True,
}


def image_to_data_url(image_path):
    with open(image_path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("utf-8")

    return f"data:image/png;base64,{encoded}"


def safe_decimal(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


def get_response_text(response):
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    try:
        chunks = []
        for item in response.output:
            for content in item.content:
                if hasattr(content, "text"):
                    chunks.append(content.text)
        return "\n".join(chunks)
    except Exception:
        return ""


def extract_plan_page_with_openai(image_path, page_number=None, known_sheet_name=""):
    """
    Envía una imagen de página a OpenAI y devuelve:
    - data: dict JSON puro, serializable.
    - raw_response: texto JSON original.
    - confidence_decimal: Decimal separado para guardar en campo DecimalField.
    """
    api_key = getattr(settings, "OPENAI_API_KEY", None) or os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    model = (
        getattr(settings, "PLAN_READER_MODEL", None)
        or os.getenv("PLAN_READER_MODEL")
        or "gpt-5.2"
    )

    client = OpenAI(api_key=api_key)
    image_data_url = image_to_data_url(image_path)

    prompt = f"""
You are reading a telecom DFN fiber construction plan page.

Extract only visible project/box items from the plan page.

Important extraction rules:
- Do not invent items.
- Read small labels near red arrows, pink/red fiber lines, splice labels, handholes, and service locations.
- The sheet name usually appears in the title as "Sheet A1", "Sheet B3", etc.
- Project names often look like 7021-005, 7020-001, 7000-016, 7022-007.
- Primary feed values often look like P0018, P0019, P0015, P0017.
- Types may look like A4 Type 1, A4 Type 2, B8G Type 2, B8G Type 3, BGP Type 2, BBP Type 2.
- BGP and BBP are not valid final box families in this system. If you see BGP, BBP, BGP Type, or BBP Type, treat it as B8G.
- For detected_box_type, return B8G for B8G, BGP, BBP, BG8, or similar text.
- For detected_box_type, return A4 for A4 text.
- Splitters may appear as P-1:8, S-1:8, S-1:4, S-1:2, T-1:4, T-1:2.
- Splice counts may appear as "1 Splice", "2 Splices", "10 Splices".
- Return empty string for unknown text fields.
- Return 0 for unknown splice_count.
- If an item is too uncertain, include it but set low confidence.

Context:
- Page number: {page_number or ""}
- Sheet detected by PDF text, if any: {known_sheet_name or ""}
"""

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    },
                    {
                        "type": "input_image",
                        "image_url": image_data_url,
                    },
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": PLAN_READER_JSON_SCHEMA["name"],
                "schema": PLAN_READER_JSON_SCHEMA["schema"],
                "strict": True,
            }
        },
    )

    response_text = get_response_text(response)

    if not response_text:
        raise RuntimeError("OpenAI returned an empty response.")

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"OpenAI returned invalid JSON: {response_text[:500]}"
        ) from exc

    confidence_decimal = safe_decimal(data.get("confidence"))

    return data, response_text, confidence_decimal
