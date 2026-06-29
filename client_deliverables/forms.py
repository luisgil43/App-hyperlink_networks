from datetime import timedelta

from django import forms
from django.utils import timezone

from .models import DeliveryPackage


class DeliveryPackageForm(forms.ModelForm):
    generate_access_key = forms.BooleanField(
        required=False,
        initial=True,
        label="Generate new numeric access key",
        help_text="The key will be shown after saving and will remain available to copy.",
    )

    custom_access_key = forms.CharField(
        required=False,
        max_length=50,
        label="Custom access key",
        help_text="Optional. Leave empty to use the recommended/generated key.",
        widget=forms.TextInput(
            attrs={
                "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                "placeholder": "Optional custom key",
                "autocomplete": "off",
            }
        ),
    )

    recommended_access_key = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.HiddenInput(),
    )

    class Meta:
        model = DeliveryPackage
        fields = [
            "name",
            "expiration_mode",
            "expiration_days",
            "expires_at",
            "requires_access_key",
            "access_key_hint",
            "message",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                    "placeholder": "Example: Project 100245 Deliverables",
                }
            ),
            "expiration_mode": forms.Select(
                attrs={
                    "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                }
            ),
            "expiration_days": forms.NumberInput(
                attrs={
                    "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                    "placeholder": "Example: 7",
                    "min": "1",
                }
            ),
            "expires_at": forms.DateTimeInput(
                attrs={
                    "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                    "type": "datetime-local",
                },
                format="%Y-%m-%dT%H:%M",
            ),
            "requires_access_key": forms.CheckboxInput(
                attrs={
                    "class": "h-5 w-5 rounded border-gray-300 text-blue-600",
                }
            ),
            "access_key_hint": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                    "placeholder": "Optional hint. Do not reveal the key.",
                }
            ),
            "message": forms.Textarea(
                attrs={
                    "class": "w-full border rounded-2xl px-4 py-3 text-sm",
                    "rows": 4,
                    "placeholder": "Optional internal/client message.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        self.generated_key = None
        self.recommended_key = kwargs.pop("recommended_access_key", "") or ""

        super().__init__(*args, **kwargs)

        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]

        if self.recommended_key:
            self.fields["recommended_access_key"].initial = self.recommended_key

        # En edición no queremos reemplazar la clave por accidente.
        if self.instance and self.instance.pk:
            self.fields["generate_access_key"].initial = False

    def clean(self):
        cleaned = super().clean()

        expiration_mode = cleaned.get("expiration_mode")
        expiration_days = cleaned.get("expiration_days")
        expires_at = cleaned.get("expires_at")

        requires_access_key = cleaned.get("requires_access_key")
        generate_access_key = cleaned.get("generate_access_key")
        custom_access_key = (cleaned.get("custom_access_key") or "").strip()

        if expiration_mode == DeliveryPackage.EXPIRATION_NONE:
            cleaned["expiration_days"] = None
            cleaned["expires_at"] = None

        elif expiration_mode == DeliveryPackage.EXPIRATION_DAYS:
            if not expiration_days:
                self.add_error(
                    "expiration_days",
                    "Enter how many days the link should remain active.",
                )
            else:
                cleaned["expires_at"] = timezone.now() + timedelta(
                    days=int(expiration_days)
                )

        elif expiration_mode == DeliveryPackage.EXPIRATION_DATE:
            cleaned["expiration_days"] = None

            if not expires_at:
                self.add_error("expires_at", "Select a specific expiration date.")

        # Si coloca clave custom o marca generar clave, automáticamente requiere clave.
        if generate_access_key or custom_access_key:
            cleaned["requires_access_key"] = True
            requires_access_key = True

        # Si requiere clave en un paquete nuevo y no tiene hash, permitimos usar recomendada/generada.
        if requires_access_key:
            existing_hash = bool(getattr(self.instance, "access_key_hash", ""))

            if not existing_hash and not generate_access_key and not custom_access_key:
                cleaned["generate_access_key"] = True

        return cleaned

    def save(self, commit=True):
        package = super().save(commit=False)

        expiration_mode = self.cleaned_data.get("expiration_mode")
        expiration_days = self.cleaned_data.get("expiration_days")
        expires_at = self.cleaned_data.get("expires_at")

        package.expiration_mode = expiration_mode
        package.expiration_days = expiration_days
        package.expires_at = expires_at

        if expiration_mode == DeliveryPackage.EXPIRATION_NONE:
            package.expiration_days = None
            package.expires_at = None

        elif expiration_mode == DeliveryPackage.EXPIRATION_DAYS and expiration_days:
            package.expires_at = timezone.now() + timedelta(days=int(expiration_days))

        elif expiration_mode == DeliveryPackage.EXPIRATION_DATE:
            package.expiration_days = None

        requires_access_key = self.cleaned_data.get("requires_access_key")
        generate_access_key = self.cleaned_data.get("generate_access_key")
        custom_access_key = (self.cleaned_data.get("custom_access_key") or "").strip()
        recommended_access_key = (
            self.cleaned_data.get("recommended_access_key")
            or self.recommended_key
            or ""
        ).strip()

        raw_key = ""

        if custom_access_key:
            raw_key = custom_access_key

        elif generate_access_key:
            raw_key = recommended_access_key or DeliveryPackage.generate_access_key()

        elif requires_access_key and not package.access_key_hash:
            raw_key = recommended_access_key or DeliveryPackage.generate_access_key()

        if raw_key:
            package.set_access_key(raw_key)
            self.generated_key = raw_key

        elif requires_access_key:
            package.requires_access_key = True

        else:
            package.set_access_key("")
            self.generated_key = None

        if commit:
            package.save()

        return package
