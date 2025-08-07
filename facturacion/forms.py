import re
from .models import CartolaMovimiento
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from decimal import Decimal
from operaciones.templatetags.custom_filters import formato_clp  # Usa tu filtro de CLP
from .models import TipoGasto
from .models import CartolaMovimiento, TipoGasto, Proyecto
from django import forms


# facturacion/forms.py

from django import forms


class CartolaAbonoForm(forms.ModelForm):
    abonos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        required=True,
        label="Abonos"
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
                'placeholder': 'Escribe una breve observación...'
            }),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].label_from_instance = lambda obj: f"{obj.identidad} - {obj.first_name} {obj.last_name}"
        for field in self.fields.values():
            field.required = True

        # Preformatear para edición
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
                "Ingrese un número válido para Abonos.")


class CartolaGastoForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        required=True,
        label="Cargos"
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
                'placeholder': 'Escribe una breve observación...'
            }),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'placeholder': 'Ej: 123456789'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['usuario'].label_from_instance = lambda obj: f"{obj.identidad} - {obj.first_name} {obj.last_name}"
        for field in self.fields.values():
            field.required = True

        # Preformatear para edición
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
                "Ingrese un número válido para Cargos.")


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
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 2}),
            'numero_transferencia': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = True

        # Preformatear para edición
        if self.instance and self.instance.pk:
            if self.instance.cargos is not None:
                self.initial['cargos'] = f"{self.instance.cargos:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")
            if self.instance.abonos is not None:
                self.initial['abonos'] = f"{self.instance.abonos:,.2f}".replace(
                    ",", "X").replace(".", ",").replace("X", ".")

    def _clean_monto(self, value, field_name):
        """Convierte texto con puntos miles y coma decimal a Decimal con 2 decimales."""
        if not value:
            return Decimal("0.00")
        value = value.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            value = Decimal(value).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            if value < 0:
                raise forms.ValidationError(
                    f"El {field_name} no puede ser negativo.")
            return value
        except InvalidOperation:
            raise forms.ValidationError(
                f"Ingrese un {field_name} válido en formato 1.234,56")

    def clean_cargos(self):
        return self._clean_monto(self.cleaned_data.get('cargos'), "cargo")

    def clean_abonos(self):
        return self._clean_monto(self.cleaned_data.get('abonos'), "abono")


class TipoGastoForm(forms.ModelForm):
    class Meta:
        model = TipoGasto
        fields = ['nombre', 'categoria']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full border rounded-xl px-3 py-2',
                'placeholder': 'Ej: Combustible'
            }),
            'categoria': forms.Select(attrs={
                'class': 'w-full border rounded-xl px-3 py-2'
            })
        }


class ProyectoForm(forms.ModelForm):
    class Meta:
        model = Proyecto
        fields = ['nombre', 'mandante']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'mandante': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }
