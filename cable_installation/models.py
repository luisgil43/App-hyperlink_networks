import os
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.text import slugify

from operaciones.models import SesionBilling, SesionBillingTecnico

WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()


def upload_to_cable_evidence(instance, filename: str) -> str:
    billing = instance.assignment_requirement.assignment.sesion
    proj_slug = (
        slugify(billing.proyecto_id or f"billing-{billing.id}")
        or f"billing-{billing.id}"
    )

    tech = instance.assignment_requirement.assignment.tecnico
    tech_name = (
        getattr(tech, "get_full_name", lambda: "")()
        or getattr(tech, "username", "")
        or f"user-{tech.id}"
    )
    tech_slug = slugify(tech_name) or f"user-{tech.id}"

    base, ext = os.path.splitext(filename or "")
    ext = (ext or ".jpg").lower()
    safe_base = slugify(base) or f"photo-{uuid4().hex}"

    return (
        f"cable_installation/{proj_slug}/billing_{billing.id}/"
        f"{tech_slug}/requirements/{instance.assignment_requirement.requirement.sequence_no}/"
        f"{safe_base}{ext}"
    )


class CableRequirement(models.Model):
    billing = models.ForeignKey(
        SesionBilling,
        on_delete=models.CASCADE,
        related_name="cable_requirements",
        db_index=True,
    )

    sequence_no = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Sequential identifier inside the billing.",
    )

    handhole = models.CharField(max_length=120)

    planned_reserve_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    warning = models.CharField(max_length=255, blank=True, default="")

    required = models.BooleanField(
        default=True,
        db_index=True,
        help_text="If enabled, technician must complete this requirement.",
    )

    order = models.PositiveIntegerField(default=0)

    # ---------------------------
    # Shared measurement fields
    # ---------------------------
    start_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    end_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    installed_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expected_end_ft_low = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    expected_end_ft_high = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    end_ft_overridden = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("order", "sequence_no", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["billing", "sequence_no"],
                name="uniq_cable_requirement_sequence_per_billing",
            ),
        ]
        indexes = [
            models.Index(fields=["billing", "order"]),
            models.Index(fields=["billing", "sequence_no"]),
            models.Index(fields=["billing", "required"]),
        ]

    def __str__(self):
        return f"Billing #{self.billing_id} · Req #{self.sequence_no} · {self.handhole}"

    @staticmethod
    def next_sequence_for_billing(billing: SesionBilling) -> int:
        last = (
            CableRequirement.objects.filter(billing=billing)
            .order_by("-sequence_no")
            .values_list("sequence_no", flat=True)
            .first()
        )
        return (last or 0) + 1

    @property
    def expected_pair(self):
        if self.start_ft is None:
            return (None, None)
        reserve = self.planned_reserve_ft or Decimal("0.00")
        low = self.start_ft - reserve
        high = self.start_ft + reserve
        return (low, high)

    @property
    def is_measurement_match(self) -> bool:
        if self.start_ft is None or self.end_ft is None:
            return False
        reserve = self.planned_reserve_ft or Decimal("0.00")
        real = abs(
            (self.end_ft or Decimal("0.00")) - (self.start_ft or Decimal("0.00"))
        )
        return real == reserve

    @property
    def measurement_warning_text(self) -> str:
        if self.start_ft is None or self.end_ft is None:
            return ""
        reserve = self.planned_reserve_ft or Decimal("0.00")
        real = abs(
            (self.end_ft or Decimal("0.00")) - (self.start_ft or Decimal("0.00"))
        )
        if real == reserve:
            return ""
        low, high = self.expected_pair
        return (
            f"Expected End ft could be {low} or {high} "
            f"(difference {reserve} ft), but saved End ft is {self.end_ft} "
            f"(difference {real} ft)."
        )

    def recalculate_measurements(self):
        if self.start_ft is None:
            self.expected_end_ft_low = None
            self.expected_end_ft_high = None
            self.installed_ft = Decimal("0.00")
            self.end_ft_overridden = False
            return

        reserve = self.planned_reserve_ft or Decimal("0.00")
        self.expected_end_ft_low = self.start_ft - reserve
        self.expected_end_ft_high = self.start_ft + reserve

        if self.end_ft is None:
            self.installed_ft = Decimal("0.00")
            self.end_ft_overridden = False
            return

        self.installed_ft = abs(self.end_ft - self.start_ft)
        self.end_ft_overridden = self.installed_ft != reserve

    def save(self, *args, **kwargs):
        self.recalculate_measurements()
        super().save(*args, **kwargs)


class CableAssignmentRequirement(models.Model):
    STATUS_PENDING = "pending"
    STATUS_COMPLETED = "completed"
    STATUS_RETAKE = "retake"
    STATUS_REJECTED = "rejected"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_RETAKE, "Retake required"),
        (STATUS_REJECTED, "Rejected"),
    ]

    assignment = models.ForeignKey(
        SesionBillingTecnico,
        on_delete=models.CASCADE,
        related_name="cable_assignment_requirements",
    )
    requirement = models.ForeignKey(
        CableRequirement,
        on_delete=models.CASCADE,
        related_name="assignment_rows",
    )

    # ---------------------------------------------------------
    # Legacy fields kept for compatibility with older migrations.
    # Shared measurement now lives on CableRequirement.
    # ---------------------------------------------------------
    start_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    end_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    installed_ft = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expected_end_ft_low = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    expected_end_ft_high = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    end_ft_overridden = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    note = models.TextField(blank=True, default="")

    supervisor_note = models.TextField(blank=True, default="")
    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    last_reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cable_requirements_reviewed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("requirement__order", "requirement__sequence_no", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "requirement"],
                name="uniq_cable_assignment_requirement",
            ),
        ]
        indexes = [
            models.Index(fields=["assignment", "status"]),
            models.Index(fields=["requirement", "status"]),
        ]

    def __str__(self):
        return (
            f"Assignment {self.assignment_id} · Requirement {self.requirement_id} "
            f"· {self.status}"
        )

    @property
    def shared_start_ft(self):
        return self.requirement.start_ft

    @property
    def shared_end_ft(self):
        return self.requirement.end_ft

    @property
    def shared_installed_ft(self):
        return self.requirement.installed_ft

    @property
    def shared_expected_end_ft_low(self):
        return self.requirement.expected_end_ft_low

    @property
    def shared_expected_end_ft_high(self):
        return self.requirement.expected_end_ft_high

    @property
    def shared_measurement_warning_text(self):
        return self.requirement.measurement_warning_text


class CableEvidence(models.Model):
    REVIEW_PENDING = "pending"
    REVIEW_APPROVED = "approved"
    REVIEW_REJECTED = "rejected"

    REVIEW_STATUS_CHOICES = [
        (REVIEW_PENDING, "Pending"),
        (REVIEW_APPROVED, "Approved"),
        (REVIEW_REJECTED, "Rejected"),
    ]

    SHOT_START_CABLE = "start_cable"
    SHOT_END_CABLE = "end_cable"
    SHOT_HANDHOLE = "handhole"

    SHOT_TYPE_CHOICES = [
        (SHOT_START_CABLE, "Start cable"),
        (SHOT_END_CABLE, "End cable"),
        (SHOT_HANDHOLE, "Handhole"),
    ]

    assignment_requirement = models.ForeignKey(
        CableAssignmentRequirement,
        on_delete=models.CASCADE,
        related_name="evidences",
    )
    image = models.ImageField(
        upload_to=upload_to_cable_evidence,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
        max_length=1024,
    )
    note = models.CharField(max_length=255, blank=True, default="")
    taken_at = models.DateTimeField(default=timezone.now)

    lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    gps_accuracy_m = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )

    shot_type = models.CharField(
        max_length=20,
        choices=SHOT_TYPE_CHOICES,
        blank=True,
        default="",
        db_index=True,
    )

    review_status = models.CharField(
        max_length=20,
        choices=REVIEW_STATUS_CHOICES,
        default=REVIEW_PENDING,
        db_index=True,
    )
    review_comment = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cable_evidences_reviewed",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        indexes = [
            models.Index(fields=["assignment_requirement", "review_status"]),
            models.Index(fields=["assignment_requirement", "shot_type"]),
        ]

    def __str__(self):
        return f"Evidence #{self.id} · AR #{self.assignment_requirement_id}"

    @property
    def uploader_name(self):
        tech = getattr(self.assignment_requirement.assignment, "tecnico", None)
        if not tech:
            return "Unknown"
        return (
            getattr(tech, "get_full_name", lambda: "")()
            or getattr(tech, "username", "")
            or "Unknown"
        )

    def approve(self, user):
        self.review_status = self.REVIEW_APPROVED
        self.review_comment = ""
        self.reviewed_at = timezone.now()
        self.reviewed_by = user
        self.save(
            update_fields=[
                "review_status",
                "review_comment",
                "reviewed_at",
                "reviewed_by",
            ]
        )

    def reject(self, user, comment: str):
        self.review_status = self.REVIEW_REJECTED
        self.review_comment = (comment or "").strip()
        self.reviewed_at = timezone.now()
        self.reviewed_by = user
        self.save(
            update_fields=[
                "review_status",
                "review_comment",
                "reviewed_at",
                "reviewed_by",
            ]
        )
