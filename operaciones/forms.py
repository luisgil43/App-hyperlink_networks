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


class MovimientoUsuarioForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        label="Amount (USD)",
        required=True,
    )

    # Main file field (optional; may be resolved via presign in the view)
    comprobante = forms.FileField(required=False, label="Receipt")

    # Alternatives if you don't use presign
    comprobante_foto = forms.ImageField(
        required=False, label="Receipt (photo)",
        widget=forms.ClearableFileInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2',
                   'accept': 'image/*'}
        ),
    )
    comprobante_archivo = forms.FileField(
        required=False, label="Receipt (file)",
        widget=forms.ClearableFileInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2',
                   'accept': 'application/pdf,image/*'}
        ),
    )

    # Fuel-only fields
    kilometraje = forms.IntegerField(
        required=False, label="Odometer (km)",
        widget=forms.NumberInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2',
                   'min': '0', 'placeholder': 'e.g. 123456'}
        ),
    )
    foto_tablero = forms.ImageField(
        required=False, label="Odometer photo (dashboard)",
        widget=forms.ClearableFileInput(
            attrs={'class': 'w-full border rounded-xl px-3 py-2',
                   'accept': 'image/*'}
        ),
    )

    class Meta:
        model = CartolaMovimiento
        fields = [
            'proyecto', 'tipo', 'cargos', 'observaciones',
            'comprobante', 'kilometraje', 'foto_tablero'
        ]
        labels = {
            'proyecto': 'Project',
            'tipo': 'Type',
            'cargos': 'Amount (USD)',
            'observaciones': 'Remarks',
            'comprobante': 'Receipt',
            'kilometraje': 'Odometer (km)',
            'foto_tablero': 'Odometer photo (dashboard)',
        }
        widgets = {
            'proyecto': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'tipo': forms.Select(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
            'observaciones': forms.Textarea(attrs={'class': 'w-full border rounded-xl px-3 py-2', 'rows': 1}),
            'comprobante': forms.ClearableFileInput(attrs={'class': 'w-full border rounded-xl px-3 py-2'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep file inputs optional; we validate explicitly
        for name in ('comprobante', 'comprobante_foto', 'comprobante_archivo', 'foto_tablero'):
            if name in self.fields:
                self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()

        # ====== Direct-upload keys (presigned) from hidden inputs ======
        wasabi_key = (self.data.get('wasabi_key') or '').strip()
        wasabi_key_odo = (self.data.get(
            'wasabi_key_foto_tablero') or '').strip()

        # ====== Resolve receipt from classic multipart if present ======
        if not cleaned.get('comprobante'):
            comp_foto = self.files.get('comprobante_foto')
            comp_arch = self.files.get('comprobante_archivo')
            comp_main = self.files.get('comprobante')
            if comp_foto:
                cleaned['comprobante'] = comp_foto
                self.instance.comprobante = comp_foto
            elif comp_arch:
                cleaned['comprobante'] = comp_arch
                self.instance.comprobante = comp_arch
            elif comp_main:
                cleaned['comprobante'] = comp_main
                self.instance.comprobante = comp_main
            # If using presign, the view will set field.name = wasabi_key

        # ====== Fuel rules ======
        tipo = cleaned.get('tipo')
        tipo_nombre = (getattr(tipo, 'nombre', '')
                       or str(tipo or '')).strip().lower()
        es_fuel = (tipo_nombre == 'fuel')

        if es_fuel:
            # Odometer km required
            if not cleaned.get('kilometraje'):
                self.add_error(
                    'kilometraje', "Odometer (km) is required for Fuel.")

            # Receipt required (accept presigned key or uploaded file)
            has_receipt = bool(
                wasabi_key or
                cleaned.get('comprobante') or
                getattr(self.instance, 'comprobante', None)
            )
            if not has_receipt:
                self.add_error(
                    'comprobante', "You must attach a receipt (photo or file) for Fuel.")

            # Dashboard photo required (accept presigned key or uploaded file)
            has_dashboard = bool(
                wasabi_key_odo or
                self.files.get('foto_tablero') or
                cleaned.get('foto_tablero') or
                getattr(self.instance, 'foto_tablero', None)
            )
            if not has_dashboard:
                self.add_error(
                    'foto_tablero', "You must attach a dashboard (odometer) photo for Fuel.")

        # 👉 Flags for the model clean() so it doesn't block when presigned keys exist
        if wasabi_key:
            setattr(self.instance, "_skip_receipt_required", True)
        if wasabi_key_odo:
            setattr(self.instance, "_skip_odo_required", True)

        return cleaned

    def clean_cargos(self):
        value = (self.cleaned_data.get('cargos') or '').strip()
        if "," in value:
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(" ", "")
        try:
            return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValidationError(
                "Enter a valid amount (e.g., 30.50 or 30,50).")


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
