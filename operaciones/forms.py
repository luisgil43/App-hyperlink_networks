# servicios/forms.py

from django.core.exceptions import ValidationError
import requests
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from facturacion.models import CartolaMovimiento
from decimal import Decimal
from django.forms import ModelMultipleChoiceField
from django.contrib.auth import get_user_model
from django import forms
from decimal import Decimal, InvalidOperation


from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django import forms


class MovimientoUsuarioForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        label="Monto (USD)",
        required=True
    )

    # <- Ahora lo hacemos opcional y validamos manualmente
    comprobante = forms.FileField(required=False)

    class Meta:
        model = CartolaMovimiento
        fields = ['proyecto', 'tipo', 'cargos', 'observaciones', 'comprobante']
        labels = {
            'cargos': 'Monto (USD)',
            'comprobante': 'Comprobante',
            'observaciones': 'Observaciones',
            'proyecto': 'Proyecto',
            'tipo': 'Tipo',
        }
        widgets = {
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields:
            if field != 'comprobante':  # dejamos comprobante libre para validarlo a mano
                self.fields[field].required = True

    def clean(self):
        cleaned_data = super().clean()
        comprobante_foto = self.files.get('comprobante_foto')
        comprobante_archivo = self.files.get('comprobante_archivo')

        if comprobante_foto:
            cleaned_data['comprobante'] = comprobante_foto
        elif comprobante_archivo:
            cleaned_data['comprobante'] = comprobante_archivo
        else:
            raise forms.ValidationError(
                "Debe adjuntar un comprobante (foto o archivo).")

        return cleaned_data

    def clean_cargos(self):
        valor = self.cleaned_data.get('cargos', '0').strip()

        # Si el valor tiene coma como decimal → formato europeo (1.234,56)
        if "," in valor:
            valor = valor.replace(".", "").replace(",", ".")
        else:
            # Si solo usa punto → asumimos que es decimal (formato US)
            valor = valor.replace(" ", "")

        try:
            return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise forms.ValidationError(
                "Ingrese un monto válido (ej: 30.50 o 30,50)")
