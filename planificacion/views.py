from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (PlanningActivityFormSet, PlanningAssignmentForm,
                    PlanningDFNFormSet, ProductivityProfileForm)
from .models import (MasterPlan, MasterPlanActivity, MasterPlanAssignment,
                     MasterPlanVersion, PlanningActivityType,
                     PlanningAssignment, PlanningAssignmentStatus,
                     PlanningBaselineActivity, PlanningCalendar,
                     PlanningClient, PlanningDFN, PlanningLocation,
                     PlanningResourceType, ProductivityProfile)
from .services.assignment_importer import (AssignmentImportError,
                                           extract_assignment)
from .services.scheduling_engine import calculate_planned_dates


def _normalized_location_value(value):
    if value is None:
        return ""

    return value.strip()


def _get_or_create_location(
    *,
    country,
    state,
    city,
    market,
):
    country = _normalized_location_value(country)
    state = _normalized_location_value(state)
    city = _normalized_location_value(city)
    market = _normalized_location_value(market)

    if not any(
        [
            country,
            state,
            city,
            market,
        ]
    ):
        return None

    location, _ = PlanningLocation.objects.get_or_create(
        country=country,
        state=state,
        city=city,
        market=market,
    )

    return location


def _get_or_create_work_calendar(work_schedule):
    if work_schedule == PlanningAssignmentForm.WORK_SCHEDULE_MON_SUN:
        calendar_name = "Hyperlink Monday - Sunday"

        defaults = {
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": True,
            "daily_work_hours": Decimal("8.00"),
            "is_active": True,
        }

    else:
        calendar_name = "Hyperlink Monday - Saturday"

        defaults = {
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": False,
            "daily_work_hours": Decimal("8.00"),
            "is_active": True,
        }

    calendar, _ = PlanningCalendar.objects.get_or_create(
        name=calendar_name,
        defaults=defaults,
    )

    return calendar


def _calendar_working_days(calendar):
    if calendar is None:
        return "0,1,2,3,4,5,6"

    working_days = []

    if calendar.sunday:
        working_days.append("0")

    if calendar.monday:
        working_days.append("1")

    if calendar.tuesday:
        working_days.append("2")

    if calendar.wednesday:
        working_days.append("3")

    if calendar.thursday:
        working_days.append("4")

    if calendar.friday:
        working_days.append("5")

    if calendar.saturday:
        working_days.append("6")

    return ",".join(working_days)


def _get_or_create_active_master_plan(user):
    master_plan = (
        MasterPlan.objects.filter(
            is_active=True,
        )
        .order_by(
            "id",
        )
        .first()
    )

    if master_plan is not None:
        return master_plan

    return MasterPlan.objects.create(
        name="Hyperlink Master Plan",
        description=("Global High-Level planning portfolio for Hyperlink assignments."),
        created_by=user,
    )


def _get_or_create_draft_version(
    *,
    master_plan,
    user,
):
    version = (
        master_plan.versions.filter(
            status=MasterPlanVersion.Status.DRAFT,
        )
        .order_by(
            "-version_number",
            "-created_at",
            "-id",
        )
        .first()
    )

    if version is not None:
        return version

    max_version = (
        master_plan.versions.aggregate(max_version=Max("version_number"))["max_version"]
        or 0
    )

    return MasterPlanVersion.objects.create(
        master_plan=master_plan,
        version_number=max_version + 1,
        status=MasterPlanVersion.Status.DRAFT,
        title=f"Draft v{max_version + 1}",
        created_by=user,
    )


def _next_master_plan_priority(version):
    current_max = (
        version.assignment_entries.aggregate(max_priority=Max("priority"))[
            "max_priority"
        ]
        or 0
    )

    return current_max + 1


def _find_activity_type(
    *,
    code,
    name,
):
    code = (code or "").strip().upper()
    name = (name or "").strip()

    if not code:
        return None

    queryset = PlanningActivityType.objects.filter(
        code__iexact=code,
        is_active=True,
    )

    if name:
        exact = queryset.filter(name__iexact=name).first()

        if exact is not None:
            return exact

    candidates = list(queryset[:2])

    if len(candidates) == 1:
        return candidates[0]

    return None


def _resolve_or_create_activity_type(
    *,
    activity_form,
):
    existing = activity_form.cleaned_data.get("activity_type")

    if existing is not None:
        return existing

    code = activity_form.cleaned_data["new_activity_code"].strip().upper()

    name = activity_form.cleaned_data["new_activity_name"].strip()

    activity_type = (
        PlanningActivityType.objects.filter(
            code__iexact=code,
            name__iexact=name,
        )
        .order_by("id")
        .first()
    )

    if activity_type is not None:
        changed = False

        if not activity_type.is_active:
            activity_type.is_active = True
            changed = True

        if activity_type.default_unit != activity_form.cleaned_data["unit"]:
            activity_type.default_unit = activity_form.cleaned_data["unit"]
            changed = True

        if (
            activity_type.default_resource_type
            != activity_form.cleaned_data["resource_type"]
        ):
            activity_type.default_resource_type = activity_form.cleaned_data[
                "resource_type"
            ]
            changed = True

        if changed:
            activity_type.save(
                update_fields=[
                    "is_active",
                    "default_unit",
                    "default_resource_type",
                    "updated_at",
                ]
            )

        return activity_type

    activity_type = PlanningActivityType(
        code=code,
        name=name,
        default_unit=activity_form.cleaned_data["unit"],
        default_resource_type=activity_form.cleaned_data["resource_type"],
        is_active=True,
    )

    activity_type.full_clean()
    activity_type.save()

    return activity_type


def _resolve_or_create_productivity_profile(
    *,
    activity_type,
    unit,
    resource_type,
    resource_count,
    production_rate,
):
    profile = (
        ProductivityProfile.objects.filter(
            activity_type=activity_type,
            unit=unit,
            resource_type=resource_type,
            production_rate=production_rate,
            default_resource_count=resource_count,
            is_active=True,
        )
        .order_by(
            "-is_default",
            "id",
        )
        .first()
    )

    if profile is not None:
        return profile

    has_default = ProductivityProfile.objects.filter(
        activity_type=activity_type,
        is_active=True,
        is_default=True,
    ).exists()

    profile = ProductivityProfile(
        name=f"{activity_type.code} {activity_type.name} Standard",
        activity_type=activity_type,
        resource_type=resource_type,
        production_rate=production_rate,
        unit=unit,
        default_resource_count=resource_count,
        is_default=not has_default,
        is_active=True,
    )

    profile.full_clean()
    profile.save()

    return profile


def _activity_defaults():
    result = {}

    activity_types = PlanningActivityType.objects.filter(
        is_active=True,
    ).order_by(
        "display_order",
        "code",
        "name",
    )

    for activity_type in activity_types:
        profile = (
            activity_type.productivity_profiles.filter(
                is_active=True,
                is_default=True,
            )
            .order_by("id")
            .first()
        )

        if profile is None:
            profile = (
                activity_type.productivity_profiles.filter(
                    is_active=True,
                )
                .order_by("id")
                .first()
            )

        result[str(activity_type.id)] = {
            "code": activity_type.code,
            "name": activity_type.name,
            "unit": activity_type.default_unit,
            "resource_type": activity_type.default_resource_type,
            "production_rate": (
                str(profile.production_rate) if profile is not None else ""
            ),
            "resource_count": (
                str(profile.default_resource_count) if profile is not None else "1"
            ),
        }

    return result


def _build_import_form_state(import_result):
    assignment_data = import_result.get(
        "assignment",
        {},
    )

    assignment_initial = {
        "client_name": assignment_data.get(
            "client_name",
            "",
        ),
        "name": assignment_data.get(
            "name",
            "",
        ),
        "assignment_type": assignment_data.get(
            "assignment_type",
            "",
        ),
        "client_reference": assignment_data.get(
            "client_reference",
            "",
        ),
        "issue_date": assignment_data.get(
            "issue_date",
        ),
        "pc": assignment_data.get(
            "pc",
            "",
        ),
        "contractor": (
            assignment_data.get(
                "contractor",
                "",
            )
            or "HYPERLINK"
        ),
        "status": PlanningAssignmentStatus.ACTIVE,
        "country": (
            assignment_data.get(
                "country",
                "",
            )
            or "United States"
        ),
        "state": assignment_data.get(
            "state",
            "",
        ),
        "city": assignment_data.get(
            "city",
            "",
        ),
        "market": assignment_data.get(
            "market",
            "",
        ),
        "work_schedule": (PlanningAssignmentForm.WORK_SCHEDULE_MON_SAT),
        "source_reference": assignment_data.get(
            "source_reference",
            "",
        ),
    }

    dfn_initial = []

    for dfn in import_result.get(
        "dfns",
        [],
    ):
        code = (
            dfn.get(
                "code",
                "",
            )
            or ""
        ).strip()

        if not code:
            continue

        dfn_initial.append(
            {
                "code": code,
                "status": PlanningDFN.Status.ACTIVE,
            }
        )

    activity_initial = []

    imported_dfns = import_result.get(
        "dfns",
        [],
    )

    single_dfn_code = ""

    if len(imported_dfns) == 1:
        single_dfn_code = (
            imported_dfns[0].get(
                "code",
                "",
            )
            or ""
        )

    for imported_activity in import_result.get(
        "activities",
        [],
    ):
        code = (
            (
                imported_activity.get(
                    "code",
                    "",
                )
                or ""
            )
            .strip()
            .upper()
        )

        name = (
            imported_activity.get(
                "name",
                "",
            )
            or ""
        ).strip()

        activity_type = _find_activity_type(
            code=code,
            name=name,
        )

        profile = None

        if activity_type is not None:
            profile = (
                activity_type.productivity_profiles.filter(
                    is_active=True,
                    is_default=True,
                )
                .order_by("id")
                .first()
            )

            if profile is None:
                profile = (
                    activity_type.productivity_profiles.filter(
                        is_active=True,
                    )
                    .order_by("id")
                    .first()
                )

        unit = imported_activity.get(
            "unit",
            "",
        ) or (activity_type.default_unit if activity_type else "other")

        row = {
            "dfn_code": single_dfn_code,
            "quantity": imported_activity.get(
                "quantity",
            ),
            "unit": unit,
            "client_start_date": imported_activity.get(
                "client_start_date",
            ),
            "client_end_date": imported_activity.get(
                "client_end_date",
            ),
            "planned_start_date": imported_activity.get(
                "client_start_date",
            ),
            "planned_end_date": None,
            "resource_type": (
                profile.resource_type
                if profile is not None
                else (
                    activity_type.default_resource_type
                    if activity_type is not None
                    else PlanningResourceType.CREW
                )
            ),
            "resource_count": (
                profile.default_resource_count if profile is not None else Decimal("1")
            ),
            "production_rate": (
                profile.production_rate if profile is not None else None
            ),
        }

        if activity_type is not None:
            row["activity_type"] = activity_type.pk

        else:
            row["new_activity_code"] = code
            row["new_activity_name"] = name

        activity_initial.append(row)

    return (
        assignment_initial,
        dfn_initial,
        activity_initial,
    )


def _latest_plan_entry(assignment):
    return (
        assignment.master_plan_entries.select_related(
            "version",
            "version__master_plan",
            "calendar",
            "version__calendar",
            "version__master_plan__default_calendar",
        )
        .order_by(
            "-version__version_number",
            "-version__created_at",
            "-id",
        )
        .first()
    )


def _build_master_plan_rows(assignments):
    rows = []

    for assignment in assignments:
        plan_entry = _latest_plan_entry(assignment)

        plan_by_baseline = {}

        if plan_entry is not None:
            plan_activities = plan_entry.activities.select_related(
                "activity_type",
                "baseline_activity",
                "baseline_activity__dfn",
            ).order_by(
                "sequence",
                "activity_type__code",
                "id",
            )

            for plan_activity in plan_activities:
                if plan_activity.baseline_activity_id:
                    plan_by_baseline[plan_activity.baseline_activity_id] = plan_activity

        effective_calendar = (
            plan_entry.effective_calendar if plan_entry is not None else None
        )

        plan_working_days = _calendar_working_days(effective_calendar)

        baseline_activities = assignment.baseline_activities.select_related(
            "activity_type",
            "dfn",
        ).order_by(
            "sequence",
            "activity_type__code",
            "id",
        )

        for baseline_activity in baseline_activities:
            plan_activity = plan_by_baseline.get(baseline_activity.id)

            planned_start = (
                plan_activity.planned_start_date if plan_activity is not None else None
            )

            planned_finish = (
                plan_activity.planned_end_date if plan_activity is not None else None
            )

            variance_days = None

            if baseline_activity.client_end_date and planned_finish:
                variance_days = (
                    planned_finish - baseline_activity.client_end_date
                ).days

            planning_status = None

            if variance_days is not None:
                planning_status = "on_track" if variance_days <= 0 else "late"

            rows.append(
                {
                    "assignment": assignment,
                    "baseline": baseline_activity,
                    "plan": plan_activity,
                    "activity_type": baseline_activity.activity_type,
                    "quantity": baseline_activity.quantity,
                    "unit": baseline_activity.get_unit_display(),
                    "dfn": baseline_activity.dfn,
                    "client_start": baseline_activity.client_start_date,
                    "client_finish": baseline_activity.client_end_date,
                    "planned_start": planned_start,
                    "planned_finish": planned_finish,
                    "variance_days": variance_days,
                    "planning_status": planning_status,
                    "master_plan_entry": plan_entry,
                    "plan_working_days": plan_working_days,
                }
            )

    return rows


def _assignment_initial_data(
    assignment,
    latest_plan_entry,
):
    work_schedule = PlanningAssignmentForm.WORK_SCHEDULE_MON_SAT

    effective_calendar = (
        latest_plan_entry.effective_calendar if latest_plan_entry is not None else None
    )

    if effective_calendar is not None and effective_calendar.sunday:
        work_schedule = PlanningAssignmentForm.WORK_SCHEDULE_MON_SUN

    return {
        "client_name": assignment.client.name,
        "name": assignment.name,
        "assignment_type": assignment.assignment_type,
        "client_reference": assignment.client_reference,
        "issue_date": assignment.issue_date,
        "pc": assignment.pc,
        "contractor": assignment.contractor,
        "status": PlanningAssignmentStatus.ACTIVE,
        "country": (assignment.location.country if assignment.location else ""),
        "state": (assignment.location.state if assignment.location else ""),
        "city": (assignment.location.city if assignment.location else ""),
        "market": (assignment.location.market if assignment.location else ""),
        "work_schedule": work_schedule,
        "source_reference": assignment.source_reference,
        "notes": assignment.notes,
    }


def _assignment_activity_initial(
    assignment,
    latest_plan_entry,
):
    plan_by_baseline = {}

    if latest_plan_entry is not None:
        for plan_activity in latest_plan_entry.activities.all():
            if plan_activity.baseline_activity_id:
                plan_by_baseline[plan_activity.baseline_activity_id] = plan_activity

    result = []

    baseline_activities = assignment.baseline_activities.select_related(
        "activity_type",
        "dfn",
    ).order_by(
        "sequence",
        "id",
    )

    for baseline in baseline_activities:
        plan = plan_by_baseline.get(baseline.id)

        result.append(
            {
                "activity_type": baseline.activity_type_id,
                "dfn_code": (baseline.dfn.code if baseline.dfn else ""),
                "quantity": baseline.quantity,
                "unit": baseline.unit,
                "client_start_date": baseline.client_start_date,
                "client_end_date": baseline.client_end_date,
                "planned_start_date": (
                    plan.planned_start_date if plan else baseline.client_start_date
                ),
                "planned_end_date": (plan.planned_end_date if plan else None),
                "resource_type": (
                    plan.resource_type
                    if plan
                    else baseline.activity_type.default_resource_type
                ),
                "resource_count": (plan.resource_count if plan else Decimal("1")),
                "production_rate": (plan.production_rate if plan else None),
            }
        )

    return result


def _clear_assignment_planning_data(
    assignment,
    plan_entry,
):
    if plan_entry is not None:
        plan_entry.activities.all().delete()

    assignment.baseline_activities.all().delete()
    assignment.dfns.all().delete()


def _save_assignment_forms(
    *,
    request,
    form,
    dfn_formset,
    activity_formset,
    assignment=None,
):
    creating = assignment is None

    with transaction.atomic():
        client_name = form.cleaned_data["client_name"]

        client = PlanningClient.objects.filter(name__iexact=client_name).first()

        if client is None:
            client = PlanningClient.objects.create(
                name=client_name,
            )

        location = _get_or_create_location(
            country=form.cleaned_data["country"],
            state=form.cleaned_data["state"],
            city=form.cleaned_data["city"],
            market=form.cleaned_data["market"],
        )

        if creating:
            assignment = PlanningAssignment(
                created_by=request.user,
            )

        assignment.client = client
        assignment.location = location
        assignment.name = form.cleaned_data["name"]
        assignment.client_reference = form.cleaned_data["client_reference"]
        assignment.assignment_type = form.cleaned_data["assignment_type"]
        assignment.issue_date = form.cleaned_data["issue_date"]
        assignment.pc = form.cleaned_data["pc"]
        assignment.contractor = form.cleaned_data["contractor"]
        assignment.status = PlanningAssignmentStatus.ACTIVE
        assignment.source_reference = form.cleaned_data["source_reference"]
        assignment.notes = form.cleaned_data["notes"]

        assignment.full_clean()
        assignment.save()

        calendar = _get_or_create_work_calendar(form.cleaned_data["work_schedule"])

        master_plan = _get_or_create_active_master_plan(request.user)

        version = _get_or_create_draft_version(
            master_plan=master_plan,
            user=request.user,
        )

        if creating:
            master_plan_assignment = MasterPlanAssignment(
                version=version,
                assignment=assignment,
                calendar=calendar,
                priority=_next_master_plan_priority(version),
            )

            master_plan_assignment.full_clean()
            master_plan_assignment.save()

        else:
            master_plan_assignment = _latest_plan_entry(assignment)

            if master_plan_assignment is None:
                master_plan_assignment = MasterPlanAssignment(
                    version=version,
                    assignment=assignment,
                    calendar=calendar,
                    priority=_next_master_plan_priority(version),
                )

                master_plan_assignment.full_clean()
                master_plan_assignment.save()

            else:
                master_plan_assignment.calendar = calendar

                _clear_assignment_planning_data(
                    assignment,
                    master_plan_assignment,
                )

        dfn_by_code = {}

        for dfn_form in dfn_formset:
            if not dfn_form.cleaned_data:
                continue

            if dfn_form.cleaned_data.get("DELETE"):
                continue

            code = (
                dfn_form.cleaned_data.get(
                    "code",
                    "",
                )
                .strip()
                .upper()
            )

            if not code:
                continue

            if code in dfn_by_code:
                continue

            dfn = PlanningDFN(
                assignment=assignment,
                code=code,
                status=PlanningDFN.Status.ACTIVE,
                created_by=request.user,
            )

            dfn.full_clean()
            dfn.save()

            dfn_by_code[code] = dfn

        planned_starts = []
        planned_finishes = []

        sequence = 0

        for activity_form in activity_formset:
            if not activity_form.cleaned_data:
                continue

            if activity_form.cleaned_data.get("DELETE"):
                continue

            quantity = activity_form.cleaned_data.get("quantity")

            if quantity is None:
                continue

            activity_type = _resolve_or_create_activity_type(
                activity_form=activity_form
            )

            sequence += 1

            dfn_code = (
                activity_form.cleaned_data.get(
                    "dfn_code",
                    "",
                )
                .strip()
                .upper()
            )

            dfn = None

            if dfn_code:
                dfn = dfn_by_code.get(dfn_code)

                if dfn is None:
                    dfn = PlanningDFN(
                        assignment=assignment,
                        code=dfn_code,
                        status=PlanningDFN.Status.ACTIVE,
                        created_by=request.user,
                    )

                    dfn.full_clean()
                    dfn.save()

                    dfn_by_code[dfn_code] = dfn

            baseline_activity = PlanningBaselineActivity(
                assignment=assignment,
                dfn=dfn,
                activity_type=activity_type,
                sequence=sequence,
                client_start_date=activity_form.cleaned_data["client_start_date"],
                client_end_date=activity_form.cleaned_data["client_end_date"],
                quantity=quantity,
                unit=activity_form.cleaned_data["unit"],
            )

            baseline_activity.full_clean()
            baseline_activity.save()

            resource_type = activity_form.cleaned_data["resource_type"]

            resource_count = activity_form.cleaned_data["resource_count"]

            production_rate = activity_form.cleaned_data["production_rate"]

            productivity_profile = _resolve_or_create_productivity_profile(
                activity_type=activity_type,
                unit=activity_form.cleaned_data["unit"],
                resource_type=resource_type,
                resource_count=resource_count,
                production_rate=production_rate,
            )

            daily_capacity = resource_count * production_rate

            calculated_workdays = quantity / daily_capacity

            planned_start_date = (
                activity_form.cleaned_data.get("planned_start_date")
                or baseline_activity.client_start_date
            )

            planned_end_date = activity_form.cleaned_data.get("planned_end_date")

            if planned_start_date and not planned_end_date:
                (
                    calculated_start,
                    calculated_finish,
                ) = calculate_planned_dates(
                    start_date=planned_start_date,
                    calculated_workdays=calculated_workdays,
                    calendar=calendar,
                )

                planned_start_date = calculated_start
                planned_end_date = calculated_finish

            plan_activity = MasterPlanActivity(
                master_plan_assignment=master_plan_assignment,
                baseline_activity=baseline_activity,
                activity_type=activity_type,
                productivity_profile=productivity_profile,
                sequence=sequence,
                quantity=quantity,
                unit=activity_form.cleaned_data["unit"],
                resource_type=resource_type,
                resource_count=resource_count,
                production_rate=production_rate,
                planned_start_date=planned_start_date,
                planned_end_date=planned_end_date,
                calculated_workdays=calculated_workdays,
                assumptions_snapshot={
                    "work_schedule": form.cleaned_data["work_schedule"],
                    "calendar_id": calendar.id,
                    "calendar_name": calendar.name,
                },
            )

            plan_activity.full_clean()
            plan_activity.save()

            if planned_start_date:
                planned_starts.append(planned_start_date)

            if planned_end_date:
                planned_finishes.append(planned_end_date)

        master_plan_assignment.planned_start_date = (
            min(planned_starts) if planned_starts else None
        )

        master_plan_assignment.planned_end_date = (
            max(planned_finishes) if planned_finishes else None
        )

        master_plan_assignment.calendar = calendar

        master_plan_assignment.full_clean()

        master_plan_assignment.save(
            update_fields=[
                "calendar",
                "planned_start_date",
                "planned_end_date",
                "updated_at",
            ]
        )

    return assignment


@login_required
def master_plan(request):
    perspective = (
        request.GET.get(
            "view",
            "general",
        )
        .strip()
        .lower()
    )

    if perspective not in {
        "general",
        "client",
        "market",
    }:
        perspective = "general"

    selected_client = request.GET.get(
        "client",
        "",
    ).strip()

    selected_market = request.GET.get(
        "market",
        "",
    ).strip()

    search = request.GET.get(
        "q",
        "",
    ).strip()

    assignments = (
        PlanningAssignment.objects.select_related(
            "client",
            "location",
        )
        .prefetch_related(
            "dfns",
            "baseline_activities__activity_type",
            "baseline_activities__dfn",
        )
        .all()
    )

    if selected_client:
        assignments = assignments.filter(client_id=selected_client)

    if selected_market:
        assignments = assignments.filter(location__market=selected_market)

    if search:
        assignments = assignments.filter(
            Q(name__icontains=search)
            | Q(client_reference__icontains=search)
            | Q(client__name__icontains=search)
            | Q(dfns__code__icontains=search)
            | Q(baseline_activities__activity_type__code__icontains=search)
            | Q(baseline_activities__activity_type__name__icontains=search)
            | Q(location__city__icontains=search)
            | Q(location__market__icontains=search)
        ).distinct()

    assignments = list(assignments)

    master_plan_rows = _build_master_plan_rows(assignments)

    active_assignments = len(assignments)

    assignment_statuses = {}

    for row in master_plan_rows:
        assignment_id = row["assignment"].id

        existing = assignment_statuses.get(assignment_id)

        if row["planning_status"] == "late":
            assignment_statuses[assignment_id] = "late"

        elif row["planning_status"] == "on_track" and existing is None:
            assignment_statuses[assignment_id] = "on_track"

    on_track = sum(1 for value in assignment_statuses.values() if value == "on_track")

    baseline_miss = sum(1 for value in assignment_statuses.values() if value == "late")

    at_risk = 0

    clients = PlanningClient.objects.filter(is_active=True).order_by("name")

    markets = (
        PlanningLocation.objects.filter(is_active=True)
        .exclude(market="")
        .values_list(
            "market",
            flat=True,
        )
        .distinct()
        .order_by("market")
    )

    return render(
        request,
        "planificacion/master_plan.html",
        {
            "page_title": "Master Plan",
            "perspective": perspective,
            "selected_client": selected_client,
            "selected_market": selected_market,
            "search": search,
            "clients": clients,
            "markets": markets,
            "master_plan_rows": master_plan_rows,
            "active_assignments": active_assignments,
            "on_track": on_track,
            "at_risk": at_risk,
            "baseline_miss": baseline_miss,
        },
    )


@login_required
def assignment_list(request):
    selected_client = request.GET.get(
        "client",
        "",
    ).strip()

    selected_city = request.GET.get(
        "city",
        "",
    ).strip()

    search = request.GET.get(
        "q",
        "",
    ).strip()

    latest_plan_finish = (
        MasterPlanAssignment.objects.filter(assignment_id=OuterRef("pk"))
        .order_by(
            "-version__version_number",
            "-version__created_at",
            "-id",
        )
        .values("planned_end_date")[:1]
    )

    assignments = (
        PlanningAssignment.objects.select_related(
            "client",
            "location",
        )
        .annotate(
            dfn_count=Count(
                "dfns",
                distinct=True,
            ),
            activity_count=Count(
                "baseline_activities",
                distinct=True,
            ),
            client_finish=Max("baseline_activities__client_end_date"),
            high_level_finish=Subquery(latest_plan_finish),
        )
        .order_by(
            "-issue_date",
            "-id",
        )
    )

    if selected_client:
        assignments = assignments.filter(client_id=selected_client)

    if selected_city:
        assignments = assignments.filter(location__city=selected_city)

    if search:
        assignments = assignments.filter(
            Q(name__icontains=search)
            | Q(client_reference__icontains=search)
            | Q(client__name__icontains=search)
            | Q(dfns__code__icontains=search)
            | Q(baseline_activities__activity_type__code__icontains=search)
            | Q(baseline_activities__activity_type__name__icontains=search)
        ).distinct()

    clients = PlanningClient.objects.filter(is_active=True).order_by("name")

    cities = (
        PlanningLocation.objects.filter(is_active=True)
        .exclude(city="")
        .values_list(
            "city",
            flat=True,
        )
        .distinct()
        .order_by("city")
    )

    return render(
        request,
        "planificacion/assignment_list.html",
        {
            "page_title": "Assignments",
            "assignments": assignments,
            "clients": clients,
            "cities": cities,
            "selected_client": selected_client,
            "selected_city": selected_city,
            "search": search,
        },
    )


@login_required
def assignment_create(request):
    import_result = None

    if request.method == "POST":
        action = request.POST.get(
            "action",
            "create_assignment",
        )

        if action == "extract_assignment":
            uploaded_file = request.FILES.get("assignment_file")

            try:
                import_result = extract_assignment(uploaded_file)

                (
                    assignment_initial,
                    dfn_initial,
                    activity_initial,
                ) = _build_import_form_state(import_result)

                form = PlanningAssignmentForm(initial=assignment_initial)

                dfn_formset = PlanningDFNFormSet(
                    initial=dfn_initial,
                    prefix="dfns",
                )

                activity_formset = PlanningActivityFormSet(
                    initial=activity_initial,
                    prefix="activities",
                )

                messages.success(
                    request,
                    (
                        f'{import_result["filename"]} was extracted. '
                        "Review all imported values before creating the Assignment."
                    ),
                )

            except AssignmentImportError as exc:
                form = PlanningAssignmentForm()

                dfn_formset = PlanningDFNFormSet(prefix="dfns")

                activity_formset = PlanningActivityFormSet(prefix="activities")

                messages.error(
                    request,
                    str(exc),
                )

            return render(
                request,
                "planificacion/assignment_form.html",
                {
                    "page_title": "New Assignment",
                    "form": form,
                    "dfn_formset": dfn_formset,
                    "activity_formset": activity_formset,
                    "activity_defaults": _activity_defaults(),
                    "import_result": import_result,
                    "is_edit": False,
                },
            )

        form = PlanningAssignmentForm(request.POST)

        dfn_formset = PlanningDFNFormSet(
            request.POST,
            prefix="dfns",
        )

        activity_formset = PlanningActivityFormSet(
            request.POST,
            prefix="activities",
        )

        if form.is_valid() and dfn_formset.is_valid() and activity_formset.is_valid():
            assignment = _save_assignment_forms(
                request=request,
                form=form,
                dfn_formset=dfn_formset,
                activity_formset=activity_formset,
            )

            messages.success(
                request,
                (
                    "Assignment, Client Baseline and "
                    "High-Level Plan created successfully."
                ),
            )

            return redirect(
                "planificacion:assignment_detail",
                pk=assignment.pk,
            )

    else:
        form = PlanningAssignmentForm()

        dfn_formset = PlanningDFNFormSet(prefix="dfns")

        activity_formset = PlanningActivityFormSet(prefix="activities")

    return render(
        request,
        "planificacion/assignment_form.html",
        {
            "page_title": "New Assignment",
            "form": form,
            "dfn_formset": dfn_formset,
            "activity_formset": activity_formset,
            "activity_defaults": _activity_defaults(),
            "import_result": import_result,
            "is_edit": False,
        },
    )


@login_required
def assignment_edit(request, pk):
    assignment = get_object_or_404(
        PlanningAssignment.objects.select_related(
            "client",
            "location",
        ),
        pk=pk,
    )

    latest_plan_entry = _latest_plan_entry(assignment)

    if request.method == "POST":
        form = PlanningAssignmentForm(request.POST)

        dfn_formset = PlanningDFNFormSet(
            request.POST,
            prefix="dfns",
        )

        activity_formset = PlanningActivityFormSet(
            request.POST,
            prefix="activities",
        )

        if form.is_valid() and dfn_formset.is_valid() and activity_formset.is_valid():
            assignment = _save_assignment_forms(
                request=request,
                form=form,
                dfn_formset=dfn_formset,
                activity_formset=activity_formset,
                assignment=assignment,
            )

            messages.success(
                request,
                "Assignment updated successfully.",
            )

            return redirect(
                "planificacion:assignment_detail",
                pk=assignment.pk,
            )

    else:
        form = PlanningAssignmentForm(
            initial=_assignment_initial_data(
                assignment,
                latest_plan_entry,
            )
        )

        dfn_formset = PlanningDFNFormSet(
            initial=[
                {
                    "code": dfn.code,
                    "status": PlanningDFN.Status.ACTIVE,
                }
                for dfn in assignment.dfns.all()
            ],
            prefix="dfns",
        )

        activity_formset = PlanningActivityFormSet(
            initial=_assignment_activity_initial(
                assignment,
                latest_plan_entry,
            ),
            prefix="activities",
        )

    return render(
        request,
        "planificacion/assignment_form.html",
        {
            "page_title": f"Edit {assignment.name}",
            "form": form,
            "dfn_formset": dfn_formset,
            "activity_formset": activity_formset,
            "activity_defaults": _activity_defaults(),
            "import_result": None,
            "is_edit": True,
            "assignment": assignment,
        },
    )


@login_required
def assignment_delete(request, pk):
    assignment = get_object_or_404(
        PlanningAssignment,
        pk=pk,
    )

    if request.method != "POST":
        return redirect(
            "planificacion:assignment_detail",
            pk=assignment.pk,
        )

    assignment_name = assignment.name

    with transaction.atomic():
        assignment.master_plan_entries.all().delete()
        assignment.delete()

    messages.success(
        request,
        f'Assignment "{assignment_name}" deleted successfully.',
    )

    return redirect("planificacion:assignment_list")


@login_required
def assignment_detail(request, pk):
    assignment = get_object_or_404(
        PlanningAssignment.objects.select_related(
            "client",
            "location",
            "created_by",
        ).prefetch_related(
            "dfns",
            "baseline_activities__activity_type",
            "baseline_activities__dfn",
        ),
        pk=pk,
    )

    baseline_activities = list(assignment.baseline_activities.all())

    client_start = None
    client_finish = None

    if baseline_activities:
        start_dates = [
            item.client_start_date
            for item in baseline_activities
            if item.client_start_date
        ]

        finish_dates = [
            item.client_end_date for item in baseline_activities if item.client_end_date
        ]

        if start_dates:
            client_start = min(start_dates)

        if finish_dates:
            client_finish = max(finish_dates)

    latest_plan_entry = _latest_plan_entry(assignment)

    plan_activities = []
    plan_by_baseline_id = {}

    if latest_plan_entry is not None:
        plan_activities = list(
            latest_plan_entry.activities.select_related(
                "activity_type",
                "baseline_activity",
                "baseline_activity__dfn",
                "productivity_profile",
            ).order_by(
                "sequence",
                "activity_type__code",
            )
        )

        plan_by_baseline_id = {
            item.baseline_activity_id: item
            for item in plan_activities
            if item.baseline_activity_id
        }

    activity_rows = []

    for baseline_activity in baseline_activities:
        plan_activity = plan_by_baseline_id.get(baseline_activity.id)

        variance_days = None

        if (
            baseline_activity.client_end_date
            and plan_activity
            and plan_activity.planned_end_date
        ):
            variance_days = (
                plan_activity.planned_end_date - baseline_activity.client_end_date
            ).days

        activity_rows.append(
            {
                "baseline": baseline_activity,
                "plan": plan_activity,
                "variance_days": variance_days,
            }
        )

    overall_variance_days = None

    if client_finish and latest_plan_entry and latest_plan_entry.planned_end_date:
        overall_variance_days = (
            latest_plan_entry.planned_end_date - client_finish
        ).days

    effective_calendar = (
        latest_plan_entry.effective_calendar if latest_plan_entry else None
    )

    return render(
        request,
        "planificacion/assignment_detail.html",
        {
            "page_title": assignment.name,
            "assignment": assignment,
            "dfns": assignment.dfns.all(),
            "baseline_activities": baseline_activities,
            "activity_rows": activity_rows,
            "client_start": client_start,
            "client_finish": client_finish,
            "latest_plan_entry": latest_plan_entry,
            "plan_activities": plan_activities,
            "effective_calendar": effective_calendar,
            "overall_variance_days": overall_variance_days,
        },
    )


def _save_productivity_profile(
    *,
    form,
):
    profile = form.save(commit=False)

    if not profile.is_active:
        profile.is_default = False

    profile.full_clean()
    profile.save()

    if profile.is_active and profile.is_default:
        (
            ProductivityProfile.objects.filter(
                activity_type=profile.activity_type,
                is_active=True,
                is_default=True,
            )
            .exclude(pk=profile.pk)
            .update(is_default=False)
        )

    return profile


@login_required
def productivity_list(request):
    search = request.GET.get(
        "q",
        "",
    ).strip()

    selected_activity = request.GET.get(
        "activity",
        "",
    ).strip()

    selected_state = request.GET.get(
        "state",
        "active",
    ).strip()

    profiles = ProductivityProfile.objects.select_related(
        "activity_type",
    ).all()

    if selected_activity:
        profiles = profiles.filter(activity_type_id=selected_activity)

    if selected_state == "active":
        profiles = profiles.filter(is_active=True)

    elif selected_state == "inactive":
        profiles = profiles.filter(is_active=False)

    if search:
        profiles = profiles.filter(
            Q(name__icontains=search)
            | Q(activity_type__code__icontains=search)
            | Q(activity_type__name__icontains=search)
            | Q(resource_type__icontains=search)
            | Q(unit__icontains=search)
        )

    profiles = profiles.order_by(
        "activity_type__display_order",
        "activity_type__code",
        "activity_type__name",
        "-is_default",
        "name",
        "id",
    )

    activities = PlanningActivityType.objects.filter(
        is_active=True,
    ).order_by(
        "display_order",
        "code",
        "name",
    )

    all_profiles = ProductivityProfile.objects.all()

    active_count = all_profiles.filter(is_active=True).count()

    default_count = all_profiles.filter(
        is_active=True,
        is_default=True,
    ).count()

    activity_count = (
        all_profiles.filter(is_active=True)
        .values("activity_type_id")
        .distinct()
        .count()
    )

    return render(
        request,
        "planificacion/productivity_list.html",
        {
            "page_title": "Productivity",
            "profiles": profiles,
            "activities": activities,
            "selected_activity": selected_activity,
            "selected_state": selected_state,
            "search": search,
            "active_count": active_count,
            "default_count": default_count,
            "activity_count": activity_count,
        },
    )


@login_required
def productivity_create(request):
    if request.method == "POST":
        form = ProductivityProfileForm(request.POST)

        if form.is_valid():
            with transaction.atomic():
                profile = _save_productivity_profile(form=form)

            messages.success(
                request,
                (f'Productivity profile "{profile.name}" ' "created successfully."),
            )

            return redirect("planificacion:productivity_list")

    else:
        form = ProductivityProfileForm(
            initial={
                "default_resource_count": Decimal("1"),
                "is_active": True,
            }
        )

    return render(
        request,
        "planificacion/productivity_form.html",
        {
            "page_title": "New Productivity Profile",
            "form": form,
            "profile": None,
            "is_edit": False,
        },
    )


@login_required
def productivity_edit(request, pk):
    profile = get_object_or_404(
        ProductivityProfile.objects.select_related("activity_type"),
        pk=pk,
    )

    if request.method == "POST":
        form = ProductivityProfileForm(
            request.POST,
            instance=profile,
        )

        if form.is_valid():
            with transaction.atomic():
                profile = _save_productivity_profile(form=form)

            messages.success(
                request,
                (f'Productivity profile "{profile.name}" ' "updated successfully."),
            )

            return redirect("planificacion:productivity_list")

    else:
        form = ProductivityProfileForm(instance=profile)

    return render(
        request,
        "planificacion/productivity_form.html",
        {
            "page_title": f"Edit {profile.name}",
            "form": form,
            "profile": profile,
            "is_edit": True,
        },
    )


@login_required
def productivity_delete(request, pk):
    profile = get_object_or_404(
        ProductivityProfile.objects.select_related("activity_type"),
        pk=pk,
    )

    if request.method != "POST":
        return redirect("planificacion:productivity_list")

    profile_name = profile.name

    with transaction.atomic():
        profile.is_active = False
        profile.is_default = False

        profile.save(
            update_fields=[
                "is_active",
                "is_default",
                "updated_at",
            ]
        )

    messages.success(
        request,
        (f'Productivity profile "{profile_name}" ' "was deactivated."),
    )

    return redirect("planificacion:productivity_list")
