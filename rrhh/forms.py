from django import forms
from .models import ContratoTrabajo


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
