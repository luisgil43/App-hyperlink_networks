# fleet/forms.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from facturacion.models import Proyecto

from .models import (Vehicle, VehicleAssignment, VehicleNotificationConfig,
                     VehicleOdometerEvent, VehicleService, VehicleServiceType,
                     VehicleStatus)

User = get_user_model()


class VehicleForm(forms.ModelForm):
    """
    Form alineado al modelo REAL: fleet.models.Vehicle
    Nota: kilometraje_actual se interpreta como MILLAS en Hyperlink.
    """

    class Meta:
        model = Vehicle
        fields = [
            "patente",
            "vin",  # ✅ NUEVO
            "marca",
            "modelo",
            "anio",
            "status",
            "kilometraje_actual",
            "fecha_revision_tecnica",
            "fecha_permiso_circulacion",
        ]
        labels = {
            "patente": "Plate",
            "vin": "VIN",  # ✅ NUEVO
            "marca": "Make",
            "modelo": "Model",
            "anio": "Year",
            "status": "Status",
            "kilometraje_actual": "Odometer (miles)",
            "fecha_revision_tecnica": "Technical inspection date",
            "fecha_permiso_circulacion": "Vehicle permit date",
        }
        widgets = {
            "patente": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "vin": forms.TextInput(  # ✅ NUEVO
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "17",
                    "placeholder": "17-char VIN",
                    "autocomplete": "off",
                }
            ),
            "marca": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "modelo": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "anio": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "1900"}
            ),
            "status": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "kilometraje_actual": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "fecha_revision_tecnica": forms.DateInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "type": "date"}
            ),
            "fecha_permiso_circulacion": forms.DateInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "type": "date"}
            ),
        }

    def clean_patente(self):
        val = (self.cleaned_data.get("patente") or "").strip()
        if not val:
            raise ValidationError("Plate is required.")
        return val.upper()

    def clean_vin(self):
        vin = (self.cleaned_data.get("vin") or "").strip().upper()
        vin = vin.replace(" ", "")

        if not vin:
            raise ValidationError("VIN is required.")

        if len(vin) != 17:
            raise ValidationError("VIN must be exactly 17 characters.")

        # opcional (más estricto): VIN normalmente no usa I, O, Q
        # if any(c in vin for c in ("I", "O", "Q")):
        #     raise ValidationError("VIN cannot contain I, O, or Q.")

        return vin

    def clean_kilometraje_actual(self):
        v = self.cleaned_data.get("kilometraje_actual")
        if v is None:
            return 0
        try:
            v = int(v)
        except Exception:
            raise ValidationError("Invalid odometer value.")
        if v < 0:
            raise ValidationError("Odometer cannot be negative.")
        return v


class VehicleStatusForm(forms.ModelForm):
    class Meta:
        model = VehicleStatus
        fields = ["name", "color", "is_active"]
        labels = {"name": "Name", "color": "Color", "is_active": "Active"}
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "color": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "gray",
                }
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "w-4 h-4"}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise ValidationError("Name is required.")
        return name


class VehicleAssignmentForm(forms.ModelForm):
    """
    Asignación de vehículo a usuario.
    Respeta: solo 1 asignación activa por vehículo (constraint).
    """

    class Meta:
        model = VehicleAssignment
        fields = ["vehicle", "user", "is_active"]
        labels = {"vehicle": "Vehicle", "user": "Driver", "is_active": "Active"}
        widgets = {
            "vehicle": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "user": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "is_active": forms.CheckboxInput(attrs={"class": "w-4 h-4"}),
        }

    def clean(self):
        cleaned = super().clean()
        vehicle = cleaned.get("vehicle")
        user = cleaned.get("user")
        is_active = cleaned.get("is_active")

        if not vehicle or not user:
            return cleaned

        if is_active:
            qs = VehicleAssignment.objects.filter(vehicle=vehicle, is_active=True)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    "This vehicle already has an active assignment. Close it first."
                )

        return cleaned


class VehicleOdometerLogForm(forms.ModelForm):
    """
    ✅ ModelForm alineado al modelo fleet.VehicleOdometerEvent
    Usa: event_at, project, notes, odometer_photo
    """

    class Meta:
        model = VehicleOdometerEvent
        fields = [
            "vehicle",
            "event_at",
            "odometer",
            "project",
            "notes",
            "odometer_photo",
        ]
        labels = {
            "vehicle": "Vehicle",
            "event_at": "Date",
            "odometer": "Odometer",
            "project": "Project (optional)",
            "notes": "Notes",
            "odometer_photo": "Odometer photo (optional)",
        }
        widgets = {
            "vehicle": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "event_at": forms.DateTimeInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "type": "datetime-local",
                }
            ),
            "odometer": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "project": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "notes": forms.Textarea(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "rows": 3}
            ),
            "odometer_photo": forms.ClearableFileInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "accept": "image/*",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # default event_at para datetime-local (YYYY-MM-DDTHH:MM)
        if not self.initial.get("event_at"):
            now = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
            self.initial["event_at"] = now.strftime("%Y-%m-%dT%H:%M")

        self.fields["vehicle"].queryset = Vehicle.objects.all().order_by("patente")
        self.fields["project"].queryset = Proyecto.objects.filter(activo=True).order_by(
            "nombre"
        )

    def clean_odometer(self):
        v = self.cleaned_data.get("odometer")
        if v is None:
            raise ValidationError("Odometer is required.")
        try:
            v = int(v)
        except Exception:
            raise ValidationError("Invalid odometer value.")
        if v < 0:
            raise ValidationError("Odometer cannot be negative.")
        return v

    def clean_event_at(self):
        dt = self.cleaned_data.get("event_at")
        if not dt:
            raise ValidationError("Date is required.")

        # normaliza a aware/local
        try:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
        except Exception:
            pass

        # no futuro
        now = timezone.localtime(timezone.now())
        if timezone.localtime(dt) > now:
            raise ValidationError(
                "You cannot register an odometer entry in the future."
            )

        return dt


class VehicleNotificationConfigForm(forms.ModelForm):
    """
    Configuración de notificaciones por vehículo.
    - enabled: activa/desactiva
    - include_assigned_driver: incluye email del chofer asignado (si existe)
    - extra_emails_to/cc: CSV de correos adicionales
    """

    class Meta:
        model = VehicleNotificationConfig
        fields = [
            "enabled",
            "include_assigned_driver",
            "extra_emails_to",
            "extra_emails_cc",
        ]
        labels = {
            "enabled": "Enable notifications",
            "include_assigned_driver": "Include assigned driver email",
            "extra_emails_to": "Extra emails (TO)",
            "extra_emails_cc": "Extra emails (CC)",
        }
        help_texts = {
            "extra_emails_to": "Comma-separated emails.",
            "extra_emails_cc": "Comma-separated emails.",
        }
        widgets = {
            "enabled": forms.CheckboxInput(attrs={"class": "w-4 h-4"}),
            "include_assigned_driver": forms.CheckboxInput(attrs={"class": "w-4 h-4"}),
            "extra_emails_to": forms.Textarea(
                attrs={"rows": 2, "class": "w-full border rounded-xl px-3 py-2 text-sm"}
            ),
            "extra_emails_cc": forms.Textarea(
                attrs={"rows": 2, "class": "w-full border rounded-xl px-3 py-2 text-sm"}
            ),
        }

    @staticmethod
    def _parse_and_validate_csv_emails(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""

        parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
        emails = []
        for e in parts:
            if not e:
                continue
            try:
                validate_email(e)
            except Exception:
                raise ValidationError(f"Invalid email: {e}")
            emails.append(e.lower())

        # dedupe manteniendo orden
        seen = set()
        out = []
        for e in emails:
            if e in seen:
                continue
            seen.add(e)
            out.append(e)

        return ", ".join(out)

    def clean_extra_emails_to(self):
        return self._parse_and_validate_csv_emails(
            self.cleaned_data.get("extra_emails_to", "")
        )

    def clean_extra_emails_cc(self):
        return self._parse_and_validate_csv_emails(
            self.cleaned_data.get("extra_emails_cc", "")
        )


class VehicleServiceTypeForm(forms.ModelForm):
    """
    Tipo configurable de servicio.
    - interval_km = miles en Hyperlink
    - steps CSV opcional
    """

    class Meta:
        model = VehicleServiceType
        fields = [
            "name",
            "is_active",
            "interval_km",
            "interval_days",
            "alert_before_km",
            "alert_before_days",
            "alert_before_km_steps",
            "alert_before_days_steps",
            "notify_on_overdue",
        ]
        labels = {
            "name": "Name",
            "is_active": "Active",
            "interval_km": "Frequency (miles)",
            "interval_days": "Frequency (days)",
            "alert_before_km": "Alert before (miles) [single]",
            "alert_before_days": "Alert before (days) [single]",
            "alert_before_km_steps": "Alert before (miles) [multiple CSV]",
            "alert_before_days_steps": "Alert before (days) [multiple CSV]",
            "notify_on_overdue": "Notify daily when overdue",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "h-5 w-5 rounded border-gray-300"}
            ),
            "interval_km": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "interval_days": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "alert_before_km": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "alert_before_days": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "alert_before_km_steps": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "e.g. 1000,500,100",
                }
            ),
            "alert_before_days_steps": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "e.g. 30,10,5",
                }
            ),
            "notify_on_overdue": forms.CheckboxInput(
                attrs={"class": "h-5 w-5 rounded border-gray-300"}
            ),
        }

    @staticmethod
    def _clean_csv_ints(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
        out = []
        for p in parts:
            if not p:
                continue
            if not p.isdigit():
                raise ValidationError(f"Invalid CSV number: {p}")
            out.append(str(int(p)))
        # dedupe manteniendo orden
        seen = set()
        cleaned = []
        for x in out:
            if x in seen:
                continue
            seen.add(x)
            cleaned.append(x)
        return ",".join(cleaned)

    def clean_alert_before_km_steps(self):
        return self._clean_csv_ints(self.cleaned_data.get("alert_before_km_steps", ""))

    def clean_alert_before_days_steps(self):
        return self._clean_csv_ints(
            self.cleaned_data.get("alert_before_days_steps", "")
        )

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        if not name:
            self.add_error("name", "Name is required.")
        return cleaned


class VehicleServiceForm(forms.ModelForm):
    """
    Crear/editar VehicleService.
    - Si el tipo configurable existe, usa service_type_obj
    - km_declarado es miles en Hyperlink
    """

    confirm_backdated_date = forms.BooleanField(
        required=False,
        label="I confirm this service date is in the past.",
        help_text="Check to confirm if you are intentionally registering a past date.",
        widget=forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-gray-300"}),
    )
    confirm_km_below_current = forms.BooleanField(
        required=False,
        label="I confirm the odometer is below current vehicle odometer.",
        help_text="Check to confirm if this is correct.",
        widget=forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-gray-300"}),
    )
    confirm_amount_increase = forms.BooleanField(
        required=False,
        label="I confirm this amount is unusually high.",
        help_text="Check to confirm if this is correct.",
        widget=forms.CheckboxInput(attrs={"class": "h-5 w-5 rounded border-gray-300"}),
    )

    class Meta:
        model = VehicleService
        fields = [
            "vehicle",
            "service_type_obj",
            "title",
            "service_date",
            "kilometraje_declarado",
            "monto",
            "notes",
        ]
        labels = {
            "vehicle": "Vehicle",
            "service_type_obj": "Service type",
            "title": "Title (optional)",
            "service_date": "Service date",
            "kilometraje_declarado": "Odometer (miles)",
            "monto": "Amount",
            "notes": "Notes",
        }
        widgets = {
            "vehicle": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "service_type_obj": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "title": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "service_date": forms.DateInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "type": "date"}
            ),
            "kilometraje_declarado": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "min": "0"}
            ),
            "monto": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "e.g. 120.00",
                }
            ),
            "notes": forms.Textarea(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "rows": 3}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service_type_obj"].queryset = VehicleServiceType.objects.filter(
            is_active=True
        ).order_by("name")
        if not self.initial.get("service_date"):
            self.initial["service_date"] = timezone.localdate()

    def clean_monto(self):
        raw = self.cleaned_data.get("monto") or ""
        if isinstance(raw, Decimal):
            return raw
        s = str(raw).strip().replace(" ", "")
        if not s:
            return Decimal("0.00")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            val = Decimal(s)
        except InvalidOperation:
            raise ValidationError("Enter a valid amount (e.g. 120.00).")
        return val.quantize(Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()

        vehicle = cleaned.get("vehicle")
        st = cleaned.get("service_type_obj")
        service_date = cleaned.get("service_date")
        km = cleaned.get("kilometraje_declarado")
        monto = cleaned.get("monto") or Decimal("0.00")

        if not vehicle:
            return cleaned

        if not st:
            self.add_error("service_type_obj", "Please select a service type.")

        if service_date and service_date < (
            timezone.localdate() - timezone.timedelta(days=7)
        ):
            if not cleaned.get("confirm_backdated_date"):
                self.add_error("confirm_backdated_date", "Confirmation required.")

        if km is not None:
            try:
                km_int = int(km)
            except Exception:
                self.add_error("kilometraje_declarado", "Invalid odometer value.")
                return cleaned

            current = int(vehicle.kilometraje_actual or 0)
            if km_int < current and not cleaned.get("confirm_km_below_current"):
                self.add_error("confirm_km_below_current", "Confirmation required.")

        if monto >= Decimal("2000.00") and not cleaned.get("confirm_amount_increase"):
            self.add_error("confirm_amount_increase", "Confirmation required.")

        return cleaned

    def save(self, commit=True):
        obj: VehicleService = super().save(commit=False)
        if not (obj.title or "").strip():
            if obj.service_type_obj_id:
                obj.title = obj.service_type_obj.name
        if commit:
            obj.save()
        return obj
