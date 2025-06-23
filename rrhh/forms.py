from rrhh.models import CronogramaPago
from .models import CronogramaPago
from .models import DocumentoTrabajador
from django import forms
from .models import ContratoTrabajo
from .models import FichaIngreso
from .models import SolicitudVacaciones
from datetime import timedelta
import holidays
from .models import Feriado
from .models import TipoDocumento
from datetime import date
from django.core.exceptions import ValidationError


class ContratoTrabajoForm(forms.ModelForm):
    reemplazar_archivo = forms.BooleanField(
        required=False,
        label='Reemplazar archivo existente',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox rounded text-emerald-600'
        })
    )

    class Meta:
        model = ContratoTrabajo
        fields = ['tecnico', 'fecha_inicio', 'fecha_termino', 'archivo']
        widgets = {
            'tecnico': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
            'fecha_inicio': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
            'fecha_termino': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
            'archivo': forms.ClearableFileInput(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 bg-white shadow-sm focus:ring-2 focus:ring-emerald-500'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha_termino'].required = False

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            if not archivo.name.lower().endswith('.pdf'):
                raise forms.ValidationError(
                    "Solo se permiten archivos en formato PDF.")
            if archivo.content_type != 'application/pdf':
                raise forms.ValidationError(
                    "El archivo debe ser un PDF válido.")
        return archivo


class FichaIngresoForm(forms.ModelForm):
    class Meta:
        model = FichaIngreso
        exclude = ['creado_por', 'usuario', 'pm', 'archivo',
                   'firma_trabajador', 'firma_pm', 'firma_rrhh', 'estado']  # 👈 Agregado aquí

        widgets = {
            'fecha_nacimiento': forms.DateInput(attrs={'type': 'date'}),
            'fecha_inicio': forms.DateInput(attrs={'type': 'date'}),
        }

        labels = {
            'rut': 'RUT',
            'afp': 'AFP',
            'salud': 'Salud',
            'faena': 'Faena o Proyecto',
            'telefono_emergencia': 'Teléfono Emergencia',
            'numero_cuenta': 'Número de Cuenta',
            'numero_cuenta_2': 'Número de Cuenta (2)',
            'banco_2': 'Banco (2)',
            'tipo_cuenta_2': 'Tipo de Cuenta (2)',
            'sueldo_liquido': 'Sueldo Líquido',
            'tipo_contrato': 'Tipo de Contrato',
            'horario_trabajo': 'Horario de Trabajo',
            'nivel_estudios': 'Nivel de Estudios',
            'profesion_u_oficio': 'Profesión u Oficio',
            'talla_polera': 'Talla Polera',
            'talla_pantalon': 'Talla Pantalón',
            'talla_zapato': 'Talla Zapato',
        }


class SolicitudVacacionesForm(forms.ModelForm):
    class Meta:
        model = SolicitudVacaciones
        fields = ['fecha_inicio', 'fecha_fin']
        widgets = {
            'fecha_inicio': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-input'  # usa tu clase Tailwind o Bootstrap si quieres
            }),
            'fecha_fin': forms.DateInput(attrs={
                'type': 'date',
                'class': 'form-input'
            }),
        }

    def __init__(self, *args, **kwargs):
        self.usuario = kwargs.pop('usuario', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        inicio = cleaned_data.get("fecha_inicio")
        fin = cleaned_data.get("fecha_fin")

        if inicio and fin and inicio > fin:
            raise forms.ValidationError(
                "La fecha de inicio no puede ser posterior a la fecha de término."
            )

        if self.usuario and inicio and fin:
            dias_solicitados = self.usuario.calcular_dias_habiles(inicio, fin)
            dias_disponibles = self.usuario.obtener_dias_vacaciones_disponibles()

            if dias_solicitados > dias_disponibles:
                raise forms.ValidationError(
                    f"No puedes solicitar más días de los disponibles. Disponibles: {dias_disponibles:.2f}, solicitados: {dias_solicitados}"
                )

            # ✅ Añadimos esto para que la vista lo pueda usar
            cleaned_data['dias_solicitados'] = dias_solicitados

        return cleaned_data


class RevisionVacacionesForm(forms.Form):
    observacion = forms.CharField(
        label="Observación (opcional)",
        widget=forms.Textarea(
            attrs={'rows': 3, 'class': 'form-textarea w-full'}),
        required=False
    )
    accion = forms.ChoiceField(
        choices=[('aprobar', 'Aprobar'), ('rechazar', 'Rechazar')],
        widget=forms.HiddenInput()
    )


class FeriadoForm(forms.ModelForm):
    class Meta:
        model = Feriado
        fields = ['nombre', 'fecha']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full rounded-xl border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400'
            }),
            'fecha': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full rounded-xl border border-gray-300 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400'
            }),
        }


class DocumentoTrabajadorForm(forms.ModelForm):
    class Meta:
        model = DocumentoTrabajador
        fields = ['trabajador', 'tipo_documento',
                  'fecha_emision', 'fecha_vencimiento', 'archivo']
        widgets = {
            'fecha_emision': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'fecha_vencimiento': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'trabajador': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'tipo_documento': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'archivo': forms.ClearableFileInput(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 bg-white shadow-sm focus:ring-2 focus:ring-blue-500',
                'accept': 'application/pdf'
            }),
        }

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')

        if not archivo:
            raise forms.ValidationError("Debes adjuntar un archivo PDF.")

        if not archivo.name.lower().endswith('.pdf'):
            raise forms.ValidationError(
                "Solo se permiten archivos en formato PDF.")

        if archivo.content_type != 'application/pdf':
            raise forms.ValidationError("El archivo debe ser un PDF válido.")

        return archivo

    def clean(self):
        cleaned_data = super().clean()
        fecha_emision = cleaned_data.get("fecha_emision")
        fecha_vencimiento = cleaned_data.get("fecha_vencimiento")
        trabajador = cleaned_data.get("trabajador")
        tipo_documento = cleaned_data.get("tipo_documento")
        hoy = date.today()

        if fecha_emision and fecha_emision > hoy:
            self.add_error("fecha_emision",
                           "La fecha de emisión no puede ser en el futuro.")

        if fecha_vencimiento and fecha_vencimiento < hoy:
            self.add_error("fecha_vencimiento",
                           "La fecha de vencimiento no puede ser en el pasado.")

        if fecha_emision and fecha_vencimiento and fecha_vencimiento < fecha_emision:
            self.add_error(
                "fecha_vencimiento", "La fecha de vencimiento no puede ser anterior a la fecha de emisión.")

        # Validación de documento duplicado
        if trabajador and tipo_documento:
            qs = DocumentoTrabajador.objects.filter(
                trabajador=trabajador,
                tipo_documento=tipo_documento
            )
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)

            if qs.exists():
                raise forms.ValidationError(
                    "Este trabajador ya tiene un documento de este tipo registrado.")


class TipoDocumentoForm(forms.ModelForm):
    class Meta:
        model = TipoDocumento
        fields = ['nombre']
        widgets = {
            'nombre': forms.TextInput(attrs={
                'class': 'w-full px-4 py-2 border rounded-xl',
                'placeholder': 'Ej. Carnet de identidad'
            }),
        }
        labels = {
            'nombre': 'Nombre del Tipo de Documento'
        }


class ReemplazoDocumentoForm(forms.Form):
    archivo = forms.FileField(label="Nuevo archivo PDF", required=True)
    fecha_emision = forms.DateField(
        label="Fecha de emisión",
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    fecha_vencimiento = forms.DateField(
        label="Fecha de expiración",
        required=True,
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        if not archivo.name.endswith('.pdf'):
            raise forms.ValidationError(
                "El archivo debe estar en formato PDF.")
        return archivo

    def clean(self):
        cleaned_data = super().clean()
        fecha_emision = cleaned_data.get("fecha_emision")
        fecha_vencimiento = cleaned_data.get("fecha_vencimiento")

        if fecha_emision and fecha_vencimiento:
            if fecha_vencimiento <= fecha_emision:
                raise forms.ValidationError(
                    "La fecha de expiración debe ser posterior a la fecha de emisión.")
            if fecha_emision > date.today():
                raise forms.ValidationError(
                    "La fecha de emisión no puede ser en el futuro.")


class FirmaForm(forms.Form):
    firma = forms.ImageField(
        label="Firma Digital (PNG)",
        required=True,
        widget=forms.ClearableFileInput(attrs={
            'accept': 'image/png',
            'class': 'w-full border border-gray-300 rounded p-2 text-sm'
        })
    )

    def clean_firma(self):
        firma = self.cleaned_data['firma']
        if not firma.name.endswith('.png'):
            raise forms.ValidationError("La firma debe estar en formato PNG.")
        return firma


class CronogramaPagoForm(forms.ModelForm):
    class Meta:
        model = CronogramaPago
        exclude = ['actualizado']
        widgets = {
            field.name: forms.DateInput(attrs={'type': 'date'})
            for field in CronogramaPago._meta.fields
            if 'fecha' in field.name
        }
