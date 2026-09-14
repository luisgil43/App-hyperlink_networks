from collections import OrderedDict
from datetime import timedelta

from django.db.models import F, Q
from django.utils import timezone

from operaciones.models import SesionBilling
from planificacion.models import PlanningDFN

MOVABLE_STATUSES = {
    "asignado",
    "en_proceso",
    "rechazado_supervisor",
    "rechazado_pm",
}


def _technician_name(user):
    if user is None:
        return "Unknown"

    full_name = (user.get_full_name() or "").strip()

    if full_name:
        return full_name

    username = (getattr(user, "username", "") or "").strip()

    if username:
        return username

    return f"User #{user.pk}"


def _technician_short_name(user):
    if user is None:
        return "Unknown"

    first_name = (
        getattr(
            user,
            "first_name",
            "",
        )
        or ""
    ).strip()

    last_name = (
        getattr(
            user,
            "last_name",
            "",
        )
        or ""
    ).strip()

    if first_name and last_name:
        return f"{first_name} {last_name}"

    if first_name:
        return first_name

    return _technician_name(user)


def _build_week_days(week_start):
    result = []

    for offset in range(7):
        day = week_start + timedelta(days=offset)

        result.append(
            {
                "date": day,
                "weekday": day.strftime("%a").upper(),
                "day_number": day.day,
                "month": day.strftime("%b").upper(),
                "is_weekend": (day.weekday() >= 5),
            }
        )

    return result


def _dfn_codes():
    codes = list(
        PlanningDFN.objects.exclude(code="")
        .values_list(
            "code",
            flat=True,
        )
        .distinct()
    )

    return sorted(
        [(code or "").strip() for code in codes if (code or "").strip()],
        key=len,
        reverse=True,
    )


def _resolve_dfn(
    project_id,
    known_codes,
):
    project_id = (project_id or "").strip()

    if not project_id:
        return ""

    project_upper = project_id.upper()

    for code in known_codes:
        code_upper = code.upper()

        if project_upper == code_upper:
            return code

        if project_upper.startswith(f"{code_upper}_"):
            return code

        if project_upper.startswith(f"{code_upper}-"):
            return code

    return ""


def _status_label(session):
    try:
        return session.get_estado_display()
    except Exception:
        return session.estado or ""


def _status_tone(status):
    status = (status or "").strip().lower()

    if status == "asignado":
        return "assigned"

    if status == "en_proceso":
        return "progress"

    if status in {
        "en_revision_supervisor",
        "aprobado_supervisor",
        "finalizado",
    }:
        return "review"

    if status in {
        "rechazado_supervisor",
        "rechazado_pm",
    }:
        return "rejected"

    if status == "aprobado_pm":
        return "completed"

    return "neutral"


def _local_session_date(session):
    if not session.creado_en:
        return None

    try:
        return timezone.localtime(session.creado_en).date()

    except Exception:
        return session.creado_en.date()


def _active_assignments(session):
    return [
        assignment
        for assignment in session.tecnicos_sesion.all()
        if assignment.is_active
    ]


def _team_key(active_assignments):
    technician_ids = sorted(
        assignment.tecnico_id
        for assignment in active_assignments
        if assignment.tecnico_id
    )

    if not technician_ids:
        return "unassigned"

    if len(technician_ids) == 1:
        return f"technician-" f"{technician_ids[0]}"

    return "team-" + "-".join(str(technician_id) for technician_id in technician_ids)


def _team_name(
    active_assignments,
):
    if not active_assignments:
        return "Unassigned"

    ordered_assignments = sorted(
        active_assignments,
        key=lambda assignment: (
            _technician_name(assignment.tecnico).lower(),
            assignment.tecnico_id or 0,
        ),
    )

    return " + ".join(
        _technician_short_name(assignment.tecnico) for assignment in ordered_assignments
    )


def _team_full_name(
    active_assignments,
):
    if not active_assignments:
        return "Unassigned"

    ordered_assignments = sorted(
        active_assignments,
        key=lambda assignment: (
            _technician_name(assignment.tecnico).lower(),
            assignment.tecnico_id or 0,
        ),
    )

    return " + ".join(
        _technician_name(assignment.tecnico) for assignment in ordered_assignments
    )


def _queue_positions(
    active_assignments,
):
    positions = []

    for assignment in active_assignments:
        queue_state = getattr(
            assignment,
            "queue_state",
            None,
        )

        if queue_state is None:
            continue

        position = getattr(
            queue_state,
            "queue_position",
            None,
        )

        if position is None:
            continue

        positions.append(
            {
                "technician_id": (assignment.tecnico_id),
                "technician_name": (_technician_name(assignment.tecnico)),
                "position": position,
            }
        )

    positions.sort(
        key=lambda item: (
            item["position"],
            item["technician_name"].lower(),
        )
    )

    return positions


def _priority_data(
    session,
    active_assignments,
):
    queue_positions = _queue_positions(active_assignments)

    unique_positions = []

    for item in queue_positions:
        position = item["position"]

        if position not in unique_positions:
            unique_positions.append(position)

    if unique_positions:
        priority_value = unique_positions[0]

        priority_label = " / ".join(f"#{position}" for position in unique_positions[:2])

        if len(unique_positions) > 2:
            priority_label += "…"

        return {
            "priority": (priority_value),
            "priority_label": (priority_label),
            "priority_rows": (queue_positions),
        }

    if session.queue_priority is not None:
        return {
            "priority": (session.queue_priority),
            "priority_label": (f"#{session.queue_priority}"),
            "priority_rows": [],
        }

    return {
        "priority": None,
        "priority_label": "",
        "priority_rows": [],
    }


def _real_plan_state(session):
    try:
        return session.real_plan_state
    except Exception:
        return None


def _card_from_session(
    session,
    *,
    known_dfn_codes,
    active_assignments,
):
    ordered_assignments = sorted(
        active_assignments,
        key=lambda assignment: (
            _technician_name(assignment.tecnico).lower(),
            assignment.tecnico_id or 0,
        ),
    )

    technician_names = [
        _technician_name(assignment.tecnico) for assignment in ordered_assignments
    ]

    technician_short_names = [
        _technician_short_name(assignment.tecnico) for assignment in ordered_assignments
    ]

    work_type = "cable" if session.is_cable_installation else "fiber"

    session_date = _local_session_date(session)

    priority_data = _priority_data(
        session,
        ordered_assignments,
    )

    state = _real_plan_state(session)

    board_position = state.board_position if state else None

    planning_mode = state.planning_mode if state else "auto"

    carried_over = bool(state and state.carried_over)

    carry_over_count = state.carry_over_count if state else 0

    can_move = (session.estado or "").strip().lower() in MOVABLE_STATUSES

    return {
        "billing_id": session.id,
        "project_id": (session.proyecto_id),
        "client": session.cliente,
        "city": session.ciudad,
        "project": session.proyecto,
        "office": session.oficina,
        "priority": (priority_data["priority"]),
        "priority_label": (priority_data["priority_label"]),
        "priority_rows": (priority_data["priority_rows"]),
        "board_position": (board_position),
        "planning_mode": (planning_mode),
        "carried_over": (carried_over),
        "carry_over_count": (carry_over_count),
        "status": session.estado,
        "status_label": (_status_label(session)),
        "status_tone": (_status_tone(session.estado)),
        "can_move": can_move,
        "work_type": work_type,
        "work_type_label": ("Cable" if work_type == "cable" else "Fiber"),
        "dfn": _resolve_dfn(
            session.proyecto_id,
            known_dfn_codes,
        ),
        "date": session_date,
        "date_iso": (session_date.isoformat() if session_date else ""),
        "technician_count": len(ordered_assignments),
        "technician_names": (technician_names),
        "technician_short_names": (technician_short_names),
        "technician_label": (
            " + ".join(technician_short_names)
            if technician_short_names
            else "Unassigned"
        ),
    }


def _empty_person_row(
    *,
    key,
    name,
    full_name,
    row_type,
    days,
):
    return {
        "key": key,
        "name": name,
        "full_name": full_name,
        "row_type": row_type,
        "backlog": [],
        "cells": [
            {
                "date": (day["date"]),
                "cards": [],
            }
            for day in days
        ],
        "project_count": 0,
        "technician_count": 0,
        "technician_ids": [],
    }


def _card_default_sort_key(card):
    priority = card.get("priority")

    if priority is None:
        priority = 10**9

    return (
        priority,
        card.get("billing_id") or 0,
    )


def _sort_cards(cards):
    if not cards:
        return

    has_manual_order = any(card.get("board_position") is not None for card in cards)

    if not has_manual_order:
        cards.sort(key=_card_default_sort_key)
        return

    cards.sort(
        key=lambda card: (
            0 if card.get("board_position") is not None else 1,
            (
                card.get("board_position")
                if card.get("board_position") is not None
                else 10**9
            ),
            *_card_default_sort_key(card),
        )
    )


def _place_card_in_row(
    row,
    card,
    *,
    week_start,
    week_end,
):
    card_date = card.get("date")

    if card_date is None:
        row["backlog"].append(card)

        return True

    if not (week_start <= card_date <= week_end):
        return False

    day_index = (card_date - week_start).days

    if day_index < 0 or day_index >= len(row["cells"]):
        return False

    row["cells"][day_index]["cards"].append(card)

    return True


def build_real_plan_board(
    *,
    week_start,
    search="",
    work_type="all",
):
    days = _build_week_days(week_start)

    week_end = week_start + timedelta(days=6)

    queryset = (
        SesionBilling.objects.filter(
            is_direct_discount=False,
        )
        .exclude(
            estado="aprobado_pm",
        )
        .select_related(
            "real_plan_state",
        )
        .prefetch_related(
            "tecnicos_sesion__tecnico",
            "tecnicos_sesion__queue_state",
        )
        .order_by(
            F("queue_priority").asc(nulls_last=True),
            "id",
        )
    )

    if work_type == "fiber":
        queryset = queryset.filter(
            is_cable_installation=False,
        )

    elif work_type == "cable":
        queryset = queryset.filter(
            is_cable_installation=True,
        )

    if search:
        queryset = queryset.filter(
            Q(proyecto_id__icontains=search)
            | Q(cliente__icontains=search)
            | Q(ciudad__icontains=search)
            | Q(proyecto__icontains=search)
            | Q(oficina__icontains=search)
            | Q(tecnicos_sesion__tecnico__first_name__icontains=search)
            | Q(tecnicos_sesion__tecnico__last_name__icontains=search)
            | Q(tecnicos_sesion__tecnico__username__icontains=search)
        ).distinct()

    known_dfn_codes = _dfn_codes()

    rows = OrderedDict()

    total_projects = 0
    unassigned_projects = 0
    team_projects = 0
    visible_projects = 0

    individual_technician_ids = set()

    for session in queryset:
        active_assignments = _active_assignments(session)

        card = _card_from_session(
            session,
            known_dfn_codes=(known_dfn_codes),
            active_assignments=(active_assignments),
        )

        total_projects += 1

        row_key = _team_key(active_assignments)

        if not active_assignments:
            row_type = "unassigned"
            row_name = "Unassigned"
            row_full_name = "No active technician " "assigned"
            technician_ids = []

        elif len(active_assignments) == 1:
            row_type = "technician"

            technician = active_assignments[0].tecnico

            row_name = _technician_name(technician)

            row_full_name = row_name

            technician_ids = [technician.id]

            individual_technician_ids.add(technician.id)

        else:
            row_type = "team"

            row_name = _team_name(active_assignments)

            row_full_name = _team_full_name(active_assignments)

            technician_ids = sorted(
                assignment.tecnico_id
                for assignment in active_assignments
                if assignment.tecnico_id
            )

            individual_technician_ids.update(technician_ids)

        if row_key not in rows:
            rows[row_key] = _empty_person_row(
                key=row_key,
                name=row_name,
                full_name=(row_full_name),
                row_type=row_type,
                days=days,
            )

            rows[row_key]["technician_count"] = len(technician_ids)

            rows[row_key]["technician_ids"] = technician_ids

            if len(technician_ids) == 1:
                rows[row_key]["technician_id"] = technician_ids[0]

        row = rows[row_key]

        placed = _place_card_in_row(
            row,
            card,
            week_start=(week_start),
            week_end=(week_end),
        )

        if not placed:
            continue

        row["project_count"] += 1

        visible_projects += 1

        if not active_assignments:
            unassigned_projects += 1

        if len(active_assignments) > 1:
            team_projects += 1

    board_rows = [row for row in rows.values() if row["project_count"]]

    for row in board_rows:
        _sort_cards(row["backlog"])

        for cell in row["cells"]:
            _sort_cards(cell["cards"])

    row_order = {
        "unassigned": 0,
        "team": 1,
        "technician": 2,
    }

    board_rows.sort(
        key=lambda item: (
            row_order.get(
                item["row_type"],
                99,
            ),
            item["name"].lower(),
        )
    )

    visible_fiber_projects = 0
    visible_cable_projects = 0

    for row in board_rows:
        for card in row["backlog"]:
            if card["work_type"] == "cable":
                visible_cable_projects += 1
            else:
                visible_fiber_projects += 1

        for cell in row["cells"]:
            for card in cell["cards"]:
                if card["work_type"] == "cable":
                    visible_cable_projects += 1
                else:
                    visible_fiber_projects += 1

    return {
        "days": days,
        "rows": board_rows,
        "summary": {
            "total_projects": (visible_projects),
            "all_active_projects": (total_projects),
            "unassigned_projects": (unassigned_projects),
            "team_projects": (team_projects),
            "fiber_projects": (visible_fiber_projects),
            "cable_projects": (visible_cable_projects),
            "technician_count": len(individual_technician_ids),
        },
    }
