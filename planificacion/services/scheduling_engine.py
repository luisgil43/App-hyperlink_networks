from datetime import timedelta
from decimal import ROUND_CEILING, Decimal


def ceil_workdays(value):
    """
    Convert a fractional working-day requirement into whole calendar
    working dates required by the planning schedule.

    Example:
        1.10 working days -> 2 working dates
        0.96 working days -> 1 working date
    """

    if value is None:
        return None

    value = Decimal(value)

    if value <= 0:
        return 0

    return int(
        value.to_integral_value(
            rounding=ROUND_CEILING,
        )
    )


def is_working_day(calendar, day):
    """
    Return whether one date is a working date for the supplied
    PlanningCalendar, respecting explicit calendar exceptions.
    """

    if calendar is None:
        return True

    exception = calendar.exceptions.filter(
        date=day,
    ).first()

    if exception is not None:
        return exception.is_working_day

    weekday_flags = {
        0: calendar.monday,
        1: calendar.tuesday,
        2: calendar.wednesday,
        3: calendar.thursday,
        4: calendar.friday,
        5: calendar.saturday,
        6: calendar.sunday,
    }

    return bool(
        weekday_flags.get(
            day.weekday(),
            False,
        )
    )


def next_working_day(calendar, day):
    """
    Return the first working date on or after `day`.
    """

    current = day

    safety = 0

    while not is_working_day(calendar, current):
        current += timedelta(days=1)

        safety += 1

        if safety > 370:
            raise RuntimeError(
                "Unable to find a working day in the supplied planning calendar."
            )

    return current


def calculate_planned_dates(
    *,
    start_date,
    calculated_workdays,
    calendar,
):
    """
    Calculate High-Level planned dates.

    The supplied start date is treated as the earliest possible start.
    If it is not a working date, the activity starts on the next
    available working date.

    Fractional capacity requirements consume whole working dates.

    Example:
        calculated_workdays = 1.10
        => 2 working dates are required.
    """

    if start_date is None:
        return None, None

    required_days = ceil_workdays(calculated_workdays)

    planned_start = next_working_day(
        calendar,
        start_date,
    )

    if required_days is None:
        return planned_start, None

    if required_days <= 0:
        return planned_start, planned_start

    current = planned_start
    consumed = 0

    safety = 0

    while True:
        if is_working_day(calendar, current):
            consumed += 1

            if consumed >= required_days:
                return planned_start, current

        current += timedelta(days=1)

        safety += 1

        if safety > 3650:
            raise RuntimeError(
                "Unable to calculate planned finish within the safety horizon."
            )
