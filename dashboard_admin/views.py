from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from dashboard.models import ProduccionTecnico
from django.contrib.auth.forms import AuthenticationForm
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied

User = get_user_model()


@login_required(login_url='dashboard_admin:login')
def admin_dashboard_view(request):
    # Cargar datos para la plantilla principal del admin dashboard
    return render(request, 'dashboard_admin/base.html')


@login_required(login_url='dashboard_admin:login')
def logout_view(request):
    logout(request)
    return redirect('dashboard_admin:login')


@staff_member_required
def inicio_admin(request):
    return render(request, 'dashboard_admin/inicio_admin.html')


@login_required(login_url='dashboard_admin:login')
def produccion_tecnico(request):
    produccion = ProduccionTecnico.objects.filter(tecnico__user=request.user)
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': produccion
    })


@login_required(login_url='dashboard_admin:login')
def grupos_view(request):
    if request.method == 'POST':
        if 'add_group' in request.POST:
            nombre = request.POST.get('nombre')
            if nombre:
                grupo, creado = Group.objects.get_or_create(name=nombre)
                if creado:
                    messages.success(
                        request, f'Grupo "{nombre}" creado exitosamente.')
                else:
                    messages.warning(
                        request, f'El grupo "{nombre}" ya existe.')
            return redirect('dashboard_admin:grupos')

        if 'delete_group' in request.POST:
            grupo_id = request.POST.get('grupo_id')
            try:
                grupo = Group.objects.get(id=grupo_id)
                grupo.delete()
                messages.success(
                    request, f'Grupo "{grupo.name}" eliminado correctamente.')
            except Group.DoesNotExist:
                messages.error(request, 'El grupo no existe.')
            return redirect('dashboard_admin:grupos')

    grupos = Group.objects.all()
    return render(request, 'dashboard_admin/grupos.html', {'grupos': grupos})


class UsuarioLoginView(LoginView):
    template_name = 'dashboard_usuario/login.html'
    authentication_form = AuthenticationForm

    def form_valid(self, form):
        user = form.get_user()

        if user.is_staff:
            # Si es staff, redirige directamente al panel admin
            return redirect('dashboard_admin:index')

        login(self.request, user)
        return super().form_valid(form)

    def get_success_url(self):
        # Redirige a `next` si es válido
        redirect_to = self.request.GET.get('next')
        if redirect_to and url_has_allowed_host_and_scheme(redirect_to, self.request.get_host()):
            return redirect_to

        # Fallback
        return reverse_lazy('dashboard_usuario:home')


@login_required(login_url='dashboard_admin:login')
def editar_usuario_view(request, user_id):
    try:
        usuario = get_object_or_404(User, id=user_id)
    except User.DoesNotExist:
        raise Http404("No CustomUser matches the given query.")

    grupos = Group.objects.all()

    if request.method == 'POST':
        usuario.username = request.POST['username']
        usuario.first_name = request.POST['first_name']
        usuario.last_name = request.POST['last_name']
        usuario.email = request.POST['email']
        usuario.is_active = 'is_active' in request.POST
        usuario.is_staff = 'is_staff' in request.POST
        usuario.is_superuser = 'is_superuser' in request.POST
        grupo_ids = request.POST.getlist('groups')
        usuario.groups.set(grupo_ids)
        usuario.save()
        messages.success(request, "Usuario actualizado exitosamente.")
        return redirect('dashboard_admin:listar_usuarios')

    return render(request, 'dashboard_admin/editar_usuario.html', {
        'usuario': usuario,
        'grupos': grupos
    })


class AdminLoginView(LoginView):
    template_name = 'dashboard_admin/login.html'
    authentication_form = AuthenticationForm

    def form_valid(self, form):
        user = form.get_user()

        if not user.is_staff:
            raise PermissionDenied(
                "No tienes permiso para acceder al área de administración.")

        login(self.request, user)
        return super().form_valid(form)

    def get_success_url(self):
        redirect_to = self.request.GET.get('next')
        if redirect_to and url_has_allowed_host_and_scheme(redirect_to, self.request.get_host()):
            return redirect_to

        return reverse_lazy('dashboard_admin:inicio_admin')


@login_required(login_url='dashboard_admin:login')
def usuarios_view(request):
    usuarios = User.objects.all()
    grupos = Group.objects.all()
    contexto = {
        'usuarios': usuarios,
        'grupos': grupos,
    }
    return render(request, 'dashboard_admin/usuarios.html')


@login_required(login_url='dashboard_admin:login')
def crear_usuario_view(request, identidad=None):
    grupos = Group.objects.all()
    usuario = None

    if identidad:
        usuario = get_object_or_404(User, identidad=identidad)

    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        first_name = request.POST['first_name']
        last_name = request.POST['last_name']
        is_active = request.POST.get('is_active') == 'on'
        is_staff = 'is_staff' in request.POST
        is_superuser = 'is_superuser' in request.POST
        grupo_ids = [int(gid) for gid in request.POST.getlist('groups')]
        identidad_post = request.POST.get('identidad')

        if password1 or password2:
            if password1 != password2:
                messages.error(request, 'Las contraseñas no coinciden.')
                return redirect(request.path)

        # Validar que identidad sea numérica o con formato correcto (opcional)
        if identidad_post and not identidad_post.replace('-', '').isdigit():
            messages.error(request, 'El campo Identidad debe ser numérico.')
            return redirect(request.path)

        if usuario:  # Editar usuario existente
            usuario.username = username
            usuario.email = email
            usuario.first_name = first_name
            usuario.last_name = last_name
            usuario.is_active = is_active
            usuario.is_staff = is_staff
            usuario.is_superuser = is_superuser
            usuario.groups.set(grupo_ids)
            usuario.identidad = identidad_post
            if password1:
                usuario.set_password(password1)
            usuario.save()
            messages.success(request, 'Usuario actualizado correctamente.')
        else:
            # Validar username único solo si es nuevo usuario
            if User.objects.filter(username=username).exists():
                messages.error(request, 'El nombre de usuario ya existe.')
                return redirect('dashboard_admin:crear_usuario')

            # Validar identidad única
            if User.objects.filter(identidad=identidad_post).exists():
                messages.error(
                    request, 'El número de identidad ya está registrado.')
                return redirect('dashboard_admin:crear_usuario')

            usuario = User.objects.create_user(
                username=username,
                email=email,
                password=password1,
                first_name=first_name,
                last_name=last_name,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser,
                identidad=identidad_post
            )
            usuario.groups.set(grupo_ids)
            usuario.save()
            messages.success(request, 'Usuario creado exitosamente.')

        return redirect('dashboard_admin:listar_usuarios')

    contexto = {
        'grupos': grupos,
        'usuario': usuario,
    }
    return render(request, 'dashboard_admin/crear_usuario.html', contexto)


@login_required(login_url='dashboard_admin:login')
def index(request):
    return render(request, 'dashboard_admin/index.html')


@login_required(login_url='dashboard_admin:login')
def listar_usuarios(request):
    if request.method == "POST" and "delete_user" in request.POST:
        user_id = request.POST.get("user_id")
        try:
            usuario = User.objects.get(id=user_id)
            usuario.delete()
            messages.success(
                request, f'Usuario "{usuario.username}" eliminado correctamente.')
        except User.DoesNotExist:
            messages.error(request, "Usuario no encontrado.")
        return redirect('dashboard_admin:listar_usuarios')

    usuarios = User.objects.all()
    return render(request, 'dashboard_admin/listar_usuarios.html', {'usuarios': usuarios})


@login_required(login_url='dashboard_admin:login')
def eliminar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        usuario.delete()
        messages.success(
            request, f'Usuario {usuario.username} eliminado correctamente.')
        return redirect('dashboard_admin:listar_usuarios')

    # GET → mostrar confirmación
    return render(request, 'dashboard_admin/eliminar_usuario_confirmacion.html', {'usuario': usuario})
