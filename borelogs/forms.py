# borelogs/forms.py
from __future__ import annotations

from django import forms

from .models import BoreLog


class BoreLogForm(forms.ModelForm):
    """
    Formulario para crear/editar Bore Log.
    - NO incluye upload de template (es global/fijo).
    """

    class Meta:
        model = BoreLog
        fields = [
            "project_name",
            "rod_length",
            "driller_name",
            "vendor_name",
            "status",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }