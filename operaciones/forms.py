# servicios/forms.py

from .models import WeeklyPayment
from .models import SesionBilling
from .models import PrecioActividadTecnico
from usuarios.models import CustomUser, Rol
from usuarios.models import CustomUser
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
        label="Amount (USD)",
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
                "Please attach a receipt (photo or file).")  # <-- EN

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
                "Enter a valid amount (e.g., 30.50 or 30,50).")  # <-- EN


class ImportarPreciosForm(forms.Form):
    archivo = forms.FileField(label="Upload Excel File", required=True)
    tecnicos = forms.ModelMultipleChoiceField(
        queryset=CustomUser.objects.filter(roles__nombre='usuario').distinct(),
        widget=forms.CheckboxSelectMultiple,
        label="Select Technicians"
    )

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if not archivo.name.endswith('.xlsx'):
            raise ValidationError("The file must be an Excel .xlsx file.")
        return archivo

    def clean_tecnicos(self):
        tecnicos = self.cleaned_data.get('tecnicos')
        if not tecnicos:
            raise ValidationError("Please select at least one technician.")
        return tecnicos


class PrecioActividadTecnicoForm(forms.ModelForm):
    """Formulario para crear o editar precios por técnico con todos los campos requeridos."""

    class Meta:
        model = PrecioActividadTecnico
        fields = [
            'tecnico',
            'ciudad',
            'proyecto',
            'oficina',
            'cliente',
            'tipo_trabajo',
            'codigo_trabajo',
            'descripcion',
            'unidad_medida',
            'precio_tecnico',
            'precio_empresa',
        ]
        labels = {
            'tecnico': "Technician",
            'ciudad': "City",
            'proyecto': "Project",
            'oficina': "Office",
            'cliente': "Client",
            'tipo_trabajo': "Work Type",
            'codigo_trabajo': "Job Code",
            'descripcion': "Description",
            'unidad_medida': "UOM",
            'precio_tecnico': "Tech Price (USD)",
            'precio_empresa': "Company Price (USD)",
        }
        widgets = {
            'tecnico': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'ciudad': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'proyecto': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'oficina': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'cliente': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo_trabajo': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'codigo_trabajo': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'descripcion': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 3}),
            'unidad_medida': forms.TextInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'precio_tecnico': forms.NumberInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'step': '0.01'}),
            'precio_empresa': forms.NumberInput(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'step': '0.01'}),
        }

    def clean_precio_tecnico(self):
        precio = self.cleaned_data.get('precio_tecnico')
        if precio < 0:
            raise ValidationError("Technician price cannot be negative.")
        return precio

    def clean_precio_empresa(self):
        precio = self.cleaned_data.get('precio_empresa')
        if precio < 0:
            raise ValidationError("Company price cannot be negative.")
        return precio

    def clean_codigo_trabajo(self):
        codigo_trabajo = self.cleaned_data.get('codigo_trabajo')
        if not codigo_trabajo:
            raise ValidationError("Job code cannot be empty.")
        return codigo_trabajo

    def clean_ciudad(self):
        ciudad = self.cleaned_data.get('ciudad')
        if not ciudad:
            raise ValidationError("City cannot be empty.")
        return ciudad

    def clean_proyecto(self):
        proyecto = self.cleaned_data.get('proyecto')
        if not proyecto:
            raise ValidationError("Project cannot be empty.")
        return proyecto


class PaymentApproveForm(forms.ModelForm):
    """
    El trabajador aprueba el monto. No edita campos, solo cambia estado en la vista.
    """
    class Meta:
        model = WeeklyPayment
        fields = []


class PaymentRejectForm(forms.ModelForm):
    """
    El trabajador rechaza e indica el motivo (obligatorio).
    """
    reject_reason = forms.CharField(
        required=True,
        label="Reason",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Tell us why you reject this amount...",
                "class": "w-full border rounded p-2",
            }
        ),
        error_messages={"required": "Please provide a reason."},
    )

    class Meta:
        model = WeeklyPayment
        fields = ["reject_reason"]


class PaymentMarkPaidForm(forms.ModelForm):
    """
    Respaldo si quisieras subir el comprobante vía Django (multipart).
    En el flujo optimizado usaremos presigned POST directo a Wasabi.
    """
    class Meta:
        model = WeeklyPayment
        fields = ["receipt"]
        labels = {"receipt": "Payment receipt (required)"}
        widgets = {
            "receipt": forms.ClearableFileInput(
                attrs={"accept": ".pdf,.jpg,.jpeg,.png", "class": "w-full"}
            )
        }

    def clean_receipt(self):
        f = self.cleaned_data.get("receipt")
        if not f:
            raise forms.ValidationError("Receipt file is required.")
        return f
