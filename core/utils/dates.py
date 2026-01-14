# core/utils/dates.py
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime


def parse_date_flexible(value: str | None) -> Optional[date]:
    """
    Acepta:
      - 'YYYY-MM-DD' (ISO)  -> recomendado (flatpickr con dateFormat='Y-m-d')
      - 'DD/MM/YYYY'        -> típico CL
      - 'MM/DD/YYYY'        -> típico US (por si alguien lo escribe manual)
    Retorna date o None.
    """
    s = (value or "").strip()
    if not s:
        return None

    # 1) ISO date: YYYY-MM-DD
    d = parse_date(s)
    if d:
        return d

    # 2) DD/MM/YYYY
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        pass

    # 3) MM/DD/YYYY
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        return None


def parse_datetime_flexible(value: str | None) -> Optional[datetime]:
    """
    Acepta ISO datetime o formatos comunes con hora.
    Retorna datetime timezone-aware (si puede) o None.
    """
    s = (value or "").strip()
    if not s:
        return None

    dt = parse_datetime(s)
    if dt:
        return _ensure_aware(dt)

    fmts = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(s, fmt)
            return _ensure_aware(dt)
        except ValueError:
            continue

    return None


def _ensure_aware(dt: datetime) -> datetime:
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt, timezone.get_current_timezone())