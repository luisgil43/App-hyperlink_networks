# fleet/forms.py
from __future__ import annotations

from django import forms
from django.utils import timezone

from .models import Vehicle, VehicleAssignment


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            "name",
            "is_active",
            "make",
            "model",
            "year",
            "purchase_price",
            "notes",
            "plate",
            "plate_state",
            "vin",
            "serials",
            "odometer_unit",
            "initial_odometer",
        ]

    def clean_year(self):
        year = self.cleaned_data.get("year")
        current = timezone.localdate().year
        if year is None:
            return year
        if year < 1980 or year > (current + 1):
            raise forms.ValidationError("Please enter a valid vehicle year.")
        return year

    def clean_vin(self):
        vin = (self.cleaned_data.get("vin") or "").strip()
        if len(vin) < 11:
            raise forms.ValidationError("VIN looks too short. Please verify.")
        return vin


class VehicleAssignmentForm(forms.ModelForm):
    class Meta:
        model = VehicleAssignment
        fields = [
            "vehicle",
            "project",
            "assigned_to",
            "supervisor",
            "pm",
            "start_date",
            "notes",
            "is_active",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        data = super().clean()
        vehicle = data.get("vehicle")
        is_active = data.get("is_active")
        if vehicle and is_active:
            # UniqueConstraint will protect too, but we make error nicer.
            qs = VehicleAssignment.objects.filter(vehicle=vehicle, is_active=True)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    "This vehicle already has an active assignment. End it first before creating a new one."
                )
        return data
    

# fleet/forms.py  (ADD)
from .models import VehicleOdometerLog


class VehicleOdometerLogForm(forms.ModelForm):
    class Meta:
        model = VehicleOdometerLog
        fields = ["vehicle", "date", "odometer", "project", "notes", "odometer_photo"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_odometer(self):
        val = self.cleaned_data.get("odometer")
        if val is None or val <= 0:
            raise forms.ValidationError("Please enter a valid odometer reading.")
        return val