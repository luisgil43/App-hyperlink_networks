from decimal import Decimal, InvalidOperation
from math import asin, cos, radians, sin, sqrt

from django.core.exceptions import ValidationError
from django.db import transaction

from maps.models import BoxLocationVerification, GeographicBox

MAX_GPS_ACCURACY_M = Decimal("50.00")
EARTH_RADIUS_M = 6371000.0


def _to_decimal(value, field_name):
    if value is None or value == "":
        raise ValidationError({field_name: "This value is required."})

    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        raise ValidationError({field_name: "Enter a valid number."})


def _validate_latitude(value, field_name):
    value = _to_decimal(value, field_name)

    if value < Decimal("-90") or value > Decimal("90"):
        raise ValidationError({field_name: "Latitude must be between -90 and 90."})

    return value


def _validate_longitude(value, field_name):
    value = _to_decimal(value, field_name)

    if value < Decimal("-180") or value > Decimal("180"):
        raise ValidationError({field_name: "Longitude must be between -180 and 180."})

    return value


def _validate_accuracy(value):
    accuracy = _to_decimal(
        value,
        "gps_accuracy_m",
    )

    if accuracy < 0:
        raise ValidationError({"gps_accuracy_m": "GPS accuracy cannot be negative."})

    return accuracy


def calculate_distance_m(
    *,
    latitude_a,
    longitude_a,
    latitude_b,
    longitude_b,
):
    """
    Calculate the great-circle distance between two coordinates.

    Uses the Haversine formula and returns meters.
    """

    lat_a = radians(float(latitude_a))
    lng_a = radians(float(longitude_a))
    lat_b = radians(float(latitude_b))
    lng_b = radians(float(longitude_b))

    delta_lat = lat_b - lat_a
    delta_lng = lng_b - lng_a

    haversine = (
        sin(delta_lat / 2) ** 2 + cos(lat_a) * cos(lat_b) * sin(delta_lng / 2) ** 2
    )

    arc = 2 * asin(sqrt(haversine))

    return Decimal(str(EARTH_RADIUS_M * arc)).quantize(Decimal("0.01"))


def location_verification_required(box):
    """
    Return True only when this Box / CTO requires technician
    geographic verification before work can start.
    """

    if not isinstance(box, GeographicBox):
        raise TypeError("box must be a GeographicBox instance.")

    return bool(
        box.active and box.location_validation_enabled and box.has_official_location
    )


def verify_technician_location(
    *,
    billing_session,
    box,
    technician,
    captured_latitude,
    captured_longitude,
    gps_accuracy_m,
):
    """
    Validate and record one technician location reading.

    The official Box / CTO coordinates are always read again from
    the database while locked. Client coordinates never overwrite
    the official location.

    Results:
        verified
        outside_radius
        insufficient_accuracy
    """

    if not isinstance(box, GeographicBox):
        raise TypeError("box must be a GeographicBox instance.")

    captured_latitude = _validate_latitude(
        captured_latitude,
        "captured_latitude",
    )

    captured_longitude = _validate_longitude(
        captured_longitude,
        "captured_longitude",
    )

    gps_accuracy_m = _validate_accuracy(
        gps_accuracy_m,
    )

    locked_box = GeographicBox.objects.select_for_update().get(pk=box.pk)

    if not locked_box.active:
        raise ValidationError({"box": "This Box / CTO is not active."})

    if not locked_box.location_validation_enabled:
        raise ValidationError(
            {
                "location_validation": "Location verification is disabled "
                "for this Box / CTO."
            }
        )

    if not locked_box.has_official_location:
        raise ValidationError(
            {"location": "This Box / CTO does not have an " "official location."}
        )

    official_latitude = locked_box.official_latitude
    official_longitude = locked_box.official_longitude
    validation_radius_m = locked_box.validation_radius_m

    distance_m = calculate_distance_m(
        latitude_a=official_latitude,
        longitude_a=official_longitude,
        latitude_b=captured_latitude,
        longitude_b=captured_longitude,
    )

    if gps_accuracy_m > MAX_GPS_ACCURACY_M:
        result = BoxLocationVerification.RESULT_INSUFFICIENT_ACCURACY
    elif distance_m <= Decimal(str(validation_radius_m)):
        result = BoxLocationVerification.RESULT_VERIFIED
    else:
        result = BoxLocationVerification.RESULT_OUTSIDE_RADIUS

    verification = BoxLocationVerification.objects.create(
        billing_session=billing_session,
        box=locked_box,
        technician=technician,
        official_latitude=official_latitude,
        official_longitude=official_longitude,
        captured_latitude=captured_latitude,
        captured_longitude=captured_longitude,
        gps_accuracy_m=gps_accuracy_m,
        distance_m=distance_m,
        validation_radius_m=validation_radius_m,
        result=result,
    )

    return verification
