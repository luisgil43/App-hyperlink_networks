from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

LATITUDE_VALIDATORS = [
    MinValueValidator(Decimal("-90")),
    MaxValueValidator(Decimal("90")),
]

LONGITUDE_VALIDATORS = [
    MinValueValidator(Decimal("-180")),
    MaxValueValidator(Decimal("180")),
]


class GeographicDFN(models.Model):
    """
    Geographic representation of a DFN.

    One DFN can group multiple operational Project IDs.

    Example:
        DFN: 0913RA_04

        Projects:
            0913RA_04_5005-008
            0913RA_04_5005-009
            0913RA_04_5005-009-3

    This model is intentionally independent from Plan Reader.
    """

    code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Complete DFN identifier, for example 0913RA_04.",
    )

    name = models.CharField(
        max_length=150,
        blank=True,
    )

    active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ("code",)
        indexes = [
            models.Index(
                fields=("active", "code"),
                name="maps_dfn_active_code_idx",
            ),
        ]
        verbose_name = "Geographic DFN"
        verbose_name_plural = "Geographic DFNs"

    def __str__(self):
        return f"DFN {self.code}"


class GeographicBox(models.Model):
    """
    Geographic point associated with an operational Project ID.

    ``identifier`` is the complete SesionBilling.proyecto_id.

    DFN is optional because legitimate Project IDs without a DFN
    are also supported.
    """

    dfn = models.ForeignKey(
        GeographicDFN,
        on_delete=models.PROTECT,
        related_name="boxes",
        null=True,
        blank=True,
    )

    identifier = models.CharField(
        max_length=120,
        unique=True,
        db_index=True,
        help_text="Complete operational Project ID.",
    )

    box_type = models.CharField(
        max_length=80,
        blank=True,
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    official_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=LATITUDE_VALIDATORS,
    )

    official_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=LONGITUDE_VALIDATORS,
    )

    validation_radius_m = models.PositiveIntegerField(
        default=20,
        help_text=(
            "Maximum distance in meters accepted for location verification."
        ),
    )

    location_validation_enabled = models.BooleanField(
        default=True,
        help_text=(
            "Require technician location verification before starting work "
            "when this Box / CTO has an official location."
        ),
    )

    active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ("identifier",)
        indexes = [
            models.Index(
                fields=("dfn", "active"),
                name="maps_box_dfn_active_idx",
            ),
        ]
        verbose_name = "Geographic Box / CTO"
        verbose_name_plural = "Geographic Boxes / CTOs"

    @property
    def has_official_location(self):
        return (
            self.official_latitude is not None and self.official_longitude is not None
        )

    def __str__(self):
        if self.dfn_id:
            return f"DFN {self.dfn.code} / {self.identifier}"

        return self.identifier


class BillingBoxAssignment(models.Model):
    """
    Explicit bridge between the existing Billing workflow and the
    geographic Box / CTO domain.

    SesionBilling remains the operational source of truth.
    """

    billing_session = models.ForeignKey(
        "operaciones.SesionBilling",
        on_delete=models.PROTECT,
        related_name="map_box_assignments",
    )

    box = models.ForeignKey(
        GeographicBox,
        on_delete=models.PROTECT,
        related_name="billing_assignments",
    )

    active = models.BooleanField(
        default=True,
    )

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="map_box_assignments_created",
    )

    assigned_at = models.DateTimeField(
        auto_now_add=True,
    )

    notes = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ("-assigned_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("billing_session", "box"),
                name="maps_unique_billing_box_assignment",
            ),
        ]
        indexes = [
            models.Index(
                fields=("billing_session", "active"),
                name="maps_bill_box_active_idx",
            ),
        ]
        verbose_name = "Billing Box Assignment"
        verbose_name_plural = "Billing Box Assignments"

    def __str__(self):
        return f"Billing {self.billing_session_id} -> {self.box}"


class BoxLocationHistory(models.Model):
    """
    Immutable audit trail for official Box / CTO coordinates.

    Interpretation:

    First location:
        old = NULL
        new = coordinates

    Move:
        old = previous coordinates
        new = new coordinates

    Remove:
        old = previous coordinates
        new = NULL
    """

    SOURCE_MANUAL = "manual"
    SOURCE_IMPORT = "import"
    SOURCE_OTHER = "other"

    SOURCE_CHOICES = (
        (SOURCE_MANUAL, "Manual map"),
        (SOURCE_IMPORT, "Coordinate import"),
        (SOURCE_OTHER, "Other"),
    )

    box = models.ForeignKey(
        GeographicBox,
        on_delete=models.PROTECT,
        related_name="location_history",
    )

    old_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=LATITUDE_VALIDATORS,
    )

    old_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=LONGITUDE_VALIDATORS,
    )

    new_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=LATITUDE_VALIDATORS,
    )

    new_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        validators=LONGITUDE_VALIDATORS,
    )

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
    )

    reason = models.TextField(
        blank=True,
    )

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="map_location_changes",
    )

    changed_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ("-changed_at",)
        indexes = [
            models.Index(
                fields=("box", "-changed_at"),
                name="maps_box_history_idx",
            ),
        ]
        verbose_name = "Box Location History"
        verbose_name_plural = "Box Location History"

    def __str__(self):
        return f"{self.box} @ {self.changed_at}"


class BoxLocationVerification(models.Model):
    RESULT_VERIFIED = "verified"
    RESULT_OUTSIDE_RADIUS = "outside_radius"
    RESULT_INSUFFICIENT_ACCURACY = "insufficient_accuracy"

    RESULT_CHOICES = (
        (RESULT_VERIFIED, "Verified"),
        (RESULT_OUTSIDE_RADIUS, "Outside allowed radius"),
        (
            RESULT_INSUFFICIENT_ACCURACY,
            "Insufficient GPS accuracy",
        ),
    )

    billing_session = models.ForeignKey(
        "operaciones.SesionBilling",
        on_delete=models.PROTECT,
        related_name="map_location_verifications",
    )

    box = models.ForeignKey(
        GeographicBox,
        on_delete=models.PROTECT,
        related_name="location_verifications",
    )

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="map_location_verifications",
    )

    official_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        validators=LATITUDE_VALIDATORS,
    )

    official_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        validators=LONGITUDE_VALIDATORS,
    )

    captured_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        validators=LATITUDE_VALIDATORS,
    )

    captured_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        validators=LONGITUDE_VALIDATORS,
    )

    gps_accuracy_m = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )

    distance_m = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    validation_radius_m = models.PositiveIntegerField()

    result = models.CharField(
        max_length=30,
        choices=RESULT_CHOICES,
        db_index=True,
    )

    verified_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ("-verified_at",)
        indexes = [
            models.Index(
                fields=("billing_session", "-verified_at"),
                name="maps_bill_verify_idx",
            ),
            models.Index(
                fields=("box", "-verified_at"),
                name="maps_box_verify_idx",
            ),
            models.Index(
                fields=("technician", "-verified_at"),
                name="maps_tech_verify_idx",
            ),
        ]
        verbose_name = "Box Location Verification"
        verbose_name_plural = "Box Location Verifications"

    def __str__(self):
        return f"Billing {self.billing_session_id} / " f"{self.box_id} / {self.result}"
