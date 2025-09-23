from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django import forms
from .models import CartolaMovimiento, TipoGasto, Proyecto


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
            attrs={'class': 'w-full border rounded-xl px-3 py-2',
                   'placeholder': 'e.g., 1,234.56'}
        ),
        required=True,
        label="Charge Amount"
    )

    class Meta:
        model = CartolaMovimiento
        fields = ['usuario', 'proyecto', 'tipo', 'observaciones',
                  'numero_transferencia', 'comprobante', 'cargos']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
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
        if self.instance and self.instance.pk and self.instance.cargos is not None:
            self.initial['cargos'] = f"{self.instance.cargos:,.2f}".replace(
                ",", "X").replace(".", ",").replace("X", ".")

    def clean_cargos(self):
        valor = self.cleaned_data.get('cargos', '0')
        valor = str(valor).replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Please enter a valid number for Charge Amount.")


class CartolaMovimientoCompletoForm(forms.ModelForm):
    cargos = forms.CharField()
    abonos = forms.CharField()

    class Meta:
        model = CartolaMovimiento
        fields = ['usuario', 'proyecto', 'tipo', 'observaciones',
                  'numero_transferencia', 'comprobante', 'cargos', 'abonos']
        widgets = {
            'usuario': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 2, 'placeholder': 'Write a brief note...'}),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'e.g., 123456789'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'accept': '.png,.jpg,.jpeg,.pdf'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True
        if self.instance and self.instance.pk:
            if self.instance.cargos is not None:
                self.initial['cargos'] = f"{self.instance.cargos:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")
            if self.instance.abonos is not None:
                self.initial['abonos'] = f"{self.instance.abonos:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")

    def _clean_monto(self, value, field_name):
        if not value:
            return Decimal("0.00")
        value = value.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(value).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value < 0:
                raise forms.ValidationError(
                    f"{field_name.capitalize()} cannot be negative.")
            return value
        except InvalidOperation:
            raise forms.ValidationError(
                f"Enter a valid {field_name} in format 1,234.56")

    def clean_cargos(self):
        return self._clean_monto(self.cleaned_data.get('cargos'), "charge")

    def clean_abonos(self):
        return self._clean_monto(self.cleaned_data.get('abonos'), "deposit")


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


class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ['nombre', 'mandante']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Project name'}),
            'mandante': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Client'}),
        }
