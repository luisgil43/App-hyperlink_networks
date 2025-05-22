import os
import tempfile
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.auth import logout
from weasyprint import HTML

from tecnicos.models import Produccion, Curso, Tecnico
from .models import ProduccionTecnico


@login_required
def dashboard_view(request):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    producciones = Produccion.objects.filter(tecnico=tecnico)
    cursos = Curso.objects.filter(tecnico=tecnico)
    return render(request, 'dashboard/inicio.html', {
        'producciones': producciones,
        'cursos': cursos,
    })


@login_required
def mis_cursos_view(request):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    # Obtener cursos asociados usando related_name
    cursos = tecnico.cursos.filter(activo=True)  # si quieres solo activos
    return render(request, 'dashboard/mis_cursos.html', {'cursos': cursos, 'tecnico': tecnico})


@login_required
def dashboard_detalle_view(request, produccion_id):
    tecnico = Tecnico.objects.filter(user=request.user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    produccion = get_object_or_404(
        Produccion, id=produccion_id, tecnico=tecnico)
    return render(request, 'dashboard/detalle.html', {'produccion': produccion})


@login_required
def produccion_tecnicos(request):
    # User es instancia de User, que es lo que necesita el filtro
    user = request.user

    # Buscamos el tecnico asociado a este user
    tecnico = Tecnico.objects.filter(user=user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    # Filtramos ProduccionTecnico por user, que es correcto porque tecnico es FK a User
    produccion = ProduccionTecnico.objects.filter(tecnico=user)
    return render(request, 'dashboard/produccion_tecnicos.html', {
        'produccion': produccion,
        'user': request.user,  # enviamos user para usar atributos en el template
        'tecnico': tecnico,
    })


@login_required
def produccion_tecnicos_pdf(request):
    user = request.user
    tecnico = Tecnico.objects.filter(user=user).first()
    if not tecnico:
        return render(request, 'dashboard/error_tecnico_no_asociado.html')

    produccion = ProduccionTecnico.objects.filter(tecnico=user)
    html_string = render_to_string('dashboard/produccion_pdf.html', {
        'produccion': produccion,
        'user': request.user,  # enviamos user para usar atributos en el template PDF
        'tecnico': tecnico,
    })

    # Generar PDF temporalmente
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        HTML(string=html_string).write_pdf(tmp_file.name)
        tmp_file.seek(0)
        pdf_content = tmp_file.read()

    os.remove(tmp_file.name)

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="produccion_tecnico.pdf"'
    return response


def logout_view(request):
    user = request.user
    logout(request)
    if user.is_staff or user.is_superuser:
        return redirect('/admin/login/')
    else:
        return redirect('/tecnicos/login/')
