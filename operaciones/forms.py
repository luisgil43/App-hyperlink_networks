# forms.py

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import ModelMultipleChoiceField
# ✅ NUEVO
from django.utils import timezone

from facturacion.models import CartolaMovimiento, TipoGasto
from usuarios.models import CustomUser, Rol

from .models import PrecioActividadTecnico, SesionBilling, WeeklyPayment

# ✅ safe imports (fleet)
try:
    from fleet.models import VehicleOdometerEvent  # ✅ IMPORTANTE
    from fleet.models import Vehicle, VehicleService, VehicleServiceType
except Exception:  # pragma: no cover
    Vehicle = None
    VehicleServiceType = None
    VehicleService = None
    VehicleOdometerEvent = None


def _normalizar_odometro(val):
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace(",", "").replace(" ", "")
    try:
        n = int(float(s))
    except Exception:
        return None
    return n if n >= 0 else None


def _is_admin_or_fleet(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    try:
        if getattr(user, "es_admin_general", False):
            return True
    except Exception:
        pass

    try:
        if getattr(user, "es_flota", False):
            return True
    except Exception:
        pass

    try:
        if (
            hasattr(user, "roles")
            and user.roles.filter(nombre__in=["admin", "flota"]).exists()
        ):
            return True
    except Exception:
        pass

    return False


def _validar_service_no_futuro(service_date, service_time):
    if not service_date:
        return True, None

    now_local = timezone.localtime(timezone.now())
    hoy = now_local.date()

    if service_date > hoy:
        return False, "You cannot register a Service with a future date."

    if service_time and service_date == hoy:
        try:
            if service_time > now_local.time().replace(second=0, microsecond=0):
                return False, "You cannot register a Service with a future time."
        except Exception:
            pass

    return True, None


from django.db.models import Q


def _fmt_date(d):
    return d.strftime("%d/%m/%Y") if d else "—"


def _fmt_time(t):
    return t.strftime("%H:%M").lstrip("0") if t else "—"


def _validar_odometro_vecinos(
    vehicle_id, service_date, service_time, odo_nuevo, exclude_service_id=None
):
    """
    Vecinos en VehicleService (fleet):
    - anterior: odo_nuevo >= odo_anterior
    - posterior: odo_nuevo <= odo_posterior
    """
    if VehicleService is None:
        return True, None

    if (
        (not vehicle_id)
        or (service_date is None)
        or (service_time is None)
        or (odo_nuevo is None)
    ):
        return True, None

    qs = (
        VehicleService.objects.filter(vehicle_id=vehicle_id)
        .exclude(kilometraje_declarado__isnull=True)
        .exclude(service_date__isnull=True)
        .exclude(service_time__isnull=True)
    )

    if exclude_service_id:
        qs = qs.exclude(pk=exclude_service_id)

    anterior = (
        qs.filter(
            Q(service_date__lt=service_date)
            | Q(service_date=service_date, service_time__lt=service_time)
        )
        .order_by("-service_date", "-service_time", "-id")
        .first()
    )

    if anterior and anterior.kilometraje_declarado is not None:
        km_anterior = int(anterior.kilometraje_declarado)
        if odo_nuevo < km_anterior:
            return False, (
                f"The odometer ({odo_nuevo} miles) cannot be lower than the previous "
                f"record ({km_anterior} miles) on {_fmt_date(anterior.service_date)} "
                f"at {_fmt_time(anterior.service_time)}."
            )

    posterior = (
        qs.filter(
            Q(service_date__gt=service_date)
            | Q(service_date=service_date, service_time__gt=service_time)
        )
        .order_by("service_date", "service_time", "id")
        .first()
    )

    if posterior and posterior.kilometraje_declarado is not None:
        km_posterior = int(posterior.kilometraje_declarado)
        if odo_nuevo > km_posterior:
            return False, (
                f"The odometer ({odo_nuevo} miles) cannot be greater than a later "
                f"record ({km_posterior} miles) on {_fmt_date(posterior.service_date)} "
                f"at {_fmt_time(posterior.service_time)}."
            )

    return True, None


class MovimientoUsuarioForm(forms.ModelForm):
    cargos = forms.CharField(
        widget=forms.TextInput(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
        label="Amount (USD)",
        required=True,
    )

    real_consumption_date = forms.DateField(
        required=False,
        label="Real consumption date",
        widget=forms.DateInput(
            attrs={"class": "w-full border rounded-xl px-3 py-2", "autocomplete": "off"}
        ),
    )

    service_type_obj = forms.ModelChoiceField(
        required=False,
        label="Service",
        queryset=(
            VehicleServiceType.objects.filter(is_active=True).order_by("name")
            if VehicleServiceType
            else forms.models.ModelChoiceField(queryset=None).queryset
        ),
        widget=forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
    )

    vehicle = forms.ModelChoiceField(
        required=False,
        label="Vehicle assigned",
        queryset=(
            Vehicle.objects.all().order_by("patente")
            if Vehicle
            else forms.models.ModelChoiceField(queryset=None).queryset
        ),
        widget=forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
    )

    service_time = forms.TimeField(
        required=False,
        label="Service time",
        widget=forms.TextInput(
            attrs={
                "class": "w-full border rounded-xl px-3 py-2",
                "autocomplete": "off",
                "placeholder": "HH:MM (24h)",
            }
        ),
        input_formats=["%H:%M", "%H:%M:%S"],
    )

    comprobante = forms.FileField(required=False, label="Receipt")

    comprobante_foto = forms.ImageField(
        required=False,
        label="Receipt (photo)",
        widget=forms.ClearableFileInput(
            attrs={"class": "w-full border rounded-xl px-3 py-2", "accept": "image/*"}
        ),
    )
    comprobante_archivo = forms.FileField(
        required=False,
        label="Receipt (file)",
        widget=forms.ClearableFileInput(
            attrs={
                "class": "w-full border rounded-xl px-3 py-2",
                "accept": "application/pdf,image/*",
            }
        ),
    )

    kilometraje = forms.IntegerField(
        required=False,
        label="Odometer (miles)",
        widget=forms.NumberInput(
            attrs={
                "class": "w-full border rounded-xl px-3 py-2",
                "min": "0",
                "placeholder": "e.g. 123456",
            }
        ),
    )

    foto_tablero = forms.ImageField(
        required=False,
        label="Odometer photo (dashboard)",
        widget=forms.ClearableFileInput(
            attrs={"class": "w-full border rounded-xl px-3 py-2", "accept": "image/*"}
        ),
    )

    class Meta:
        model = CartolaMovimiento
        fields = [
            "proyecto",
            "tipo",
            "service_type_obj",
            "vehicle",
            "service_date",
            "service_time",
            "real_consumption_date",
            "cargos",
            "observaciones",
            "comprobante",
            "kilometraje",
            "foto_tablero",
        ]
        widgets = {
            "proyecto": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "tipo": forms.Select(attrs={"class": "w-full border rounded-xl px-3 py-2"}),
            "service_type_obj": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "observaciones": forms.Textarea(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "rows": 1}
            ),
            "comprobante": forms.ClearableFileInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        is_edit = bool(getattr(self.instance, "pk", None))

        # ✅ FIX: mostrar solo tipos activos en rendiciones
        if "tipo" in self.fields:
            qs = TipoGasto.objects.filter(is_active=True).order_by("nombre")

            # si estoy editando y el movimiento tiene un tipo inactivo,
            # lo agrego igual para que el form no falle y se pueda ver/guardar
            try:
                current_tipo_id = getattr(self.instance, "tipo_id", None)
                if current_tipo_id:
                    current_tipo = TipoGasto.objects.filter(pk=current_tipo_id).first()
                    if current_tipo and not getattr(current_tipo, "is_active", True):
                        qs = (
                            TipoGasto.objects.filter(pk=current_tipo_id)
                            .union(qs)
                            .order_by("nombre")
                        )
            except Exception:
                pass

            self.fields["tipo"].queryset = qs

        if not is_edit and not self.initial.get("real_consumption_date"):
            self.initial["real_consumption_date"] = timezone.localdate()

        if "real_consumption_date" in self.fields:
            if not is_edit:
                self.fields["real_consumption_date"].required = True
                self.fields["real_consumption_date"].widget.attrs[
                    "required"
                ] = "required"
            else:
                self.fields["real_consumption_date"].required = False
                self.fields["real_consumption_date"].widget.attrs.pop("required", None)

        for name in (
            "comprobante",
            "comprobante_foto",
            "comprobante_archivo",
            "foto_tablero",
        ):
            if name in self.fields:
                self.fields[name].required = False

        if Vehicle and "vehicle" in self.fields:
            user = self.request_user
            qs = Vehicle.objects.all().order_by("patente")

            if not _is_admin_or_fleet(user):
                try:
                    qs = (
                        qs.filter(assignments__user=user, assignments__is_active=True)
                        .distinct()
                        .order_by("patente")
                    )
                except Exception:
                    qs = qs.none()

            self.fields["vehicle"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        is_edit = bool(getattr(self.instance, "pk", None))

        if not is_edit and not cleaned.get("real_consumption_date"):
            self.add_error("real_consumption_date", "This field is required.")

        wasabi_key = (self.data.get("wasabi_key") or "").strip()
        wasabi_key_odo = (self.data.get("wasabi_key_foto_tablero") or "").strip()

        if not cleaned.get("comprobante"):
            comp_foto = self.files.get("comprobante_foto")
            comp_arch = self.files.get("comprobante_archivo")
            comp_main = self.files.get("comprobante")
            if comp_foto:
                cleaned["comprobante"] = comp_foto
                self.instance.comprobante = comp_foto
            elif comp_arch:
                cleaned["comprobante"] = comp_arch
                self.instance.comprobante = comp_arch
            elif comp_main:
                cleaned["comprobante"] = comp_main
                self.instance.comprobante = comp_main

        tipo = cleaned.get("tipo")
        tipo_nombre = (getattr(tipo, "nombre", "") or str(tipo or "")).strip().lower()
        es_service = tipo_nombre.startswith("service")

        st = cleaned.get("service_type_obj")
        st_name = (getattr(st, "name", "") or "").strip().lower() if st else ""
        es_fuel = es_service and (st_name == "fuel")

        if es_service and not st:
            self.add_error("service_type_obj", "Please select a service.")

        if es_service:
            v = cleaned.get("vehicle")
            t = cleaned.get("service_time")
            d = cleaned.get("real_consumption_date")
            odo_nuevo = _normalizar_odometro(cleaned.get("kilometraje"))

            if not v:
                self.add_error("vehicle", "Please select a vehicle.")
            if not t:
                self.add_error("service_time", "Service time is required for Service.")
            if odo_nuevo is None:
                self.add_error(
                    "kilometraje", "Odometer (miles) is required for Service."
                )

            if (
                v
                and Vehicle
                and "vehicle" in self.fields
                and not _is_admin_or_fleet(self.request_user)
            ):
                allowed_ids = set(
                    self.fields["vehicle"].queryset.values_list("id", flat=True)
                )
                if v.id not in allowed_ids:
                    self.add_error("vehicle", "You are not assigned to that vehicle.")
                    v = None

            if d and t:
                ok_nf, msg_nf = _validar_service_no_futuro(d, t)
                if not ok_nf:
                    if "time" in (msg_nf or "").lower():
                        self.add_error("service_time", msg_nf)
                    else:
                        self.add_error("real_consumption_date", msg_nf)

            if v and d and t and odo_nuevo is not None:
                ok_km, msg_km = _validar_odometro_vecinos(
                    vehicle_id=v.id,
                    service_date=d,
                    service_time=t,
                    odo_nuevo=odo_nuevo,
                    exclude_service_id=None,
                )
                if not ok_km:
                    self.add_error("kilometraje", msg_km)

            cleaned["service_date"] = d
            cleaned["service_time"] = t

        if es_service:
            has_dashboard = bool(
                wasabi_key_odo
                or self.files.get("foto_tablero")
                or cleaned.get("foto_tablero")
                or getattr(self.instance, "foto_tablero", None)
            )
            if not has_dashboard:
                self.add_error(
                    "foto_tablero",
                    "You must attach a dashboard (odometer) photo for Service.",
                )

        if es_fuel:
            has_receipt = bool(
                wasabi_key
                or cleaned.get("comprobante")
                or getattr(self.instance, "comprobante", None)
            )
            if not has_receipt:
                self.add_error(
                    "comprobante",
                    "You must attach a receipt (photo or file) for Fuel.",
                )

        if wasabi_key:
            setattr(self.instance, "_skip_receipt_required", True)
            setattr(self.instance, "_wasabi_key_receipt", wasabi_key)

        if wasabi_key_odo:
            setattr(self.instance, "_skip_odo_required", True)
            setattr(self.instance, "_wasabi_key_foto_tablero", wasabi_key_odo)

        if es_service:
            odo_nuevo = _normalizar_odometro(cleaned.get("kilometraje"))
            if odo_nuevo is not None:
                cleaned["kilometraje"] = odo_nuevo

        return cleaned

    def clean_cargos(self):
        value = (self.cleaned_data.get("cargos") or "").strip()
        if "," in value:
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(" ", "")
        try:
            return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise ValidationError("Enter a valid amount (e.g., 30.50 or 30,50).")

    def save(self, commit=True):
        """
        Guarda CartolaMovimiento y crea/actualiza Fleet.VehicleOdometerEvent
        para que SIEMPRE quede el PROJECT (sin foto en Fleet).
        """
        instance: CartolaMovimiento = super().save(commit=False)

        tipo = self.cleaned_data.get("tipo")
        tipo_nombre = (getattr(tipo, "nombre", "") or str(tipo or "")).strip().lower()
        es_service = tipo_nombre.startswith("service")

        st = self.cleaned_data.get("service_type_obj")
        st_name = (getattr(st, "name", "") or "").strip().lower() if st else ""
        es_fuel = es_service and (st_name == "fuel")

        if es_service:
            instance.vehicle = self.cleaned_data.get("vehicle")
            instance.service_date = self.cleaned_data.get(
                "service_date"
            ) or self.cleaned_data.get("real_consumption_date")
            instance.service_time = self.cleaned_data.get("service_time")
        else:
            instance.vehicle = None
            instance.service_date = None
            instance.service_time = None

        if not commit:
            return instance

        instance.save()
        self.save_m2m()

        # Fleet log solo si es Service y hay vehículo+odómetro
        try:
            if not (
                es_service
                and Vehicle
                and VehicleOdometerEvent
                and getattr(instance, "vehicle_id", None)
            ):
                return instance

            odo = _normalizar_odometro(getattr(instance, "kilometraje", None))
            if odo is None:
                return instance

            if instance.service_date and instance.service_time:
                try:
                    dt_naive = timezone.datetime.combine(
                        instance.service_date, instance.service_time
                    )
                    event_at = timezone.make_aware(
                        dt_naive, timezone.get_current_timezone()
                    )
                except Exception:
                    event_at = timezone.now()
            else:
                event_at = timezone.now()

            marker = f"CartolaMovimiento#{instance.pk}"
            notes_txt = (instance.observaciones or "").strip()
            notes_txt = f"{notes_txt} [{marker}]" if notes_txt else f"[{marker}]"

            source = "fuel" if es_fuel else "service"

            # ✅ usar lo que quedó guardado realmente
            project_obj = getattr(instance, "proyecto", None)

            existing = (
                VehicleOdometerEvent.objects.filter(
                    vehicle_id=instance.vehicle_id,
                    source=source,
                    notes__icontains=marker,
                )
                .order_by("-event_at", "-id")
                .first()
            )

            if existing:
                changed = []

                if (
                    getattr(existing, "project_id", None) is None
                    and project_obj is not None
                ):
                    existing.project = project_obj
                    changed.append("project")

                if getattr(existing, "event_at", None) != event_at:
                    existing.event_at = event_at
                    changed.append("event_at")

                if int(getattr(existing, "odometer", 0) or 0) != int(odo):
                    existing.odometer = int(odo)
                    changed.append("odometer")

                if changed:
                    existing.save(update_fields=list(dict.fromkeys(changed)))

            else:
                current = int(instance.vehicle.kilometraje_actual or 0)

                VehicleOdometerEvent.objects.create(
                    vehicle=instance.vehicle,
                    event_at=event_at,
                    project=project_obj if project_obj else None,
                    notes=notes_txt,
                    odometer=int(odo),
                    prev_odometer=current,
                    source=source,
                )

                instance.vehicle.kilometraje_actual = int(odo)
                instance.vehicle.last_movement_at = event_at
                instance.vehicle.save(
                    update_fields=["kilometraje_actual", "last_movement_at"]
                )

        except Exception:
            pass

        return instance


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


from django import forms
from django.core.exceptions import ValidationError

from .models import PrecioActividadTecnico


class PrecioActividadTecnicoForm(forms.ModelForm):
    """
    Formulario para crear o editar precios por técnico.
    Regla:
    Para un mismo:
      Technician + City + Project + Office + Client + Work Type
    debe existir un único payment_weeks.
    """

    class Meta:
        model = PrecioActividadTecnico
        fields = [
            "tecnico",
            "ciudad",
            "proyecto",
            "oficina",
            "cliente",
            "tipo_trabajo",
            "codigo_trabajo",
            "descripcion",
            "unidad_medida",
            "payment_weeks",
            "precio_tecnico",
            "precio_empresa",
        ]
        labels = {
            "tecnico": "Technician",
            "ciudad": "City",
            "proyecto": "Project",
            "oficina": "Office",
            "cliente": "Client",
            "tipo_trabajo": "Work Type",
            "codigo_trabajo": "Job Code",
            "descripcion": "Description",
            "unidad_medida": "UOM",
            "payment_weeks": "Payment Weeks",
            "precio_tecnico": "Tech Price (USD)",
            "precio_empresa": "Company Price (USD)",
        }
        widgets = {
            "tecnico": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "ciudad": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "proyecto": forms.Select(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "oficina": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "cliente": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "tipo_trabajo": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "codigo_trabajo": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "descripcion": forms.Textarea(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "rows": 3}
            ),
            "unidad_medida": forms.TextInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2"}
            ),
            "payment_weeks": forms.NumberInput(
                attrs={
                    "class": "w-full border rounded-xl px-3 py-2",
                    "min": "0",
                    "step": "1",
                }
            ),
            "precio_tecnico": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "step": "0.01"}
            ),
            "precio_empresa": forms.NumberInput(
                attrs={"class": "w-full border rounded-xl px-3 py-2", "step": "0.01"}
            ),
        }

    def clean_precio_tecnico(self):
        precio = self.cleaned_data.get("precio_tecnico")
        if precio is None:
            return precio
        if precio < 0:
            raise ValidationError("Technician price cannot be negative.")
        return precio

    def clean_precio_empresa(self):
        precio = self.cleaned_data.get("precio_empresa")
        if precio is None:
            return precio
        if precio < 0:
            raise ValidationError("Company price cannot be negative.")
        return precio

    def clean_payment_weeks(self):
        value = self.cleaned_data.get("payment_weeks")
        if value in (None, ""):
            return 0
        if value < 0:
            raise ValidationError("Payment Weeks cannot be negative.")
        return int(value)

    def clean_codigo_trabajo(self):
        codigo_trabajo = (self.cleaned_data.get("codigo_trabajo") or "").strip()
        if not codigo_trabajo:
            raise ValidationError("Job code cannot be empty.")
        return codigo_trabajo

    def clean_ciudad(self):
        ciudad = (self.cleaned_data.get("ciudad") or "").strip()
        if not ciudad:
            raise ValidationError("City cannot be empty.")
        return ciudad

    def clean_proyecto(self):
        proyecto = self.cleaned_data.get("proyecto")
        if not proyecto:
            raise ValidationError("Project cannot be empty.")
        return proyecto

    def clean_oficina(self):
        return (self.cleaned_data.get("oficina") or "").strip()

    def clean_cliente(self):
        return (self.cleaned_data.get("cliente") or "").strip()

    def clean_tipo_trabajo(self):
        tipo_trabajo = (self.cleaned_data.get("tipo_trabajo") or "").strip()
        if not tipo_trabajo:
            raise ValidationError("Work Type cannot be empty.")
        return tipo_trabajo

    def clean(self):
        cleaned = super().clean()

        tecnico = cleaned.get("tecnico")
        ciudad = (cleaned.get("ciudad") or "").strip()
        proyecto = cleaned.get("proyecto")
        oficina = (cleaned.get("oficina") or "").strip()
        cliente = (cleaned.get("cliente") or "").strip()
        tipo_trabajo = (cleaned.get("tipo_trabajo") or "").strip()
        payment_weeks = cleaned.get("payment_weeks")

        # Si ya hay errores base, no seguir metiendo ruido
        if self.errors:
            return cleaned

        # Validaciones mínimas
        if not tecnico:
            self.add_error("tecnico", "Technician is required.")
        if not ciudad:
            self.add_error("ciudad", "City cannot be empty.")
        if not proyecto:
            self.add_error("proyecto", "Project is required.")
        if not cliente:
            self.add_error("cliente", "Client cannot be empty.")
        if not tipo_trabajo:
            self.add_error("tipo_trabajo", "Work Type cannot be empty.")

        if self.errors:
            return cleaned

        if payment_weeks is None:
            payment_weeks = 0

        qs = PrecioActividadTecnico.objects.filter(
            tecnico=tecnico,
            ciudad=ciudad,
            proyecto=proyecto,
            oficina=oficina,
            cliente=cliente,
            tipo_trabajo=tipo_trabajo,
        )

        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        existing_weeks = set(
            int(x or 0) for x in qs.values_list("payment_weeks", flat=True).distinct()
        )

        if existing_weeks and existing_weeks != {int(payment_weeks)}:
            existing_list = ", ".join(str(x) for x in sorted(existing_weeks))
            msg = (
                "Payment Weeks must be the same for this combination of "
                "Technician / City / Project / Office / Client / Work Type. "
                f"Existing value(s): {existing_list}. Attempted value: {payment_weeks}."
            )

            self.add_error("payment_weeks", msg)

        return cleaned


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
