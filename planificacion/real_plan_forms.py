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
