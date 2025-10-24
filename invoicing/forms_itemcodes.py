# invoicing/forms.py
from django import forms

from .models import ItemCode


class ItemCodeForm(forms.ModelForm):
    class Meta:
        model = ItemCode
        fields = [
            "city", "project", "office", "client", "work_type",
            "job_code", "description", "uom", "rate"
        ]
        widgets = {
            "city": forms.TextInput(attrs={
                "placeholder": "Greenville",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "project": forms.TextInput(attrs={
                "placeholder": "Mastec – Optimum",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "office": forms.TextInput(attrs={
                "placeholder": "Charter-Gr",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "client": forms.TextInput(attrs={
                "placeholder": "Optimum",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "work_type": forms.TextInput(attrs={
                "placeholder": "Fiber",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "job_code": forms.TextInput(attrs={
                "placeholder": "C117-OPT",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
                "style": "text-transform:uppercase",
            }),
            "description": forms.Textarea(attrs={
                "rows": 2,
                "placeholder": "Node Replacement",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "uom": forms.TextInput(attrs={
                "placeholder": "EA",
                "class": "w-full border rounded-lg px-3 py-2 text-sm",
            }),
            "rate": forms.NumberInput(attrs={
                "placeholder": "174.00",
                "step": "0.01",
                "min": "0",
                "inputmode": "decimal",
                "class": "w-full border rounded-lg px-3 py-2 text-sm text-right",
            }),
        }


class ItemCodeImportForm(forms.Form):
    file = forms.FileField(
        label="Excel file (.xlsx)",
        help_text=(
            "Headers required: City, Project, Office, Client, Work Type, "
            "Job Code, Description, UOM, Rate"
        ),
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx"})
    )