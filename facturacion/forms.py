import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# facturacion/forms.py
from django import forms

from .models import CartolaMovimiento, Proyecto, TipoGasto

# facturacion/forms.py  — SOLO el form ProyectoForm
CODE_RE = re.compile(r'^PRJ-\d{6}$')

class CartolaAbonoForm(forms.ModelForm):
    abonos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2',
                   'placeholder': 'e.g., 1,234.56'}
        ),
        required=True,
        label="Deposit Amount"
    )

    class Meta:
        model = CartolaMovimiento
        fields = ['usuario', 'proyecto', 'observaciones',
                  'numero_transferencia', 'comprobante', 'abonos']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'rows': 2,
                'placeholder': 'Write a brief note...'
            }),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'e.g., 123456789'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'accept': '.png,.jpg,.jpeg,.pdf'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].label_from_instance = lambda obj: f"{obj.identidad} - {obj.first_name} {obj.last_name}"
        for field in self.fields.values():
            field.required = True
        if self.instance and self.instance.pk and self.instance.abonos is not None:
            self.initial['abonos'] = f"{self.instance.abonos:,.2f}".replace(
                ",", "X").replace(".", ",").replace("X", ".")

    def clean_abonos(self):
        valor = self.cleaned_data.get('abonos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Please enter a valid number for Deposit Amount.")


class CartolaGastoForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={
                "class": "w-full border rounded-xl px-3 py-2",
                "placeholder": "e.g., 1,234.56",
            }
        ),
        required=True,
        label="Charge Amount",
    )

    class Meta:
        model = CartolaMovimiento
        fields = [
            "usuario",
            "proyecto",
            "tipo",
            "observaciones",
            "numero_transferencia",
            "comprobante",
            "cargos",
        ]
        widgets = {
            "usuario": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "proyecto": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "tipo": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "observaciones": forms.Textarea(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "rows": 2,
                    "placeholder": "Write a brief note...",
                }
            ),
            "numero_transferencia": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "e.g., 123456789",
                }
            ),
            "comprobante": forms.ClearableFileInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "accept": ".png,.jpg,.jpeg,.pdf",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ✅ Solo tipos activos (pero si estás editando y el tipo quedó inactivo,
        # lo incluimos para no romper el form)
        current_tipo_id = None
        try:
            if self.instance and self.instance.pk:
                current_tipo_id = self.instance.tipo_id
        except Exception:
            current_tipo_id = None

        if current_tipo_id:
            self.fields["tipo"].queryset = TipoGasto.objects.filter(
                forms.models.Q(is_active=True) | forms.models.Q(pk=current_tipo_id)
            ).order_by("nombre")
        else:
            self.fields["tipo"].queryset = TipoGasto.objects.filter(
                is_active=True
            ).order_by("nombre")

        self.fields["usuario"].label_from_instance = (
            lambda obj: f"{obj.identidad} - {obj.first_name} {obj.last_name}"
        )
        for field in self.fields.values():
            field.required = True

        if self.instance and self.instance.pk and self.instance.cargos is not None:
            self.initial["cargos"] = (
                f"{self.instance.cargos:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

    def clean_cargos(self):
        valor = self.cleaned_data.get("cargos", "0")
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Please enter a valid number for Charge Amount."
            )


class CartolaMovimientoCompletoForm(forms.ModelForm):
    cargos = forms.CharField()
    abonos = forms.CharField()

    class Meta:
        model = CartolaMovimiento
        fields = [
            "usuario",
            "proyecto",
            "tipo",
            "observaciones",
            "numero_transferencia",
            "comprobante",
            "cargos",
            "abonos",
        ]
        widgets = {
            "usuario": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "proyecto": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "tipo": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "observaciones": forms.Textarea(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "rows": 2,
                    "placeholder": "Write a brief note...",
                }
            ),
            "numero_transferencia": forms.TextInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "placeholder": "e.g., 123456789",
                }
            ),
            "comprobante": forms.ClearableFileInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "accept": ".png,.jpg,.jpeg,.pdf",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ✅ Solo tipos activos (pero si estás editando y el tipo quedó inactivo,
        # lo incluimos para no romper el form)
        current_tipo_id = None
        try:
            if self.instance and self.instance.pk:
                current_tipo_id = self.instance.tipo_id
        except Exception:
            current_tipo_id = None

        if current_tipo_id:
            self.fields["tipo"].queryset = TipoGasto.objects.filter(
                forms.models.Q(is_active=True) | forms.models.Q(pk=current_tipo_id)
            ).order_by("nombre")
        else:
            self.fields["tipo"].queryset = TipoGasto.objects.filter(
                is_active=True
            ).order_by("nombre")

        for field in self.fields.values():
            field.required = True

        if self.instance and self.instance.pk:
            if self.instance.cargos is not None:
                self.initial["cargos"] = (
                    f"{self.instance.cargos:,.2f}".replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )
            if self.instance.abonos is not None:
                self.initial["abonos"] = (
                    f"{self.instance.abonos:,.2f}".replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )

    def _clean_monto(self, value, field_name):
        if not value:
            return Decimal("0.00")
        value = value.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value < 0:
                raise forms.ValidationError(
                    f"{field_name.capitalize()} cannot be negative."
                )
            return value
        except InvalidOperation:
            raise forms.ValidationError(
                f"Enter a valid {field_name} in format 1,234.56"
            )

    def clean_cargos(self):
        return self._clean_monto(self.cleaned_data.get("cargos"), "charge")

    def clean_abonos(self):
        return self._clean_monto(self.cleaned_data.get("abonos"), "deposit")


class TipoGastoForm(forms.ModelForm):
    class Meta:
        model = TipoGasto
        fields = ['nombre', 'categoria']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'placeholder': 'e.g., Fuel'
            }),
            'categoria': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'})
        }


# forms.py
# forms.py
import re  # Asegúrate de tener este import arriba

from django import forms

from .models import Proyecto


class ProyectoForm(forms.ModelForm):
    REQUIRED_MSG = "This field is required."

    ACTIVO_CHOICES = ((True, 'Active'), (False, 'Inactive'))
    activo = forms.TypedChoiceField(
        label='Active',
        choices=ACTIVO_CHOICES,
        coerce=lambda v: str(v).lower() in ('true', '1', 'on', 'yes', 'y'),
        widget=forms.RadioSelect,
        required=True,
        error_messages={'required': REQUIRED_MSG,
                        'invalid_choice': "Please select Active or Inactive."},
    )

    class Meta:
        model = Proyecto
        fields = ['codigo', 'nombre', 'mandante', 'ciudad', 'estado', 'oficina', 'activo']
        labels = {
            'codigo':   'Project ID (code)',
            'nombre':   'Project Name',
            'mandante': 'Client',
            'ciudad':   'City',
            'estado':   'State',
            'oficina':  'Office',
            'activo':   'Active',
        }
        widgets = {
            'codigo':   forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'e.g., PRJ-001', 'required': 'required'}),
            'nombre':   forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Project name', 'required': 'required'}),
            'mandante': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Client', 'required': 'required'}),
            'ciudad':   forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'City', 'required': 'required'}),
            'estado':   forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'State', 'required': 'required'}),
            'oficina':  forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Office', 'required': 'required'}),
        }
        error_messages = {'codigo': {'unique': "A project with this code already exists."}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name != 'activo':
                field.required = True
                field.widget.attrs['required'] = 'required'
                field.error_messages['required'] = self.REQUIRED_MSG
        self.initial.setdefault('activo', bool(getattr(self.instance, 'activo', True)))

    def _norm(self, s: str) -> str:
        # normaliza: trim y colapsa espacios, compara case-insensitive
        return re.sub(r'\s+', ' ', (s or '').strip()).casefold()

    def clean(self):
        cleaned = super().clean()
        nombre   = self._norm(cleaned.get('nombre'))
        mandante = self._norm(cleaned.get('mandante'))
        ciudad   = self._norm(cleaned.get('ciudad'))
        estado   = self._norm(cleaned.get('estado'))
        oficina  = self._norm(cleaned.get('oficina'))

        if all([nombre, mandante, ciudad, estado, oficina]):
            qs = (Proyecto.objects
                    .filter(nombre__iexact=cleaned.get('nombre', '').strip(),
                            mandante__iexact=cleaned.get('mandante', '').strip(),
                            ciudad__iexact=cleaned.get('ciudad', '').strip(),
                            estado__iexact=cleaned.get('estado', '').strip(),
                            oficina__iexact=cleaned.get('oficina', '').strip()))
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                msg = "A project with the same Name, Client, City, State, and Office already exists."
                for f in ('nombre', 'mandante', 'ciudad', 'estado', 'oficina'):
                    self.add_error(f, msg)
                raise forms.ValidationError(msg)
        return cleaned


class CartolaMovimientoEdicionForm(forms.ModelForm):
    """
    Formulario exclusivo de edición administrativa.

    No modifica aprobaciones ni datos relacionados con Fleet.
    """

    fecha_edicion = forms.SplitDateTimeField(
        label="Date",
        required=True,
        input_date_formats=["%Y-%m-%d"],
        input_time_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date"},
            time_attrs={"type": "time"},
            date_format="%Y-%m-%d",
            time_format="%H:%M",
        ),
    )

    class Meta:
        model = CartolaMovimiento

        fields = [
            "usuario",
            "proyecto",
            "tipo",
            "real_consumption_date",
            "observaciones",
            "numero_transferencia",
            "comprobante",
            "cargos",
            "abonos",
            "service_type_obj",
            "vehicle",
            "service_date",
            "service_time",
            "kilometraje",
            "foto_tablero",
        ]

        widgets = {
            "real_consumption_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date"},
            ),
            "observaciones": forms.Textarea(
                attrs={"rows": 3},
            ),
            "comprobante": forms.ClearableFileInput(
                attrs={
                    "accept": ".pdf,.jpg,.jpeg,.png",
                    "class": "hidden",
                },
            ),
            "foto_tablero": forms.ClearableFileInput(
                attrs={
                    "accept": "image/jpeg,image/png",
                    "class": "hidden",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from django.utils import timezone

        movimiento = self.instance

        self.es_abono = bool(
            movimiento.pk
            and movimiento.tipo
            and movimiento.tipo.categoria == "abono"
        )

        if movimiento.pk and movimiento.fecha:
            fecha_local = timezone.localtime(movimiento.fecha)

            self.initial["fecha_edicion"] = [
                fecha_local.date(),
                fecha_local.time().replace(
                    second=0,
                    microsecond=0,
                ),
            ]

        if "tipo" in self.fields:
            categorias_permitidas = (
                ["abono"]
                if self.es_abono
                else ["gasto", "costo", "inversion"]
            )

            from django.db.models import Q

            filtro = Q(
                categoria__in=categorias_permitidas,
                is_active=True,
            )

            if movimiento.pk and movimiento.tipo_id:
                filtro |= Q(
                    pk=movimiento.tipo_id,
                    categoria__in=categorias_permitidas,
                )

            self.fields["tipo"].queryset = (
                TipoGasto.objects.filter(filtro).order_by("nombre")
            )

        for nombre in (
            "observaciones",
            "numero_transferencia",
            "comprobante",
            "real_consumption_date",
        ):
            if nombre in self.fields:
                self.fields[nombre].required = False

        if self.es_abono:
            self.fields.pop("cargos", None)
        else:
            self.fields.pop("abonos", None)

        for nombre in ("usuario", "proyecto", "tipo"):
            if nombre in self.fields:
                self.fields[nombre].required = True

    def clean_fecha_edicion(self):
        from django.utils import timezone

        fecha = self.cleaned_data["fecha_edicion"]

        if timezone.is_naive(fecha):
            fecha = timezone.make_aware(
                fecha,
                timezone.get_current_timezone(),
            )

        return fecha

    def clean(self):
        cleaned = super().clean()

        tipo = cleaned.get("tipo")

        if tipo:
            categorias_permitidas = (
                {"abono"}
                if self.es_abono
                else {"gasto", "costo", "inversion"}
            )

            if tipo.categoria not in categorias_permitidas:
                self.add_error(
                    "tipo",
                    "Transaction cannot be converted between expense and deposit.",
                )

            tipo_original = (
                self.instance.tipo
                if self.instance.pk
                else None
            )

            original_es_service = bool(
                tipo_original
                and (tipo_original.nombre or "").strip().lower() in {"service", "services"}
            )

            nuevo_es_service = (
                (tipo.nombre or "").strip().lower() in {"service", "services"}
            )

            if (
                original_es_service != nuevo_es_service
                or (
                    original_es_service
                    and tipo_original.pk != tipo.pk
                )
            ):
                self.add_error(
                    "tipo",
                    "Service transactions cannot change their type "
                    "from this editor because Fleet history must be preserved.",
                )

        monto = cleaned.get(
            "abonos" if self.es_abono else "cargos"
        )

        if monto is not None and monto < 0:
            self.add_error(
                "abonos" if self.es_abono else "cargos",
                "Amount cannot be negative.",
            )

        return cleaned
