import base64
import json
import os
import tempfile
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from dashboard.models import ProduccionTecnico
from operaciones.models import SesionBillingTecnico
from usuarios.models import Notificacion


@login_required
def inicio(request):
    user = request.user
    today = timezone.localdate()

    start_week = today - timedelta(days=today.weekday())
    end_week = start_week + timedelta(days=6)

    start_prev_week = start_week - timedelta(days=7)
    end_prev_week = start_week - timedelta(days=1)

    iso_year, iso_week, _ = today.isocalendar()
    week_label = f"{iso_year}-W{int(iso_week):02d}"
    week_range_label = (
        f"{start_week.strftime('%b %d')} - {end_week.strftime('%b %d, %Y')}"
    )

    qs = SesionBillingTecnico.objects.filter(
        tecnico=user,
        is_active=True,
    ).select_related("sesion")

    approved_states = ["aprobado_supervisor", "aprobado_pm"]

    total_assigned = qs.filter(
        aceptado_en__isnull=True,
        finalizado_en__isnull=True,
    ).count()

    total_in_progress = qs.filter(
        aceptado_en__isnull=False,
        finalizado_en__isnull=True,
    ).count()

    total_submitted_review = (
        qs.filter(
            finalizado_en__isnull=False,
            supervisor_revisado_en__isnull=True,
        )
        .exclude(estado__in=approved_states)
        .count()
    )

    completed_week = qs.filter(
        estado__in=approved_states,
        supervisor_revisado_en__date__range=[start_week, end_week],
    ).count()

    completed_prev_week = qs.filter(
        estado__in=approved_states,
        supervisor_revisado_en__date__range=[start_prev_week, end_prev_week],
    ).count()

    total_current = (
        total_assigned + total_in_progress + total_submitted_review + completed_week
    )

    performance = round((completed_week / total_current) * 100) if total_current else 0

    if completed_prev_week > 0:
        vs_last_week = round(
            ((completed_week - completed_prev_week) / completed_prev_week) * 100
        )
    else:
        vs_last_week = 100 if completed_week > 0 else 0

    chart_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    chart_data = []

    for i in range(7):
        day = start_week + timedelta(days=i)

        chart_data.append(
            qs.filter(
                estado__in=approved_states,
                supervisor_revisado_en__date=day,
            ).count()
        )

    notificaciones = Notificacion.objects.filter(usuario=user).order_by(
        "leido", "-fecha"
    )[:10]

    producciones = ProduccionTecnico.objects.filter(tecnico=user)

    cursos = []
    if hasattr(user, "cursos"):
        try:
            cursos = user.cursos.filter(activo=True)
        except Exception:
            cursos = []

    return render(
        request,
        "dashboard/inicio.html",
        {
            "notificaciones": notificaciones,
            "producciones": producciones,
            "cursos": cursos,
            "week_label": week_label,
            "week_range_label": week_range_label,
            "total_assigned": total_assigned,
            "total_in_progress": total_in_progress,
            "total_submitted_review": total_submitted_review,
            "completed_week": completed_week,
            "completed_prev_week": completed_prev_week,
            "performance": performance,
            "vs_last_week": vs_last_week,
            "chart_labels": json.dumps(chart_labels),
            "chart_data": json.dumps(chart_data),
        },
    )


@login_required
def mis_cursos_view(request):
    usuario = request.user
    cursos = usuario.cursos.all() if hasattr(usuario, "cursos") else []

    return render(
        request,
        "dashboard/mis_cursos.html",
        {
            "cursos": cursos,
            "tecnico": usuario,
            "today": date.today(),
        },
    )


@login_required
def dashboard_detalle_view(request, produccion_id):
    produccion = get_object_or_404(
        ProduccionTecnico,
        id=produccion_id,
        tecnico=request.user,
    )

    return render(
        request,
        "dashboard/detalle.html",
        {
            "produccion": produccion,
        },
    )


@login_required
def produccion_tecnicos_pdf(request):
    usuario = request.user

    produccion = ProduccionTecnico.objects.filter(tecnico=usuario).order_by(
        "fecha_aprobacion"
    )

    try:
        total_monto = produccion.aggregate(total=Sum("monto"))["total"] or 0
    except Exception:
        total_monto = 0

    html_string = render_to_string(
        "dashboard/produccion_pdf.html",
        {
            "user": usuario,
            "tecnico": usuario,
            "produccion": produccion,
            "total_monto": total_monto,
            "now": timezone.now(),
        },
    )

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        HTML(
            string=html_string,
            base_url=request.build_absolute_uri(),
        ).write_pdf(tmp_file.name)

        tmp_file.seek(0)
        pdf_content = tmp_file.read()

    os.remove(tmp_file.name)

    response = HttpResponse(pdf_content, content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="produccion_tecnico.pdf"'

    return response


@login_required
def produccion_tecnicos_view(request):
    producciones = ProduccionTecnico.objects.filter(tecnico=request.user)

    return render(
        request,
        "dashboard/produccion_tecnico.html",
        {
            "produccion": producciones,
        },
    )


@login_required
def logout_view(request):
    user = request.user
    logout(request)

    if user.is_superuser:
        return redirect("/admin/login/")

    return redirect("usuarios:login")


@login_required
def produccion_tecnico(request):
    return render(request, "dashboard_admin/produccion_tecnico.html")


@login_required
def registrar_firma_usuario(request):
    user = request.user

    if user.firma_digital:
        return render(
            request,
            "liquidaciones/firmar.html",
            {
                "tecnico": user,
                "solo_lectura": True,
            },
        )

    if request.method == "POST":
        firma_data = request.POST.get("firma_digital")

        if firma_data:
            formato, imgstr = firma_data.split(";base64,")
            nombre_archivo = f"usuario_{user.id}_firma.png"
            data = ContentFile(base64.b64decode(imgstr), name=nombre_archivo)

            user.firma_digital.save(nombre_archivo, data)
            user.save()

            messages.success(request, "Firma registrada correctamente.")
            return redirect("dashboard:registrar_firma_usuario")

        messages.error(request, "No se recibió la firma. Intenta nuevamente.")

    return render(
        request,
        "liquidaciones/firmar.html",
        {
            "tecnico": user,
            "solo_lectura": False,
        },
    )


def index(request):
    return render(request, "dashboard/index.html")
