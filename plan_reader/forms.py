from django import forms

from facturacion.models import Proyecto
from operaciones.models import PrecioActividadTecnico

from .models import PlanReaderItem, PlanReaderJob


def _clean_choice_value(value):
    value = str(value or "").strip()

    if value in {"", "-", "—", "N/A", "NA", "None", "none", "NULL", "null"}:
        return ""

    return value


def _unique_choices(values, empty_label="Select option"):
    choices = [("", empty_label)]
    seen = set()

    clean_values = []

    for value in values:
        clean_value = _clean_choice_value(value)

        if not clean_value:
            continue

        key = clean_value.lower()

        if key in seen:
            continue

        seen.add(key)
        clean_values.append(clean_value)

    clean_values = sorted(clean_values, key=lambda x: x.lower())

    for value in clean_values:
        choices.append((value, value))

    return choices


def _price_distinct_values(field_name):
    """
    Lee valores únicos desde operaciones.PrecioActividadTecnico.

    Campos usados:
    - cliente
    - ciudad
    - oficina
    """
    try:
        return list(
            PrecioActividadTecnico.objects.exclude(**{f"{field_name}__isnull": True})
            .exclude(**{field_name: ""})
            .values_list(field_name, flat=True)
            .distinct()
        )
    except Exception:
        return []


def _project_values():
    """
    Lee proyectos activos desde facturacion.Proyecto.

    Se guarda Proyecto.nombre porque Bulk Billing valida el nombre del proyecto.
    """
    try:
        return list(
            Proyecto.objects.filter(activo=True)
            .exclude(nombre__isnull=True)
            .exclude(nombre="")
            .order_by("nombre")
            .values_list("nombre", flat=True)
            .distinct()
        )
    except Exception:
        return []


def _project_choices():
    try:
        qs = Proyecto.objects.filter(activo=True).order_by("nombre", "codigo")
    except Exception:
        qs = Proyecto.objects.none()

    choices = [("", "Select project")]
    seen = set()

    for project in qs:
        nombre = _clean_choice_value(getattr(project, "nombre", ""))

        if not nombre:
            continue

        key = nombre.lower()

        if key in seen:
            continue

        seen.add(key)

        codigo = _clean_choice_value(getattr(project, "codigo", ""))
        mandante = _clean_choice_value(getattr(project, "mandante", ""))
        ciudad = _clean_choice_value(getattr(project, "ciudad", ""))
        oficina = _clean_choice_value(getattr(project, "oficina", ""))

        label = nombre

        extras = []

        if codigo:
            extras.append(codigo)

        if mandante:
            extras.append(mandante)

        if ciudad:
            extras.append(ciudad)

        if oficina:
            extras.append(oficina)

        if extras:
            label = f"{nombre} — {' / '.join(extras)}"

        choices.append((nombre, label))

    return choices


class PlanReaderJobForm(forms.ModelForm):
    client = forms.ChoiceField(
        required=True,
        choices=[("", "Select client")],
        widget=forms.Select(
            attrs={
                "class": "w-full border rounded-lg px-3 py-2 bg-white",
            }
        ),
    )

    city = forms.ChoiceField(
        required=True,
        choices=[("", "Select city")],
        widget=forms.Select(
            attrs={
                "class": "w-full border rounded-lg px-3 py-2 bg-white",
            }
        ),
    )

    project = forms.ChoiceField(
        required=True,
        choices=[("", "Select project")],
        widget=forms.Select(
            attrs={
                "class": "w-full border rounded-lg px-3 py-2 bg-white",
            }
        ),
    )

    office = forms.ChoiceField(
        required=True,
        choices=[("", "Select office")],
        widget=forms.Select(
            attrs={
                "class": "w-full border rounded-lg px-3 py-2 bg-white",
            }
        ),
    )

    class Meta:
        model = PlanReaderJob
        fields = [
            "pdf_file",
            "client",
            "city",
            "project",
            "office",
            "co",
            "dfn",
            "notes",
        ]

        widgets = {
            "pdf_file": forms.ClearableFileInput(
                attrs={
                    "class": "block w-full text-sm border rounded-lg p-2 bg-white",
                    "accept": "application/pdf",
                }
            ),
            "co": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "placeholder": "Example: 0913RA",
                }
            ),
            "dfn": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "placeholder": "Example: 02",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "w-full border rounded-lg px-3 py-2",
                    "rows": 3,
                    "placeholder": "Optional notes",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        client_values = _price_distinct_values("cliente")
        city_values = _price_distinct_values("ciudad")
        office_values = _price_distinct_values("oficina")

        # Fallback adicional desde Proyecto, por si alguna lista de precios no tiene datos
        # o si quieres que también salgan valores registrados en proyectos.
        try:
            client_values += list(
                Proyecto.objects.filter(activo=True)
                .exclude(mandante__isnull=True)
                .exclude(mandante="")
                .values_list("mandante", flat=True)
                .distinct()
            )

            city_values += list(
                Proyecto.objects.filter(activo=True)
                .exclude(ciudad__isnull=True)
                .exclude(ciudad="")
                .values_list("ciudad", flat=True)
                .distinct()
            )

            office_values += list(
                Proyecto.objects.filter(activo=True)
                .exclude(oficina__isnull=True)
                .exclude(oficina="")
                .values_list("oficina", flat=True)
                .distinct()
            )
        except Exception:
            pass

        self.fields["client"].choices = _unique_choices(
            client_values,
            empty_label="Select client",
        )
        self.fields["city"].choices = _unique_choices(
            city_values,
            empty_label="Select city",
        )
        self.fields["office"].choices = _unique_choices(
            office_values,
            empty_label="Select office",
        )
        self.fields["project"].choices = _project_choices()

        # Asegura que el widget también reciba las opciones.
        self.fields["client"].widget.choices = self.fields["client"].choices
        self.fields["city"].widget.choices = self.fields["city"].choices
        self.fields["office"].widget.choices = self.fields["office"].choices
        self.fields["project"].widget.choices = self.fields["project"].choices

        self.fields["co"].required = True
        self.fields["dfn"].required = True

    def clean_pdf_file(self):
        pdf_file = self.cleaned_data.get("pdf_file")

        if not pdf_file:
            raise forms.ValidationError("Please upload a PDF file.")

        if not pdf_file.name.lower().endswith(".pdf"):
            raise forms.ValidationError("Only PDF files are allowed.")

        return pdf_file

    def clean_client(self):
        value = _clean_choice_value(self.cleaned_data.get("client"))

        if not value:
            raise forms.ValidationError("Please select a client.")

        return value

    def clean_city(self):
        value = _clean_choice_value(self.cleaned_data.get("city"))

        if not value:
            raise forms.ValidationError("Please select a city.")

        return value

    def clean_project(self):
        value = _clean_choice_value(self.cleaned_data.get("project"))

        if not value:
            raise forms.ValidationError("Please select a project.")

        return value

    def clean_office(self):
        value = _clean_choice_value(self.cleaned_data.get("office"))

        if not value:
            raise forms.ValidationError("Please select an office.")

        return value

    def clean_co(self):
        value = str(self.cleaned_data.get("co") or "").strip()

        if not value:
            raise forms.ValidationError("CO is required.")

        return value

    def clean_dfn(self):
        value = str(self.cleaned_data.get("dfn") or "").strip()

        if not value:
            raise forms.ValidationError("DFN is required.")

        return value


class PlanReaderItemReviewForm(forms.ModelForm):
    class Meta:
        model = PlanReaderItem
        fields = [
            "sheet",
            "co",
            "dfn",
            "project_name",
            "primary_feed",
            "visible_type",
            "detected_box_type",
            "has_p",
            "s_splitter",
            "t_splitter",
            "splice_count",
            "calculated_box_type",
            "c108_ug",
            "c109_splices",
            "c110_splitters",
            "observation",
            "needs_review",
            "is_duplicate",
        ]

        widgets = {
            "sheet": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "co": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "dfn": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "project_name": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "primary_feed": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "visible_type": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "detected_box_type": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "s_splitter": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "t_splitter": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "splice_count": forms.NumberInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2", "min": "0"}
            ),
            "calculated_box_type": forms.TextInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2"}
            ),
            "c108_ug": forms.NumberInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2", "min": "0"}
            ),
            "c109_splices": forms.NumberInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2", "min": "0"}
            ),
            "c110_splitters": forms.NumberInput(
                attrs={"class": "w-full border rounded-lg px-3 py-2", "min": "0"}
            ),
            "observation": forms.Textarea(
                attrs={"class": "w-full border rounded-lg px-3 py-2", "rows": 3}
            ),
        }
