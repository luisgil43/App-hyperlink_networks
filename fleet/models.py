# fleet/models.py
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.module_loading import import_string

from facturacion.models import Proyecto

WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()


def upload_to_odometer_photo(instance, filename: str) -> str:
    """
    Compatibilidad con migración antigua 0003_vehicleodometerlog.py
    (cuando existía VehicleOdometerLog y usaba upload_to=fleet.models.upload_to_odometer_photo)
    """
    # ruta simple y estable
    return f"fleet/odometer_photos/{timezone.now():%Y/%m}/{filename}"


class Sequence(models.Model):
    """
    Correlativos no reutilizables (para códigos de servicios, etc.)
    """

    key = models.CharField(max_length=64, unique=True)
    value = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.key}: {self.value}"

    @classmethod
    def next(cls, key: str) -> int:
        obj, _ = cls.objects.select_for_update().get_or_create(key=key)
        obj.value += 1
        obj.save(update_fields=["value"])
        return obj.value


class VehicleStatus(models.Model):
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=40, default="gray")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Vehicle(models.Model):
    """
    En Hyperlink, el "kilometraje_actual" se interpreta como MILLAS.
    Mantengo el nombre para compatibilidad, pero en UI mostramos "Miles".
    """

    patente = models.CharField(max_length=32, unique=True)

    # ✅ NUEVO: VIN (USA)
    # vin = models.CharField(max_length=17, unique=True, db_index=True)
    vin = models.CharField(
        max_length=17, unique=True, db_index=True, null=True, blank=True
    )

    marca = models.CharField(max_length=80, blank=True, default="")
    modelo = models.CharField(max_length=80, blank=True, default="")
    anio = models.PositiveIntegerField(null=True, blank=True)

    status = models.ForeignKey(
        VehicleStatus, on_delete=models.SET_NULL, null=True, blank=True
    )

    kilometraje_actual = models.PositiveIntegerField(
        default=0, help_text="Odometer (miles)"
    )
    last_movement_at = models.DateTimeField(null=True, blank=True)

    # Fechas informativas (si las usas en GZ)
    fecha_revision_tecnica = models.DateField(null=True, blank=True)
    fecha_permiso_circulacion = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["patente"]

    def __str__(self):
        return self.patente

    def update_kilometraje(
        self,
        value: int | None,
        strict: bool = False,
        source: str = "manual",
        project=None,
        notes: str = "",
        odometer_photo=None,
        event_at=None,
    ):

        if value is None:
            return

        try:
            value = int(value)
        except Exception:
            return

        if value < 0:
            return

        current = int(self.kilometraje_actual or 0)

        if strict and value < current:
            return

        if value == current:
            return

        if event_at is None:
            event_at = timezone.now()
        else:
            # normaliza a aware
            try:
                if timezone.is_naive(event_at):
                    event_at = timezone.make_aware(
                        event_at, timezone.get_current_timezone()
                    )
            except Exception:
                pass

        ev_kwargs = {
            "vehicle": self,
            "odometer": value,
            "prev_odometer": current,
            "source": (source or "manual"),
        }

        #    Campos extendidos (si existen en el modelo/DB)
        if hasattr(VehicleOdometerEvent, "event_at"):
            ev_kwargs["event_at"] = event_at

        if hasattr(VehicleOdometerEvent, "project"):
            ev_kwargs["project"] = project if project else None

        if hasattr(VehicleOdometerEvent, "notes"):
            ev_kwargs["notes"] = (notes or "").strip()

        ev = VehicleOdometerEvent.objects.create(**ev_kwargs)

        # foto: asignar después de create para no romper si viene None o storage raro
        if odometer_photo and hasattr(ev, "odometer_photo"):
            try:
                if isinstance(odometer_photo, str):
                    ev.odometer_photo.name = odometer_photo
                else:
                    ev.odometer_photo = odometer_photo
                ev.save(update_fields=["odometer_photo"])
            except Exception:
                pass

        self.kilometraje_actual = value
        self.last_movement_at = timezone.now()
        self.save(update_fields=["kilometraje_actual", "last_movement_at"])


class VehicleAssignment(models.Model):
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.CASCADE, related_name="assignments"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vehicle_assignments",
    )

    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["vehicle"],
                condition=Q(is_active=True),
                name="uq_vehicle_one_active_assignment",
            )
        ]
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.vehicle} -> {self.user} ({'active' if self.is_active else 'closed'})"

    def close(self):
        if not self.is_active:
            return
        self.is_active = False
        self.closed_at = timezone.now()
        self.save(update_fields=["is_active", "closed_at"])


class VehicleOdometerEvent(models.Model):
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.CASCADE, related_name="odometer_events"
    )

    # ✅ fecha/hora del evento (lo que editas en el form)
    event_at = models.DateTimeField(default=timezone.now, db_index=True)

    # ✅ proyecto y metadatos
    project = models.ForeignKey(
        "facturacion.Proyecto", on_delete=models.SET_NULL, null=True, blank=True
    )
    notes = models.TextField(blank=True, default="")

    # ✅ foto del tablero
    odometer_photo = models.FileField(
        upload_to="fleet/odometer_photos/",
        storage=wasabi_storage,
        blank=True,
        null=True,
        max_length=1024,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )

    # audit
    created_at = models.DateTimeField(auto_now_add=True)

    odometer = models.PositiveIntegerField(help_text="Odometer (miles)")
    prev_odometer = models.PositiveIntegerField(
        default=0, help_text="Previous odometer (miles)"
    )
    source = models.CharField(max_length=64, default="manual")

    class Meta:
        ordering = ["-event_at", "-id"]

    def __str__(self):
        return f"{self.vehicle.patente}: {self.prev_odometer} -> {self.odometer} ({self.source})"

    @property
    def delta_since_last(self) -> int:
        try:
            return int(self.odometer or 0) - int(self.prev_odometer or 0)
        except Exception:
            return 0


class VehicleServiceType(models.Model):
    """
    Tipo configurable de servicio/mantención.
    interval_km/interval_days => en Hyperlink interval_km lo interpretamos como MILLAS.
    """

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    interval_km = models.PositiveIntegerField(
        null=True, blank=True, help_text="Interval (miles)"
    )
    interval_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Interval (days)"
    )

    alert_before_km = models.PositiveIntegerField(
        null=True, blank=True, help_text="Alert before (miles)"
    )
    alert_before_days = models.PositiveIntegerField(
        null=True, blank=True, help_text="Alert before (days)"
    )

    alert_before_km_steps = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="CSV miles steps: e.g. 1000,500,100",
    )
    alert_before_days_steps = models.CharField(
        max_length=120, blank=True, default="", help_text="CSV days steps: e.g. 30,10,5"
    )

    notify_on_overdue = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_km_steps(self) -> list[int]:
        out: list[int] = []
        for x in (self.alert_before_km_steps or "").split(","):
            x = (x or "").strip()
            if not x:
                continue
            try:
                out.append(int(x))
            except Exception:
                continue
        if out:
            out = sorted(set(out), reverse=True)
        elif self.alert_before_km is not None:
            out = [int(self.alert_before_km)]
        return out

    def get_day_steps(self) -> list[int]:
        out: list[int] = []
        for x in (self.alert_before_days_steps or "").split(","):
            x = (x or "").strip()
            if not x:
                continue
            try:
                out.append(int(x))
            except Exception:
                continue
        if out:
            out = sorted(set(out), reverse=True)
        elif self.alert_before_days is not None:
            out = [int(self.alert_before_days)]
        return out


class VehicleService(models.Model):
    """
    Registro de un servicio realizado.
    next_due_km/next_due_date => en Hyperlink, next_due_km es en MILLAS.
    """

    LEGACY_CHOICES = [
        ("combustible", "Fuel"),
        ("aceite", "Oil change"),
        ("neumaticos", "Tires"),
        ("revision_tecnica", "Technical inspection"),
        ("permiso_circulacion", "Vehicle permit"),
        ("otro", "Other"),
    ]

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.CASCADE, related_name="services"
    )

    service_code = models.PositiveIntegerField(default=0, editable=False)

    service_type = models.CharField(
        max_length=40, choices=LEGACY_CHOICES, default="otro"
    )
    service_type_obj = models.ForeignKey(
        VehicleServiceType, on_delete=models.SET_NULL, null=True, blank=True
    )

    title = models.CharField(max_length=200, blank=True, default="")

    service_date = models.DateField(default=timezone.localdate)
    service_time = models.TimeField(null=True, blank=True)

    kilometraje_declarado = models.PositiveIntegerField(
        null=True, blank=True, help_text="Odometer (miles)"
    )
    monto = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    notes = models.TextField(blank=True, default="")

    next_due_km = models.PositiveIntegerField(
        null=True, blank=True, help_text="Next due (miles)"
    )
    next_due_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-service_date", "-id"]

    def __str__(self):
        return (
            f"{self.vehicle.patente} - {self.title or self.get_service_type_display()}"
        )

    def save(self, *args, **kwargs):
        creating = self.pk is None

        # correlativo
        if creating and not self.service_code:
            from django.db import transaction

            with transaction.atomic():
                self.service_code = Sequence.next("vehicle_service_code")

        super().save(*args, **kwargs)

        # update odometer vehicle si viene declarado
        if self.kilometraje_declarado is not None:
            self.vehicle.update_kilometraje(
                self.kilometraje_declarado, strict=False, source="service"
            )

        # recalcular due
        self.recompute_next_due(save_self=True)

    def recompute_next_due(self, save_self: bool = True):
        st = self.service_type_obj
        next_km = None
        next_date = None

        if self.kilometraje_declarado is not None:
            base_km = int(self.kilometraje_declarado)
        else:
            base_km = int(self.vehicle.kilometraje_actual or 0)

        if st and st.interval_km:
            next_km = base_km + int(st.interval_km)

        if st and st.interval_days:
            try:
                next_date = self.service_date + timezone.timedelta(
                    days=int(st.interval_days)
                )
            except Exception:
                next_date = None

        changed = False
        if self.next_due_km != next_km:
            self.next_due_km = next_km
            changed = True
        if self.next_due_date != next_date:
            self.next_due_date = next_date
            changed = True

        if save_self and changed:
            VehicleService.objects.filter(pk=self.pk).update(
                next_due_km=self.next_due_km,
                next_due_date=self.next_due_date,
            )


class VehicleNotificationConfig(models.Model):
    """
    Configuración por vehículo para notificaciones (email).
    (Luego lo ocupamos en el cron de alertas de mantención)
    """

    vehicle = models.OneToOneField(
        Vehicle, on_delete=models.CASCADE, related_name="notification_cfg"
    )

    enabled = models.BooleanField(default=False)
    include_assigned_driver = models.BooleanField(default=True)

    # Comma-separated
    extra_emails_to = models.TextField(blank=True, default="")
    extra_emails_cc = models.TextField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notifications({self.vehicle.patente})"


class FlotaCronDiarioEjecutado(models.Model):
    nombre = models.CharField(max_length=80)
    fecha = models.DateField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["nombre", "fecha"], name="uniq_fleet_cron_por_dia"
            )
        ]

    def __str__(self):
        return f"{self.nombre} - {self.fecha}"


class FlotaAlertaEnviada(models.Model):
    MODE_CHOICES = [
        ("pre_km", "Pre miles"),
        ("pre_days", "Pre days"),
        ("overdue_km", "Overdue miles"),
        ("overdue_days", "Overdue days"),
    ]

    vehicle = models.ForeignKey(
        "fleet.Vehicle", on_delete=models.CASCADE, related_name="alerts_sent"
    )
    service_type = models.ForeignKey(
        "fleet.VehicleServiceType", on_delete=models.CASCADE, related_name="alerts_sent"
    )
    base_service = models.ForeignKey(
        "fleet.VehicleService", on_delete=models.CASCADE, related_name="alerts_base"
    )

    mode = models.CharField(max_length=20, choices=MODE_CHOICES)

    # pre: threshold (1000/500/100 or 10/7/1)
    # overdue: 0
    threshold = models.PositiveIntegerField(default=0)

    # pre: NULL
    # overdue: date sent (1/day)
    sent_on = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["vehicle", "service_type", "base_service", "mode"]),
            models.Index(fields=["sent_on"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "vehicle",
                    "service_type",
                    "base_service",
                    "mode",
                    "threshold",
                    "sent_on",
                ],
                name="uniq_fleet_alert_combo",
            ),
        ]

    def __str__(self):
        return f"{self.vehicle_id} {self.service_type_id} {self.mode} thr={self.threshold} on={self.sent_on}"
