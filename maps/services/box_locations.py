from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from maps.models import BoxLocationHistory, GeographicBox


def _coordinate_to_decimal(value, field_name):
    if value is None or value == "":
        raise ValidationError({field_name: "This coordinate is required."})

    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        raise ValidationError({field_name: "Enter a valid coordinate."})


def _validate_source(source):
    valid_sources = {choice[0] for choice in BoxLocationHistory.SOURCE_CHOICES}

    if source not in valid_sources:
        raise ValidationError({"source": "Invalid location source."})


@transaction.atomic
def set_official_box_location(
    *,
    box,
    latitude,
    longitude,
    changed_by=None,
    source=BoxLocationHistory.SOURCE_MANUAL,
    reason="",
    require_reason_for_move=True,
):
    """
    Create or move the official Box / CTO location.

    Initial placement:
        reason may be blank.

    Existing location move:
        reason is required by default.
    """

    if not isinstance(box, GeographicBox):
        raise TypeError("box must be a GeographicBox instance.")

    _validate_source(source)

    latitude = _coordinate_to_decimal(
        latitude,
        "official_latitude",
    )

    longitude = _coordinate_to_decimal(
        longitude,
        "official_longitude",
    )

    locked_box = GeographicBox.objects.select_for_update().get(pk=box.pk)

    old_latitude = locked_box.official_latitude
    old_longitude = locked_box.official_longitude

    had_location = old_latitude is not None and old_longitude is not None

    reason = (reason or "").strip()

    coordinates_changed = old_latitude != latitude or old_longitude != longitude

    if not coordinates_changed:
        return locked_box, None

    if had_location and require_reason_for_move and not reason:
        raise ValidationError(
            {"reason": "A reason is required to move an existing location."}
        )

    locked_box.official_latitude = latitude
    locked_box.official_longitude = longitude

    locked_box.full_clean()

    locked_box.save(
        update_fields=[
            "official_latitude",
            "official_longitude",
            "updated_at",
        ]
    )

    history = BoxLocationHistory.objects.create(
        box=locked_box,
        old_latitude=old_latitude,
        old_longitude=old_longitude,
        new_latitude=latitude,
        new_longitude=longitude,
        source=source,
        reason=reason,
        changed_by=changed_by,
    )

    return locked_box, history


@transaction.atomic
def remove_official_box_location(
    *,
    box,
    changed_by=None,
    source=BoxLocationHistory.SOURCE_MANUAL,
    reason="",
):
    """
    Remove only the official geographic coordinates.

    The GeographicBox, DFN, Billing association and audit history
    remain intact.
    """

    if not isinstance(box, GeographicBox):
        raise TypeError("box must be a GeographicBox instance.")

    _validate_source(source)

    reason = (reason or "").strip()

    if not reason:
        raise ValidationError({"reason": "A reason is required to remove a location."})

    locked_box = GeographicBox.objects.select_for_update().get(pk=box.pk)

    old_latitude = locked_box.official_latitude
    old_longitude = locked_box.official_longitude

    if old_latitude is None or old_longitude is None:
        raise ValidationError(
            {"location": "This project does not currently have an official location."}
        )

    locked_box.official_latitude = None
    locked_box.official_longitude = None

    locked_box.full_clean()

    locked_box.save(
        update_fields=[
            "official_latitude",
            "official_longitude",
            "updated_at",
        ]
    )

    history = BoxLocationHistory.objects.create(
        box=locked_box,
        old_latitude=old_latitude,
        old_longitude=old_longitude,
        new_latitude=None,
        new_longitude=None,
        source=source,
        reason=reason,
        changed_by=changed_by,
    )

    return locked_box, history
