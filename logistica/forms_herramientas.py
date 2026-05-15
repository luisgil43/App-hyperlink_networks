# logistica/forms_herramientas.py

from __future__ import annotations

from uuid import uuid4

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    Bodega,
    Herramienta,
    HerramientaAsignacion,
    HerramientaInventario,
)


class BodegaForm(forms.ModelForm):
    class Meta:
        model = Bodega
        fields = ["nombre", "ubicacion"]
        labels = {
            "nombre": "Warehouse name",
            "ubicacion": "Location",
        }
        widgets = {
            "nombre": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring focus:ring-emerald-500",
                    "placeholder": "Warehouse name",
                }
            ),
            "ubicacion": forms.TextInput(
                attrs={
                    "class": "w-full px-4 py-2 border rounded-xl focus:ring focus:ring-emerald-500",
                    "placeholder": "Location",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["nombre"].required = True
        self.fields["ubicacion"].required = True

    def clean(self):
        cleaned = super().clean()

        nombre = (cleaned.get("nombre") or "").strip()
        ubicacion = (cleaned.get("ubicacion") or "").strip()

        if not nombre:
            self.add_error("nombre", "Warehouse name is required.")
        if not ubicacion:
            self.add_error("ubicacion", "Location is required.")

        cleaned["nombre"] = nombre
        cleaned["ubicacion"] = ubicacion
        return cleaned


class HerramientaForm(forms.ModelForm):
    sin_serial = forms.BooleanField(
        required=False,
        label="No serial number / Not applicable",
    )

    class Meta:
        model = Herramienta
        fields = [
            "nombre",
            "descripcion",
            "serial",
            "cantidad",
            "valor_comercial",
            "foto",
            "bodega",
            "status",
            "status_justificacion",
        ]
        labels = {
            "nombre": "Name",
            "descripcion": "Description",
            "serial": "Serial",
            "cantidad": "Quantity",
            "valor_comercial": "Commercial value",
            "foto": "Photo (optional)",
            "bodega": "Warehouse",
            "status": "Status",
            "status_justificacion": "Justification (required if damaged/lost/stolen)",
        }
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 3}),
            "status_justificacion": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["serial"].required = False

        self.is_create = not bool(self.instance and self.instance.pk)

        self.fields["cantidad"].widget.attrs["min"] = "1" if self.is_create else "0"

    def clean_valor_comercial(self):
        v = self.cleaned_data.get("valor_comercial")
        if v is None:
            return v

        if v < 0:
            raise ValidationError("Commercial value cannot be negative.")

        return v

    def clean_cantidad(self):
        c = self.cleaned_data.get("cantidad")

        if c is None:
            if self.is_create:
                raise ValidationError("Quantity is required.")
            return 0

        try:
            c = int(c)
        except Exception:
            raise ValidationError("Invalid quantity.")

        if c < 0:
            raise ValidationError("Quantity cannot be negative.")

        if self.is_create and c <= 0:
            raise ValidationError("Quantity must be greater than 0.")

        return c

    def _gen_serial_unico(self) -> str:
        for _ in range(30):
            gen = f"AUTO-{uuid4().hex[:10].upper()}"
            if not Herramienta.objects.filter(serial=gen).exists():
                return gen

        raise ValidationError(
            "Could not generate an automatic serial number. Please try again."
        )

    def clean(self):
        cleaned = super().clean()

        sin_serial = bool(cleaned.get("sin_serial"))
        serial = (cleaned.get("serial") or "").strip()

        if sin_serial:
            if (
                self.instance
                and self.instance.pk
                and (self.instance.serial or "").strip()
            ):
                cleaned["serial"] = self.instance.serial
            else:
                cleaned["serial"] = self._gen_serial_unico()
        else:
            if not serial:
                self.add_error(
                    "serial",
                    "Complete this field or check 'No serial number / Not applicable'.",
                )
            else:
                cleaned["serial"] = serial

        status = (cleaned.get("status") or "").strip()
        just = (cleaned.get("status_justificacion") or "").strip()

        if status in ("danada", "extraviada", "robada") and not just:
            self.add_error(
                "status_justificacion",
                "You must provide a justification for this status.",
            )

        return cleaned


class HerramientaAsignarForm(forms.Form):
    """
    Used to assign / reassign or leave unassigned.
    - asignado_a is optional:
        - If empty => the active assignment is closed, if any, and the tool stays in warehouse.
    """

    asignado_a = forms.ModelChoiceField(
        queryset=None,
        label="Assign to",
        required=False,
        empty_label="— Unassigned / keep in warehouse —",
        widget=forms.Select(attrs={"class": "w-full"}),
    )
    asignado_at = forms.DateTimeField(
        label="Assignment date",
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, user_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user_qs is None:
            raise ValueError("You must pass user_qs to the assignment form.")

        self.fields["asignado_a"].queryset = user_qs

        dt = timezone.localtime(timezone.now())
        self.initial.setdefault("asignado_at", dt.strftime("%Y-%m-%dT%H:%M"))


class InventarioUploadForm(forms.ModelForm):
    class Meta:
        model = HerramientaInventario
        fields = ["foto"]
        labels = {
            "foto": "Inventory photo",
        }
        widgets = {
            "foto": forms.ClearableFileInput(),
        }


class InventarioReviewForm(forms.Form):
    motivo_rechazo = forms.CharField(
        label="Reason (required)",
        required=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean_motivo_rechazo(self):
        m = (self.cleaned_data.get("motivo_rechazo") or "").strip()
        if not m:
            raise ValidationError("You must enter a reason.")
        return m


class RejectAssignmentForm(forms.Form):
    comentario = forms.CharField(
        label="Comment (required)",
        required=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean_comentario(self):
        c = (self.cleaned_data.get("comentario") or "").strip()
        if not c:
            raise ValidationError("You must enter a comment.")
        return c