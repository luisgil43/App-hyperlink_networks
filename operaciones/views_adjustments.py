# operations/views_adjustments.py
from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponseBadRequest
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal
from .models import AdjustmentEntry
from django.contrib.auth import get_user_model


from urllib.parse import urlencode
from django.utils import timezone
from django.db.models.functions import Cast
from .models import SesionBilling
from facturacion.models import Proyecto

User = get_user_model()


@login_required
def adjustment_new(request):
    """
    Form para crear AdjustmentEntry:
      - Project es un select de facturacion.Proyecto
      - NO se muestran client/city/office/project_id/note
      - Se copian datos 'ligeros' desde Proyecto
    """
    # semana ISO actual
    y, w, _ = timezone.now().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    if request.method == "POST":
        tech_id = request.POST.get("technician")
        adj_type = request.POST.get("adjustment_type")
        week = (request.POST.get("week") or current_week).strip()
        amount_raw = request.POST.get("amount") or "0"
        proyecto_id = request.POST.get("project_select")  # <- del <select>

        # objetos
        technician = User.objects.get(pk=tech_id)
        proyecto = Proyecto.objects.filter(pk=proyecto_id).first()

        amount = Decimal(str(amount_raw))

        # mapear datos ligeros desde Proyecto
        project_name = proyecto.nombre if proyecto else ""
        client_name = (proyecto.mandante or "") if proyecto else ""
        project_code = str(proyecto.pk) if proyecto else ""

        AdjustmentEntry.objects.create(
            technician=technician,
            week=week,
            adjustment_type=adj_type,
            amount=amount,
            # Ligeros en tabla (solo visuales)
            project=project_name,
            client=client_name,
            project_id=project_code,
            city="",
            office="",
            created_by=request.user,
        )
        return redirect("operaciones:produccion_admin")

    techs = User.objects.filter(is_active=True).order_by(
        "first_name", "last_name", "username")
    projects = Proyecto.objects.all().order_by("nombre")

    return render(request, "operaciones/adjustment_new.html", {
        "techs": techs,
        "projects": projects,
        "current_week": current_week,
    })


@login_required
def adjustment_edit(request, pk):
    """
    Edita un AdjustmentEntry y precarga el formulario con sus datos.
    """
    adj = get_object_or_404(AdjustmentEntry, pk=pk)

    if request.method == "POST":
        # Campos básicos
        tech_id = request.POST.get("technician") or adj.technician_id
        adj.technician_id = int(tech_id)
        adj.adjustment_type = request.POST.get(
            "adjustment_type") or adj.adjustment_type
        adj.week = request.POST.get("week") or adj.week

        amount_raw = (request.POST.get("amount") or "").replace(",", "")
        try:
            adj.amount = Decimal(
                amount_raw) if amount_raw != "" else adj.amount
        except (InvalidOperation, TypeError):
            adj.amount = adj.amount  # deja el anterior si viene mal

        # Campos “ligeros” (si los usas todavía, si no, estos quedan vacíos)
        adj.client = request.POST.get("client", adj.client or "")
        adj.city = request.POST.get("city", adj.city or "")
        adj.project = request.POST.get("project", adj.project or "")
        adj.office = request.POST.get("office", adj.office or "")
        adj.project_id = request.POST.get("project_id", adj.project_id or "")
        adj.note = request.POST.get("note", adj.note or "")

        adj.save()
        return redirect("operaciones:produccion_admin")

    # GET → precargar opciones
    User = get_user_model()
    techs = User.objects.order_by("first_name", "last_name", "username")

    return render(
        request,
        "operaciones/adjustment_new.html",
        {
            "current_week": adj.week,  # para mostrar la semana actual del ajuste
            "techs": techs,
            "editing": True,  # bandera para el template
            "adj": adj,       # objeto a precargar
        },
    )


@login_required
@require_POST
def adjustment_delete(request):
    import json
    try:
        payload = json.loads(request.body.decode("utf-8"))
        pk = int(payload.get("id"))
    except Exception:
        return HttpResponseBadRequest("Invalid payload")
    adj = get_object_or_404(AdjustmentEntry, pk=pk)
    adj.delete()
    return JsonResponse({"ok": True})
