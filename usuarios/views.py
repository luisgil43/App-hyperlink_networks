from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ObjectDoesNotExist
# Asegúrate de que esta importación sea correcta
from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.views import LoginView
from django.urls import reverse_lazy
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


class UsuarioLoginView(LoginView):
    template_name = 'dashboard/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated and not user.is_staff:
            # ← esta vista debe estar disponible
            return reverse_lazy('dashboard:inicio')
        logout(self.request)
        return reverse_lazy('usuarios:login')


class AdminLoginView(LoginView):
    template_name = 'dashboard_admin/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated and user.is_staff:
            return reverse_lazy('admin:index')
        logout(self.request)
        return reverse_lazy('usuarios:admin_login')


def grupos_view(request):
    return render(request, 'usuarios/grupos.html')


def usuarios_view(request):
    return render(request, 'usuarios/usuarios.html')


def inicio_view(request):
    try:
        tecnico = Tecnico.objects.get(user=request.user)
        context = {'tecnico': tecnico}
        return render(request, 'dashboard/tecnico_dashboard.html', context)
    except ObjectDoesNotExist:
        # Si no es técnico, verificar si es superusuario y redirigir
        if request.user.is_superuser:
            return redirect('dashboard_admin:index')
        else:
            # No es técnico ni superusuario, redirigir al login o página de error
            return redirect('usuarios:login')


def logout_view(request):
    logout(request)
    return redirect('usuarios:login')


User = get_user_model()


def lista_usuarios(request):
    usuarios = User.objects.all()
    return render(request, "tu_template.html", {"usuarios": usuarios})


User = get_user_model()


def crear_usuario(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, "✅ Usuario creado correctamente.")
            # Ajusta esta URL a la vista de listar
            return redirect('dashboard_admin:listar_usuarios')
        else:
            messages.error(request, "❌ Hay errores en el formulario.")
    else:
        form = UserCreationForm()

    return render(request, 'usuarios/crear_usuario.html', {'form': form})
