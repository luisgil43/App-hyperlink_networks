from django.db import transaction

from maps.models import BillingBoxAssignment, GeographicBox


@transaction.atomic
def associate_existing_located_box_to_billing(
    *,
    session,
    user=None,
):
    """
    Associate a newly created Billing session with an already-known
    geographic Box / CTO for the exact same Project ID.

    This is intentionally conservative:

    - It NEVER creates a GeographicBox.
    - It NEVER changes official coordinates.
    - It NEVER changes location history.
    - It NEVER changes historical verifications.
    - It NEVER removes assignments from older Billing sessions.
    - It only reuses an ACTIVE Box that already has an official location.
    - It is idempotent for the same Billing + Box.

    This supports legitimate repeated Billing cycles for the same
    physical Project ID, for example repair/correction work after an
    earlier Billing has already reached Invoice List.
    """

    project_id = (
        getattr(session, "proyecto_id", "")
        or ""
    ).strip()

    if not project_id:
        return None

    box = (
        GeographicBox.objects
        .select_for_update()
        .filter(
            identifier=project_id,
            active=True,
            official_latitude__isnull=False,
            official_longitude__isnull=False,
        )
        .order_by("id")
        .first()
    )

    if box is None:
        return None

    # A Billing session must never have two active geographic Boxes.
    (
        BillingBoxAssignment.objects
        .filter(
            billing_session=session,
            active=True,
        )
        .exclude(box=box)
        .update(active=False)
    )

    assignment, _ = (
        BillingBoxAssignment.objects
        .get_or_create(
            billing_session=session,
            box=box,
            defaults={
                "active": True,
                "assigned_by": user,
                "notes": (
                    "Automatically associated with an existing "
                    "located Box / CTO for the same Project ID."
                ),
            },
        )
    )

    update_fields = []

    if not assignment.active:
        assignment.active = True
        update_fields.append("active")

    if (
        assignment.assigned_by_id is None
        and user is not None
    ):
        assignment.assigned_by = user
        update_fields.append("assigned_by")

    if update_fields:
        assignment.save(update_fields=update_fields)

    return assignment
