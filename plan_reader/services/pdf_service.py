import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import fitz  # PyMuPDF

SHEET_NAME_REGEX = re.compile(
    r"\bSheet\s+([A-Z]\d+)\b",
    re.IGNORECASE,
)


@contextmanager
def get_pdf_temp_path(file_field):
    """
    Devuelve una ruta local temporal del PDF.

    Funciona con storage local y storage remoto compatible con .open().
    """
    if not file_field:
        raise ValueError("PDF file is missing.")

    temp_file = tempfile.NamedTemporaryFile(
        suffix=".pdf",
        prefix="plan_reader_",
        delete=False,
    )
    temp_path = temp_file.name

    try:
        temp_file.close()

        with file_field.open("rb") as source, open(temp_path, "wb") as target:
            shutil.copyfileobj(source, target)

        yield temp_path

    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


@contextmanager
def get_plan_reader_temp_dir(job_id):
    """
    Carpeta temporal por job.
    Se elimina automáticamente al finalizar.
    """
    temp_dir = tempfile.mkdtemp(prefix=f"plan_reader_job_{job_id}_")

    try:
        yield temp_dir
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def count_pdf_pages(pdf_path):
    """
    Cuenta páginas del PDF.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with fitz.open(pdf_path) as doc:
        return doc.page_count


def extract_sheet_name_from_page(pdf_path, page_number):
    """
    Intenta detectar el nombre de la hoja desde texto embebido.
    page_number es 1-based.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with fitz.open(pdf_path) as doc:
        index = page_number - 1

        if index < 0 or index >= doc.page_count:
            return ""

        page = doc.load_page(index)
        text = page.get_text("text") or ""

    match = SHEET_NAME_REGEX.search(text)

    if match:
        return match.group(1).upper().strip()

    return ""


def render_pdf_page_to_image(pdf_path, page_number, output_dir, zoom=3.0):
    """
    Convierte una página del PDF en imagen PNG.
    page_number es 1-based.
    """
    if not pdf_path or not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_path = output_dir / f"page_{page_number:03d}.png"

    with fitz.open(pdf_path) as doc:
        index = page_number - 1

        if index < 0 or index >= doc.page_count:
            raise ValueError(f"Invalid page number: {page_number}")

        page = doc.load_page(index)
        matrix = fitz.Matrix(float(zoom), float(zoom))
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        pixmap.save(str(image_path))

    return str(image_path)
