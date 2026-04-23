from django import forms

from .models import DocumentKey, Ombording, OmbordingDocument, Position


class DateInput(forms.DateInput):
    input_type = "text"


class PositionForm(forms.ModelForm):
    class Meta:
        model = Position
        fields = ["name", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "rounded border"}),
        }


YES_NO_SELECT = [
    ("", "Select"),
    ("yes", "Yes"),
    ("no", "No"),
]


class OmbordingForm(forms.ModelForm):
    send_email_now = forms.BooleanField(required=False, label="Send email now")

    contractor_agreement_base = forms.FileField(
        required=False, label="Independent Contractor Agreement"
    )
    exhibit_base = forms.FileField(required=False, label="Exhibit")
    w9_base = forms.FileField(required=False, label="W-9 Base")

    passport_front = forms.FileField(required=False, label="Passport Front")
    passport_back = forms.FileField(required=False, label="Passport Back")
    address_proof = forms.FileField(required=False, label="Address Proof")
    ssn_front = forms.FileField(required=False, label="Social Security Front")
    ssn_back = forms.FileField(required=False, label="Social Security Back")
    work_permit_front = forms.FileField(required=False, label="Work Permit Front")
    work_permit_back = forms.FileField(required=False, label="Work Permit Back")
    driver_license_front = forms.FileField(required=False, label="Driver License Front")
    driver_license_back = forms.FileField(required=False, label="Driver License Back")

    has_ssn = forms.ChoiceField(required=False, choices=YES_NO_SELECT)
    has_work_permit = forms.ChoiceField(required=False, choices=YES_NO_SELECT)
    has_driver_license = forms.ChoiceField(required=False, choices=YES_NO_SELECT)
    w9_part3b_required = forms.ChoiceField(required=False, choices=YES_NO_SELECT)

    class Meta:
        model = Ombording
        fields = [
            "first_name",
            "last_name",
            "email",
            "position",
            "internal_notes",
            "date_of_birth",
            "nationality",
            "street_address",
            "apt_suite",
            "city",
            "state",
            "zip_code",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "has_ssn",
            "ssn_number",
            "passport_number",
            "has_work_permit",
            "has_driver_license",
            "business_name",
            "w9_tax_classification",
            "w9_llc_classification",
            "w9_other_text",
            "w9_part3b_required",
            "w9_exempt_payee_code",
            "w9_fatca_exemption_code",
            "w9_account_numbers",
            "ein_number",
            "bank_name",
            "account_type",
            "routing_number",
            "account_number",
        ]
        widgets = {
            "first_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "last_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "email": forms.EmailInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "position": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "internal_notes": forms.Textarea(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "rows": 3}
            ),
            "date_of_birth": DateInput(
                attrs={
                    "class": "js-date-picker w-full border rounded-xl px-3 py-2",
                    "placeholder": "Select date",
                }
            ),
            "nationality": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "list": "country-options",
                }
            ),
            "street_address": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "Street address",
                    "maxlength": "255",
                }
            ),
            "apt_suite": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "Apt, suite, unit, building, floor, etc. (optional)",
                    "maxlength": "120",
                }
            ),
            "city": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "City",
                    "maxlength": "120",
                }
            ),
            "state": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "State",
                    "maxlength": "120",
                }
            ),
            "zip_code": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "ZIP code",
                    "maxlength": "10",
                    "inputmode": "numeric",
                }
            ),
            "phone_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "20",
                    "inputmode": "tel",
                }
            ),
            "emergency_contact_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "emergency_contact_phone": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "20",
                    "inputmode": "tel",
                }
            ),
            "emergency_contact_relationship": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "has_ssn": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "ssn_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "9",
                    "inputmode": "numeric",
                    "placeholder": "9 digits",
                }
            ),
            "passport_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "50",
                }
            ),
            "has_work_permit": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "has_driver_license": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "business_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_tax_classification": forms.Select(
                choices=[
                    ("", "Select"),
                    ("individual", "Individual / sole proprietor"),
                    ("c_corp", "C corporation"),
                    ("s_corp", "S corporation"),
                    ("partnership", "Partnership"),
                    ("trust_estate", "Trust/estate"),
                    ("llc", "LLC"),
                    ("other", "Other"),
                ],
                attrs={"class": "w-full border rounded-xl px-3 py-2"},
            ),
            "w9_llc_classification": forms.Select(
                choices=[
                    ("", "Select"),
                    ("C", "C (C corporation)"),
                    ("S", "S (S corporation)"),
                    ("P", "P (Partnership)"),
                ],
                attrs={"class": "w-full border rounded-xl px-3 py-2"},
            ),
            "w9_other_text": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "Other (see instructions)",
                }
            ),
            "w9_part3b_required": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_exempt_payee_code": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "20",
                }
            ),
            "w9_fatca_exemption_code": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "20",
                }
            ),
            "w9_account_numbers": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "ein_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "9",
                    "inputmode": "numeric",
                    "placeholder": "9 digits",
                }
            ),
            "bank_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "account_type": forms.Select(
                choices=[
                    ("", "Select"),
                    ("checking", "Checking"),
                    ("savings", "Savings"),
                ],
                attrs={"class": "w-full border rounded-xl px-3 py-2"},
            ),
            "routing_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "9",
                    "inputmode": "numeric",
                    "placeholder": "9 digits",
                }
            ),
            "account_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "50",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["position"].queryset = Position.objects.filter(
            is_active=True
        ).order_by("name")

        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.fields["email"].required = True
        self.fields["position"].required = True

        optional_fields = [
            "internal_notes",
            "date_of_birth",
            "nationality",
            "street_address",
            "apt_suite",
            "city",
            "state",
            "zip_code",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "has_ssn",
            "ssn_number",
            "passport_number",
            "has_work_permit",
            "has_driver_license",
            "business_name",
            "w9_tax_classification",
            "w9_llc_classification",
            "w9_other_text",
            "w9_part3b_required",
            "w9_exempt_payee_code",
            "w9_fatca_exemption_code",
            "w9_account_numbers",
            "ein_number",
            "bank_name",
            "account_type",
            "routing_number",
            "account_number",
        ]
        for field_name in optional_fields:
            if field_name in self.fields:
                self.fields[field_name].required = False

        self.fields["business_name"].label = (
            "2. Business name/disregarded entity name, if different from above"
        )
        self.fields["w9_tax_classification"].label = (
            "3a. Check the appropriate box for federal tax classification"
        )
        self.fields["w9_llc_classification"].label = (
            "LLC only: enter the tax classification (C = C corporation, S = S corporation, P = Partnership)"
        )
        self.fields["w9_other_text"].label = "Other (see instructions)"
        self.fields["w9_part3b_required"].label = (
            "3b. If line 3a is Partnership, Trust/estate, or LLC with code P: does this box apply?"
        )
        self.fields["w9_exempt_payee_code"].label = "4. Exempt payee code (if any)"
        self.fields["w9_fatca_exemption_code"].label = (
            "4. Exemption from Foreign Account Tax Compliance Act (FATCA) reporting code (if any)"
        )
        self.fields["w9_account_numbers"].label = (
            "7. List account number(s) here (optional)"
        )
        self.fields["ein_number"].label = "Employer identification number (EIN)"

        if self.instance and self.instance.pk:
            self.fields["has_ssn"].initial = (
                "yes"
                if self.instance.has_ssn is True
                else "no" if self.instance.has_ssn is False else ""
            )
            self.fields["has_work_permit"].initial = (
                "yes"
                if self.instance.has_work_permit is True
                else "no" if self.instance.has_work_permit is False else ""
            )
            self.fields["has_driver_license"].initial = (
                "yes"
                if self.instance.has_driver_license is True
                else "no" if self.instance.has_driver_license is False else ""
            )
            self.fields["w9_part3b_required"].initial = (
                "yes"
                if self.instance.w9_part3b_required is True
                else "no" if self.instance.w9_part3b_required is False else ""
            )

    def clean_has_ssn(self):
        value = self.cleaned_data.get("has_ssn")
        if value == "yes":
            return True
        if value == "no":
            return False
        return None

    def clean_has_work_permit(self):
        value = self.cleaned_data.get("has_work_permit")
        if value == "yes":
            return True
        if value == "no":
            return False
        return None

    def clean_has_driver_license(self):
        value = self.cleaned_data.get("has_driver_license")
        if value == "yes":
            return True
        if value == "no":
            return False
        return None

    def clean_w9_part3b_required(self):
        value = self.cleaned_data.get("w9_part3b_required")
        if value == "yes":
            return True
        if value == "no":
            return False
        return None

    def clean_ssn_number(self):
        value = (self.cleaned_data.get("ssn_number") or "").strip()
        if not value:
            return ""
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) != 9:
            raise forms.ValidationError(
                "Social Security Number must contain exactly 9 digits."
            )
        return digits

    def clean_ein_number(self):
        value = (self.cleaned_data.get("ein_number") or "").strip()
        if not value:
            return ""
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) != 9:
            raise forms.ValidationError(
                "Employer Identification Number must contain exactly 9 digits."
            )
        return digits

    def clean_routing_number(self):
        value = (self.cleaned_data.get("routing_number") or "").strip()
        if not value:
            return ""
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) != 9:
            raise forms.ValidationError("Routing Number must contain exactly 9 digits.")
        return digits

    def clean_zip_code(self):
        value = (self.cleaned_data.get("zip_code") or "").strip()
        if not value:
            return ""
        normalized = value.replace(" ", "")
        if len(normalized) == 5 and normalized.isdigit():
            return normalized
        if (
            len(normalized) == 10
            and normalized[:5].isdigit()
            and normalized[5] == "-"
            and normalized[6:].isdigit()
        ):
            return normalized
        raise forms.ValidationError(
            "ZIP Code must be 5 digits or ZIP+4 format (example: 12345 or 12345-6789)."
        )

    def clean_phone_number(self):
        value = (self.cleaned_data.get("phone_number") or "").strip()
        if not value:
            return ""
        allowed = set("0123456789+()- .")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError("Phone Number contains invalid characters.")
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 7 or len(digits) > 15:
            raise forms.ValidationError(
                "Phone Number must contain between 7 and 15 digits."
            )
        return value

    def clean_emergency_contact_phone(self):
        value = (self.cleaned_data.get("emergency_contact_phone") or "").strip()
        if not value:
            return ""
        allowed = set("0123456789+()- .")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError(
                "Emergency Contact Phone contains invalid characters."
            )
        digits = "".join(ch for ch in value if ch.isdigit())
        if len(digits) < 7 or len(digits) > 15:
            raise forms.ValidationError(
                "Emergency Contact Phone must contain between 7 and 15 digits."
            )
        return value

    def clean_passport_number(self):
        value = (self.cleaned_data.get("passport_number") or "").strip()
        if not value:
            return ""
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789- "
        )
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError(
                "Passport Number can only contain letters, numbers, spaces and hyphens."
            )
        return value

    def clean_state(self):
        value = (self.cleaned_data.get("state") or "").strip()
        if not value:
            return ""
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz ")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError("State can only contain letters and spaces.")
        return value

    def clean_city(self):
        value = (self.cleaned_data.get("city") or "").strip()
        if not value:
            return ""
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,'-"
        )
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError("City contains invalid characters.")
        return value

    def clean_street_address(self):
        value = (self.cleaned_data.get("street_address") or "").strip()
        if not value:
            return ""
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 #.,'/-"
        )
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError("Street Address contains invalid characters.")
        return value

    def clean_apt_suite(self):
        value = (self.cleaned_data.get("apt_suite") or "").strip()
        if not value:
            return ""
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 #.,'/-"
        )
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError("Apt / Suite contains invalid characters.")
        return value

    def clean_account_number(self):
        value = (self.cleaned_data.get("account_number") or "").strip()
        if not value:
            return ""
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError(
                "Account Number can only contain letters, numbers and hyphens."
            )
        return value

    def clean(self):
        cleaned_data = super().clean()

        nationality = (cleaned_data.get("nationality") or "").strip().lower()
        phone_number = (cleaned_data.get("phone_number") or "").strip()
        emergency_phone = (cleaned_data.get("emergency_contact_phone") or "").strip()

        has_ssn = cleaned_data.get("has_ssn")
        ssn_number = (cleaned_data.get("ssn_number") or "").strip()
        passport_number = (cleaned_data.get("passport_number") or "").strip()

        w9_tax_classification = (
            cleaned_data.get("w9_tax_classification") or ""
        ).strip()
        w9_llc_classification = (
            cleaned_data.get("w9_llc_classification") or ""
        ).strip()
        w9_other_text = (cleaned_data.get("w9_other_text") or "").strip()
        ein_number = (cleaned_data.get("ein_number") or "").strip()

        if phone_number and emergency_phone and phone_number == emergency_phone:
            self.add_error(
                "emergency_contact_phone",
                "Emergency contact phone cannot be the same as the main phone number.",
            )

        if has_ssn is True and not ssn_number:
            self.add_error(
                "ssn_number",
                "This field is required when the worker has Social Security.",
            )

        if nationality and nationality != "united states" and not passport_number:
            self.add_error(
                "passport_number",
                "Passport number is required when nationality is different from United States.",
            )

        if has_ssn is False and not passport_number:
            self.add_error(
                "passport_number",
                "Passport number is required when the worker does not have Social Security.",
            )

        if w9_tax_classification == "llc" and not w9_llc_classification:
            self.add_error(
                "w9_llc_classification",
                "Please select the LLC classification (C, S or P).",
            )

        if w9_tax_classification != "llc":
            cleaned_data["w9_llc_classification"] = ""

        if w9_tax_classification == "other" and not w9_other_text:
            self.add_error(
                "w9_other_text",
                "Please enter the text for Other.",
            )

        if w9_tax_classification != "other":
            cleaned_data["w9_other_text"] = ""

        if (
            has_ssn is not True
            and not ein_number
            and w9_tax_classification
            in ("c_corp", "s_corp", "partnership", "trust_estate", "llc", "other")
        ):
            self.add_error(
                "ein_number",
                "Employer Identification Number is required for this tax classification when there is no SSN.",
            )

        return cleaned_data


class PublicAccessCodeForm(forms.Form):
    access_code = forms.CharField(
        max_length=12,
        label="Access code",
        widget=forms.TextInput(
            attrs={
                "class": "w-full border rounded-xl px-3 py-2 uppercase tracking-[0.2em]",
                "placeholder": "Enter access code",
                "autocomplete": "off",
            }
        ),
    )

    def clean_access_code(self):
        value = (self.cleaned_data.get("access_code") or "").strip().upper()
        return value.replace(" ", "").replace("-", "")


class PublicAcceptanceForm(forms.Form):
    accepted_documents = forms.BooleanField(
        required=True,
        label="I reviewed the documents and I agree to continue with this onboarding process.",
    )


class _PublicBaseMixin:
    def _normalize_yes_no(self, value):
        if value == "yes":
            return True
        if value == "no":
            return False
        return None

    def _digits_only(self, value):
        return "".join(ch for ch in (value or "") if ch.isdigit())

    def _existing_document(self, document_key):
        instance = getattr(self, "instance", None)
        if not instance or not instance.pk:
            return None
        return (
            OmbordingDocument.objects.filter(
                ombording=instance,
                document_key=document_key,
            )
            .order_by("-id")
            .first()
        )

    def _has_existing_document(self, document_key):
        doc = self._existing_document(document_key)
        return bool(doc and doc.file)

    def _existing_document_name(self, document_key):
        doc = self._existing_document(document_key)
        if not doc or not doc.file:
            return ""
        return doc.original_name or doc.label or document_key


class PublicPersonalForm(_PublicBaseMixin, forms.ModelForm):
    address_proof = forms.FileField(required=False, label="Address Proof")

    class Meta:
        model = Ombording
        fields = [
            "date_of_birth",
            "nationality",
            "street_address",
            "apt_suite",
            "city",
            "state",
            "zip_code",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
        ]
        widgets = {
            "date_of_birth": DateInput(
                attrs={
                    "class": "js-date-picker w-full border rounded-xl px-3 py-2",
                    "placeholder": "Select date",
                }
            ),
            "nationality": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "list": "country-options",
                }
            ),
            "street_address": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "apt_suite": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "city": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "state": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "zip_code": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "10",
                    "inputmode": "numeric",
                }
            ),
            "phone_number": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "emergency_contact_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "emergency_contact_phone": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "emergency_contact_relationship": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
        }

    def clean_zip_code(self):
        value = (self.cleaned_data.get("zip_code") or "").strip()
        if not value:
            return ""
        normalized = value.replace(" ", "")
        if len(normalized) == 5 and normalized.isdigit():
            return normalized
        if (
            len(normalized) == 10
            and normalized[:5].isdigit()
            and normalized[5] == "-"
            and normalized[6:].isdigit()
        ):
            return normalized
        raise forms.ValidationError(
            "ZIP Code must be 5 digits or ZIP+4 format (example: 12345 or 12345-6789)."
        )

    def clean_phone_number(self):
        value = (self.cleaned_data.get("phone_number") or "").strip()
        if not value:
            return ""
        allowed = set("0123456789+()- .")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError("Phone Number contains invalid characters.")
        digits = self._digits_only(value)
        if len(digits) < 7 or len(digits) > 15:
            raise forms.ValidationError(
                "Phone Number must contain between 7 and 15 digits."
            )
        return value

    def clean_emergency_contact_phone(self):
        value = (self.cleaned_data.get("emergency_contact_phone") or "").strip()
        if not value:
            return ""
        allowed = set("0123456789+()- .")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError(
                "Emergency Contact Phone contains invalid characters."
            )
        digits = self._digits_only(value)
        if len(digits) < 7 or len(digits) > 15:
            raise forms.ValidationError(
                "Emergency Contact Phone must contain between 7 and 15 digits."
            )
        return value

    def clean(self):
        cleaned_data = super().clean()

        required_fields = [
            "date_of_birth",
            "nationality",
            "street_address",
            "city",
            "state",
            "zip_code",
            "phone_number",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
        ]
        for field_name in required_fields:
            if not cleaned_data.get(field_name):
                self.add_error(field_name, "This field is required.")

        phone_number = (cleaned_data.get("phone_number") or "").strip()
        emergency_phone = (cleaned_data.get("emergency_contact_phone") or "").strip()
        if phone_number and emergency_phone and phone_number == emergency_phone:
            self.add_error(
                "emergency_contact_phone",
                "Emergency contact phone cannot be the same as the main phone number.",
            )

        address_proof = self.files.get("address_proof") or self.files.get(
            "address_proof_camera"
        )
        if not address_proof and not self._has_existing_document(
            DocumentKey.ADDRESS_PROOF
        ):
            self.add_error("address_proof", "Address Proof is required.")

        return cleaned_data


class PublicIdentityForm(_PublicBaseMixin, forms.ModelForm):
    has_ssn = forms.ChoiceField(required=False, choices=YES_NO_SELECT)
    has_work_permit = forms.ChoiceField(required=False, choices=YES_NO_SELECT)
    has_driver_license = forms.ChoiceField(required=False, choices=YES_NO_SELECT)

    passport_front = forms.FileField(required=False, label="Passport Front")
    passport_back = forms.FileField(required=False, label="Passport Back")
    ssn_front = forms.FileField(required=False, label="Social Security Front")
    ssn_back = forms.FileField(required=False, label="Social Security Back")
    work_permit_front = forms.FileField(required=False, label="Work Permit Front")
    work_permit_back = forms.FileField(required=False, label="Work Permit Back")
    driver_license_front = forms.FileField(required=False, label="Driver License Front")
    driver_license_back = forms.FileField(required=False, label="Driver License Back")

    class Meta:
        model = Ombording
        fields = [
            "has_ssn",
            "ssn_number",
            "passport_number",
            "has_work_permit",
            "has_driver_license",
        ]
        widgets = {
            "has_ssn": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "ssn_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "9",
                    "inputmode": "numeric",
                }
            ),
            "passport_number": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "has_work_permit": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "has_driver_license": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.initial["has_ssn"] = (
                "yes"
                if self.instance.has_ssn is True
                else "no" if self.instance.has_ssn is False else ""
            )
            self.initial["has_work_permit"] = (
                "yes"
                if self.instance.has_work_permit is True
                else "no" if self.instance.has_work_permit is False else ""
            )
            self.initial["has_driver_license"] = (
                "yes"
                if self.instance.has_driver_license is True
                else "no" if self.instance.has_driver_license is False else ""
            )

    def clean_has_ssn(self):
        return self._normalize_yes_no(self.cleaned_data.get("has_ssn"))

    def clean_has_work_permit(self):
        return self._normalize_yes_no(self.cleaned_data.get("has_work_permit"))

    def clean_has_driver_license(self):
        return self._normalize_yes_no(self.cleaned_data.get("has_driver_license"))

    def clean_ssn_number(self):
        value = (self.cleaned_data.get("ssn_number") or "").strip()
        if not value:
            return ""
        digits = self._digits_only(value)
        if len(digits) != 9:
            raise forms.ValidationError(
                "Social Security Number must contain exactly 9 digits."
            )
        return digits

    def clean_passport_number(self):
        value = (self.cleaned_data.get("passport_number") or "").strip()
        if not value:
            return ""
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789- "
        )
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError(
                "Passport Number can only contain letters, numbers, spaces and hyphens."
            )
        return value

    def clean(self):
        cleaned_data = super().clean()

        nationality = (getattr(self.instance, "nationality", "") or "").strip().lower()
        has_ssn = cleaned_data.get("has_ssn")
        has_work_permit = cleaned_data.get("has_work_permit")
        has_driver_license = cleaned_data.get("has_driver_license")
        ssn_number = (cleaned_data.get("ssn_number") or "").strip()
        passport_number = (cleaned_data.get("passport_number") or "").strip()

        if has_ssn is None:
            self.add_error("has_ssn", "This field is required.")
        if has_work_permit is None:
            self.add_error("has_work_permit", "This field is required.")
        if has_driver_license is None:
            self.add_error("has_driver_license", "This field is required.")

        needs_passport = nationality != "united states" or has_ssn is False

        if has_ssn is True and not ssn_number:
            self.add_error(
                "ssn_number",
                "This field is required when the worker has Social Security.",
            )

        if needs_passport and not passport_number:
            self.add_error(
                "passport_number",
                "Passport number is required for this worker.",
            )

        ssn_front = self.files.get("ssn_front") or self.files.get("ssn_front_camera")
        ssn_back = self.files.get("ssn_back") or self.files.get("ssn_back_camera")
        passport_front = self.files.get("passport_front") or self.files.get(
            "passport_front_camera"
        )
        passport_back = self.files.get("passport_back") or self.files.get(
            "passport_back_camera"
        )
        work_permit_front = self.files.get("work_permit_front") or self.files.get(
            "work_permit_front_camera"
        )
        work_permit_back = self.files.get("work_permit_back") or self.files.get(
            "work_permit_back_camera"
        )
        driver_license_front = self.files.get("driver_license_front") or self.files.get(
            "driver_license_front_camera"
        )
        driver_license_back = self.files.get("driver_license_back") or self.files.get(
            "driver_license_back_camera"
        )

        if has_ssn is True:
            if not ssn_front and not self._has_existing_document(DocumentKey.SSN_FRONT):
                self.add_error("ssn_front", "Social Security Front is required.")
            if not ssn_back and not self._has_existing_document(DocumentKey.SSN_BACK):
                self.add_error("ssn_back", "Social Security Back is required.")

        if needs_passport:
            if not passport_front and not self._has_existing_document(
                DocumentKey.PASSPORT_FRONT
            ):
                self.add_error("passport_front", "Passport Front is required.")
            if not passport_back and not self._has_existing_document(
                DocumentKey.PASSPORT_BACK
            ):
                self.add_error("passport_back", "Passport Back is required.")

        if has_work_permit is True:
            if not work_permit_front and not self._has_existing_document(
                DocumentKey.WORK_PERMIT_FRONT
            ):
                self.add_error("work_permit_front", "Work Permit Front is required.")
            if not work_permit_back and not self._has_existing_document(
                DocumentKey.WORK_PERMIT_BACK
            ):
                self.add_error("work_permit_back", "Work Permit Back is required.")

        if has_driver_license is True:
            if not driver_license_front and not self._has_existing_document(
                DocumentKey.DRIVER_LICENSE_FRONT
            ):
                self.add_error(
                    "driver_license_front", "Driver License Front is required."
                )
            if not driver_license_back and not self._has_existing_document(
                DocumentKey.DRIVER_LICENSE_BACK
            ):
                self.add_error(
                    "driver_license_back", "Driver License Back is required."
                )

        return cleaned_data


class PublicTaxBankingForm(_PublicBaseMixin, forms.ModelForm):
    w9_part3b_required = forms.ChoiceField(required=False, choices=YES_NO_SELECT)

    class Meta:
        model = Ombording
        fields = [
            "business_name",
            "w9_tax_classification",
            "w9_llc_classification",
            "w9_other_text",
            "w9_part3b_required",
            "w9_exempt_payee_code",
            "w9_fatca_exemption_code",
            "w9_account_numbers",
            "ein_number",
            "bank_name",
            "account_type",
            "routing_number",
            "account_number",
        ]
        widgets = {
            "business_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_tax_classification": forms.Select(
                choices=[
                    ("", "Select"),
                    ("individual", "Individual / sole proprietor"),
                    ("c_corp", "C corporation"),
                    ("s_corp", "S corporation"),
                    ("partnership", "Partnership"),
                    ("trust_estate", "Trust/estate"),
                    ("llc", "LLC"),
                    ("other", "Other"),
                ],
                attrs={"class": "w-full border rounded-xl px-3 py-2"},
            ),
            "w9_llc_classification": forms.Select(
                choices=[
                    ("", "Select"),
                    ("C", "C (C corporation)"),
                    ("S", "S (S corporation)"),
                    ("P", "P (Partnership)"),
                ],
                attrs={"class": "w-full border rounded-xl px-3 py-2"},
            ),
            "w9_other_text": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_part3b_required": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_exempt_payee_code": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_fatca_exemption_code": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "w9_account_numbers": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "ein_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "9",
                    "inputmode": "numeric",
                }
            ),
            "bank_name": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "account_type": forms.Select(
                choices=[
                    ("", "Select"),
                    ("checking", "Checking"),
                    ("savings", "Savings"),
                ],
                attrs={"class": "w-full border rounded-xl px-3 py-2"},
            ),
            "routing_number": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "maxlength": "9",
                    "inputmode": "numeric",
                }
            ),
            "account_number": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.initial["w9_part3b_required"] = (
                "yes"
                if self.instance.w9_part3b_required is True
                else "no" if self.instance.w9_part3b_required is False else ""
            )

    def clean_w9_part3b_required(self):
        return self._normalize_yes_no(self.cleaned_data.get("w9_part3b_required"))

    def clean_ein_number(self):
        value = (self.cleaned_data.get("ein_number") or "").strip()
        if not value:
            return ""
        digits = self._digits_only(value)
        if len(digits) != 9:
            raise forms.ValidationError(
                "Employer Identification Number must contain exactly 9 digits."
            )
        return digits

    def clean_routing_number(self):
        value = (self.cleaned_data.get("routing_number") or "").strip()
        if not value:
            return ""
        digits = self._digits_only(value)
        if len(digits) != 9:
            raise forms.ValidationError("Routing Number must contain exactly 9 digits.")
        return digits

    def clean_account_number(self):
        value = (self.cleaned_data.get("account_number") or "").strip()
        if not value:
            return ""
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")
        if any(ch not in allowed for ch in value):
            raise forms.ValidationError(
                "Account Number can only contain letters, numbers and hyphens."
            )
        return value

    def clean(self):
        cleaned_data = super().clean()

        w9_tax_classification = (
            cleaned_data.get("w9_tax_classification") or ""
        ).strip()
        w9_llc_classification = (
            cleaned_data.get("w9_llc_classification") or ""
        ).strip()
        w9_other_text = (cleaned_data.get("w9_other_text") or "").strip()
        ein_number = (cleaned_data.get("ein_number") or "").strip()
        has_ssn = getattr(self.instance, "has_ssn", None)

        required_fields = [
            "w9_tax_classification",
            "bank_name",
            "account_type",
            "routing_number",
            "account_number",
        ]
        for field_name in required_fields:
            if not cleaned_data.get(field_name):
                self.add_error(field_name, "This field is required.")

        if cleaned_data.get("w9_part3b_required") is None:
            self.add_error("w9_part3b_required", "This field is required.")

        if w9_tax_classification == "llc" and not w9_llc_classification:
            self.add_error(
                "w9_llc_classification",
                "Please select the LLC classification (C, S or P).",
            )

        if w9_tax_classification != "llc":
            cleaned_data["w9_llc_classification"] = ""

        if w9_tax_classification == "other" and not w9_other_text:
            self.add_error("w9_other_text", "Please enter the text for Other.")

        if w9_tax_classification != "other":
            cleaned_data["w9_other_text"] = ""

        if (
            has_ssn is not True
            and not ein_number
            and w9_tax_classification
            in ("c_corp", "s_corp", "partnership", "trust_estate", "llc", "other")
        ):
            self.add_error(
                "ein_number",
                "Employer Identification Number is required for this tax classification when there is no SSN.",
            )

        return cleaned_data


class PublicSignatureForm(forms.Form):
    signature_name = forms.CharField(
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
    )
    signature_dataurl = forms.CharField(widget=forms.HiddenInput())
