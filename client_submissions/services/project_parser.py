from __future__ import annotations

import re
from dataclasses import dataclass

# ============================================================
# Excepciones
# ============================================================


class ProjectIdParseError(ValueError):
    """
    Error específico para Project IDs que no pueden ser interpretados.

    Esto permite distinguir:
    - un error de formato del Project ID;
    - cualquier otro error inesperado de la aplicación.
    """

    pass


# ============================================================
# Resultado normalizado
# ============================================================


@dataclass(frozen=True)
class ParsedProjectId:
    """
    Resultado estructurado del análisis de un Project ID.

    Ejemplo:
        original:
            0913RA_04_5005-008-7

        normalized:
            0913RA_04_5005-008-7

        dfn_name:
            0913RA_04

        access_point_id:
            5005-008-7
    """

    original: str
    normalized: str
    dfn_name: str
    access_point_id: str

    def as_dict(self) -> dict:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "dfn_name": self.dfn_name,
            "access_point_id": self.access_point_id,
        }


# ============================================================
# Helpers internos
# ============================================================


def _clean_text(value) -> str:
    """
    Convierte cualquier valor a texto limpio.

    No modifica:
    - guiones internos;
    - underscores internos;
    - letras;
    - números.

    Solo:
    - convierte a str;
    - elimina espacios al inicio y al final.
    """

    if value is None:
        return ""

    return str(value).strip()


def normalize_project_id(value) -> str:
    """
    Normaliza un Project ID sin alterar su significado.

    Operaciones realizadas:
    - elimina espacios al inicio/final;
    - elimina espacios alrededor de "_";
    - elimina espacios alrededor de "-";
    - convierte múltiples underscores consecutivos en uno solo.

    Ejemplos:
        " 0913RA_04_5005-008 "
            -> "0913RA_04_5005-008"

        "0913RA _ 04 _ 5005 - 008"
            -> "0913RA_04_5005-008"
    """

    value = _clean_text(value)

    if not value:
        return ""

    value = re.sub(r"\s*_\s*", "_", value)
    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"_+", "_", value)

    return value.strip()


def _validate_dfn_name(dfn_name: str) -> None:
    """
    Valida la parte DFN.

    La regla actual del cliente es:

        <parte_1>_<parte_2>

    Ejemplo:
        0913RA_04

    No imponemos todavía una estructura excesivamente rígida
    porque pueden aparecer nuevos mercados o convenciones.
    """

    if not dfn_name:
        raise ProjectIdParseError(
            "DFN Name could not be determined from the Project ID."
        )

    parts = dfn_name.split("_")

    if len(parts) != 2:
        raise ProjectIdParseError(
            "DFN Name must contain exactly two sections separated by an underscore."
        )

    if not all(part.strip() for part in parts):
        raise ProjectIdParseError("DFN Name contains an empty section.")


def _validate_access_point_id(access_point_id: str) -> None:
    """
    Valida el Access Point ID.

    Formatos permitidos actualmente:

        5005-008
        5005-008-7
        5005-008-12

    La cantidad de segmentos posteriores puede crecer,
    por eso permitimos uno o más grupos separados por guiones.

    No se elimina ningún guion.
    """

    if not access_point_id:
        raise ProjectIdParseError(
            "Access Point ID could not be determined from the Project ID."
        )

    if "_" in access_point_id:
        raise ProjectIdParseError("Access Point ID cannot contain underscores.")

    pattern = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+$")

    if not pattern.fullmatch(access_point_id):
        raise ProjectIdParseError(
            (
                "Access Point ID has an invalid format. "
                "Expected a value such as '5005-008' "
                "or '5005-008-7'."
            )
        )


# ============================================================
# Parser principal
# ============================================================


def parse_project_id(value) -> ParsedProjectId:
    """
    Convierte el Project ID completo de Hyperlink en los valores
    requeridos por el formulario del cliente.

    Regla:

        Project ID:
            0913RA_04_5005-008

        DFN Name:
            0913RA_04

        Access Point ID:
            5005-008


        Project ID:
            0913RA_04_5005-008-7

        DFN Name:
            0913RA_04

        Access Point ID:
            5005-008-7


    Importante:
    Se utiliza split("_", 2).

    Esto significa que se separan únicamente las primeras
    tres secciones lógicas:

        parte 1
        parte 2
        todo lo restante como Access Point ID

    Así preservamos completamente:
        5005-008
        5005-008-7
        5005-008-7-2
    """

    original = _clean_text(value)
    normalized = normalize_project_id(original)

    if not normalized:
        raise ProjectIdParseError("Project ID is required.")

    parts = normalized.split("_", 2)

    if len(parts) != 3:
        raise ProjectIdParseError(
            (
                "Invalid Project ID format. "
                "Expected a value such as "
                "'0913RA_04_5005-008'."
            )
        )

    dfn_part_1 = parts[0].strip()
    dfn_part_2 = parts[1].strip()
    access_point_id = parts[2].strip()

    if not dfn_part_1:
        raise ProjectIdParseError("The first DFN section is empty.")

    if not dfn_part_2:
        raise ProjectIdParseError("The second DFN section is empty.")

    if not access_point_id:
        raise ProjectIdParseError("Access Point ID is empty.")

    dfn_name = f"{dfn_part_1}_{dfn_part_2}"

    _validate_dfn_name(dfn_name)
    _validate_access_point_id(access_point_id)

    return ParsedProjectId(
        original=original,
        normalized=normalized,
        dfn_name=dfn_name,
        access_point_id=access_point_id,
    )


# ============================================================
# Helpers públicos
# ============================================================


def get_dfn_name(project_id) -> str:
    """
    Devuelve solamente el DFN Name.
    """

    return parse_project_id(project_id).dfn_name


def get_access_point_id(project_id) -> str:
    """
    Devuelve solamente el Access Point ID.
    """

    return parse_project_id(project_id).access_point_id


def project_id_is_valid(project_id) -> bool:
    """
    Validación rápida para previews o filtros.

    No levanta excepción.
    """

    try:
        parse_project_id(project_id)
        return True
    except ProjectIdParseError:
        return False


def parse_project_id_safe(project_id) -> dict:
    """
    Versión segura para vistas, previews y procesos masivos.

    Nunca levanta ProjectIdParseError.

    Resultado correcto:
        {
            "ok": True,
            "original": "...",
            "normalized": "...",
            "dfn_name": "...",
            "access_point_id": "...",
            "error": "",
        }

    Resultado con error:
        {
            "ok": False,
            "original": "...",
            "normalized": "...",
            "dfn_name": "",
            "access_point_id": "",
            "error": "...",
        }
    """

    original = _clean_text(project_id)
    normalized = normalize_project_id(original)

    try:
        parsed = parse_project_id(project_id)

        return {
            "ok": True,
            "original": parsed.original,
            "normalized": parsed.normalized,
            "dfn_name": parsed.dfn_name,
            "access_point_id": parsed.access_point_id,
            "error": "",
        }

    except ProjectIdParseError as exc:
        return {
            "ok": False,
            "original": original,
            "normalized": normalized,
            "dfn_name": "",
            "access_point_id": "",
            "error": str(exc),
        }


def parse_many_project_ids(project_ids) -> list[dict]:
    """
    Procesa una colección completa de Project IDs.

    Se utilizará, por ejemplo, cuando el usuario seleccione
    100 invoices desde Ready to Invoice.

    Cada elemento devuelve su propio resultado para que un
    Project ID inválido no detenga el análisis de los demás.
    """

    results = []

    for index, project_id in enumerate(project_ids, start=1):
        result = parse_project_id_safe(project_id)

        result["sequence_number"] = index

        results.append(result)

    return results
