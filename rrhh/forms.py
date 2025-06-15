from django import forms
from .models import ContratoTrabajo
from .models import FichaIngreso
from .models import SolicitudVacaciones
from datetime import timedelta
import holidays


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
            'fecha_inicio': forms.DateInput(attrs={'type': 'date', 'class': 'input input-bordered w-full'}),
            'fecha_fin': forms.DateInput(attrs={'type': 'date', 'class': 'input input-bordered w-full'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        fecha_inicio = cleaned_data.get('fecha_inicio')
        fecha_fin = cleaned_data.get('fecha_fin')

        if fecha_inicio and fecha_fin:
            if fecha_fin < fecha_inicio:
                raise forms.ValidationError(
                    "La fecha de término no puede ser anterior a la fecha de inicio.")

            # Calcular días hábiles entre las fechas
            dias = 0
            feriados_cl = holidays.CL(years=fecha_inicio.year)

            for i in range((fecha_fin - fecha_inicio).days + 1):
                dia = fecha_inicio + timedelta(days=i)
                if dia.weekday() < 5 and dia not in feriados_cl:
                    dias += 1

            cleaned_data['dias_solicitados'] = dias

            if dias <= 0:
                raise forms.ValidationError(
                    "Debe seleccionar al menos un día hábil.")
        return cleaned_data
