from django import forms
from .models import ContratoTrabajo
from .models import FichaIngreso
from .models import SolicitudVacaciones
from datetime import timedelta
import holidays
from .models import Feriado


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
        fields = ['tecnico', 'archivo']
        widgets = {
            'tecnico': forms.Select(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 shadow-sm focus:ring-2 focus:ring-blue-500'
            }),
            'archivo': forms.ClearableFileInput(attrs={
                'class': 'w-full border-gray-300 rounded-xl px-4 py-2 bg-white shadow-sm focus:ring-2 focus:ring-blue-500',
                'accept': 'application/pdf'
            }),
        }

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            if not archivo.name.lower().endswith('.pdf'):
                raise forms.ValidationError("Solo se permiten archivos PDF.")
            if archivo.content_type != 'application/pdf':
                raise forms.ValidationError(
                    "El archivo debe ser un PDF válido.")
        else:
            raise forms.ValidationError("Debes adjuntar un archivo PDF.")
        return archivo


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
