# fleet/models.py
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Vehicle(models.Model):
    ODOMETER_UNITS = [
        ("mi", "Miles"),
        ("km", "Kilometers"),
    ]

    # Unique internal ID (like project code style)
    fleet_id = models.CharField(max_length=32, unique=True, db_index=True, blank=True, default="")

    # Required core data
    name = models.CharField(max_length=120)
    make = models.CharField(max_length=80)
    model = models.CharField(max_length=80)
    year = models.PositiveIntegerField()

    purchase_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    notes = models.TextField()

    # Identifiers (required)
    plate = models.CharField(max_length=20)
    plate_state = models.CharField(max_length=20)
    vin = models.CharField(max_length=32, unique=True, db_index=True)
    serials = models.TextField()

    # Odometer (required)
    odometer_unit = models.CharField(max_length=2, choices=ODOMETER_UNITS, default="mi")
    initial_odometer = models.PositiveIntegerField(default=0)
    last_odometer = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-is_active", "name", "id")
        constraints = [
            models.UniqueConstraint(fields=["plate_state", "plate"], name="uniq_vehicle_plate_state_plate"),
        ]
        indexes = [
            models.Index(fields=["is_active"]),
            models.Index(fields=["name"]),
            models.Index(fields=["fleet_id"]),
            models.Index(fields=["vin"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.plate_state} {self.plate})"

    @property
    def unit_label(self) -> str:
        return "mi" if self.odometer_unit == "mi" else "km"

    def save(self, *args, **kwargs):
        creating = self.pk is None

        # On create: last_odometer starts at initial_odometer
        if creating:
            if self.last_odometer in (None, 0):
                self.last_odometer = int(self.initial_odometer or 0)

        super().save(*args, **kwargs)

        # fleet_id after we have an ID
        if not self.fleet_id:
            self.fleet_id = f"VH-{self.pk:06d}"
            super().save(update_fields=["fleet_id"])


class VehicleAssignment(models.Model):
    """
    Assign a vehicle to a project + assigned person, plus supervisor/PM for notifications.
    Only ONE active assignment per vehicle at a time.
    """

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="assignments", db_index=True)

    # We use the same project model you already have in Hyperlink
    project = models.ForeignKey("facturacion.Proyecto", on_delete=models.CASCADE, related_name="vehicle_assignments", db_index=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vehicle_assignments",
        db_index=True,
    )

    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicle_assignments_as_supervisor",
        db_index=True,
    )

    pm = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicle_assignments_as_pm",
        db_index=True,
    )

    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.CharField(max_length=255, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-is_active", "-created_at", "-id")
        indexes = [
            models.Index(fields=["vehicle", "is_active"]),
            models.Index(fields=["project", "is_active"]),
            models.Index(fields=["assigned_to", "is_active"]),
        ]
        constraints = [
            # Only one active assignment per vehicle
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=models.Q(is_active=True),
                name="uniq_active_assignment_per_vehicle",
            )
        ]

    def __str__(self) -> str:
        return f"{self.vehicle} → {self.project} ({'Active' if self.is_active else 'Inactive'})"

    def close(self, when=None):
        when = when or timezone.localdate()
        self.is_active = False
        self.end_date = when
        self.save(update_fields=["is_active", "end_date"])



# fleet/models.py  (ADD AT END)
import os
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator
# Reutilizamos el storage Wasabi que ya tienes en tu proyecto (como en operaciones)
from django.utils.module_loading import import_string
from django.utils.text import slugify

WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()


def _vehicle_slug(vehicle) -> str:
    base = (getattr(vehicle, "fleet_id", "") or getattr(vehicle, "name", "") or "vehicle").strip()
    return slugify(base) or "vehicle"


def upload_to_odometer_photo(instance, filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    ext = (ext or ".jpg").lower()
    vslug = _vehicle_slug(instance.vehicle)
    return f"fleet/odometer/{vslug}/{instance.date}/odometer_{uuid4().hex}{ext}"


class VehicleOdometerLog(models.Model):
    """
    Historical odometer readings (miles or km depending on vehicle.odometer_unit).
    This is the source of truth for mileage history and service tracking.
    """

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name="odometer_logs", db_index=True)

    date = models.DateField(default=timezone.localdate, db_index=True)

    odometer = models.PositiveIntegerField()
    delta_since_last = models.PositiveIntegerField(default=0)

    # Optional linkage to project/assignment (useful for filtering)
    project = models.ForeignKey("facturacion.Proyecto", on_delete=models.SET_NULL, null=True, blank=True, db_index=True)

    notes = models.CharField(max_length=255, blank=True, default="")

    # Optional photo
    odometer_photo = models.ImageField(
        upload_to=upload_to_odometer_photo,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
        blank=True,
        null=True,
        max_length=1024,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="odometer_logs_created",
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-date", "-id")
        indexes = [
            models.Index(fields=["vehicle", "date"]),
            models.Index(fields=["vehicle", "odometer"]),
        ]

    def __str__(self):
        return f"{self.vehicle} • {self.date} • {self.odometer}{self.vehicle.unit_label}"

    def clean(self):
        # Soft validation could be added later (e.g. odometer must be >= last)
        pass

    def save(self, *args, **kwargs):
        creating = self.pk is None

        # Compute delta vs last known (vehicle.last_odometer)
        last = int(getattr(self.vehicle, "last_odometer", 0) or 0)
        self.delta_since_last = max(0, int(self.odometer or 0) - last)

        super().save(*args, **kwargs)

        # Update vehicle.last_odometer if this log is newer/higher
        v = self.vehicle
        if int(self.odometer or 0) >= int(v.last_odometer or 0):
            v.last_odometer = int(self.odometer or 0)
            v.save(update_fields=["last_odometer", "updated_at"])