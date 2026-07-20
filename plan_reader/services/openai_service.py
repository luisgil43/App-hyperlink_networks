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
                "description": (
                    "Sheet name visible on the plan, " "for example A1, B1, C3, D2."
                ),
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
                            "description": (
                                "Complete visible box/project identifier exactly as shown in the plan. "
                                "Examples: 5005-009, 5005-009-7, 7020-001, 7020-001-1. "
                                "Preserve every visible numeric suffix after the base identifier. "
                                "Never shorten 5005-009-7 to 5005-009. "
                                "Before returning this field, inspect the complete identifier again "
                                "and verify whether a final -number suffix is visible."
                            ),
                        },
                        "primary_feed": {
                            "type": "string",
                            "description": (
                                "Primary feed value such as " "P0018, P0019, P0015."
                            ),
                        },
                        "visible_type": {
                            "type": "string",
                            "description": (
                                "Visible type text exactly as read from the plan, "
                                "such as B8G Type 2, A4 Type 1, "
                                "BGP Type 2, BBP Type 2."
                            ),
                        },
                        "detected_box_type": {
                            "type": "string",
                            "description": (
                                "Normalized box family detected from visible text. "
                                "Use B8G or A4 when possible. "
                                "Normalize BGP, BBP, BG8, B6G, B86, B8C and BBG "
                                "to B8G. Return empty string if unclear."
                            ),
                        },
                        "splitter_lines": {
                            "type": "array",
                            "description": (
                                "All valid P/S/T splitter lines visible below this box, "
                                "preserved in the same top-to-bottom vertical order "
                                "as they appear in the plan."
                            ),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "level": {
                                        "type": "string",
                                        "enum": ["P", "S", "T"],
                                        "description": (
                                            "Splitter level determined exclusively "
                                            "from the beginning of the visible line."
                                        ),
                                    },
                                    "ratio": {
                                        "type": "string",
                                        "enum": ["1:2", "1:4", "1:8"],
                                        "description": "Normalized splitter ratio.",
                                    },
                                    "raw_text": {
                                        "type": "string",
                                        "description": (
                                            "Complete visible splitter line "
                                            "exactly as read from the plan."
                                        ),
                                    },
                                },
                                "required": [
                                    "level",
                                    "ratio",
                                    "raw_text",
                                ],
                            },
                        },
                        "has_p": {
                            "type": "boolean",
                            "description": (
                                "Legacy compatibility field. True if any valid "
                                "splitter_lines entry has level P."
                            ),
                        },
                        "s_splitter": {
                            "type": "string",
                            "description": (
                                "Legacy compatibility field. Ratio of the last "
                                "valid S splitter line, such as 1:2, 1:4, 1:8. "
                                "Empty if none."
                            ),
                        },
                        "t_splitter": {
                            "type": "string",
                            "description": (
                                "Legacy compatibility field. Ratio of the last "
                                "valid T splitter line, such as 1:2, 1:4, 1:8. "
                                "Empty if none."
                            ),
                        },
                        "splice_count": {
                            "type": "integer",
                            "description": (
                                "Exact number of splices visibly indicated "
                                "near the item. Use 0 if not visible."
                            ),
                        },
                        "raw_text": {
                            "type": "string",
                            "description": (
                                "Short complete raw text block read from the plan "
                                "for this item."
                            ),
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
                        "splitter_lines",
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


def extract_plan_page_with_openai(
    image_path,
    page_number=None,
    known_sheet_name="",
):
    """
    Envía una imagen de página a OpenAI y devuelve:

    - data: dict JSON puro, serializable.
    - raw_response: texto JSON original.
    - confidence_decimal: Decimal separado para guardar en DecimalField.

    splitter_lines es la fuente principal de información de splitters.

    Los campos legacy:
    - has_p
    - s_splitter
    - t_splitter

    se mantienen por compatibilidad.
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

GENERAL RULES:
- Do not invent items.
- Read small labels near red arrows, pink/red fiber lines, splice labels,
  handholes and service locations.
- The sheet name usually appears in the title as:
  "Sheet A1"
  "Sheet B3"
  "Sheet C2"
- Return empty string for unknown text fields.
- Return 0 for unknown splice_count.
- If an item is uncertain, include it but use low confidence.
- Preserve the complete visible text associated with each box.

PROJECT NAME / PROJECT ID:

A valid Project ID must begin with this exact numeric structure:

  ####-###

This means:

- exactly four numeric digits;
- followed by a hyphen;
- followed by exactly three numeric digits.

After the required ####-### base, the Project ID may contain one or more
additional numeric suffixes joined by hyphens.

Valid examples:

  7021-005

  7020-001

  7000-016

  5005-009

  5005-009-7

  5000-039-1

  5000-039-1-1

  5000-039-1-3

  7020-001-1

CRITICAL PROJECT ID RULE:

- Read the COMPLETE visible Project ID.

- Preserve every numeric suffix visibly connected to the Project ID
  by a hyphen.

- Never shorten:

  5000-039-1-3

  into:

  5000-039-1

- Never shorten:

  5000-039-1-1

  into:

  5000-039-1

- Never shorten:

  5005-009-7

  into:

  5005-009

- Before returning project_name, inspect the complete identifier a second time.

- A valid project_name must contain the numeric ####-### structure.

- If no valid numeric ####-### identifier is visible for an item,
  return an empty string for project_name.

INVALID PROJECT ID EXAMPLES:

The following are NOT Project IDs:

  0913RA_P0043:1-4;

  0913RA,P0045;1-3;

  0913RA,P0045;3;

  0913RA,P0045,S3:2;

  P0045

  P0043:1-4

  S3:T1

  1-12XD

  14-24XD

  16-24XD

These are cable, fiber, route, feed or splitter annotations.

Never return them as project_name.

CRITICAL SPATIAL RULE:

- A splice quantity shown above a box is not part of the Project ID.

- A cable annotation shown in red or orange near an arrow is not a Project ID.

- A number belongs to the Project ID only when it is visibly connected
  to the valid ####-### identifier by a hyphen.

Example:

Visible text:

  1 Splice
  5000-039-1-3; A4 Type 2
  T-1:4(P0045,S3:T2)

Return:

  project_name = "5000-039-1-3"
  splice_count = 1

Example:

Visible text:

  3 Splices
  5000-039-1-1; A4 Type 1
  S-1:2(P0045:S3)
  T-1:4(P0045,S3:T1)

Return:

  project_name = "5000-039-1-1"
  splice_count = 3

Example:

Visible text:

  0913RA_P0043:1-4;
  16-24XD

Return no item from this annotation alone.

Do not return:

  project_name = "0913RA_P0043:1-4;"

The fields project_name and raw_text must preserve the complete valid
numeric Project ID when it is clearly visible.

Do not invent a suffix that is not visible.

PRIMARY FEED:

- Primary feed always follows the pattern:

  P + exactly 4 digits

Examples:

  P0018

  P0019

  P0049

  P0053

- Always look carefully for a visible P#### associated with the box.

- The Primary Feed may appear:

  - directly below or near the box number;

  - inside the visible text block for the box;

  - inside splitter text such as:

    P-1:8(P0049)

    S-1:2(P0053,S5:T1)

- If a valid P#### is visible anywhere in the text block associated with the box,

  return that value as primary_feed.

- Return only the P#### value, for example:

  P0049

- Do not return:

  P-1:8

  S5

  T1

- If there is no valid P#### associated with the box, return an empty string.

BOX FAMILY:
- Types may look like:
  A4 Type 1
  A4 Type 2
  B8G Type 1
  B8G Type 2
  B8G Type 3
  BGP Type 2
  BBP Type 2

- Normalize these common OCR variants to detected_box_type="B8G":
  B8G
  BGP
  BBP
  BG8
  B6G
  B86
  B8C
  BBG

- For A4 text:
  detected_box_type="A4"

- If the family is not visible or is unclear:
  detected_box_type=""

- visible_type must preserve the visible type text as closely as possible.

SPLITTER LINES:

splitter_lines is the PRIMARY source for splitter information.

A line is a valid splitter line only when, after optional leading spaces,
the visible line itself begins with P, S or T followed by a valid splitter ratio.

Valid canonical patterns:

P-1:2
P-1:4
P-1:8

S-1:2
S-1:4
S-1:8

T-1:2
T-1:4
T-1:8

Also accept reasonable visible variations such as:

P 1:8
P-1:8
P–1:8
P—1:8
P-1X8
P-1x8

S 1:2
S-1X4
S-1x8

T 1:2
T-1X4
T-1x8

CRITICAL SPLITTER RULE:
Determine the splitter level exclusively from the BEGINNING of the line.

Do not search for P, S or T inside parentheses to determine the level.

Example:

S-1:2(P0053,S5:T1)

This must be interpreted as:

level = "S"
ratio = "1:2"

The T inside:
(P0053,S5:T1)

does NOT make this a T splitter.

Another example:

P-1:8(P0049)

must be:

level = "P"
ratio = "1:8"

Another example:

T-1:4(P0049,S3:T1)

must be:

level = "T"
ratio = "1:4"

SPLITTER ORDER:
- Preserve every valid splitter line.
- Preserve splitter_lines in the same top-to-bottom vertical order
  in which the lines appear below the box.
- Do not combine multiple S lines into one entry.
- Do not combine multiple P lines into one entry.
- Do not combine multiple T lines into one entry.
- Each valid visible splitter line must be a separate splitter_lines entry.

Each splitter_lines entry must contain:

level:
- P
- S
- T

ratio:
- 1:2
- 1:4
- 1:8

raw_text:
- The complete visible splitter line as read from the plan.

Example:

Visible text:

P-1:8(P0049)
S-1:2(P0049:S3)
T-1:4(P0049,S3:T1)

Return:

splitter_lines = [
    {{
        "level": "P",
        "ratio": "1:8",
        "raw_text": "P-1:8(P0049)"
    }},
    {{
        "level": "S",
        "ratio": "1:2",
        "raw_text": "S-1:2(P0049:S3)"
    }},
    {{
        "level": "T",
        "ratio": "1:4",
        "raw_text": "T-1:4(P0049,S3:T1)"
    }}
]

LEGACY COMPATIBILITY FIELDS:

The fields:
- has_p
- s_splitter
- t_splitter

must still be returned for compatibility with the existing Django system.

Derive them from splitter_lines.

Rules:

has_p:
- true if any valid splitter_lines entry has level P.
- false otherwise.

s_splitter:
- ratio of the LAST valid S line in splitter_lines.
- empty string if no S line exists.

t_splitter:
- ratio of the LAST valid T line in splitter_lines.
- empty string if no T line exists.

Do not use:
has_p
s_splitter
t_splitter

as the primary splitter source.

splitter_lines is the primary splitter source.

SPLICE COUNT:
- Splice counts may appear as:
  "1 Splice"
  "2 Splices"
  "10 Splices"
- splice_count must be exactly the visible splice quantity associated
  with the box.
- Do not infer splice_count from P/S/T splitter lines.
- Do not add or subtract splice_count based on splitters.
- Do not append splice_count to project_name.
- If no splice quantity is visible, return 0.

RAW TEXT:
- raw_text should contain the short complete visible text block associated
  with the box.
- Include when visible:
  project number
  visible type
  splitter lines
  splice text
- Preserve enough text so the backend can defensively verify the extraction.

CONFIDENCE:
- Use lower confidence when:
  project name is incomplete
  primary feed is unclear
  family is unclear
  splitter text is partially unreadable
  splice quantity is uncertain

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
