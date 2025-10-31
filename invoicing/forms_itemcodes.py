from decimal import Decimal, InvalidOperation

from django import forms

from .models import ItemCode


class ItemCodeForm(forms.ModelForm):
    # Evita tooltips nativos del navegador (en español).
    use_required_attribute = False

    class Meta:
        model = ItemCode
        fields = [
            "city", "project", "office", "client", "work_type",
            "job_code", "description", "uom", "rate",
        ]
        widgets = {
            "city": forms.TextInput(attrs={"placeholder": "Greenville", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "project": forms.TextInput(attrs={"placeholder": "Mastec – Optimum", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "office": forms.TextInput(attrs={"placeholder": "Charter-Gr", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "client": forms.TextInput(attrs={"placeholder": "Optimum", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "work_type": forms.TextInput(attrs={"placeholder": "Fiber", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "job_code": forms.TextInput(attrs={"placeholder": "C117-OPT", "class": "w-full border rounded-lg px-3 py-2 text-sm", "style": "text-transform:uppercase"}),
            "description": forms.Textarea(attrs={"rows": 2, "placeholder": "Node Replacement", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "uom": forms.TextInput(attrs={"placeholder": "EA", "class": "w-full border rounded-lg px-3 py-2 text-sm"}),
            "rate": forms.NumberInput(attrs={"placeholder": "174.00", "step": "0.01", "min": "0", "inputmode": "decimal", "class": "w-full border rounded-lg px-3 py-2 text-sm text-right"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Todos los campos requeridos con mensajes en inglés
        for name, field in self.fields.items():
            field.required = True
            field.error_messages.update({"required": "This field is required."})

    def clean_job_code(self):
        code = (self.cleaned_data.get("job_code") or "").strip().upper()
        if not code:
            raise forms.ValidationError("This field is required.")
        return code

    def clean_rate(self):
        rate = self.cleaned_data.get("rate")
        try:
            if rate is None or str(rate).strip() == "":
                raise InvalidOperation
            rate = Decimal(rate)
        except Exception:
            raise forms.ValidationError("Rate must be a number.")
        if rate < 0:
            raise forms.ValidationError("Rate must be zero or positive.")
        return rate

    def clean(self):
        cleaned = super().clean()
        # Además del error por campo, muestra un aviso general si falta algo
        missing = [n for n in self.fields if not cleaned.get(n)]
        if missing:
            self.add_error(None, "All fields are required.")
        # Unicidad por (job_code, city)
        job_code = (cleaned.get("job_code") or "").strip().upper()
        city     = (cleaned.get("city") or "").strip()
        if job_code and city:
            qs = ItemCode.objects.filter(job_code=job_code, city=city)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("job_code", f"Job Code '{job_code}' already exists for city '{city}'.")
        return cleaned


class ItemCodeImportForm(forms.Form):
    file = forms.FileField(
        label="Excel file (.xlsx)",
        help_text=(
            "Headers required: City, Project, Office, Client, Work Type, "
            "Job Code, Description, UOM, Rate"
        ),
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"})
    )


# invoicing/forms_customer.py
from django import forms

from .models import Customer


class CustomerForm(forms.ModelForm):
    # evitamos tooltips nativos del navegador
    use_required_attribute = False

    class Meta:
        model = Customer
        fields = [
            "name", "mnemonic", "client",
            "email", "phone",
            "street_1", "city", "state", "zip_code",
            "status",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        required_fields = [
            "name", "mnemonic", "client",
            "email",
            "street_1", "city", "state", "zip_code",
            "status",
        ]
        for fname in required_fields:
            self.fields[fname].required = True
            self.fields[fname].error_messages.update(
                {"required": "This field is required."}
            )

        # phone NO requerido
        self.fields["phone"].required = False

        # placeholders/clases
        ph = {
            "name": "Customer Name",
            "mnemonic": "Mnemonic (e.g., ACME)",
            "client": "Client label (internal or display)",
            "email": "name@company.com",
            "phone": "Optional phone",
            "street_1": "Street Address",
            "city": "City",
            "state": "State",
            "zip_code": "ZIP",
        }
        for k, v in ph.items():
            if k in self.fields:
                self.fields[k].widget.attrs.update({
                    "placeholder": v,
                    "class": "border rounded-lg px-3 py-2 text-sm",
                })

    def clean_mnemonic(self):
        value = (self.cleaned_data.get("mnemonic") or "").upper().strip()
        if not value:
            raise forms.ValidationError("This field is required.")
        # unicidad manual si editando
        qs = Customer.objects.filter(mnemonic=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"Mnemonic '{value}' is already in use.")
        return value