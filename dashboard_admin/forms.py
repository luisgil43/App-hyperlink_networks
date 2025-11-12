from django import forms
from django.core.exceptions import ValidationError

from usuarios.models import CustomUser, Rol  # asegúrate de tener este modelo


class UsuarioForm(forms.ModelForm):
    # Passwords no-model, para validar lado servidor
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput,
        required=False,
        help_text="Leave blank to keep current password."
    )
    password2 = forms.CharField(
        label='Confirm Password',
        widget=forms.PasswordInput,
        required=False
    )

    # Roles (ManyToMany) requerido
    roles = forms.ModelMultipleChoiceField(
        queryset=Rol.objects.all(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        error_messages={'required': 'Please select at least one role.'}
    )

    class Meta:
        model = CustomUser
        # IMPORTANTE: usamos 'roles' (M2M). Quita cualquier 'rol' singular.
        fields = [
            'username', 'first_name', 'last_name', 'email', 'identidad',
            'is_active', 'is_staff', 'is_superuser', 'roles'
        ]
        labels = {
            'username': 'Username',
            'first_name': 'First Name',
            'last_name': 'Last Name',
            'email': 'Email',
            'identidad': 'ID / Identification Number',
            'is_active': 'Active',
            'is_staff': 'Staff',
            'is_superuser': 'Superuser',
            'roles': 'User Roles',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Fuerza required en servidor con mensajes en inglés
        for name in ['username', 'first_name', 'last_name', 'email', 'identidad', 'roles']:
            self.fields[name].required = True
            self.fields[name].error_messages['required'] = 'This field is required.'

        # HTML5 email
        self.fields['email'].widget.attrs.setdefault('type', 'email')

    def clean_identidad(self):
        ident = self.cleaned_data.get('identidad')
        if not ident:
            return ident
        qs = CustomUser.objects.filter(identidad=ident)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('ID number is already registered.')
        return ident

    def clean(self):
        cleaned = super().clean()

        # --- Passwords ---
        pwd1 = cleaned.get('password1') or ''
        pwd2 = cleaned.get('password2') or ''

        # Al crear: password obligatorio
        if self.instance.pk is None and not pwd1:
            self.add_error('password1', 'Password is required to create a user.')

        # Si alguno está, deben coincidir
        if (pwd1 or pwd2) and pwd1 != pwd2:
            self.add_error('password2', 'Passwords do not match.')

        # --- Estados: al menos uno marcado ---
        is_active = bool(cleaned.get('is_active'))
        is_staff = bool(cleaned.get('is_staff'))
        is_super = bool(cleaned.get('is_superuser'))
        if not (is_active or is_staff or is_super):
            # ponemos el error en is_active para mostrarlo en UI
            self.add_error('is_active', 'Please choose at least one status (Active, Staff, or Superuser).')

        # --- Proyectos: al menos uno ---
        # Leemos directamente de POST porque el campo no es de este ModelForm
        proj_ids = []
        if hasattr(self, 'data'):
            # En tus plantillas el name es "proyectos"
            proj_ids = self.data.getlist('proyectos')
        if not proj_ids:
            # non-field error + marca un pseudo-campo 'proyectos' para que puedas mostrarlo si lo necesitas
            self.add_error(None, ValidationError('Please select at least one project.'))

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        pwd1 = self.cleaned_data.get('password1')
        if pwd1:
            # soporta ambos nombres de método
            if hasattr(user, 'setPassword'):
                user.setPassword(pwd1)
            else:
                user.set_password(pwd1)

        if commit:
            user.save()
            # Necesario para guardar M2M (roles)
            if hasattr(self, 'save_m2m'):
                self.save_m2m()
        return user