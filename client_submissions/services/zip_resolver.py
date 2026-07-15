from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)


# ============================================================
# Constantes
# ============================================================

SAFE_REPLACEMENT = "–"

DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30

DEFAULT_MAX_ZIP_SIZE_BYTES = 500 * 1024 * 1024  # 500 MB

CHUNK_SIZE = 1024 * 1024  # 1 MB


# ============================================================
# Excepciones
# ============================================================


class ZipResolverError(Exception):
    """
    Error base para generación y materialización de ZIPs.
    """


class ZipNoEvidenceError(ZipResolverError):
    """
    La sesión no contiene evidencias utilizables.
    """


class ZipEvidenceReadError(ZipResolverError):
    """
    Una o más evidencias no pudieron leerse.
    """


class ZipCreationError(ZipResolverError):
    """
    Error al crear físicamente el ZIP.
    """


class ZipTooLargeError(ZipResolverError):
    """
    El ZIP generado excede el límite configurado.
    """


# ============================================================
# Resultados estructurados
# ============================================================


@dataclass(frozen=True)
class ZipEvidenceEntry:
    """
    Describe una evidencia que será incluida en el ZIP.
    """

    evidence_id: int
    assignment_id: int
    technician_id: int | None
    technician_name: str

    title: str

    storage_name: str
    public_url: str
    extension: str

    archive_path: str

    def as_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "assignment_id": self.assignment_id,
            "technician_id": self.technician_id,
            "technician_name": self.technician_name,
            "title": self.title,
            "storage_name": self.storage_name,
            "public_url": self.public_url,
            "extension": self.extension,
            "archive_path": self.archive_path,
        }


@dataclass(frozen=True)
class ProjectZipManifest:
    """
    Resultado de analizar una SesionBilling antes de crear el ZIP.
    """

    billing_session_id: int
    project_id: str
    zip_filename: str
    root_folder: str

    evidence_count: int
    entries: tuple[ZipEvidenceEntry, ...]

    warnings: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "billing_session_id": self.billing_session_id,
            "project_id": self.project_id,
            "zip_filename": self.zip_filename,
            "root_folder": self.root_folder,
            "evidence_count": self.evidence_count,
            "entries": [entry.as_dict() for entry in self.entries],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class GeneratedProjectZip:
    """
    ZIP ya generado físicamente en el worker.
    """

    billing_session_id: int
    project_id: str

    path: str
    filename: str
    size: int

    evidence_count: int
    failed_evidence_count: int

    temporary: bool

    manifest: ProjectZipManifest

    def as_dict(self) -> dict:
        return {
            "billing_session_id": self.billing_session_id,
            "project_id": self.project_id,
            "path": self.path,
            "filename": self.filename,
            "size": self.size,
            "evidence_count": self.evidence_count,
            "failed_evidence_count": self.failed_evidence_count,
            "temporary": self.temporary,
            "manifest": self.manifest.as_dict(),
        }


# ============================================================
# Helpers de nombres
# ============================================================


def safe_component_preserve(
    value: str,
    *,
    fallback: str = "(sin-titulo)",
    max_len: int = 120,
) -> str:
    """
    Limpia solamente caracteres peligrosos para rutas ZIP.

    Mantiene:
    - espacios;
    - letras;
    - números;
    - guiones;
    - underscores.

    Reemplaza:
    - /
    - \\

    por un en-dash para evitar crear carpetas accidentales.
    """

    value = str(value or "")

    if not value:
        value = fallback

    value = "".join(char for char in value if char >= " " and char != "\x7f")

    value = value.replace("/", SAFE_REPLACEMENT).replace("\\", SAFE_REPLACEMENT).strip()

    value = value or fallback

    if len(value) > max_len:
        value = value[:max_len].rstrip()

    return value


def guess_extension(
    name_or_url: str,
    *,
    default: str = ".jpg",
) -> str:
    """
    Obtiene la extensión desde:
    - nombre del storage;
    - URL pública.
    """

    if not name_or_url:
        return default

    try:
        if "://" in name_or_url:
            path = urlparse(name_or_url).path
        else:
            path = name_or_url
    except Exception:
        path = name_or_url

    _, extension = os.path.splitext(os.path.basename(path))

    extension = (extension or default).lower()

    return extension


def get_project_root_name(
    billing_session,
) -> str:
    """
    Nombre de carpeta interna del ZIP.
    """

    project_id = (
        getattr(
            billing_session,
            "proyecto_id",
            "",
        )
        or f"Billing_{billing_session.pk}"
    )

    return safe_component_preserve(
        project_id,
        fallback=f"Billing_{billing_session.pk}",
        max_len=100,
    )


def get_project_zip_filename(
    billing_session,
) -> str:
    """
    Nombre final del archivo ZIP.

    Ejemplo:
        0913RA_04_5005-008.zip
    """

    root_name = get_project_root_name(billing_session)

    return f"{root_name}.zip"


# ============================================================
# Datos de evidencia
# ============================================================


def get_technician_name(
    assignment,
) -> str:
    technician = getattr(
        assignment,
        "tecnico",
        None,
    )

    if technician is None:
        return ""

    try:
        full_name = (technician.get_full_name() or "").strip()

        if full_name:
            return full_name
    except Exception:
        pass

    return (
        getattr(
            technician,
            "username",
            "",
        )
        or ""
    ).strip()


def get_evidence_title(
    billing_session,
    evidence,
) -> str:
    """
    Usa exactamente la misma regla que tu ZIP actual.

    Proyecto especial + extra:
        titulo_manual

    Evidencia con requisito:
        requisito.titulo

    Fallback:
        Extra
    """

    is_special_project = bool(
        getattr(
            billing_session,
            "proyecto_especial",
            False,
        )
    )

    requirement_id = getattr(
        evidence,
        "requisito_id",
        None,
    )

    if is_special_project and not requirement_id:
        raw_title = (
            getattr(
                evidence,
                "titulo_manual",
                "",
            )
            or "Extra"
        )

    else:
        requirement = getattr(
            evidence,
            "requisito",
            None,
        )

        raw_title = (
            getattr(
                requirement,
                "titulo",
                "",
            )
            or "Extra"
        )

    return safe_component_preserve(
        raw_title,
        fallback="Extra",
        max_len=120,
    )


# ============================================================
# Query de evidencias
# ============================================================


def get_session_assignments(
    billing_session,
):
    """
    Recupera todas las asignaciones.

    Incluimos evidencias de todas las asignaciones porque la descarga
    manual existente hace lo mismo.
    """

    return (
        billing_session.tecnicos_sesion.select_related(
            "tecnico",
        )
        .prefetch_related(
            "evidencias__requisito",
        )
        .all()
    )


def iter_session_evidences(
    billing_session,
) -> Iterator[tuple]:
    """
    Yield:
        (assignment, evidence)
    """

    for assignment in get_session_assignments(billing_session):
        evidence_manager = getattr(
            assignment,
            "evidencias",
            None,
        )

        if evidence_manager is None:
            continue

        for evidence in evidence_manager.all():
            yield assignment, evidence


# ============================================================
# Manifest
# ============================================================


def build_project_zip_manifest(
    billing_session,
) -> ProjectZipManifest:
    """
    Construye la lista exacta de evidencias y nombres internos
    antes de generar físicamente el ZIP.
    """

    if billing_session is None:
        raise ZipResolverError("Billing session is required.")

    root_name = get_project_root_name(billing_session)

    zip_filename = get_project_zip_filename(billing_session)

    project_id = (
        getattr(
            billing_session,
            "proyecto_id",
            "",
        )
        or ""
    ).strip()

    used_paths: set[str] = set()

    entries: list[ZipEvidenceEntry] = []
    warnings: list[str] = []

    for assignment, evidence in iter_session_evidences(billing_session):
        image_field = getattr(
            evidence,
            "imagen",
            None,
        )

        if not image_field:
            warnings.append(
                (
                    f"Evidence #{getattr(evidence, 'id', '?')} "
                    "does not contain an image."
                )
            )
            continue

        storage_name = (
            getattr(
                image_field,
                "name",
                "",
            )
            or ""
        ).strip()

        public_url = ""

        try:
            public_url = (image_field.url or "").strip()
        except Exception:
            public_url = ""

        extension = guess_extension(
            storage_name or public_url,
            default=".jpg",
        )

        title = get_evidence_title(
            billing_session,
            evidence,
        )

        evidence_id = int(
            getattr(
                evidence,
                "id",
                0,
            )
            or 0
        )

        base_archive_path = f"{root_name}/" f"{title}" f"{extension}"

        archive_path = base_archive_path

        if archive_path in used_paths:
            archive_path = f"{root_name}/" f"{title} ({evidence_id})" f"{extension}"

            duplicate_number = 2

            while archive_path in used_paths:
                archive_path = (
                    f"{root_name}/"
                    f"{title} ({evidence_id})_{duplicate_number}"
                    f"{extension}"
                )

                duplicate_number += 1

        used_paths.add(archive_path)

        entries.append(
            ZipEvidenceEntry(
                evidence_id=evidence_id,
                assignment_id=int(
                    getattr(
                        assignment,
                        "id",
                        0,
                    )
                    or 0
                ),
                technician_id=getattr(
                    assignment,
                    "tecnico_id",
                    None,
                ),
                technician_name=get_technician_name(assignment),
                title=title,
                storage_name=storage_name,
                public_url=public_url,
                extension=extension,
                archive_path=archive_path,
            )
        )

    if not entries:
        raise ZipNoEvidenceError(
            (
                "No photos are available for "
                f"Project ID '{project_id or billing_session.pk}'."
            )
        )

    return ProjectZipManifest(
        billing_session_id=billing_session.pk,
        project_id=project_id,
        zip_filename=zip_filename,
        root_folder=root_name,
        evidence_count=len(entries),
        entries=tuple(entries),
        warnings=tuple(warnings),
    )


# ============================================================
# Lectura desde Wasabi / URL
# ============================================================


def read_evidence_bytes(
    *,
    storage,
    storage_name: str,
    public_url: str,
    timeout: int = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
) -> bytes:
    """
    Prioridad:

    1. Storage real asociado al ImageField.
    2. Fallback por URL pública.

    Esto conserva exactamente el comportamiento actual,
    pero centralizado para descarga manual y worker.
    """

    if storage is not None and storage_name:
        try:
            with storage.open(
                storage_name,
                "rb",
            ) as file_handle:
                return file_handle.read()

        except Exception as exc:
            logger.warning(
                "Client submission ZIP: storage read failed " "for '%s': %s",
                storage_name,
                exc,
            )

    if public_url and (
        public_url.startswith("http://") or public_url.startswith("https://")
    ):
        try:
            response = requests.get(
                public_url,
                timeout=timeout,
            )

            response.raise_for_status()

            return response.content

        except Exception as exc:
            logger.warning(
                "Client submission ZIP: URL download failed " "for '%s': %s",
                public_url,
                exc,
            )

    raise ZipEvidenceReadError(
        (
            "Could not read evidence from storage or URL. "
            f"Storage name: {storage_name or 'empty'}"
        )
    )


def get_evidence_by_id(
    billing_session,
    evidence_id: int,
):
    """
    Recupera la evidencia desde los objetos ya relacionados
    con la sesión.
    """

    for assignment, evidence in iter_session_evidences(billing_session):
        if evidence.pk == evidence_id:
            return evidence

    return None


# ============================================================
# Generación física del ZIP
# ============================================================


def create_temporary_zip_path(
    zip_filename: str,
) -> tuple[str, str]:
    """
    Retorna:
        temporary_directory
        zip_path
    """

    temporary_directory = tempfile.mkdtemp(prefix="hyperlink_client_submission_")

    zip_path = os.path.join(
        temporary_directory,
        zip_filename,
    )

    return (
        temporary_directory,
        zip_path,
    )


def generate_project_zip(
    billing_session,
    *,
    fail_if_any_evidence_fails: bool = False,
    max_size_bytes: int = DEFAULT_MAX_ZIP_SIZE_BYTES,
) -> GeneratedProjectZip:
    """
    Genera físicamente un ZIP temporal con todas las evidencias
    de una SesionBilling.

    Por defecto:
        si falla una imagen, continúa con las demás.

    Para automatización del cliente:
        podemos usar fail_if_any_evidence_fails=True
        si decidimos exigir que absolutamente todas las fotos
        estén disponibles antes del envío.
    """

    manifest = build_project_zip_manifest(billing_session)

    temporary_directory, zip_path = create_temporary_zip_path(manifest.zip_filename)

    added_count = 0
    failed_count = 0

    try:
        with zipfile.ZipFile(
            zip_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as zip_file:

            for entry in manifest.entries:
                evidence = get_evidence_by_id(
                    billing_session,
                    entry.evidence_id,
                )

                if evidence is None:
                    failed_count += 1

                    message = f"Evidence #{entry.evidence_id} " "could not be found."

                    logger.warning(message)

                    if fail_if_any_evidence_fails:
                        raise ZipEvidenceReadError(message)

                    continue

                image_field = getattr(
                    evidence,
                    "imagen",
                    None,
                )

                if not image_field:
                    failed_count += 1

                    message = (
                        f"Evidence #{entry.evidence_id} " "does not contain an image."
                    )

                    if fail_if_any_evidence_fails:
                        raise ZipEvidenceReadError(message)

                    continue

                storage = getattr(
                    image_field,
                    "storage",
                    None,
                )

                try:
                    data = read_evidence_bytes(
                        storage=storage,
                        storage_name=entry.storage_name,
                        public_url=entry.public_url,
                    )

                    zip_file.writestr(
                        entry.archive_path,
                        data,
                    )

                    added_count += 1

                except Exception as exc:
                    failed_count += 1

                    logger.warning(
                        (
                            "Client submission ZIP: "
                            "failed to add evidence=%s "
                            "archive_path='%s': %s"
                        ),
                        entry.evidence_id,
                        entry.archive_path,
                        exc,
                    )

                    if fail_if_any_evidence_fails:
                        raise

        if added_count == 0:
            raise ZipNoEvidenceError(
                (
                    "No photos could be added to the ZIP for "
                    f"Project ID '{manifest.project_id}'."
                )
            )

        zip_size = os.path.getsize(zip_path)

        if zip_size <= 0:
            raise ZipCreationError("The generated ZIP is empty.")

        if max_size_bytes and zip_size > max_size_bytes:
            raise ZipTooLargeError(
                (
                    "The generated ZIP exceeds the configured limit. "
                    f"Size: {zip_size} bytes. "
                    f"Maximum: {max_size_bytes} bytes."
                )
            )

        logger.info(
            (
                "Client submission ZIP generated "
                "billing=%s project=%s "
                "added=%s failed=%s size=%s"
            ),
            billing_session.pk,
            manifest.project_id,
            added_count,
            failed_count,
            zip_size,
        )

        return GeneratedProjectZip(
            billing_session_id=billing_session.pk,
            project_id=manifest.project_id,
            path=zip_path,
            filename=manifest.zip_filename,
            size=zip_size,
            evidence_count=added_count,
            failed_evidence_count=failed_count,
            temporary=True,
            manifest=manifest,
        )

    except Exception:
        shutil.rmtree(
            temporary_directory,
            ignore_errors=True,
        )

        raise


# ============================================================
# Limpieza
# ============================================================


def cleanup_generated_zip(
    generated_zip: GeneratedProjectZip,
) -> None:
    if not generated_zip:
        return

    path = Path(generated_zip.path)

    if not generated_zip.temporary:
        return

    try:
        parent = path.parent

        if path.exists():
            path.unlink()

        if parent.exists():
            shutil.rmtree(
                parent,
                ignore_errors=True,
            )

    except Exception as exc:
        logger.warning(
            "Could not clean temporary project ZIP '%s': %s",
            generated_zip.path,
            exc,
        )


# ============================================================
# Context manager recomendado para Playwright
# ============================================================


@contextmanager
def project_zip_for_submission(
    billing_session,
    *,
    fail_if_any_evidence_fails: bool = False,
    max_size_bytes: int = DEFAULT_MAX_ZIP_SIZE_BYTES,
):
    """
    Uso recomendado en el worker:

        with project_zip_for_submission(billing) as generated_zip:
            await file_input.set_input_files(generated_zip.path)

    Al salir:
        el ZIP temporal se elimina automáticamente.
    """

    generated_zip = generate_project_zip(
        billing_session,
        fail_if_any_evidence_fails=fail_if_any_evidence_fails,
        max_size_bytes=max_size_bytes,
    )

    try:
        yield generated_zip

    finally:
        cleanup_generated_zip(generated_zip)


# ============================================================
# Preview / validación
# ============================================================


def inspect_billing_zip(
    billing_session,
) -> dict:
    """
    No descarga fotografías y no crea el ZIP.

    Sirve para la pantalla Preview.

    Retorna cuántas evidencias pueden incluirse y cómo se llamaría
    el archivo final.
    """

    try:
        manifest = build_project_zip_manifest(billing_session)

        return {
            "ok": True,
            "available": True,
            "billing_session_id": manifest.billing_session_id,
            "project_id": manifest.project_id,
            "zip_filename": manifest.zip_filename,
            "evidence_count": manifest.evidence_count,
            "warnings": list(manifest.warnings),
            "error": "",
        }

    except ZipResolverError as exc:
        return {
            "ok": False,
            "available": False,
            "billing_session_id": getattr(
                billing_session,
                "pk",
                None,
            ),
            "project_id": (
                getattr(
                    billing_session,
                    "proyecto_id",
                    "",
                )
                or ""
            ),
            "zip_filename": "",
            "evidence_count": 0,
            "warnings": [],
            "error": str(exc),
        }


# ============================================================
# Metadata para ClientSubmission
# ============================================================


def build_zip_snapshot(
    billing_session,
) -> dict:
    """
    Snapshot ligero para guardar al crear ClientSubmission.

    Importante:
    El ZIP físico se genera posteriormente en el worker.
    """

    result = inspect_billing_zip(billing_session)

    return {
        "ok": result["ok"],
        "available": result["available"],
        "project_id": result["project_id"],
        "zip_filename": result["zip_filename"],
        "evidence_count": result["evidence_count"],
        "warnings": result["warnings"],
        "error": result["error"],
        "checked_at": timezone.now().isoformat(),
    }
