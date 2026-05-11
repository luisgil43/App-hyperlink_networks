from django import forms


class BillingMasivoUploadForm(forms.Form):
    archivo = forms.FileField(
        label="Bulk billing Excel file",
        required=True,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "w-full border rounded-xl px-3 py-2",
                "accept": ".xlsx",
            }
        ),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data.get("archivo")

        if not archivo:
            raise forms.ValidationError("Please upload an Excel file.")

        name = (archivo.name or "").lower().strip()

        if not name.endswith(".xlsx"):
            raise forms.ValidationError("The file must be an Excel .xlsx file.")

        return archivo
