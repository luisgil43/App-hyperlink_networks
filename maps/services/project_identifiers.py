import re


DFN_PROJECT_PATTERN = re.compile(
    r"^(?P<dfn>[A-Za-z0-9]+_[A-Za-z0-9]+)_(?P<project>.+)$"
)


def parse_project_identifier(project_id):
    """
    Parse an operational SesionBilling.proyecto_id.

    Examples:

        0913RA_04_5005-008
            DFN: 0913RA_04
            display: 5005-008

        0913RA_04_5005-009-3
            DFN: 0913RA_04
            display: 5005-009-3

        NB3231
            DFN: None
            display: NB3231

    No DFN is invented when the identifier does not match the
    established operational format.
    """

    value = (project_id or "").strip()

    if not value:
        return {
            "full": "",
            "dfn": None,
            "display": "",
            "has_dfn": False,
        }

    match = DFN_PROJECT_PATTERN.match(value)

    if not match:
        return {
            "full": value,
            "dfn": None,
            "display": value,
            "has_dfn": False,
        }

    dfn = match.group("dfn").strip()
    display = match.group("project").strip()

    if not dfn or not display:
        return {
            "full": value,
            "dfn": None,
            "display": value,
            "has_dfn": False,
        }

    return {
        "full": value,
        "dfn": dfn,
        "display": display,
        "has_dfn": True,
    }
