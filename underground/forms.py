from django import forms

from .models import Route


class RouteForm(forms.ModelForm):
    class Meta:
        model = Route
        fields = ("name", "start_ft", "end_ft", "segment_length_ft")
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border p-2"}),
            "start_ft": forms.NumberInput(attrs={"class": "w-full rounded-lg border p-2", "step": "0.01"}),
            "end_ft": forms.NumberInput(attrs={"class": "w-full rounded-lg border p-2", "step": "0.01"}),
            "segment_length_ft": forms.NumberInput(attrs={"class": "w-full rounded-lg border p-2", "step": "0.01"}),
        }