from django.shortcuts import get_object_or_404, redirect
from .models import Notificacion
from hyperlink_networks.utils.email_utils import enviar_correo_manual
from email.utils import formataddr
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.utils import timezone
from usuarios.models import FirmaRepresentanteLegal  # 👈 importa el modelo
import base64
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.utils.decorators import method_decorator
from usuarios.decoradores import ratelimit, axes_dispatch
from functools import wraps
from usuarios.decoradores import axes_post_only


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
@ratelimit(key='ip', rate='10/m', block=True, method=['POST'])
@axes_post_only  # Axes solo actúa en POST (no bloquea el GET del login)
def login_unificado(request):
    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            if user.roles.count() == 1 and user.tiene_rol('usuario'):
                return redirect('dashboard:index')
            return redirect('usuarios:seleccionar_rol')
        else:
            messages.error(request, "Invalid credentials.")
    return render(request, 'usuarios/login.html', {'form': form})


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
