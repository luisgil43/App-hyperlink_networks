from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from plan_reader.models import (PlanReaderMaterialRequest,
                                PlanReaderMaterialRequestItem)


class PlanReaderMaterialRequestForm(forms.ModelForm):
    """
    Formulario para editar los datos generales de una solicitud de material.

    Se utiliza tanto para solicitudes:

    - Splicing
    - Cable

    El tipo de solicitud y el Job no se editan desde este formulario.
    """

    class Meta:
        model = PlanReaderMaterialRequest

        fields = [
            "subcontractor",
            "request_date",
            "market",
            "dfn",
            "contractor_employee_name",
            "contractor_employee_signature",
            "notes",
        ]

        widgets = {
            "subcontractor": forms.TextInput(
                attrs={
                    "class": "material-request-input",
                    "autocomplete": "organization",
                    "placeholder": "Subcontractor",
                }
            ),
            "request_date": forms.DateInput(
                attrs={
                    "class": "material-request-input",
                    "type": "date",
                },
                format="%Y-%m-%d",
            ),
            "market": forms.TextInput(
                attrs={
                    "class": "material-request-input",
                    "placeholder": "Market",
                }
            ),
            "dfn": forms.TextInput(
                attrs={
                    "class": "material-request-input",
                    "placeholder": "DFN",
                }
            ),
            "contractor_employee_name": forms.TextInput(
                attrs={
                    "class": "material-request-input",
                    "autocomplete": "name",
                    "placeholder": "Contractor employee name",
                }
            ),
            "contractor_employee_signature": forms.TextInput(
                attrs={
                    "class": "material-request-input",
                    "placeholder": "Typed signature",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "material-request-textarea",
                    "rows": 3,
                    "placeholder": "Additional notes",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["request_date"].input_formats = [
            "%Y-%m-%d",
        ]

        for field in self.fields.values():
            field.required = False

    def clean_subcontractor(self):
        value = str(self.cleaned_data.get("subcontractor") or "").strip()

        return value

    def clean_market(self):
        value = str(self.cleaned_data.get("market") or "").strip()

        return value

    def clean_dfn(self):
        value = str(self.cleaned_data.get("dfn") or "").strip()

        return value

    def clean_contractor_employee_name(self):
        value = str(self.cleaned_data.get("contractor_employee_name") or "").strip()

        return value

    def clean_contractor_employee_signature(self):
        value = str(
            self.cleaned_data.get("contractor_employee_signature") or ""
        ).strip()

        return value

    def clean_notes(self):
        value = str(self.cleaned_data.get("notes") or "").strip()

        return value


class PlanReaderMaterialRequestItemForm(forms.ModelForm):
    """
    Formulario individual para una fila del Material Request.

    Las columnas descriptivas se muestran en la plantilla, pero no se
    modifican directamente:

    - material_type
    - category
    - material_name
    - uom

    El usuario puede editar:

    - quantity_requested
    - quantity_received
    """

    class Meta:
        model = PlanReaderMaterialRequestItem

        fields = [
            "quantity_requested",
            "quantity_received",
        ]

        widgets = {
            "quantity_requested": forms.NumberInput(
                attrs={
                    "class": ("material-quantity-input " "material-quantity-requested"),
                    "min": "0",
                    "step": "1",
                    "inputmode": "decimal",
                    "placeholder": "",
                }
            ),
            "quantity_received": forms.NumberInput(
                attrs={
                    "class": ("material-quantity-input " "material-quantity-received"),
                    "min": "0",
                    "step": "1",
                    "inputmode": "decimal",
                    "placeholder": "",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["quantity_requested"].required = False
        self.fields["quantity_received"].required = False

        if self.instance and self.instance.pk:
            requested = self.instance.quantity_requested
            received = self.instance.quantity_received

            if requested in (
                None,
                Decimal("0"),
                Decimal("0.00"),
            ):
                self.initial["quantity_requested"] = ""

            if received in (
                None,
                Decimal("0"),
                Decimal("0.00"),
            ):
                self.initial["quantity_received"] = ""

    @staticmethod
    def _clean_quantity(
        value,
        *,
        field_label: str,
        allow_none: bool,
    ):
        """
        Convierte entradas vacías de la pantalla en:

        - 0 para quantity_requested
        - None para quantity_received

        La base de datos puede conservar cero, mientras que la pantalla
        y el PDF lo mostrarán vacío.
        """

        if value in (
            None,
            "",
        ):
            if allow_none:
                return None

            return Decimal("0")

        try:
            decimal_value = Decimal(str(value))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise forms.ValidationError(
                f"{field_label} must be a valid number."
            ) from exc

        if decimal_value < 0:
            raise forms.ValidationError(f"{field_label} cannot be negative.")

        return decimal_value

    def clean_quantity_requested(self):
        return self._clean_quantity(
            self.cleaned_data.get("quantity_requested"),
            field_label="Quantity requested",
            allow_none=False,
        )

    def clean_quantity_received(self):
        return self._clean_quantity(
            self.cleaned_data.get("quantity_received"),
            field_label="Quantity received",
            allow_none=True,
        )


class BasePlanReaderMaterialRequestItemFormSet(BaseInlineFormSet):
    """
    Formset encargado de validar y guardar todas las filas de materiales.

    No permite eliminar filas porque todas deben permanecer disponibles
    para mostrarse e imprimirse en el formulario completo.
    """

    def clean(self):
        super().clean()

        if any(self.errors):
            return

        seen_item_ids = set()

        for form in self.forms:
            if not hasattr(
                form,
                "cleaned_data",
            ):
                continue

            instance = form.instance

            if not instance or not instance.pk:
                continue

            if instance.pk in seen_item_ids:
                raise forms.ValidationError(
                    "A material row was submitted more than once."
                )

            seen_item_ids.add(instance.pk)

    def save(
        self,
        commit=True,
    ):
        """
        Guarda las cantidades y detecta si una cantidad automática fue
        modificada manualmente.

        Cuando el usuario cambia una cantidad automática, la fuente pasa
        a automatic_edited. Esto evita que un recálculo normal sobrescriba
        la modificación.
        """

        instances = super().save(commit=False)

        saved_instances = []

        for instance in instances:
            previous_instance = (
                PlanReaderMaterialRequestItem.objects.filter(
                    pk=instance.pk,
                )
                .only(
                    "quantity_requested",
                    "automatic_quantity",
                    "source",
                )
                .first()
            )

            if previous_instance is not None:
                automatic_quantity = previous_instance.automatic_quantity or Decimal(
                    "0"
                )

                requested_quantity = instance.quantity_requested or Decimal("0")

                automatic_sources = {
                    PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC,
                    PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC_EDITED,
                }

                if previous_instance.source in automatic_sources:
                    if requested_quantity != automatic_quantity:
                        instance.source = (
                            PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC_EDITED
                        )
                    else:
                        instance.source = PlanReaderMaterialRequestItem.SOURCE_AUTOMATIC

            if commit:
                instance.save(
                    update_fields=[
                        "quantity_requested",
                        "quantity_received",
                        "source",
                        "updated_at",
                    ]
                )

            saved_instances.append(instance)

        if commit:
            self.save_m2m()

        return saved_instances


PlanReaderMaterialRequestItemFormSet = inlineformset_factory(
    parent_model=PlanReaderMaterialRequest,
    model=PlanReaderMaterialRequestItem,
    form=PlanReaderMaterialRequestItemForm,
    formset=BasePlanReaderMaterialRequestItemFormSet,
    fields=[
        "quantity_requested",
        "quantity_received",
    ],
    extra=0,
    can_delete=False,
)
