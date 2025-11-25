import base64
import secrets
from email.utils import formataddr
from functools import wraps

import pyotp
from axes.decorators import axes_dispatch
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives, send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.decorators import method_decorator

from hyperlink_networks.utils.email_utils import enviar_correo_manual
from usuarios.decoradores import axes_dispatch, axes_post_only, ratelimit
from usuarios.models import FirmaRepresentanteLegal  # 👈 importa el modelo

from .decoradores import axes_post_only
from .models import CustomUser, Notificacion, TrustedDevice

TRUSTED_DEVICE_COOKIE_NAME = "hl_trusted_device"
TRUSTED_DEVICE_DAYS = 30  # días que el dispositivo se considera confiable

def _get_2fa_days_left() -> int | None:
    """
    Calcula cuántos días faltan para que 2FA sea obligatorio.
    Devuelve:
      - un entero (puede ser positivo, cero o negativo)
      - o None si no se configuró TWO_FACTOR_ENFORCE_DATE.
    """
    enforce_date = getattr(settings, "TWO_FACTOR_ENFORCE_DATE", None)
    if not enforce_date:
        return None

    today = timezone.now().date()
    return (enforce_date - today).days

def _user_requires_2fa(user: CustomUser) -> bool:
    """
    Determina si el usuario debe usar 2FA al iniciar sesión.
    Por ahora:
      - Solo se exige a usuarios staff (is_staff = True)
      - Y únicamente si tienen two_factor_enabled = True.
    """
    # Si el usuario no tiene 2FA activado, no se le exige todavía
    if not getattr(user, "two_factor_enabled", False):
        return False

    # Solo personal staff (backoffice, admins, PM, finanzas, etc.)
    return bool(user.is_staff)


def _has_valid_trusted_device(request, user: CustomUser) -> bool:
    token = request.COOKIES.get(TRUSTED_DEVICE_COOKIE_NAME)
    if not token:
        return False
    try:
        device = TrustedDevice.objects.get(user=user, token=token)
    except TrustedDevice.DoesNotExist:
        return False
    if not device.is_valid():
        return False
    # actualizar last_used_at de manera perezosa
    device.last_used_at = timezone.now()
    device.save(update_fields=["last_used_at"])
    return True


def _create_trusted_device(request, user: CustomUser) -> TrustedDevice:
    """
    Crea un TrustedDevice y retorna la instancia. La cookie se setea en la vista.
    """
    token = secrets.token_urlsafe(32)
    expires_at = timezone.now() + timezone.timedelta(days=TRUSTED_DEVICE_DAYS)
    device = TrustedDevice.objects.create(
        user=user,
        token=token,
        expires_at=expires_at,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
        ip_address=(request.META.get("REMOTE_ADDR") or None),
    )
    return device


def _verify_totp_code(user: CustomUser, code: str) -> bool:
    """
    Verifica el código TOTP enviado por el usuario.
    """
    if not user.two_factor_secret:
        return False
    if not code:
        return False

    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False

    totp = pyotp.TOTP(user.two_factor_secret)
    # valid_window=1 acepta un paso hacia atrás/adelante (desfase de tiempo pequeño)
    return totp.verify(code, valid_window=1)


from django.shortcuts import redirect


def _redirect_after_login(request, user):
    """
    Redirección después de login:
      - Si el usuario solo tiene rol 'usuario' (o ningún rol) → dashboard principal.
      - Si tiene otros roles → pantalla de selección de rol.
    """
    # Obtener nombres de roles asociados al usuario (según tu modelo Rol: campos id, nombre, customuser)
    roles_nombres = []
    if hasattr(user, "roles"):
        # Usamos 'nombre' porque el modelo Rol no tiene campo 'codigo'
        roles_nombres = list(user.roles.values_list("nombre", flat=True))

    # Normalizamos a minúsculas por si en la BD están con mayúsculas
    roles_nombres = [r.lower() for r in roles_nombres]

    # Caso 1: sin roles o solo rol 'usuario' → va directo al dashboard normal
    if not roles_nombres or (len(roles_nombres) == 1 and roles_nombres[0] == "usuario"):
        return redirect("dashboard:index")

    # Caso 2: tiene más de un rol o algún rol administrativo → va a seleccionar_rol
    return redirect("usuarios:seleccionar_rol")
    

def rate_limit_handler(request, exception=None):
    # Usa tu template si quieres:
    return render(request, 'usuarios/too_many_requests.html', status=429)
    # o simple:
    # return HttpResponseTooManyRequests("Too many requests. Please try again later.")


def no_autorizado_view(request):
    return render(request, 'usuarios/no_autorizado.html', status=403)


@staff_member_required
def subir_firma_representante(request):
    if request.method == 'POST':
        data_url = request.POST.get('firma_digital')
        if not data_url or not data_url.startswith('data:image/png;base64,'):
            messages.error(request, "Firma inválida o vacía.")
            return redirect(request.path)

        try:
            formato, img_base64 = data_url.split(';base64,')
            data = base64.b64decode(img_base64)
            content = ContentFile(data)
            nombre_archivo = "firma.png"

            # Eliminar firma anterior (incluyendo el archivo en Cloudinary)
            firma_anterior = FirmaRepresentanteLegal.objects.first()
            if firma_anterior:
                if firma_anterior.archivo:
                    firma_anterior.archivo.delete(
                        save=False)  # Elimina de Cloudinary
                firma_anterior.delete()  # Elimina el registro en DB

            # Crear nueva firma
            firma = FirmaRepresentanteLegal(fecha_subida=timezone.now())
            firma.archivo.save(nombre_archivo, content, save=True)

            messages.success(
                request, "Firma del representante legal subida correctamente.")
            return redirect('liquidaciones:admin_lista')

        except Exception as e:
            messages.error(request, f"Error al guardar firma: {e}")
            return redirect(request.path)

    return render(request, 'usuarios/subir_firma_representante.html')


User = get_user_model()


@ratelimit(key='ip', rate='5/m', block=True)          # máx 5 por minuto por IP
@ratelimit(key='post:email', rate='3/h', block=True)
def recuperar_contraseña(request):
    es_admin_param = request.GET.get('admin') == 'true'

    if request.method == 'POST':
        email = request.POST.get('email')
        usuario = User.objects.filter(email=email).first()

        if usuario:
            es_admin = usuario.is_staff or usuario.is_superuser or es_admin_param
            token = get_random_string(64)
            cache.set(f"token_recuperacion_{usuario.id}", token, timeout=3600)

            reset_url = request.build_absolute_uri(
                reverse('usuarios:resetear_contraseña',
                        args=[usuario.id, token])
            ).replace("127.0.0.1:8000", "app-hyperlink-networks.onrender.com")

            # --- Asunto y contenido del correo en inglés ---
            asunto = 'Password Reset - Hyperlink Networks Platform'
            text_content = f"""
Hello {usuario.get_full_name() or usuario.username},

We received a request to reset your password on the Hyperlink Networks platform.

Click the following link to create a new password:

{reset_url}

If you didn’t request this, please ignore this message.
"""

            html_content = render_to_string('usuarios/correo_recuperacion.html', {
                'usuario': usuario,
                'reset_url': reset_url
            })

            try:
                resultado = enviar_correo_manual(
                    destinatario=email,
                    asunto=asunto,
                    cuerpo_texto=text_content,
                    cuerpo_html=html_content
                )

                if resultado:
                    # --- Mensaje mostrado al usuario en inglés ---
                    messages.success(
                        request, 'We have sent you a link to your registered email address to reset your password.')

                    return redirect(f"{reverse('usuarios:confirmacion_envio')}?es_admin={str(es_admin).lower()}")
                else:
                    messages.error(
                        request, 'We could not send the email. Please try again later.')

            except Exception as e:
                messages.error(request, f'Error al enviar correo: {str(e)}')
        else:
            messages.error(
                request, 'No user was found with that email address.')

        return redirect('usuarios:recuperar_contraseña')

    return render(request, 'usuarios/recuperar_contraseña.html')


def resetear_contraseña(request, usuario_id, token):
    usuario = User.objects.filter(id=usuario_id).first()
    token_guardado = cache.get(f"token_recuperacion_{usuario_id}")

    if not usuario or token != token_guardado:
        messages.error(
            request, "The recovery link is invalid or has expired.")
        return redirect('usuarios:recuperar_contraseña')

    if request.method == 'POST':
        nueva_contraseña = request.POST.get('nueva')
        confirmar_contraseña = request.POST.get('confirmar')

        if nueva_contraseña != confirmar_contraseña:
            messages.error(request, "Passwords do not match.")
        else:
            usuario.set_password(nueva_contraseña)
            usuario.save()
            cache.delete(f"token_recuperacion_{usuario_id}")
            messages.success(
                request, "Your password has been successfully updated.")
            return redirect('usuarios:login')

    return render(request, 'usuarios/resetear_contraseña.html', {'usuario': usuario})


def axes_post_only(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.method.upper() == 'POST':
            return axes_dispatch(view_func)(request, *args, **kwargs)
        return view_func(request, *args, **kwargs)
    return _wrapped


# throttle por IP solo en POST
# throttle por IP solo en POST
@ratelimit(key='ip', rate='10/m', block=True, method=['POST'])
@axes_post_only
def login_unificado(request):
    """
    Login centralizado:
      1) Valida usuario + password (AuthenticationForm + Axes + ratelimit).
      2) Si el usuario NO requiere 2FA → login normal + redirect según rol.
      3) Si requiere 2FA:
         - Si tiene dispositivo confiable válido → login normal.
         - Si no → guarda user_id y backend en sesión y redirige a two_factor_verify.
      4) Maneja el aviso / obligación de activar 2FA para usuarios staff según fecha límite.
    """
    if request.user.is_authenticated:
        # Usuario ya logueado: lo mandamos a donde corresponda
        return _redirect_after_login(request, request.user)

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            # ¿Este usuario requiere 2FA?
            if _user_requires_2fa(user) and not _has_valid_trusted_device(request, user):
                # Guardamos en sesión el usuario pendiente de 2FA
                request.session["pending_2fa_user_id"] = user.pk

                # Guardar también el backend con el que se autenticó
                backend_path = getattr(user, "backend", None)
                if backend_path:
                    request.session["pending_2fa_backend"] = backend_path

                # Por si quieres respetar ?next=
                next_url = request.GET.get("next") or request.POST.get("next")
                if next_url:
                    request.session["pending_2fa_next"] = next_url

                messages.info(
                    request,
                    "For security reasons, please verify your two-factor authentication code."
                )
                return redirect("usuarios:two_factor_verify")

            # Si no requiere 2FA, o el dispositivo ya es confiable → login normal
            login(request, user)

            # --- Lógica de cuenta regresiva / obligatoriedad de 2FA para staff ---
            days_left = _get_2fa_days_left()
            if user.is_staff and not getattr(user, "two_factor_enabled", False) and days_left is not None:
                if days_left > 0:
                    # Aún estamos en periodo de gracia → solo aviso
                    messages.warning(
                        request,
                        (
                            f"Two-factor authentication will become mandatory in {days_left} days. "
                            "Do not wait until the deadline — go to Security and enable it now."
                        )
                    )
                else:
                    # Fecha alcanzada o pasada → 2FA obligatorio para staff
                    messages.warning(
                        request,
                        (
                            "Two-factor authentication is now mandatory for staff accounts. "
                            "Please complete the setup before accessing the platform."
                        )
                    )
                    return redirect("usuarios:two_factor_setup")

            return _redirect_after_login(request, user)

        else:
            messages.error(request, "Invalid credentials.")
    else:
        form = AuthenticationForm(request)

    return render(request, "usuarios/login.html", {"form": form})


@login_required
def seleccionar_rol(request):
    usuario = request.user
    roles_usuario = usuario.roles.all()

    if request.method == 'POST':
        opcion = request.POST.get('opcion')
        if opcion == 'usuario':
            return redirect('dashboard:index')
        elif opcion in ['admin', 'rrhh', 'supervisor', 'pm', 'facturacion', 'logistica', 'subcontrato', 'flota', 'bodeguero', 'prevencion']:
            return redirect('dashboard_admin:index')
        else:
            messages.error(request, "Rol no reconocido.")
            return redirect('usuarios:seleccionar_rol')

    return render(request, 'usuarios/seleccionar_rol.html', {'roles': roles_usuario})


@login_required
def marcar_notificacion_como_leida(request, pk):
    notificacion = get_object_or_404(Notificacion, pk=pk, usuario=request.user)
    notificacion.leido = True
    notificacion.save()

    if notificacion.url:
        return redirect(notificacion.url)

    # Todos los roles de administración redirigen a dashboard_admin
    if request.user.is_superuser or request.user.roles.filter(
        nombre__in=[
            'admin', 'rrhh', 'pm', 'prevencion', 'logistica', 'flota', 'subcontrato', 'facturacion'
        ]
    ).exists():
        return redirect('dashboard_admin:inicio_admin')

    # Técnicos normales
    return redirect('dashboard:inicio_tecnico')


def csrf_error_view(request, reason=""):
    messages.error(
        request, "Your session has expired. Please log in again.")
    # or wherever it should redirect
    return redirect('usuarios:login_unificado')


def two_factor_verify(request):
    """
    Paso intermedio del login cuando el usuario tiene 2FA activo
    y el dispositivo no está marcado como confiable.
    """
    pending_user_id = request.session.get("pending_2fa_user_id")
    if not pending_user_id:
        messages.error(
            request,
            "Your verification session has expired. Please sign in again."
        )
        return redirect("usuarios:login_unificado")

    user = get_object_or_404(CustomUser, pk=pending_user_id)

    if request.method == "POST":
        code = request.POST.get("code", "")
        remember_device = request.POST.get("remember_device") == "on"

        if not _verify_totp_code(user, code):
            messages.error(
                request,
                "The verification code is not valid. Please try again."
            )
            return render(
                request,
                "usuarios/two_factor_verify.html",
                {"user": user},
            )

        # Código correcto → recuperamos backend y limpiamos sesión temporal
        backend_path = request.session.pop("pending_2fa_backend", None)
        request.session.pop("pending_2fa_user_id", None)
        next_url = request.session.pop("pending_2fa_next", None)

        # Si por alguna razón no tenemos backend en sesión, usamos el primero de settings
        if not backend_path:
            backend_path = settings.AUTHENTICATION_BACKENDS[0]

        # Hacemos login definitivo indicando el backend
        login(request, user, backend=backend_path)

        # Creamos dispositivo confiable si el usuario lo pidió
        if remember_device:
            device = _create_trusted_device(request, user)
            response = _redirect_after_login(request, user)
            max_age = TRUSTED_DEVICE_DAYS * 24 * 60 * 60
            response.set_cookie(
                TRUSTED_DEVICE_COOKIE_NAME,
                device.token,
                max_age=max_age,
                secure=not settings.DEBUG,
                httponly=True,
                samesite="Lax",
            )
        else:
            response = _redirect_after_login(request, user)

        # Si había un next, lo respetamos
        if next_url:
            from django.shortcuts import redirect as _redirect
            response = _redirect(next_url)

        return response

    # GET
    return render(
        request,
        "usuarios/two_factor_verify.html",
        {"user": user},
    )


import pyotp
from django.contrib.auth.decorators import login_required


@login_required(login_url="usuarios:login_unificado")
def two_factor_setup(request):
    """
    Vista de seguridad:
      - Configurar y activar 2FA.
      - Listar dispositivos de confianza del usuario.
      - Permitir eliminar dispositivos de confianza.
    """
    user: CustomUser = request.user

    # Generar o recuperar el secreto TOTP del usuario
    secret = user.get_or_create_two_factor_secret()

    # Nombre que verá el usuario en la app de autenticación
    issuer_name = getattr(settings, "TWO_FACTOR_ISSUER_NAME", "Hyperlink Networks")

    # Crear objeto TOTP y URI para la app (Google Authenticator, etc.)
    totp = pyotp.TOTP(secret)
    otp_uri = totp.provisioning_uri(name=user.username, issuer_name=issuer_name)

    # Listar dispositivos de confianza del usuario
    devices = user.trusted_devices.order_by("-created_at")

    if request.method == "POST":
        # Distinguimos qué acción se está ejecutando en esta vista
        action = request.POST.get("action", "enable_2fa")

        if action == "enable_2fa":
            # Código que el usuario ingresa desde su app de autenticación
            code = request.POST.get("code", "")

            # Verificamos usando el mismo helper que en el login
            if _verify_totp_code(user, code):
                # Activar 2FA en la cuenta del usuario
                user.two_factor_enabled = True
                user.save(update_fields=["two_factor_enabled"])

                messages.success(
                    request,
                    "Two-factor authentication has been enabled for your account."
                )
                return redirect("usuarios:two_factor_setup")
            else:
                messages.error(
                    request,
                    "The verification code is not valid. Please try again."
                )

        elif action == "delete_device":
            # Eliminar un dispositivo de confianza
            device_id = request.POST.get("device_id")
            try:
                device = TrustedDevice.objects.get(id=device_id, user=user)
                device.delete()
                messages.success(
                    request,
                    "Trusted device has been removed."
                )
            except TrustedDevice.DoesNotExist:
                messages.error(
                    request,
                    "The selected trusted device could not be found."
                )
            return redirect("usuarios:two_factor_setup")

    context = {
        "secret": secret,
        "otp_uri": otp_uri,
        "two_factor_enabled": user.two_factor_enabled,
        "devices": devices,
    }
    return render(request, "usuarios/two_factor_setup.html", context)