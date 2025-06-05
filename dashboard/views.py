"""from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.auth import logout
from weasyprint import HTML
from django.urls import reverse_lazy
from django.db.models import Sum
from django.utils import timezone
from datetime import date
import os
import tempfile

from tecnicos.models import Curso, Tecnico
from dashboard.models import ProduccionTecnico


@login_required(login_url='dashboard_admin:login')
def inicio(request):
    return render(request, 'dashboard/inicio.html')


@login_required
def dashboard_view(request):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    producciones = tecnico.producciones_dashboard.all()

  # related_name correcto
    cursos = tecnico.cursos.filter(activo=True)
    return render(request, 'dashboard/inicio.html', {
        'producciones': producciones,
        'cursos': cursos,
    })


@login_required
def mis_cursos_view(request):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    cursos = tecnico.cursos.all()  # quitar filtro activo
    return render(request, 'dashboard/mis_cursos.html', {
        'cursos': cursos,
        'tecnico': tecnico,
        'today': date.today(),  # <== pasar fecha actual
    })


@login_required
def dashboard_detalle_view(request, produccion_id):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    produccion = get_object_or_404(
        ProduccionTecnico, id=produccion_id, tecnico=tecnico
    )
    return render(request, 'dashboard/detalle.html', {'produccion': produccion})


@login_required
def produccion_tecnicos_pdf(request):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return HttpResponse("El usuario no tiene asociado un técnico.", status=400)

    produccion = ProduccionTecnico.objects.filter(
        tecnico=tecnico).order_by('fecha_aprobacion')

    total_monto = produccion.aggregate(total=Sum('monto'))['total'] or 0

    html_string = render_to_string('dashboard/produccion_pdf.html', {
        'user': request.user,
        'tecnico': tecnico,
        'produccion': produccion,
        'total_monto': total_monto,
        'now': timezone.now()
    })

    # Generar PDF temporal
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        HTML(string=html_string, base_url=request.build_absolute_uri()
             ).write_pdf(tmp_file.name)
        tmp_file.seek(0)
        pdf_content = tmp_file.read()

    os.remove(tmp_file.name)

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="produccion_tecnico.pdf"'
    return response


@login_required
def produccion_tecnicos_view(request):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    producciones = tecnico.producciones_dashboard.all()
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': producciones,
    })


def logout_view(request):
    user = request.user
    logout(request)
    if user.is_staff or user.is_superuser:
        return redirect('/admin/login/')
    return redirect('/usuarios/login/')


def inicio_tecnico(request):
    # o el nombre correcto de tu plantilla de inicio
    return render(request, 'dashboard/inicio.html')


def produccion_tecnico(request):
    return render(request, 'dashboard_admin/produccion_tecnico.html')


# class UsuarioLoginView(LoginView):
    # template_name = 'usuarios/login.html'
    # success_url = reverse_lazy('dashboard:inicio')"""

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.auth import logout
from weasyprint import HTML
from django.db.models import Sum
from django.utils import timezone
from datetime import date
import os
import tempfile

from dashboard.models import ProduccionTecnico
from usuarios.models import CustomUser  # ← tu nuevo modelo
# Ya no se importa Tecnico ni Curso


@login_required(login_url='dashboard_admin:login')
def inicio(request):
    return render(request, 'dashboard/inicio.html')


@login_required
def dashboard_view(request):
    usuario = request.user
    producciones = ProduccionTecnico.objects.filter(tecnico=usuario)
    cursos = usuario.cursos.filter(
        activo=True) if hasattr(usuario, 'cursos') else []

    return render(request, 'dashboard/inicio.html', {
        'producciones': producciones,
        'cursos': cursos,
    })


@login_required
def mis_cursos_view(request):
    usuario = request.user
    cursos = usuario.cursos.all() if hasattr(usuario, 'cursos') else []

    return render(request, 'dashboard/mis_cursos.html', {
        'cursos': cursos,
        'tecnico': usuario,
        'today': date.today(),
    })


@login_required
def dashboard_detalle_view(request, produccion_id):
    produccion = get_object_or_404(
        ProduccionTecnico, id=produccion_id, tecnico=request.user
    )
    return render(request, 'dashboard/detalle.html', {'produccion': produccion})


@login_required
def produccion_tecnicos_pdf(request):
    usuario = request.user
    produccion = ProduccionTecnico.objects.filter(
        tecnico=usuario).order_by('fecha_aprobacion')
    total_monto = produccion.aggregate(total=Sum('monto'))['total'] or 0

    html_string = render_to_string('dashboard/produccion_pdf.html', {
        'user': usuario,
        'tecnico': usuario,
        'produccion': produccion,
        'total_monto': total_monto,
        'now': timezone.now()
    })

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        HTML(string=html_string, base_url=request.build_absolute_uri()
             ).write_pdf(tmp_file.name)
        tmp_file.seek(0)
        pdf_content = tmp_file.read()

    os.remove(tmp_file.name)

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="produccion_tecnico.pdf"'
    return response


@login_required
def produccion_tecnicos_view(request):
    producciones = ProduccionTecnico.objects.filter(tecnico=request.user)
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': producciones,
    })


def logout_view(request):
    user = request.user
    logout(request)
    if user.is_staff or user.is_superuser:
        return redirect('/admin/login/')
    return redirect('/usuarios/login/')


def inicio_tecnico(request):
    return render(request, 'dashboard/inicio.html')


def produccion_tecnico(request):
    return render(request, 'dashboard_admin/produccion_tecnico.html')
