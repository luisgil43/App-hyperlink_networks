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
from django.contrib.auth.decorators import login_required


class UsuarioLoginView(LoginView):
    template_name = 'dashboard/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        user = self.request.user
        if user.is_authenticated:
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


def no_autorizado_view(request):
    return render(request, 'usuarios/no_autorizado.html', status=403)
