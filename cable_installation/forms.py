from decimal import Decimal, InvalidOperation

from django import forms

from .models import CableRequirement


def _clean_decimal_or_zero(value):
    if value in (None, "", " "):
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (InvalidOperation, ValueError, TypeError):
        raise forms.ValidationError("Enter a valid number.")


class CableRequirementForm(forms.ModelForm):
    required = forms.TypedChoiceField(
        label="Required",
        choices=(
            ("yes", "Yes"),
            ("no", "No"),
        ),
        coerce=lambda v: str(v).strip().lower()
        in {"yes", "true", "1", "si", "sí", "s"},
        empty_value=True,
        initial="yes",
        widget=forms.Select(
            attrs={
                "class": "w-full border rounded-xl p-2",
            }
        ),
    )

    class Meta:
        model = CableRequirement
        fields = [
            "handhole",
            "planned_reserve_ft",
            "warning",
            "required",
            "order",
        ]
        widgets = {
            "handhole": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl p-2",
                    "placeholder": "Handhole",
                }
            ),
            "planned_reserve_ft": forms.NumberInput(
                attrs={
                    "class": "w-full border rounded-xl p-2",
                    "placeholder": "Optional",
                    "step": "any",
                    "min": "0",
                }
            ),
            "warning": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl p-2",
                    "placeholder": "Optional warning for technician",
                }
            ),
            "order": forms.NumberInput(
                attrs={
                    "class": "w-full border rounded-xl p-2",
                    "min": "0",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Si viene desde instancia bool, convertirlo para el select yes/no
        current_required = getattr(self.instance, "required", True)
        if "required" not in self.initial:
            self.initial["required"] = "yes" if current_required else "no"

        # Mostrar 30 y no 30.00
        reserve = getattr(self.instance, "planned_reserve_ft", None)
        if reserve is not None and "planned_reserve_ft" not in self.initial:
            reserve = Decimal(reserve)
            self.initial["planned_reserve_ft"] = (
                int(reserve)
                if reserve == reserve.to_integral()
                else reserve.normalize()
            )

    def clean_handhole(self):
        value = (self.cleaned_data.get("handhole") or "").strip()
        if not value:
            raise forms.ValidationError("Handhole is required.")
        return value

    def clean_planned_reserve_ft(self):
        value = self.cleaned_data.get("planned_reserve_ft")
        value = _clean_decimal_or_zero(value)

        if value < 0:
            raise forms.ValidationError("Reserve cannot be negative.")

        return value

    def clean_order(self):
        value = self.cleaned_data.get("order")
        if value in (None, ""):
            return 0
        if value < 0:
            raise forms.ValidationError("Order cannot be negative.")
        return value


class CableRequirementImportRowForm(forms.Form):
    handhole = forms.CharField(
        required=True,
        max_length=120,
    )
    planned_reserve_ft = forms.CharField(
        required=False,
    )
    warning = forms.CharField(
        required=False,
        max_length=255,
    )
    required = forms.CharField(
        required=False,
    )
    order = forms.CharField(
        required=False,
    )

    def clean_handhole(self):
        value = (self.cleaned_data.get("handhole") or "").strip()
        if not value:
            raise forms.ValidationError("Handhole is required.")
        return value

    def clean_planned_reserve_ft(self):
        raw = self.cleaned_data.get("planned_reserve_ft")
        return _clean_decimal_or_zero(raw)

    def clean_required(self):
        raw = self.cleaned_data.get("required")

        if raw in (None, "", " "):
            return True

        text = str(raw).strip().lower()

        yes_values = {"1", "true", "t", "yes", "y", "si", "sí", "s"}
        no_values = {"0", "false", "f", "no", "n"}

        if text in yes_values:
            return True
        if text in no_values:
            return False

        raise forms.ValidationError(
            "Required must be yes/no, si/no, true/false, or 1/0."
        )

    def clean_order(self):
        raw = self.cleaned_data.get("order")
        if raw in (None, "", " "):
            return 0

        try:
            value = int(str(raw).strip())
        except Exception:
            raise forms.ValidationError("Order must be an integer.")

        if value < 0:
            raise forms.ValidationError("Order cannot be negative.")

        return value
