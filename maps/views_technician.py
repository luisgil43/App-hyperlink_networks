from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from maps.models import BillingBoxAssignment, BoxLocationVerification
from maps.services.location_verification import (
    location_verification_required, verify_technician_location)
from operaciones.models import SesionBillingTecnico

LOCATION_START_SESSION_KEY = "maps_location_start_authorizations"

LOCATION_START_MAX_AGE = timedelta(
    minutes=2,
)


def _get_technician_assignment_or_404(
    request,
    assignment_id,
):
    """
    Return only an active assignment that belongs to the
    authenticated technician.
    """

    assignment = get_object_or_404(
        SesionBillingTecnico.objects.select_related(
            "sesion",
        ),
        pk=assignment_id,
        tecnico=request.user,
        is_active=True,
    )

    return assignment


def _get_active_box_assignment(
    billing_session,
):
    """
    Resolve the active geographic Box / CTO associated with the
    operational billing session.

    Returns None when the billing has no active geographic
    assignment.

    Raises ValidationError when inconsistent data contains more
    than one active Box / CTO for the same billing. This prevents
    an ambiguous geographic assignment from bypassing location
    validation.
    """

    assignments = list(
        BillingBoxAssignment.objects.select_related(
            "box",
            "box__dfn",
        )
        .filter(
            billing_session=billing_session,
            active=True,
            box__active=True,
        )
        .order_by("id")[:2]
    )

    if not assignments:
        return None

    if len(assignments) > 1:
        raise ValidationError(
            "This billing has more than one active geographic " "Box / CTO assignment."
        )

    return assignments[0]


def _validation_error_message(exc):
    if hasattr(exc, "message_dict"):
        messages = []

        for field_messages in exc.message_dict.values():
            messages.extend(field_messages)

        if messages:
            return " ".join(str(message) for message in messages)

    if hasattr(exc, "messages") and exc.messages:
        return " ".join(str(message) for message in exc.messages)

    return str(exc)


def _save_start_authorization(
    request,
    *,
    assignment,
    verification,
):
    authorizations = dict(
        request.session.get(
            LOCATION_START_SESSION_KEY,
            {},
        )
    )

    authorizations[str(assignment.pk)] = {
        "verification_id": verification.pk,
    }

    request.session[LOCATION_START_SESSION_KEY] = authorizations

    request.session.modified = True


def technician_start_location_required(
    assignment,
):
    """
    Return whether this assignment currently requires geographic
    verification before Start / Continue.

    No official location:
        existing Start flow remains unchanged.

    Validation disabled by admin:
        existing Start flow remains unchanged.
    """

    box_assignment = _get_active_box_assignment(
        assignment.sesion,
    )

    if box_assignment is None:
        return False

    return location_verification_required(box_assignment.box)


def validate_start_location_authorization(
    request,
    assignment,
):
    """
    Validate the short-lived geographic authorization associated
    with this exact Start / Continue.

    Returns:
        (True, None) when geographic validation is not required.
        (True, verification) when a valid authorization exists.
        (False, None) otherwise.

    Ambiguous geographic assignments fail closed.

    This function does not consume the authorization.
    """

    try:
        box_assignment = _get_active_box_assignment(
            assignment.sesion,
        )
    except ValidationError:
        return False, None

    if box_assignment is None:
        return True, None

    box = box_assignment.box

    if not location_verification_required(box):
        return True, None

    authorizations = request.session.get(
        LOCATION_START_SESSION_KEY,
        {},
    )

    authorization = authorizations.get(str(assignment.pk))

    if not authorization:
        return False, None

    verification_id = authorization.get("verification_id")

    if not verification_id:
        return False, None

    verification = BoxLocationVerification.objects.filter(
        pk=verification_id,
        billing_session=assignment.sesion,
        box=box,
        technician=request.user,
        result=(BoxLocationVerification.RESULT_VERIFIED),
    ).first()

    if verification is None:
        return False, None

    minimum_time = timezone.now() - LOCATION_START_MAX_AGE

    if verification.verified_at < minimum_time:
        return False, None

    if (
        verification.official_latitude != box.official_latitude
        or verification.official_longitude != box.official_longitude
        or verification.validation_radius_m != box.validation_radius_m
    ):
        return False, None

    return True, verification


def consume_start_location_authorization(
    request,
    assignment,
):
    """
    Consume the authorization after a successful Start / Continue.
    """

    authorizations = dict(
        request.session.get(
            LOCATION_START_SESSION_KEY,
            {},
        )
    )

    assignment_key = str(assignment.pk)

    if assignment_key not in authorizations:
        return

    authorizations.pop(
        assignment_key,
        None,
    )

    request.session[LOCATION_START_SESSION_KEY] = authorizations

    request.session.modified = True


@login_required
@require_GET
def technician_location_check(
    request,
    assignment_id,
):
    """
    Tell the technician interface whether geographic verification
    is required before starting or resuming this assignment.

    Only information for the technician's own assigned Box / CTO
    is returned.
    """

    assignment = _get_technician_assignment_or_404(
        request,
        assignment_id,
    )

    try:
        box_assignment = _get_active_box_assignment(
            assignment.sesion,
        )
    except ValidationError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": (_validation_error_message(exc)),
            },
            status=400,
        )

    if box_assignment is None:
        return JsonResponse(
            {
                "ok": True,
                "requires_verification": False,
            }
        )

    box = box_assignment.box

    if not location_verification_required(box):
        return JsonResponse(
            {
                "ok": True,
                "requires_verification": False,
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "requires_verification": True,
            "project": {
                "project_id": (assignment.sesion.proyecto_id or "").strip(),
                "latitude": str(box.official_latitude),
                "longitude": str(box.official_longitude),
                "validation_radius_m": (box.validation_radius_m),
            },
        }
    )


@login_required
@require_POST
def technician_location_verify(
    request,
    assignment_id,
):
    """
    Validate one current technician GPS reading against the
    official Box / CTO location.

    This endpoint never changes the official Box coordinates and
    never starts the assignment.
    """

    assignment = _get_technician_assignment_or_404(
        request,
        assignment_id,
    )

    try:
        box_assignment = _get_active_box_assignment(
            assignment.sesion,
        )
    except ValidationError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": (_validation_error_message(exc)),
            },
            status=400,
        )

    if box_assignment is None:
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "This assignment does not have an active " "geographic Box / CTO."
                ),
            },
            status=400,
        )

    box = box_assignment.box

    if not location_verification_required(box):
        return JsonResponse(
            {
                "ok": True,
                "requires_verification": False,
            }
        )

    try:
        verification = verify_technician_location(
            billing_session=assignment.sesion,
            box=box,
            technician=request.user,
            captured_latitude=request.POST.get("latitude"),
            captured_longitude=request.POST.get("longitude"),
            gps_accuracy_m=request.POST.get("accuracy"),
        )

    except ValidationError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": (_validation_error_message(exc)),
            },
            status=400,
        )

    verified = verification.result == BoxLocationVerification.RESULT_VERIFIED

    if verified:
        _save_start_authorization(
            request,
            assignment=assignment,
            verification=verification,
        )

    return JsonResponse(
        {
            "ok": True,
            "requires_verification": True,
            "verification": {
                "id": verification.id,
                "result": verification.result,
                "verified": verified,
                "distance_m": str(verification.distance_m),
                "gps_accuracy_m": (
                    str(verification.gps_accuracy_m)
                    if (verification.gps_accuracy_m is not None)
                    else None
                ),
                "validation_radius_m": (verification.validation_radius_m),
            },
        }
    )
