from decimal import Decimal

from django import forms
from django.forms import formset_factory

from .models import (PlanningActivityType, PlanningAssignmentStatus,
                     PlanningDFN, PlanningResourceType, PlanningUnit,
                     ProductivityProfile)

FIELD_CLASS = (
    "w-full rounded-xl border border-gray-300 px-4 py-3 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
)

DATE_FIELD_CLASS = (
    "w-full rounded-xl border border-gray-300 px-3 py-2.5 bg-white "
    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 "
    "planning-date-input"
)


def compact_decimal(value):
    if value in (
        None,
        "",
    ):
        return ""

    try:
        decimal_value = Decimal(str(value))
    except Exception:
        return value

    normalized = format(
        decimal_value,
        "f",
    )

    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")

    return normalized


class PlanningAssignmentForm(forms.Form):
    """
    Assignment creation form.

    Status remains internal for model compatibility, but is not
    presented to the planner. New Assignments are ACTIVE.
    """

    WORK_SCHEDULE_MON_SAT = "mon_sat"
    WORK_SCHEDULE_MON_SUN = "mon_sun"

    WORK_SCHEDULE_CHOICES = (
        (
            WORK_SCHEDULE_MON_SAT,
            "Monday - Saturday",
        ),
        (
            WORK_SCHEDULE_MON_SUN,
            "Monday - Sunday",
        ),
    )

    client_name = forms.CharField(
        label="Client",
        max_length=180,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. ITG",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    name = forms.CharField(
        label="Assignment Name",
        max_length=220,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Wisconsin NTP - September 2026",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    assignment_type = forms.CharField(
        label="Assignment Type",
        max_length=80,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. NTP, Work Order, Project Package",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    client_reference = forms.CharField(
        label="Client Reference",
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Optional NTP / Work Order / PO / Client ID",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    issue_date = forms.DateField(
        label="Issue Date",
        required=False,
        input_formats=[
            "%Y-%m-%d",
            "%m/%d/%Y",
        ],
        widget=forms.TextInput(
            attrs={
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "class": DATE_FIELD_CLASS,
                "data-planning-date": "1",
            }
        ),
    )

    pc = forms.CharField(
        label="PC",
        max_length=80,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. 676",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    contractor = forms.CharField(
        label="Contractor",
        max_length=150,
        required=False,
        initial="HYPERLINK",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. HYPERLINK",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    status = forms.ChoiceField(
        choices=PlanningAssignmentStatus.choices,
        initial=PlanningAssignmentStatus.ACTIVE,
        required=False,
        widget=forms.HiddenInput(),
    )

    country = forms.CharField(
        label="Country",
        max_length=120,
        required=False,
        initial="United States",
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. United States",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    state = forms.CharField(
        label="State",
        max_length=120,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Wisconsin",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    city = forms.CharField(
        label="City",
        max_length=120,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. Green Bay",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    market = forms.CharField(
        label="Market",
        max_length=120,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. GREEN BAY",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    work_schedule = forms.ChoiceField(
        label="Work Schedule",
        choices=WORK_SCHEDULE_CHOICES,
        initial=WORK_SCHEDULE_MON_SAT,
        widget=forms.RadioSelect,
    )

    source_reference = forms.CharField(
        label="Source Reference",
        max_length=255,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Document, email, NTP file or source reference",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    notes = forms.CharField(
        label="Notes",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Optional assignment notes...",
                "class": FIELD_CLASS,
            }
        ),
    )

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        if not self.is_bound:
            issue_date = self.initial.get("issue_date")

            if issue_date:
                try:
                    self.initial["issue_date"] = issue_date.strftime("%m/%d/%Y")
                except AttributeError:
                    pass

    def clean_client_name(self):
        return self.cleaned_data["client_name"].strip()

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_assignment_type(self):
        return self.cleaned_data["assignment_type"].strip()

    def clean_client_reference(self):
        return self.cleaned_data["client_reference"].strip()

    def clean_pc(self):
        return self.cleaned_data["pc"].strip()

    def clean_contractor(self):
        return self.cleaned_data["contractor"].strip()

    def clean_country(self):
        return self.cleaned_data["country"].strip()

    def clean_state(self):
        return self.cleaned_data["state"].strip()

    def clean_city(self):
        return self.cleaned_data["city"].strip()

    def clean_market(self):
        return self.cleaned_data["market"].strip()

    def clean_source_reference(self):
        return self.cleaned_data["source_reference"].strip()

    def clean_notes(self):
        return self.cleaned_data["notes"].strip()


class PlanningDFNEntryForm(forms.Form):

    code = forms.CharField(
        label="DFN",
        max_length=80,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. 0913TA_04",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    status = forms.ChoiceField(
        choices=PlanningDFN.Status.choices,
        initial=PlanningDFN.Status.ACTIVE,
        required=False,
        widget=forms.HiddenInput(),
    )

    def clean_code(self):
        return self.cleaned_data["code"].strip()


class PlanningActivityEntryForm(forms.Form):
    """
    One Client Baseline activity plus Hyperlink's independent
    High-Level Plan.

    Activity can be selected from the catalog or created as a new
    activity. New activities are persisted by the view only when the
    Assignment itself is confirmed.
    """

    activity_type = forms.ModelChoiceField(
        label="Activity",
        queryset=PlanningActivityType.objects.none(),
        required=False,
        empty_label="Select an activity...",
        widget=forms.Select(
            attrs={
                "class": FIELD_CLASS,
            }
        ),
    )

    new_activity_code = forms.CharField(
        label="Activity Code",
        max_length=50,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. C-108",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    new_activity_name = forms.CharField(
        label="Activity Name",
        max_length=180,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "e.g. AER Splicing / Testing",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    dfn_code = forms.CharField(
        label="DFN",
        max_length=80,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Optional DFN",
                "autocomplete": "off",
                "class": FIELD_CLASS,
            }
        ),
    )

    quantity = forms.DecimalField(
        label="Quantity",
        max_digits=16,
        decimal_places=4,
        min_value=Decimal("0"),
        widget=forms.NumberInput(
            attrs={
                "step": "any",
                "min": "0",
                "class": FIELD_CLASS,
            }
        ),
    )

    unit = forms.ChoiceField(
        label="Unit",
        choices=PlanningUnit.choices,
        widget=forms.Select(
            attrs={
                "class": FIELD_CLASS,
            }
        ),
    )

    client_start_date = forms.DateField(
        label="Client Start",
        required=False,
        input_formats=[
            "%Y-%m-%d",
            "%m/%d/%Y",
        ],
        widget=forms.TextInput(
            attrs={
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "class": DATE_FIELD_CLASS,
                "data-planning-date": "1",
            }
        ),
    )

    client_end_date = forms.DateField(
        label="Client Finish",
        required=False,
        input_formats=[
            "%Y-%m-%d",
            "%m/%d/%Y",
        ],
        widget=forms.TextInput(
            attrs={
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "class": DATE_FIELD_CLASS,
                "data-planning-date": "1",
            }
        ),
    )

    resource_type = forms.ChoiceField(
        label="Resource Type",
        choices=PlanningResourceType.choices,
        widget=forms.Select(
            attrs={
                "class": FIELD_CLASS,
            }
        ),
    )

    resource_count = forms.DecimalField(
        label="Resources",
        max_digits=8,
        decimal_places=2,
        min_value=Decimal("0.01"),
        initial=Decimal("1"),
        widget=forms.NumberInput(
            attrs={
                "step": "any",
                "min": "0.01",
                "class": FIELD_CLASS,
            }
        ),
    )

    production_rate = forms.DecimalField(
        label="Productivity per Resource / Day",
        max_digits=14,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        widget=forms.NumberInput(
            attrs={
                "step": "any",
                "min": "0.0001",
                "class": FIELD_CLASS,
            }
        ),
    )

    planned_start_date = forms.DateField(
        label="Internal Start",
        required=False,
        input_formats=[
            "%Y-%m-%d",
            "%m/%d/%Y",
        ],
        widget=forms.TextInput(
            attrs={
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "class": DATE_FIELD_CLASS,
                "data-planning-date": "1",
            }
        ),
    )

    planned_end_date = forms.DateField(
        label="Internal Finish",
        required=False,
        input_formats=[
            "%Y-%m-%d",
            "%m/%d/%Y",
        ],
        widget=forms.TextInput(
            attrs={
                "placeholder": "MM/DD/YYYY",
                "autocomplete": "off",
                "class": DATE_FIELD_CLASS,
                "data-planning-date": "1",
                "data-planning-finish": "1",
            }
        ),
    )

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.fields["activity_type"].queryset = PlanningActivityType.objects.filter(
            is_active=True,
        ).order_by(
            "display_order",
            "code",
            "name",
        )

        if not self.is_bound:
            for field_name in (
                "quantity",
                "resource_count",
                "production_rate",
            ):
                if field_name in self.initial:
                    self.initial[field_name] = compact_decimal(self.initial[field_name])

            for field_name in (
                "client_start_date",
                "client_end_date",
                "planned_start_date",
                "planned_end_date",
            ):
                value = self.initial.get(field_name)

                if value:
                    try:
                        self.initial[field_name] = value.strftime("%m/%d/%Y")
                    except AttributeError:
                        pass

    def clean_new_activity_code(self):
        return self.cleaned_data["new_activity_code"].strip().upper()

    def clean_new_activity_name(self):
        return self.cleaned_data["new_activity_name"].strip()

    def clean_dfn_code(self):
        return self.cleaned_data["dfn_code"].strip()

    def clean(self):
        cleaned_data = super().clean()

        activity_type = cleaned_data.get("activity_type")

        new_activity_code = cleaned_data.get(
            "new_activity_code",
            "",
        )

        new_activity_name = cleaned_data.get(
            "new_activity_name",
            "",
        )

        if not activity_type:
            if not new_activity_code:
                self.add_error(
                    "new_activity_code",
                    "Select an existing activity or enter a new activity code.",
                )

            if not new_activity_name:
                self.add_error(
                    "new_activity_name",
                    "Enter the name of the new activity.",
                )

        if activity_type and (new_activity_code or new_activity_name):
            self.add_error(
                "activity_type",
                "Use either an existing activity or a new activity, not both.",
            )

        client_start_date = cleaned_data.get("client_start_date")

        client_end_date = cleaned_data.get("client_end_date")

        if (
            client_start_date
            and client_end_date
            and client_end_date < client_start_date
        ):
            self.add_error(
                "client_end_date",
                "Client finish cannot be earlier than Client start.",
            )

        planned_start_date = cleaned_data.get("planned_start_date")

        planned_end_date = cleaned_data.get("planned_end_date")

        if (
            planned_start_date
            and planned_end_date
            and planned_end_date < planned_start_date
        ):
            self.add_error(
                "planned_end_date",
                "Internal finish cannot be earlier than Internal start.",
            )

        return cleaned_data


PlanningDFNFormSet = formset_factory(
    PlanningDFNEntryForm,
    extra=0,
    can_delete=True,
)

PlanningActivityFormSet = formset_factory(
    PlanningActivityEntryForm,
    extra=0,
    can_delete=True,
)


class ProductivityProfileForm(forms.ModelForm):
    """
    Administrative form for reusable Planning productivity assumptions.

    A profile defines:

        production per resource per working day

    Example:

        Fiber Placement
        4,000 Foot / Crew / Day
        Default Resources: 1

    The profile is reusable when creating future Assignments. Existing
    Master Plan activities retain their own stored production values.
    """

    class Meta:
        model = ProductivityProfile

        fields = [
            "name",
            "activity_type",
            "unit",
            "resource_type",
            "production_rate",
            "default_resource_count",
            "is_default",
            "is_active",
            "notes",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": "e.g. Standard, Rural, Urban",
                    "autocomplete": "off",
                    "class": FIELD_CLASS,
                }
            ),
            "activity_type": forms.Select(
                attrs={
                    "class": FIELD_CLASS,
                }
            ),
            "unit": forms.Select(
                attrs={
                    "class": FIELD_CLASS,
                }
            ),
            "resource_type": forms.Select(
                attrs={
                    "class": FIELD_CLASS,
                }
            ),
            "production_rate": forms.NumberInput(
                attrs={
                    "step": "any",
                    "min": "0.0001",
                    "placeholder": "e.g. 4000",
                    "class": FIELD_CLASS,
                }
            ),
            "default_resource_count": forms.NumberInput(
                attrs={
                    "step": "any",
                    "min": "0.01",
                    "placeholder": "e.g. 1",
                    "class": FIELD_CLASS,
                }
            ),
            "is_default": forms.CheckboxInput(
                attrs={
                    "class": (
                        "w-5 h-5 rounded border-gray-300 text-blue-600 "
                        "focus:ring-blue-500"
                    ),
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": (
                        "w-5 h-5 rounded border-gray-300 text-blue-600 "
                        "focus:ring-blue-500"
                    ),
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Optional productivity notes...",
                    "class": FIELD_CLASS,
                }
            ),
        }

        labels = {
            "name": "Profile Name",
            "activity_type": "Activity",
            "unit": "Unit",
            "resource_type": "Resource Type",
            "production_rate": "Productivity per Resource / Day",
            "default_resource_count": "Default Resources",
            "is_default": "Default Profile",
            "is_active": "Active",
            "notes": "Notes",
        }

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.fields["activity_type"].queryset = PlanningActivityType.objects.filter(
            is_active=True,
        ).order_by(
            "display_order",
            "code",
            "name",
        )

        if not self.is_bound:
            for field_name in (
                "production_rate",
                "default_resource_count",
            ):
                value = getattr(
                    self.instance,
                    field_name,
                    None,
                )

                if value is not None:
                    self.initial[field_name] = compact_decimal(value)

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean(self):
        cleaned_data = super().clean()

        production_rate = cleaned_data.get("production_rate")

        default_resource_count = cleaned_data.get("default_resource_count")

        if production_rate is not None and production_rate <= 0:
            self.add_error(
                "production_rate",
                "Productivity per resource / day must be greater than zero.",
            )

        if default_resource_count is not None and default_resource_count <= 0:
            self.add_error(
                "default_resource_count",
                "Default resources must be greater than zero.",
            )

        return cleaned_data
