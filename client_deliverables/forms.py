from django import forms
from django.utils import timezone

from .models import DeliveryPackage


class DeliveryPackageForm(forms.ModelForm):
    generate_access_key = forms.BooleanField(
        required=False,
        label="Generate access key",
        help_text="Generate a security key that must be sent separately.",
        widget=forms.CheckboxInput(
            attrs={
                "class": "rounded border-gray-300",
            }
        ),
    )

    custom_access_key = forms.CharField(
        required=False,
        label="Custom access key",
        help_text="Optional. Leave empty to generate a numeric key automatically.",
        widget=forms.TextInput(
            attrs={
                "class": "w-full border rounded-lg px-3 py-2",
                "placeholder": "Optional custom key",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = DeliveryPackage
        fields = (
            "name",
            "message",
            "expiration_mode",
            "expiration_days",
            "expires_at",
            "requires_access_key",
            "access_key_hint",
        )
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "placeholder": "Example: Deliverables Project 100245",
                }
            ),
            "message": forms.Textarea(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "rows": 4,
                    "placeholder": "Optional internal note or message.",
                }
            ),
            "expiration_mode": forms.Select(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                }
            ),
            "expiration_days": forms.NumberInput(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "min": "1",
                    "placeholder": "Example: 15",
                }
            ),
            "expires_at": forms.DateTimeInput(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "type": "datetime-local",
                },
                format="%Y-%m-%dT%H:%M",
            ),
            "requires_access_key": forms.CheckboxInput(
                attrs={
                    "class": "rounded border-gray-300",
                }
            ),
            "access_key_hint": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "placeholder": "Optional hint. Do not reveal the key.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        self.generated_key = None
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and self.instance.expires_at:
            self.initial["expires_at"] = timezone.localtime(
                self.instance.expires_at
            ).strftime("%Y-%m-%dT%H:%M")

    def clean(self):
        cleaned = super().clean()

        expiration_mode = cleaned.get("expiration_mode")
        expiration_days = cleaned.get("expiration_days")
        expires_at = cleaned.get("expires_at")

        requires_access_key = cleaned.get("requires_access_key")
        generate_access_key = cleaned.get("generate_access_key")
        custom_access_key = (cleaned.get("custom_access_key") or "").strip()

        if expiration_mode == DeliveryPackage.EXPIRATION_DAYS:
            if not expiration_days:
                self.add_error(
                    "expiration_days",
                    "Enter the number of days before this link expires.",
                )

        if expiration_mode == DeliveryPackage.EXPIRATION_DATE:
            if not expires_at:
                self.add_error(
                    "expires_at",
                    "Select the specific expiration date.",
                )

        if expiration_mode == DeliveryPackage.EXPIRATION_NONE:
            cleaned["expiration_days"] = None
            cleaned["expires_at"] = None

        if requires_access_key:
            has_existing_key = bool(
                self.instance and self.instance.pk and self.instance.access_key_hash
            )

            if (
                not has_existing_key
                and not generate_access_key
                and not custom_access_key
            ):
                self.add_error(
                    "generate_access_key",
                    "Generate an access key or enter a custom access key.",
                )

        return cleaned

    def save(self, commit=True):
        package = super().save(commit=False)

        requires_access_key = self.cleaned_data.get("requires_access_key")
        generate_access_key = self.cleaned_data.get("generate_access_key")
        custom_access_key = (self.cleaned_data.get("custom_access_key") or "").strip()

        if requires_access_key:
            if custom_access_key:
                raw_key = custom_access_key
                package.set_access_key(raw_key)
                self.generated_key = raw_key
            elif generate_access_key:
                raw_key = DeliveryPackage.generate_access_key()
                package.set_access_key(raw_key)
                self.generated_key = raw_key
        else:
            package.set_access_key("")

        if commit:
            package.save()

        return package
