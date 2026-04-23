import io
import os
import re
import tempfile
from datetime import timedelta

import pdfplumber
from django.core.files.base import ContentFile, File
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .models import (STEP_ORDER, DocumentKey, OmbordingAuditLog,
                     OmbordingDocument, OmbordingEmailLog,
                     OmbordingFieldReview, OmbordingStatus, OmbordingStep,
                     OmbordingTempUpload, ReviewStatus)

FIELD_GROUPS = [
    (OmbordingStep.INITIAL, "first_name", "First Name"),
    (OmbordingStep.INITIAL, "last_name", "Last Name"),
    (OmbordingStep.INITIAL, "email", "Email"),
    (OmbordingStep.INITIAL, "position", "Position"),
    (OmbordingStep.PERSONAL, "date_of_birth", "Date of Birth"),
    (OmbordingStep.PERSONAL, "nationality", "Nationality"),
    (OmbordingStep.PERSONAL, "address", "Address"),
    (OmbordingStep.PERSONAL, "phone_number", "Phone Number"),
    (OmbordingStep.PERSONAL, "emergency_contact_name", "Emergency Contact Name"),
    (OmbordingStep.PERSONAL, "emergency_contact_phone", "Emergency Contact Phone"),
    (
        OmbordingStep.PERSONAL,
        "emergency_contact_relationship",
        "Emergency Contact Relationship",
    ),
    (OmbordingStep.IDENTITY, "has_ssn", "Has Social Security"),
    (OmbordingStep.IDENTITY, "ssn_number", "Social Security Number"),
    (OmbordingStep.IDENTITY, "passport_number", "Passport Number"),
    (OmbordingStep.IDENTITY, "has_work_permit", "Has Work Permit"),
    (OmbordingStep.IDENTITY, "has_driver_license", "Has Driver License"),
    (OmbordingStep.SIGNATURE, "business_name", "Business Name"),
    (OmbordingStep.SIGNATURE, "w9_tax_classification", "W-9 Tax Classification"),
    (OmbordingStep.BANKING, "bank_name", "Bank Name"),
    (OmbordingStep.BANKING, "account_type", "Account Type"),
    (OmbordingStep.BANKING, "routing_number", "Routing Number"),
    (OmbordingStep.BANKING, "account_number", "Account Number"),
]

DOCUMENT_FIELD_MAP = [
    (
        "contractor_agreement_base",
        DocumentKey.CONTRACTOR_AGREEMENT_BASE,
        "Independent Contractor Agreement",
    ),
    ("exhibit_base", DocumentKey.EXHIBIT_BASE, "Exhibit"),
    ("w9_base", DocumentKey.W9_BASE, "W-9 Base"),
    ("passport_front", DocumentKey.PASSPORT_FRONT, "Passport Front"),
    ("passport_back", DocumentKey.PASSPORT_BACK, "Passport Back"),
    ("address_proof", DocumentKey.ADDRESS_PROOF, "Address Proof"),
    ("ssn_front", DocumentKey.SSN_FRONT, "Social Security Front"),
    ("ssn_back", DocumentKey.SSN_BACK, "Social Security Back"),
    ("work_permit_front", DocumentKey.WORK_PERMIT_FRONT, "Work Permit Front"),
    ("work_permit_back", DocumentKey.WORK_PERMIT_BACK, "Work Permit Back"),
    ("driver_license_front", DocumentKey.DRIVER_LICENSE_FRONT, "Driver License Front"),
    ("driver_license_back", DocumentKey.DRIVER_LICENSE_BACK, "Driver License Back"),
]

FILLED_DOCUMENT_MAP = {
    DocumentKey.CONTRACTOR_AGREEMENT_BASE: (
        DocumentKey.CONTRACTOR_AGREEMENT_FILLED,
        "Independent Contractor Agreement Filled",
    ),
    DocumentKey.EXHIBIT_BASE: (
        DocumentKey.EXHIBIT_FILLED,
        "Exhibit Filled",
    ),
    DocumentKey.W9_BASE: (
        DocumentKey.W9_FILLED,
        "W-9 Filled",
    ),
}


def _build_w9_address_lines(payload):
    line5 = " ".join(
        x
        for x in [payload.get("street_address", ""), payload.get("apt_suite", "")]
        if x
    ).strip()

    city = payload.get("city", "").strip()
    state = payload.get("state", "").strip()
    zip_code = payload.get("zip_code", "").strip()

    line6_left = ", ".join(x for x in [city, state] if x).strip()
    line6 = f"{line6_left} {zip_code}".strip() if zip_code else line6_left

    return line5, line6


def _draw_ssn_in_boxes(
    c, ssn_value, start_x, y, box_step=18.6, font_name="Helvetica", font_size=11
):
    digits = "".join(ch for ch in (ssn_value or "") if ch.isdigit())
    if not digits:
        return

    c.setFont(font_name, font_size)

    # Formato esperado: 9 dígitos
    for idx, ch in enumerate(digits[:9]):
        # salta visualmente los guiones del formato XXX-XX-XXXX
        if idx >= 5:
            extra = 2
        elif idx >= 3:
            extra = 1
        else:
            extra = 0

        x = start_x + ((idx + extra) * box_step)
        c.drawCentredString(x, y, ch)


def _draw_ein_in_boxes(
    c, ein_value, start_x, y, box_step=14.9, font_name="Helvetica", font_size=11
):
    digits = "".join(ch for ch in (ein_value or "") if ch.isdigit())
    if not digits:
        return

    c.setFont(font_name, font_size)

    # EIN esperado: 9 dígitos, formato XX-XXXXXXX
    for idx, ch in enumerate(digits[:9]):
        extra = 1 if idx >= 2 else 0
        x = start_x + ((idx + extra) * box_step)
        c.drawCentredString(x, y, ch)


def _split_address_for_w9(address_text):
    raw = (address_text or "").strip()
    if not raw:
        return "", ""

    lines = [x.strip() for x in raw.replace("\r", "\n").split("\n") if x.strip()]
    if len(lines) >= 2:
        return lines[0], " ".join(lines[1:])

    parts = [x.strip() for x in raw.split(",") if x.strip()]
    if len(parts) >= 2:
        return parts[0], ", ".join(parts[1:])

    return raw, ""


def _document_by_key(ombording, document_key):
    return (
        OmbordingDocument.objects.filter(
            ombording=ombording,
            document_key=document_key,
        )
        .order_by("-id")
        .first()
    )


def _safe_text(value):
    return (value or "").strip()


def _bool_to_yes_no(value):
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return ""


def _build_pdf_payload(ombording):
    return {
        "full_name": _safe_text(ombording.full_name),
        "first_name": _safe_text(ombording.first_name),
        "last_name": _safe_text(ombording.last_name),
        "email": _safe_text(ombording.email),
        "initials": _safe_text(ombording.signature_initials_value()),
        "signature_name": _safe_text(ombording.signature_full_name()),
        "sign_date": _safe_text(ombording.signing_date_value()),
        "date_of_birth": (
            ombording.date_of_birth.strftime("%m/%d/%Y")
            if ombording.date_of_birth
            else ""
        ),
        "age": str(ombording.age or ""),
        "nationality": _safe_text(ombording.nationality),
        "street_address": _safe_text(getattr(ombording, "street_address", "")),
        "apt_suite": _safe_text(getattr(ombording, "apt_suite", "")),
        "city": _safe_text(getattr(ombording, "city", "")),
        "state": _safe_text(getattr(ombording, "state", "")),
        "zip_code": _safe_text(getattr(ombording, "zip_code", "")),
        "address": _safe_text(getattr(ombording, "full_address", "")),
        "phone_number": _safe_text(ombording.phone_number),
        "emergency_contact_name": _safe_text(ombording.emergency_contact_name),
        "emergency_contact_phone": _safe_text(ombording.emergency_contact_phone),
        "emergency_contact_relationship": _safe_text(
            ombording.emergency_contact_relationship
        ),
        "has_ssn": _bool_to_yes_no(ombording.has_ssn),
        "ssn_number": _safe_text(ombording.ssn_number),
        "passport_number": _safe_text(ombording.passport_number),
        "ein_number": _safe_text(getattr(ombording, "ein_number", "")),
        "has_work_permit": _bool_to_yes_no(ombording.has_work_permit),
        "has_driver_license": _bool_to_yes_no(ombording.has_driver_license),
        "business_name": _safe_text(ombording.business_name),
        "w9_tax_classification": _safe_text(ombording.w9_tax_classification),
        "w9_llc_classification": _safe_text(
            getattr(ombording, "w9_llc_classification", "")
        ),
        "w9_other_text": _safe_text(getattr(ombording, "w9_other_text", "")),
        "w9_part3b_required": bool(getattr(ombording, "w9_part3b_required", False)),
        "w9_exempt_payee_code": _safe_text(
            getattr(ombording, "w9_exempt_payee_code", "")
        ),
        "w9_fatca_exemption_code": _safe_text(
            getattr(ombording, "w9_fatca_exemption_code", "")
        ),
        "w9_account_numbers": _safe_text(getattr(ombording, "w9_account_numbers", "")),
        "bank_name": _safe_text(ombording.bank_name),
        "account_type": _safe_text(ombording.account_type),
        "routing_number": _safe_text(ombording.routing_number),
        "account_number": _safe_text(ombording.account_number),
        "position_name": _safe_text(
            ombording.position.name if ombording.position_id else ""
        ),
    }


def _create_overlay_for_page(page_width, page_height, draw_callback):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(float(page_width), float(page_height)))
    draw_callback(c, float(page_width), float(page_height))
    c.save()
    buffer.seek(0)
    return PdfReader(buffer).pages[0]


def _draw_signature_image(c, image_field, x, y, width=140, height=40):
    if not image_field:
        return
    try:
        image_field.open("rb")
        img = ImageReader(image_field)
        c.drawImage(
            img,
            x,
            y,
            width=width,
            height=height,
            preserveAspectRatio=True,
            mask="auto",
        )
    finally:
        try:
            image_field.close()
        except Exception:
            pass


def _normalize_anchor_text(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _extract_words_by_page(base_doc):
    base_doc.file.open("rb")
    try:
        with pdfplumber.open(base_doc.file) as pdf:
            pages_words = []
            for page in pdf.pages:
                words = (
                    page.extract_words(
                        use_text_flow=True,
                        keep_blank_chars=False,
                        x_tolerance=1,
                        y_tolerance=3,
                    )
                    or []
                )
                pages_words.append(words)
            return pages_words
    finally:
        try:
            base_doc.file.close()
        except Exception:
            pass


def _find_words_containing(words, needle, min_top=None):
    needle_norm = _normalize_anchor_text(needle)
    results = []

    for word in words:
        text_norm = _normalize_anchor_text(word.get("text", ""))
        if needle_norm and needle_norm in text_norm:
            top_val = float(word.get("top", 0))
            if min_top is not None and top_val < float(min_top):
                continue
            results.append(word)

    return results


def _find_first_word_containing(words, needle, min_top=None):
    items = _find_words_containing(words, needle, min_top=min_top)
    return items[0] if items else None


def _find_last_word_containing(words, needle, min_top=None):
    items = _find_words_containing(words, needle, min_top=min_top)
    return items[-1] if items else None


def _canvas_y_from_word(page_height, word, baseline_offset=0):
    return float(page_height) - float(word["bottom"]) + float(baseline_offset)


def _draw_text_after_word(
    c,
    word,
    page_height,
    text,
    dx=6,
    dy=4,
    font_name="Helvetica",
    font_size=10,
):
    if not word or not text:
        return

    c.setFont(font_name, font_size)
    x = float(word["x1"]) + float(dx)
    y = _canvas_y_from_word(page_height, word, dy)
    c.drawString(x, y, str(text))


def _draw_signature_after_word(
    c,
    word,
    page_height,
    image_field=None,
    fallback_text="",
    dx=8,
    dy=14,
    width=150,
    height=34,
):
    if not word:
        return

    x = float(word["x1"]) + float(dx)
    baseline_y = _canvas_y_from_word(page_height, word, 4)

    if image_field:
        image_y = baseline_y - (float(height) * 0.55)
        _draw_signature_image(
            c,
            image_field,
            x,
            image_y,
            width=width,
            height=height,
        )
    elif fallback_text:
        c.setFont("Helvetica", 10)
        c.drawString(x, baseline_y, fallback_text)


def _draw_initial_anchor(c, word, page_height, initials):
    if not word or not initials:
        return

    c.setFont("Helvetica", 10)

    word_x0 = float(word["x0"])
    word_x1 = float(word["x1"])
    word_width = word_x1 - word_x0

    # Mete las iniciales sobre la línea, no después de la línea
    x = word_x1 - min(34, max(18, word_width * 0.28))
    y = _canvas_y_from_word(page_height, word, 5)

    c.drawString(x, y, initials)


def _find_top_initial_anchor(words):
    initial_words = _find_words_containing(words, "Initial")
    if not initial_words:
        return None
    return sorted(initial_words, key=lambda w: float(w.get("top", 0)))[0]


def _find_bottom_initial_anchor(words, min_top=None):
    initial_words = _find_words_containing(words, "Initial", min_top=min_top)
    if not initial_words:
        return None
    return sorted(initial_words, key=lambda w: float(w.get("top", 0)))[-1]


def _find_agreement_last_page_labels(words):
    subcontractor_anchor = _find_last_word_containing(words, "Subcontractor")
    min_top = float(subcontractor_anchor["top"]) if subcontractor_anchor else None

    name_word = _find_first_word_containing(words, "Name", min_top=min_top)
    signature_word = _find_first_word_containing(words, "Signature", min_top=min_top)
    date_word = _find_first_word_containing(words, "Date", min_top=min_top)
    initial_word = _find_bottom_initial_anchor(words, min_top=min_top)

    return {
        "section_anchor": subcontractor_anchor,
        "name": name_word,
        "signature": signature_word,
        "date": date_word,
        "initial": initial_word,
    }


def _find_exhibit_last_page_labels(words):
    acceptance_anchor = _find_first_word_containing(words, "Acceptance")
    min_top = float(acceptance_anchor["top"]) if acceptance_anchor else None

    name_word = _find_first_word_containing(words, "Name", min_top=min_top)
    date_word = _find_first_word_containing(words, "Date", min_top=min_top)
    signature_word = _find_first_word_containing(words, "Signature", min_top=min_top)

    return {
        "section_anchor": acceptance_anchor,
        "name": name_word,
        "date": date_word,
        "signature": signature_word,
    }


def _merge_overlay(base_doc, overlay_pages):
    base_doc.file.open("rb")
    try:
        reader = PdfReader(base_doc.file)
        writer = PdfWriter()

        total_pages = len(reader.pages)
        for idx, page in enumerate(reader.pages):
            page_obj = page
            if idx < len(overlay_pages) and overlay_pages[idx] is not None:
                page_obj.merge_page(overlay_pages[idx])
            writer.add_page(page_obj)

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return output, total_pages
    finally:
        try:
            base_doc.file.close()
        except Exception:
            pass


def _upsert_generated_document(ombording, user, document_key, label, bytes_buffer):
    filename = f"{document_key}.pdf"
    content = ContentFile(bytes_buffer.getvalue(), name=filename)

    existing = _document_by_key(ombording, document_key)
    if existing:
        existing.file.save(filename, content, save=False)
        existing.label = label
        existing.uploaded_by = user
        if existing.review_status != ReviewStatus.APPROVED:
            existing.review_status = ReviewStatus.COMPLETED
        existing.review_comment = ""
        existing.reviewed_by = None
        existing.reviewed_at = None
        existing.save()
        return existing

    return OmbordingDocument.objects.create(
        ombording=ombording,
        document_key=document_key,
        label=label,
        uploaded_by=user,
        file=content,
        review_status=ReviewStatus.COMPLETED,
    )


def _agreement_overlay_pages(ombording, total_pages, page_width, page_height):
    payload = _build_pdf_payload(ombording)
    signature_obj = getattr(ombording, "signature", None)
    signature_file = getattr(signature_obj, "signature_file", None)

    base_doc = _document_by_key(ombording, DocumentKey.CONTRACTOR_AGREEMENT_BASE)
    words_by_page = _extract_words_by_page(base_doc) if base_doc else []

    pages = []
    for page_index in range(total_pages):
        page_words = (
            words_by_page[page_index] if page_index < len(words_by_page) else []
        )

        def draw(c, w, h, idx=page_index, words=page_words):
            c.setFont("Helvetica", 10)

            # Initial superior de cada página
            top_initial_anchor = _find_top_initial_anchor(words)
            if payload["initials"]:
                _draw_initial_anchor(c, top_initial_anchor, h, payload["initials"])

            # Última página: bloque Subcontractor
            if idx == total_pages - 1:
                labels = _find_agreement_last_page_labels(words)

                _draw_text_after_word(
                    c,
                    labels["name"],
                    h,
                    payload["signature_name"],
                    dx=6,
                    dy=6,
                )
                _draw_text_after_word(
                    c,
                    labels["date"],
                    h,
                    payload["sign_date"],
                    dx=6,
                    dy=6,
                )
                _draw_signature_after_word(
                    c,
                    labels["signature"],
                    h,
                    image_field=(
                        signature_file
                        if ombording.should_generate_signed_documents()
                        else None
                    ),
                    fallback_text=payload["signature_name"],
                    dx=8,
                    dy=8,
                    width=150,
                    height=34,
                )

                # Initial final dentro del bloque de cierre
                bottom_initial_anchor = labels["initial"]
                if payload["initials"]:
                    _draw_initial_anchor(
                        c, bottom_initial_anchor, h, payload["initials"]
                    )

        pages.append(_create_overlay_for_page(page_width, page_height, draw))
    return pages


def _exhibit_overlay_pages(ombording, total_pages, page_width, page_height):
    payload = _build_pdf_payload(ombording)
    signature_obj = getattr(ombording, "signature", None)
    signature_file = getattr(signature_obj, "signature_file", None)

    base_doc = _document_by_key(ombording, DocumentKey.EXHIBIT_BASE)
    words_by_page = _extract_words_by_page(base_doc) if base_doc else []

    pages = []
    for page_index in range(total_pages):
        page_words = (
            words_by_page[page_index] if page_index < len(words_by_page) else []
        )

        def draw(c, w, h, idx=page_index, words=page_words):
            c.setFont("Helvetica", 10)

            # Initial superior de cada página
            top_initial_anchor = _find_top_initial_anchor(words)
            if payload["initials"]:
                _draw_initial_anchor(c, top_initial_anchor, h, payload["initials"])

            # Última página: bloque Acceptance
            if idx == total_pages - 1:
                labels = _find_exhibit_last_page_labels(words)

                _draw_text_after_word(
                    c,
                    labels["name"],
                    h,
                    payload["signature_name"],
                    dx=6,
                    dy=6,
                )
                _draw_text_after_word(
                    c,
                    labels["date"],
                    h,
                    payload["sign_date"],
                    dx=6,
                    dy=6,
                )
                _draw_signature_after_word(
                    c,
                    labels["signature"],
                    h,
                    image_field=(
                        signature_file
                        if ombording.should_generate_signed_documents()
                        else None
                    ),
                    fallback_text=payload["signature_name"],
                    dx=8,
                    dy=8,
                    width=150,
                    height=34,
                )

        pages.append(_create_overlay_for_page(page_width, page_height, draw))
    return pages


def _w9_overlay_pages(ombording, total_pages, page_width, page_height):
    payload = _build_pdf_payload(ombording)
    signature_obj = getattr(ombording, "signature", None)
    signature_file = getattr(signature_obj, "signature_file", None)

    pages = []

    def draw(c, w, h):
        c.setFont("Helvetica", 11)

        line1_name = payload["full_name"]
        line2_business = payload["business_name"]
        line5_address, line6_city_state_zip = _build_w9_address_lines(payload)

        def mark_box(x, y, size=11):
            c.setFont("Helvetica-Bold", size)
            c.drawCentredString(x, y, "X")
            c.setFont("Helvetica", 11)

        if line1_name:
            c.drawString(70, 663, line1_name)

        if line2_business:
            c.drawString(70, 640, line2_business)

        tax_class = (payload["w9_tax_classification"] or "").strip()
        llc_class = (payload["w9_llc_classification"] or "").strip().upper()
        other_text = payload["w9_other_text"]

        if tax_class in ("", "individual"):
            mark_box(77, 604)
        elif tax_class == "c_corp":
            mark_box(184, 604)
        elif tax_class == "s_corp":
            mark_box(256, 604)
        elif tax_class == "partnership":
            mark_box(328, 604)
        elif tax_class == "trust_estate":
            mark_box(401, 604)
        elif tax_class == "llc":
            mark_box(77, 579)
            if llc_class in ("C", "S", "P"):
                c.drawString(425, 579, llc_class)
        elif tax_class == "other":
            mark_box(77, 554)
            if other_text:
                c.drawString(165, 555, other_text)

        if payload["w9_part3b_required"]:
            mark_box(445, 521, size=10)

        if payload["w9_exempt_payee_code"]:
            c.drawString(543, 591, payload["w9_exempt_payee_code"])

        if payload["w9_fatca_exemption_code"]:
            c.drawString(500, 554, payload["w9_fatca_exemption_code"])

        if line5_address:
            c.drawString(70, 496, line5_address)

        if line6_city_state_zip:
            c.drawString(70, 470, line6_city_state_zip)

        if payload["w9_account_numbers"]:
            c.drawString(70, 446, payload["w9_account_numbers"])

        if payload["ssn_number"]:
            _draw_ssn_in_boxes(
                c,
                payload["ssn_number"],
                start_x=422,
                y=403,
                box_step=14.9,
                font_name="Helvetica",
                font_size=11,
            )

        if payload["ein_number"]:
            _draw_ein_in_boxes(
                c,
                payload["ein_number"],
                start_x=422,
                y=360,
                box_step=14.9,
                font_name="Helvetica",
                font_size=11,
            )

        if ombording.should_generate_signed_documents() and signature_file:
            _draw_signature_image(
                c,
                signature_file,
                130,
                180,
                width=120,
                height=28,
            )
        else:
            c.drawString(130, 196, payload["signature_name"])

        if payload["sign_date"]:
            c.drawString(415, 196, payload["sign_date"])

    for page_index in range(total_pages):
        if page_index == 0:
            pages.append(_create_overlay_for_page(page_width, page_height, draw))
        else:
            pages.append(None)

    return pages


def _generate_from_base_document(
    ombording,
    user,
    base_key,
    filled_key,
    filled_label,
    overlay_factory,
):
    base_doc = _document_by_key(ombording, base_key)
    if not base_doc or not base_doc.file:
        return None

    base_doc.file.open("rb")
    try:
        reader = PdfReader(base_doc.file)
        first_page = reader.pages[0]
        page_width = float(first_page.mediabox.width)
        page_height = float(first_page.mediabox.height)
        total_pages = len(reader.pages)
    finally:
        try:
            base_doc.file.close()
        except Exception:
            pass

    overlay_pages = overlay_factory(
        ombording,
        total_pages,
        page_width,
        page_height,
    )
    output, _ = _merge_overlay(base_doc, overlay_pages)
    return _upsert_generated_document(
        ombording,
        user,
        filled_key,
        filled_label,
        output,
    )


def generate_filled_documents(ombording, user=None):
    generated = []

    agreement = _generate_from_base_document(
        ombording=ombording,
        user=user,
        base_key=DocumentKey.CONTRACTOR_AGREEMENT_BASE,
        filled_key=DocumentKey.CONTRACTOR_AGREEMENT_FILLED,
        filled_label="Independent Contractor Agreement Filled",
        overlay_factory=_agreement_overlay_pages,
    )
    if agreement:
        generated.append(agreement)

    exhibit = _generate_from_base_document(
        ombording=ombording,
        user=user,
        base_key=DocumentKey.EXHIBIT_BASE,
        filled_key=DocumentKey.EXHIBIT_FILLED,
        filled_label="Exhibit Filled",
        overlay_factory=_exhibit_overlay_pages,
    )
    if exhibit:
        generated.append(exhibit)

    w9 = _generate_from_base_document(
        ombording=ombording,
        user=user,
        base_key=DocumentKey.W9_BASE,
        filled_key=DocumentKey.W9_FILLED,
        filled_label="W-9 Filled",
        overlay_factory=_w9_overlay_pages,
    )
    if w9:
        generated.append(w9)

    if generated:
        ombording.documents_generated_at = timezone.now()
        ombording.save(update_fields=["documents_generated_at", "updated_at"])

    return generated


def register_audit_log(ombording, action, performed_by=None, detail=""):
    OmbordingAuditLog.objects.create(
        ombording=ombording,
        action=action,
        detail=detail,
        performed_by=performed_by,
    )


def ensure_field_reviews(ombording):
    if not ombording.pk:
        raise ValueError("Ombording must be saved before creating field reviews.")

    for step, field_key, field_label in FIELD_GROUPS:
        obj, _ = OmbordingFieldReview.objects.get_or_create(
            ombording=ombording,
            field_key=field_key,
            defaults={
                "field_label": field_label,
                "step": step,
                "review_status": ReviewStatus.PENDING,
            },
        )

        value = getattr(ombording, field_key, None)
        is_complete = value not in (None, "", False) or value is True
        desired = ReviewStatus.COMPLETED if is_complete else ReviewStatus.PENDING

        if obj.review_status not in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
            if obj.review_status != desired:
                obj.review_status = desired
                obj.save(update_fields=["review_status", "updated_at"])


def ordered_field_reviews(ombording):
    items = list(ombording.field_reviews.all())
    return sorted(
        items,
        key=lambda x: (
            STEP_ORDER.get(x.step, 999),
            x.field_label.lower(),
        ),
    )


def resolve_uploaded_file(files, key):
    return files.get(key) or files.get(f"{key}_camera")


def get_temp_uploads_map(session_key):
    return {
        x.field_name: x
        for x in OmbordingTempUpload.objects.filter(session_key=session_key).order_by(
            "-id"
        )
    }


def save_temp_uploads_from_request(session_key, user, files):
    for field_name, _, _ in DOCUMENT_FIELD_MAP:
        uploaded = resolve_uploaded_file(files, field_name)
        if not uploaded:
            continue

        existing = OmbordingTempUpload.objects.filter(
            session_key=session_key,
            field_name=field_name,
        ).first()

        if existing and existing.file:
            existing.file.delete(save=False)
            existing.delete()

        OmbordingTempUpload.objects.create(
            session_key=session_key,
            field_name=field_name,
            file=uploaded,
            uploaded_by=user,
            original_name=getattr(uploaded, "name", ""),
        )


def save_document_file(ombording, user, file_obj, document_key, label):
    doc = (
        OmbordingDocument.objects.filter(
            ombording=ombording,
            document_key=document_key,
        )
        .order_by("-id")
        .first()
    )

    if doc:
        doc.file = file_obj
        doc.label = label
        doc.uploaded_by = user
        if doc.review_status != ReviewStatus.APPROVED:
            doc.review_status = ReviewStatus.COMPLETED
        doc.review_comment = ""
        doc.reviewed_by = None
        doc.reviewed_at = None
        doc.save()
        return doc

    return OmbordingDocument.objects.create(
        ombording=ombording,
        document_key=document_key,
        label=label,
        uploaded_by=user,
        file=file_obj,
        review_status=ReviewStatus.COMPLETED,
    )


def save_uploaded_documents_from_form(ombording, user, files):
    for key, document_key, label in DOCUMENT_FIELD_MAP:
        uploaded = resolve_uploaded_file(files, key)
        if uploaded:
            save_document_file(ombording, user, uploaded, document_key, label)


def consume_temp_uploads_into_ombording(ombording, user, session_key):
    temp_map = get_temp_uploads_map(session_key)

    for key, document_key, label in DOCUMENT_FIELD_MAP:
        temp = temp_map.get(key)
        if not temp or not temp.file:
            continue

        temp.file.open("rb")
        django_file = File(temp.file, name=temp.original_name or temp.file.name)
        save_document_file(ombording, user, django_file, document_key, label)
        temp.file.close()

    for temp in temp_map.values():
        if temp.file:
            temp.file.delete(save=False)
        temp.delete()


def refresh_document_review_states(ombording):
    for doc in ombording.documents.all():
        if doc.review_status != ReviewStatus.APPROVED:
            if doc.review_status != ReviewStatus.REJECTED:
                doc.review_status = ReviewStatus.COMPLETED
                doc.save(update_fields=["review_status"])


def refresh_current_step(ombording):
    if not ombording.is_initial_complete():
        ombording.current_step = OmbordingStep.INITIAL
        return
    if not ombording.is_personal_complete():
        ombording.current_step = OmbordingStep.PERSONAL
        return
    if not ombording.is_identity_complete():
        ombording.current_step = OmbordingStep.IDENTITY
        return
    if not ombording.is_banking_complete():
        ombording.current_step = OmbordingStep.BANKING
        return
    ombording.current_step = OmbordingStep.REVIEW


def sync_status_after_internal_save(ombording):
    refresh_current_step(ombording)

    if ombording.send_email_on_create:
        if ombording.is_admin_complete():
            ombording.status = OmbordingStatus.SUBMITTED
            if not ombording.submitted_at:
                ombording.submitted_at = timezone.now()
        else:
            ombording.status = OmbordingStatus.PENDING_USER
        return

    if ombording.is_admin_complete():
        ombording.status = OmbordingStatus.UNDER_REVIEW
        if not ombording.submitted_at:
            ombording.submitted_at = timezone.now()
    elif ombording.is_initial_complete():
        ombording.status = OmbordingStatus.IN_PROGRESS
    else:
        ombording.status = OmbordingStatus.DRAFT


def sync_status_after_review_update(ombording):
    total_reviews = ombording.field_reviews.count() + ombording.documents.count()
    approved_reviews = (
        ombording.field_reviews.filter(review_status=ReviewStatus.APPROVED).count()
        + ombording.documents.filter(review_status=ReviewStatus.APPROVED).count()
    )
    rejected_exists = (
        ombording.field_reviews.filter(review_status=ReviewStatus.REJECTED).exists()
        or ombording.documents.filter(review_status=ReviewStatus.REJECTED).exists()
    )

    ombording.current_step = OmbordingStep.REVIEW

    if total_reviews > 0 and approved_reviews == total_reviews:
        ombording.status = OmbordingStatus.APPROVED
        ombording.approved_at = timezone.now()
        ombording.rejected_at = None
        ombording.reviewed_by = None
        ombording.save(
            update_fields=[
                "status",
                "current_step",
                "approved_at",
                "rejected_at",
                "reviewed_by",
                "updated_at",
            ]
        )
        return

    if rejected_exists:
        ombording.status = OmbordingStatus.REJECTED_PARTIAL
        ombording.rejected_at = timezone.now()
        ombording.approved_at = None
        ombording.save(
            update_fields=[
                "status",
                "current_step",
                "rejected_at",
                "approved_at",
                "updated_at",
            ]
        )
        return

    if ombording.is_admin_complete():
        ombording.status = OmbordingStatus.UNDER_REVIEW
        if not ombording.submitted_at:
            ombording.submitted_at = timezone.now()
        ombording.approved_at = None
        ombording.rejected_at = None
        ombording.save(
            update_fields=[
                "status",
                "current_step",
                "submitted_at",
                "approved_at",
                "rejected_at",
                "updated_at",
            ]
        )
        return

    sync_status_after_internal_save(ombording)
    ombording.save()


def prepare_new_ombording(ombording, created_by=None):
    if not ombording.link_expires_at:
        ombording.link_expires_at = timezone.now() + timedelta(days=7)
    refresh_current_step(ombording)


def update_field_review(review_obj, review_status, review_comment, user):
    review_status = (review_status or "").strip()

    if review_status not in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
        raise ValueError("Invalid review status.")

    if review_status == ReviewStatus.REJECTED and not (review_comment or "").strip():
        raise ValueError("A comment is required when rejecting a field.")

    review_obj.review_status = review_status
    review_obj.review_comment = (review_comment or "").strip()
    review_obj.reviewed_by = user
    review_obj.reviewed_at = timezone.now()
    review_obj.save(
        update_fields=[
            "review_status",
            "review_comment",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )

    sync_status_after_review_update(review_obj.ombording)
    return review_obj


def update_document_review(document_obj, review_status, review_comment, user):
    review_status = (review_status or "").strip()

    if review_status not in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
        raise ValueError("Invalid review status.")

    if review_status == ReviewStatus.REJECTED and not (review_comment or "").strip():
        raise ValueError("A comment is required when rejecting a document.")

    document_obj.review_status = review_status
    document_obj.review_comment = (review_comment or "").strip()
    document_obj.reviewed_by = user
    document_obj.reviewed_at = timezone.now()
    document_obj.save(
        update_fields=[
            "review_status",
            "review_comment",
            "reviewed_by",
            "reviewed_at",
        ]
    )

    sync_status_after_review_update(document_obj.ombording)
    return document_obj


def send_ombording_email(ombording, request=None, email_type="initial"):
    if not ombording.email:
        return False, "Missing email."

    public_path = reverse(
        "ombording:public_start", kwargs={"token": ombording.link_token}
    )
    public_url = request.build_absolute_uri(public_path) if request else public_path

    company_name = "Hyperlink Networks"
    worker_name = ombording.full_name or "Team Member"

    if email_type == "approved":
        subject = f"Welcome to {company_name} – Onboarding Approved"
        body = (
            f"Hello {worker_name},\n\n"
            f"Welcome to {company_name}.\n\n"
            f"We are pleased to let you know that your onboarding has been approved successfully.\n\n"
            f"We are excited to have you as part of our team and look forward to working together.\n\n"
            f"Best regards,\n"
            f"{company_name}"
        )
    elif email_type == "rejected":
        subject = f"{company_name} Onboarding – Action Required"
        body = (
            f"Hello {worker_name},\n\n"
            f"Welcome to {company_name}.\n\n"
            f"Thank you for completing your onboarding information. After review, we need a few corrections before we can finalize your process.\n\n"
            f"Please use the secure link below to continue your onboarding:\n"
            f"{public_url}\n\n"
            f"For security, you will also need the access code provided separately by your company contact.\n\n"
            f"If you need assistance, please contact our team.\n\n"
            f"Best regards,\n"
            f"{company_name}"
        )
    else:
        subject = f"Welcome to {company_name} – Complete Your Onboarding"
        body = (
            f"Hello {worker_name},\n\n"
            f"Welcome to {company_name}.\n\n"
            f"We are excited to have you begin your onboarding process with our company.\n\n"
            f"To get started, please use the secure link below:\n"
            f"{public_url}\n\n"
            f"For security, you will also need the access code provided separately by your company contact.\n\n"
            f"If you have any questions or need assistance during the process, please contact our team.\n\n"
            f"We look forward to working with you.\n\n"
            f"Best regards,\n"
            f"{company_name}"
        )

    try:
        send_mail(subject, body, None, [ombording.email], fail_silently=False)
        OmbordingEmailLog.objects.create(
            ombording=ombording,
            email_type=email_type,
            subject=subject,
            recipient=ombording.email,
            success=True,
        )
        return True, ""
    except Exception as exc:
        OmbordingEmailLog.objects.create(
            ombording=ombording,
            email_type=email_type,
            subject=subject,
            recipient=ombording.email,
            success=False,
            error_message=str(exc),
        )
        return False, str(exc)
