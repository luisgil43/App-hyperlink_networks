from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils.text import slugify

from .models import RequirementList, RequirementListItem

INPUT_CLASS = "w-full border rounded-xl px-3 py-2 text-sm"
CHECK_CLASS = "h-4 w-4"


class RequirementListForm(forms.ModelForm):
    class Meta:
        model = RequirementList
        fields = [
            "name",
            "project",
            "list_type",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Example: CTO B8G / DROP BASIC / CABLE BASIC",
                    "autocomplete": "off",
                }
            ),
            "project": forms.Select(
                attrs={
                    "class": INPUT_CLASS,
                }
            ),
            "list_type": forms.Select(
                attrs={
                    "class": INPUT_CLASS,
                    "id": "id_list_type",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": CHECK_CLASS,
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and "list_type" in self.fields:
            self.fields["list_type"].disabled = True

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean(self):
        cleaned = super().clean()

        name = (cleaned.get("name") or "").strip()
        project = cleaned.get("project")
        list_type = cleaned.get("list_type")

        if not name:
            self.add_error("name", "List name is required.")

        if not project:
            self.add_error("project", "Project is required.")

        if not list_type:
            self.add_error("list_type", "List type is required.")

        if name and project and list_type:
            qs = RequirementList.objects.filter(
                project=project,
                list_type=list_type,
                name__iexact=name,
            )

            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                self.add_error(
                    "name",
                    "A requirement list with this name, project and type already exists.",
                )

        return cleaned


class RequirementListItemForm(forms.ModelForm):
    class Meta:
        model = RequirementListItem
        fields = [
            "order",
            "title",
            "description",
            "required",
            "needs_power_reading",
            "needs_light_source_reading",
            "power_port_no",
            "handhole",
            "planned_reserve_ft",
            "warning",
        ]
        widgets = {
            "order": forms.NumberInput(
                attrs={
                    "class": "w-24 border rounded-xl px-2 py-1 text-sm text-right",
                    "min": "0",
                    "step": "1",
                    "autocomplete": "off",
                }
            ),
            "title": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Example: Splice photo",
                    "autocomplete": "off",
                }
            ),
            "description": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Optional description",
                    "autocomplete": "off",
                }
            ),
            "required": forms.CheckboxInput(
                attrs={
                    "class": CHECK_CLASS,
                }
            ),
            "needs_power_reading": forms.CheckboxInput(
                attrs={
                    "class": CHECK_CLASS,
                }
            ),
            "needs_light_source_reading": forms.CheckboxInput(
                attrs={
                    "class": CHECK_CLASS,
                }
            ),
            "power_port_no": forms.NumberInput(
                attrs={
                    "class": "w-24 border rounded-xl px-2 py-1 text-sm text-right",
                    "min": "1",
                    "max": "99",
                    "autocomplete": "off",
                }
            ),
            "handhole": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Example: HH-01",
                    "autocomplete": "off",
                }
            ),
            "planned_reserve_ft": forms.NumberInput(
                attrs={
                    "class": "w-28 border rounded-xl px-2 py-1 text-sm text-right",
                    "min": "0",
                    "step": "any",
                    "placeholder": "0",
                    "autocomplete": "off",
                }
            ),
            "warning": forms.TextInput(
                attrs={
                    "class": INPUT_CLASS,
                    "placeholder": "Optional warning for technician",
                    "autocomplete": "off",
                }
            ),
        }

    def clean_title(self):
        return (self.cleaned_data.get("title") or "").strip()

    def clean_handhole(self):
        return (self.cleaned_data.get("handhole") or "").strip()

    def clean_power_port_no(self):
        value = self.cleaned_data.get("power_port_no")

        if value in (None, ""):
            return None

        if value < 1:
            raise ValidationError("Port must be greater than 0.")

        return value

    def clean_planned_reserve_ft(self):
        value = self.cleaned_data.get("planned_reserve_ft")

        if value in (None, ""):
            return Decimal("0.00")

        if value < 0:
            raise ValidationError("Planned reserve cannot be negative.")

        return value


class BaseRequirementListItemFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        self.list_type = kwargs.pop("list_type", None)

        super().__init__(*args, **kwargs)

        if not self.list_type:
            self.list_type = getattr(
                self.instance,
                "list_type",
                RequirementList.LIST_TYPE_FIBER,
            )

        if not self.list_type:
            self.list_type = RequirementList.LIST_TYPE_FIBER

    def clean(self):
        super().clean()

        if any(self.errors):
            return

        active_forms = []
        seen = set()

        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue

            cleaned = form.cleaned_data

            if not cleaned:
                continue

            if cleaned.get("DELETE"):
                continue

            title = (cleaned.get("title") or "").strip()
            handhole = (cleaned.get("handhole") or "").strip()
            description = (cleaned.get("description") or "").strip()
            warning = (cleaned.get("warning") or "").strip()
            power_port_no = cleaned.get("power_port_no")
            needs_power = bool(cleaned.get("needs_power_reading"))
            needs_light = bool(cleaned.get("needs_light_source_reading"))

            # Importante:
            # NO contamos order, required ni planned_reserve_ft como valor activo,
            # porque esos campos pueden venir con default aunque la fila esté vacía.
            has_real_value = any(
                [
                    title,
                    handhole,
                    description,
                    warning,
                    power_port_no not in (None, ""),
                    needs_power,
                    needs_light,
                ]
            )

            is_existing = bool(getattr(form.instance, "pk", None))

            if not has_real_value:
                # Si es una fila nueva totalmente vacía, se ignora.
                if not is_existing:
                    cleaned["DELETE"] = True
                    continue

                # Si era una fila existente y quedó vacía, sí mostramos error.
                if self.list_type == RequirementList.LIST_TYPE_CABLE:
                    form.add_error(
                        "handhole",
                        "Handhole is required for cable lists.",
                    )
                else:
                    form.add_error(
                        "title",
                        "Title is required for fiber/photo lists.",
                    )

                continue

            active_forms.append(form)

            if self.list_type == RequirementList.LIST_TYPE_CABLE:
                if not handhole:
                    form.add_error("handhole", "Handhole is required for cable lists.")
                    continue

                normalized = slugify(handhole)

                if not normalized:
                    form.add_error("handhole", "Enter a valid handhole.")
                    continue

            else:
                if not title:
                    form.add_error("title", "Title is required for fiber/photo lists.")
                    continue

                normalized = slugify(title)

                if not normalized:
                    form.add_error("title", "Enter a valid title.")
                    continue

            if normalized in seen:
                if self.list_type == RequirementList.LIST_TYPE_CABLE:
                    form.add_error(
                        "handhole",
                        "This handhole is duplicated in this list.",
                    )
                else:
                    form.add_error(
                        "title",
                        "This requirement is duplicated in this list.",
                    )
            else:
                seen.add(normalized)

        if not active_forms:
            raise forms.ValidationError(
                "You must add at least one requirement to the list."
            )


RequirementListItemFormSet = inlineformset_factory(
    RequirementList,
    RequirementListItem,
    form=RequirementListItemForm,
    formset=BaseRequirementListItemFormSet,
    extra=0,
    can_delete=True,
)
