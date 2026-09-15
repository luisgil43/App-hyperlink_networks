import json
from datetime import date, datetime, timedelta

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from operaciones.models import SesionBilling

from .models import RealPlanProjectState
from .real_plan_forms import RealPlanBoardFilterForm
from .services.real_plan.board_service import build_real_plan_board

REAL_PLAN_MOVABLE_STATUSES = {
    "asignado",
    "en_proceso",
    "rechazado_supervisor",
    "rechazado_pm",
}


def _monday_for_day(value):
    return value - timedelta(days=value.weekday())


def _parse_week_start(value):
    if not value:
        return _monday_for_day(date.today())

    try:
        parsed = datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()

    except ValueError:
        return _monday_for_day(date.today())

    return _monday_for_day(parsed)


def _can_manage_real_plan(user):
    if not user or not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    return bool(
        getattr(
            user,
            "es_admin_general",
            False,
        )
        or getattr(
            user,
            "es_pm",
            False,
        )
        or getattr(
            user,
            "es_supervisor",
            False,
        )
    )


def _active_technician_signature(
    session,
):
    return tuple(
        sorted(
            assignment.tecnico_id
            for assignment in session.tecnicos_sesion.all()
            if (assignment.is_active and assignment.tecnico_id)
        )
    )


def _parse_id_list(value):
    if value is None:
        return []

    if not isinstance(
        value,
        list,
    ):
        raise ValueError("Order must be a list.")

    result = []
    seen = set()

    for raw in value:
        try:
            item_id = int(raw)

        except (
            TypeError,
            ValueError,
        ):
            raise ValueError("Invalid project order.")

        if item_id <= 0:
            raise ValueError("Invalid project order.")

        if item_id in seen:
            continue

        seen.add(item_id)

        result.append(item_id)

    return result


def _local_date(
    session,
):
    if not session.creado_en:
        return None

    return timezone.localtime(session.creado_en).date()


def _set_session_date(
    session,
    target_date,
):
    tz = timezone.get_current_timezone()

    current_local = (
        timezone.localtime(
            session.creado_en,
            tz,
        )
        if session.creado_en
        else None
    )

    if current_local:
        current_time = current_local.time().replace(
            second=0,
            microsecond=0,
        )

    else:
        current_time = datetime.min.time()

    new_datetime = datetime.combine(
        target_date,
        current_time,
    )

    return timezone.make_aware(
        new_datetime,
        tz,
    )


def _save_positions(
    *,
    sessions_by_id,
    ordered_ids,
    user,
):
    for position, billing_id in enumerate(
        ordered_ids,
        start=1,
    ):
        session = sessions_by_id[billing_id]

        (
            state,
            _created,
        ) = RealPlanProjectState.objects.select_for_update().get_or_create(
            billing=session,
            defaults={
                "board_position": (position),
                "planning_mode": (RealPlanProjectState.PLANNING_MODE_MANUAL),
                "original_planned_date": (_local_date(session)),
                "updated_by": user,
            },
        )

        update_fields = []

        if state.board_position != position:
            state.board_position = position

            update_fields.append("board_position")

        if state.planning_mode != RealPlanProjectState.PLANNING_MODE_MANUAL:
            state.planning_mode = RealPlanProjectState.PLANNING_MODE_MANUAL

            update_fields.append("planning_mode")

        if state.original_planned_date is None:
            state.original_planned_date = _local_date(session)

            update_fields.append("original_planned_date")

        if state.updated_by_id != user.id:
            state.updated_by = user

            update_fields.append("updated_by")

        if update_fields:
            update_fields.append("updated_at")

            state.save(update_fields=(update_fields))


@login_required
def real_plan_board(request):
    filter_form = RealPlanBoardFilterForm(
        request.GET or None,
    )

    search = ""
    work_type = "all"

    if filter_form.is_valid():
        search = filter_form.cleaned_data.get(
            "q",
            "",
        )

        work_type = filter_form.cleaned_data.get(
            "work_type",
            "all",
        )

    week_start = _parse_week_start(
        request.GET.get(
            "week",
            "",
        )
    )

    board = build_real_plan_board(
        week_start=week_start,
        search=search,
        work_type=work_type,
    )

    week_end = week_start + timedelta(days=20)

    today_week = _monday_for_day(
        date.today()
    )

  
    selector_start = today_week - timedelta(
        weeks=12
    )

    selector_end = today_week + timedelta(
        weeks=52
    )

 
    if week_start < selector_start:
        selector_start = week_start - timedelta(
            weeks=4
        )

    if week_start > selector_end:
        selector_end = week_start + timedelta(
            weeks=12
        )

    week_options = []

    current_week = selector_start

    while current_week <= selector_end:
        iso_calendar = current_week.isocalendar()

        week_options.append(
            {
                "start": current_week,
                "year": iso_calendar.year,
                "week": iso_calendar.week,
                "label": (
                    f"W{iso_calendar.week:02d}"
                ),
                "full_label": (
                    f"W{iso_calendar.week:02d} "
                    f"· "
                    f"{current_week.strftime('%b %d')}"
                    f" — "
                    f"{(current_week + timedelta(days=6)).strftime('%b %d')}"
                ),
                "selected": (
                    current_week
                    == week_start
                ),
                "is_current": (
                    current_week
                    == today_week
                ),
            }
        )

        current_week += timedelta(
            weeks=1
        )

    context = {
        "page_title": ("Real Plan"),
        "filter_form": (filter_form),
        "search": search,
        "work_type": (work_type),
        "week_start": (week_start),
        "week_end": (week_end),
        "previous_week": (
            week_start
            - timedelta(days=7)
        ),
        "next_week": (
            week_start
            + timedelta(days=7)
        ),
        "today_week": (today_week),
        "week_options": (week_options),
        "current_week_number": (
            week_start.isocalendar().week
        ),
        "current_week_year": (
            week_start.isocalendar().year
        ),
        "days": (board["days"]),
        "board_rows": (board["rows"]),
        "summary": (board["summary"]),
    }

    if request.headers.get(
        "X-Requested-With"
    ) == "XMLHttpRequest":
        return render(
            request,
            "planificacion/real_plan/partials/_workspace.html",
            context,
        )

    return render(
        request,
        "planificacion/real_plan/board.html",
        context,
    )


@login_required
@csrf_protect
@require_POST
def real_plan_move_project(
    request,
    sesion_id: int,
):
    if not _can_manage_real_plan(request.user):
        return JsonResponse(
            {
                "ok": False,
                "error": ("FORBIDDEN"),
                "message": (
                    "You do not have " "permission to move " "projects in Real Plan."
                ),
            },
            status=403,
        )

    try:
        payload = json.loads(request.body or b"{}")

    except json.JSONDecodeError:
        return JsonResponse(
            {
                "ok": False,
                "error": ("INVALID_JSON"),
                "message": ("Invalid request."),
            },
            status=400,
        )

    raw_date = (payload.get("date") or "").strip()

    if not raw_date:
        return JsonResponse(
            {
                "ok": False,
                "error": ("DATE_REQUIRED"),
                "message": ("Date is required."),
            },
            status=400,
        )

    try:
        target_date = datetime.strptime(
            raw_date,
            "%Y-%m-%d",
        ).date()

    except ValueError:
        return JsonResponse(
            {
                "ok": False,
                "error": ("INVALID_DATE"),
                "message": ("Invalid date format. " "Use YYYY-MM-DD."),
            },
            status=400,
        )

    try:
        target_order = _parse_id_list(
            payload.get(
                "target_order",
                [],
            )
        )

        source_order = _parse_id_list(
            payload.get(
                "source_order",
                [],
            )
        )

    except ValueError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": ("INVALID_ORDER"),
                "message": str(exc),
            },
            status=400,
        )

    if sesion_id not in target_order:
        return JsonResponse(
            {
                "ok": False,
                "error": ("MOVED_PROJECT_MISSING"),
                "message": (
                    "The moved project " "must be present in " "the target order."
                ),
            },
            status=400,
        )

    all_ids = list(
        dict.fromkeys(
            [
                sesion_id,
                *source_order,
                *target_order,
            ]
        )
    )

    with transaction.atomic():
        sessions = list(
            SesionBilling.objects.select_for_update()
            .prefetch_related(
                "tecnicos_sesion",
            )
            .filter(
                id__in=all_ids,
                is_direct_discount=False,
            )
        )

        sessions_by_id = {session.id: session for session in sessions}

        if sesion_id not in sessions_by_id:
            return JsonResponse(
                {
                    "ok": False,
                    "error": ("NOT_FOUND"),
                    "message": ("Project not found."),
                },
                status=404,
            )

        if len(sessions_by_id) != len(all_ids):
            return JsonResponse(
                {
                    "ok": False,
                    "error": ("INVALID_PROJECT_SET"),
                    "message": ("One or more " "projects are no " "longer available."),
                },
                status=409,
            )

        moved_session = sessions_by_id[sesion_id]

        status = (moved_session.estado or "").strip().lower()

        if status not in REAL_PLAN_MOVABLE_STATUSES:
            try:
                status_label = moved_session.get_estado_display()

            except Exception:
                status_label = moved_session.estado or "Unknown"

            return JsonResponse(
                {
                    "ok": False,
                    "error": ("STATUS_LOCKED"),
                    "message": (
                        "This project "
                        "cannot be moved "
                        "while its status "
                        f"is '{status_label}'."
                    ),
                    "status": status,
                    "status_label": (status_label),
                },
                status=409,
            )

        moved_signature = _active_technician_signature(moved_session)

        for billing_id in all_ids:
            candidate = sessions_by_id[billing_id]

            candidate_signature = _active_technician_signature(candidate)

            if candidate_signature != moved_signature:
                return JsonResponse(
                    {
                        "ok": False,
                        "error": ("ROW_MISMATCH"),
                        "message": (
                            "Projects cannot "
                            "be moved between "
                            "different people "
                            "or teams from "
                            "Real Plan."
                        ),
                    },
                    status=409,
                )

        previous_date = _local_date(moved_session)

        (
            moved_state,
            _created,
        ) = RealPlanProjectState.objects.select_for_update().get_or_create(
            billing=(moved_session),
            defaults={
                "original_planned_date": (previous_date),
                "planning_mode": (RealPlanProjectState.PLANNING_MODE_MANUAL),
                "updated_by": (request.user),
            },
        )

        if moved_state.original_planned_date is None:
            moved_state.original_planned_date = previous_date

        moved_state.planning_mode = RealPlanProjectState.PLANNING_MODE_MANUAL

        moved_state.carried_over = False

        moved_state.updated_by = request.user

        moved_state.save(
            update_fields=[
                "original_planned_date",
                "planning_mode",
                "carried_over",
                "updated_by",
                "updated_at",
            ]
        )

        if previous_date != target_date:
            moved_session.creado_en = _set_session_date(
                moved_session,
                target_date,
            )

            moved_session.save(
                update_fields=[
                    "creado_en",
                ]
            )

        _save_positions(
            sessions_by_id=(sessions_by_id),
            ordered_ids=(target_order),
            user=request.user,
        )

        if previous_date != target_date and source_order:
            _save_positions(
                sessions_by_id=(sessions_by_id),
                ordered_ids=(source_order),
                user=request.user,
            )

    return JsonResponse(
        {
            "ok": True,
            "changed": True,
            "billing_id": (moved_session.id),
            "project_id": (moved_session.proyecto_id),
            "previous_date": (previous_date.isoformat() if previous_date else ""),
            "date": (target_date.isoformat()),
            "target_order": (target_order),
            "source_order": (source_order),
        }
    )
