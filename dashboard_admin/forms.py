from django import forms
from django.core.exceptions import ValidationError

from usuarios.models import CustomUser, Rol


class UsuarioForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
            }
        ),
        required=False,
        help_text="",
    )

    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
            }
        ),
        required=False,
    )

    roles = forms.ModelMultipleChoiceField(
        queryset=Rol.objects.all(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
        error_messages={
            "required": "Please select at least one role.",
        },
    )

    class Meta:
        model = CustomUser

        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "identidad",
            "is_active",
            "is_staff",
            "is_superuser",
            "roles",
        ]

        labels = {
            "username": "Username",
            "first_name": "First Name",
            "last_name": "Last Name",
            "email": "Email",
            "identidad": "ID / Identification Number",
            "is_active": "Active",
            "is_staff": "Staff",
            "is_superuser": "Superuser",
            "roles": "User Roles",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ==========================================================
        # REQUIRED BASE FIELDS
        # ==========================================================

        for name in [
            "username",
            "first_name",
            "last_name",
            "email",
            "identidad",
            "roles",
        ]:
            self.fields[name].required = True

            self.fields[name].error_messages["required"] = "This field is required."

        # ==========================================================
        # EMAIL HTML5
        # ==========================================================

        self.fields["email"].widget.attrs.setdefault(
            "type",
            "email",
        )

        # ==========================================================
        # PASSWORD RULES
        #
        # CREATE:
        # password obligatorio.
        #
        # EDIT:
        # password opcional.
        # ==========================================================

        is_create = self.instance is None or self.instance.pk is None

        self.fields["password1"].required = is_create
        self.fields["password2"].required = is_create

        if is_create:
            self.fields["password1"].help_text = ""

            self.fields["password1"].error_messages[
                "required"
            ] = "Password is required to create a user."

            self.fields["password2"].error_messages[
                "required"
            ] = "Please confirm the password."

        else:
            self.fields["password1"].help_text = "Leave blank to keep current password."

    def clean_identidad(self):
        ident = self.cleaned_data.get("identidad")

        if not ident:
            return ident

        qs = CustomUser.objects.filter(identidad=ident)

        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        if qs.exists():
            raise ValidationError("ID number is already registered.")

        return ident

    def clean(self):
        cleaned = super().clean()

        pwd1 = cleaned.get("password1") or ""

        pwd2 = cleaned.get("password2") or ""

        is_create = self.instance is None or self.instance.pk is None

        # ==========================================================
        # CREATE
        # ==========================================================

        if is_create:

            if not pwd1:
                self.add_error(
                    "password1",
                    "Password is required to create a user.",
                )

            if not pwd2:
                self.add_error(
                    "password2",
                    "Please confirm the password.",
                )

        # ==========================================================
        # PASSWORD MATCH
        #
        # En edición:
        # ambos vacíos = no cambiar password.
        #
        # Si cualquiera tiene valor:
        # ambos deben existir y coincidir.
        # ==========================================================

        if pwd1 or pwd2:

            if not pwd1:
                self.add_error(
                    "password1",
                    "Please enter the new password.",
                )

            elif not pwd2:
                self.add_error(
                    "password2",
                    "Please confirm the password.",
                )

            elif pwd1 != pwd2:
                self.add_error(
                    "password2",
                    "Passwords do not match.",
                )

        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)

        pwd1 = self.cleaned_data.get("password1")

        if pwd1:

            if hasattr(
                user,
                "setPassword",
            ):
                user.setPassword(pwd1)

            else:
                user.set_password(pwd1)

        if commit:
            user.save()

            if hasattr(
                self,
                "save_m2m",
            ):
                self.save_m2m()

        return user
