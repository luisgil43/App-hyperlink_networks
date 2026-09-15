from django import forms

FIELD_CLASS = (
    "w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 "
    "text-sm text-gray-700 focus:border-blue-500 focus:outline-none "
    "focus:ring-2 focus:ring-blue-500"
)


class RealPlanBoardFilterForm(forms.Form):
    WORK_TYPE_CHOICES = (
        ("all", "All"),
        ("fiber", "Fiber"),
        ("cable", "Cable"),
    )

    STATUS_CHOICES = (
        ("asignado", "Assigned"),
        ("en_proceso", "In progress"),
        (
            "en_revision_supervisor",
            "Submitted — supervisor review",
        ),
        (
            "rechazado_supervisor",
            "Rejected by supervisor",
        ),
        (
            "aprobado_supervisor",
            "Approved by supervisor",
        ),
    )

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search Project ID, DFN, technician, client...",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    work_type = forms.ChoiceField(
        required=False,
        choices=WORK_TYPE_CHOICES,
        initial="all",
        widget=forms.Select(
            attrs={
                "class": FIELD_CLASS,
            }
        ),
    )

    statuses = forms.MultipleChoiceField(
        required=False,
        choices=STATUS_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )

    def clean_q(self):
        return (self.cleaned_data.get("q") or "").strip()

    def clean_work_type(self):
        value = (self.cleaned_data.get("work_type") or "all").strip().lower()

        if value not in {
            "all",
            "fiber",
            "cable",
        }:
            return "all"

        return value

    def clean_statuses(self):
        valid_statuses = {value for value, _label in self.STATUS_CHOICES}

        return [
            status
            for status in self.cleaned_data.get(
                "statuses",
                [],
            )
            if status in valid_statuses
        ]
