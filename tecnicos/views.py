from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import Produccion, Curso, Tecnico
from .forms import FirmaForm
from dashboard.models import ProduccionTecnico


def login_tecnico(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('dashboard:dashboard')
        else:
            messages.error(request, 'Usuario o contraseña incorrectos.')
    else:
        form = AuthenticationForm()

    return render(request, 'tecnicos/login.html', {'form': form})


def login_view(request):
    form = AuthenticationForm(request, data=request.POST or None)

    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            if user.is_superuser:
                return redirect('dashboard_admin:home')
            return redirect('dashboard:home')
        else:
            messages.error(request, 'Credenciales inválidas')

    return render(request, 'dashboard/login.html', {'form': form})


def mis_cursos_view(request):
    return render(request, 'tecnicos/mis_cursos.html')


@login_required
def dashboard_view(request):
    tecnico = Tecnico.objects.get(user=request.user)
    producciones = Produccion.objects.filter(tecnico=tecnico)
    cursos = Curso.objects.filter(tecnico=tecnico)

    return render(request, 'tecnicos/dashboard.html', {
        'producciones': producciones,
        'cursos': cursos,
    })


@login_required
def dashboard_detalle_view(request, produccion_id):
    tecnico = Tecnico.objects.get(user=request.user)
    produccion = get_object_or_404(
        Produccion, id=produccion_id, tecnico=tecnico)
    return render(request, 'tecnicos/dashboard_detalle.html', {
        'produccion': produccion,
    })


@login_required
def registrar_firma(request):
    tecnico = request.user.tecnico

    if request.method == 'POST':
        form = FirmaForm(request.POST)
        if form.is_valid():
            firma = form.cleaned_data['firma']
            # Decode base64 to image
            format, imgstr = firma.split(';base64,')
            ext = format.split('/')[-1]
            file_name = f"{request.user.username}_firma.{ext}"

            firma_path = f'firmas/{file_name}'
            firma_full_path = os.path.join(settings.MEDIA_ROOT, firma_path)

            with open(firma_full_path, 'wb') as f:
                f.write(base64.b64decode(imgstr))

            tecnico.firma_digital = firma_path
            tecnico.save()
            return redirect('dashboard:inicio')

    else:
        form = FirmaForm()

    return render(request, 'tecnicos/registrar_firma.html', {'form': form})
