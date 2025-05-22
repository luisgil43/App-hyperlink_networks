# forms.py

from django import forms


class FirmaForm(forms.Form):
    firma = forms.CharField(widget=forms.HiddenInput())
