# operations/views_adjustments.py
from decimal import Decimal, InvalidOperation
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
    Crear AdjustmentEntry:
      - Project es un <select> de facturacion.Proyecto
      - Se copian datos “ligeros” desde Proyecto a los campos del ajuste
    """
    y, w, _ = timezone.now().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    if request.method == "POST":
        tech_id = request.POST.get("technician")
        adj_type = request.POST.get("adjustment_type")
        week = (request.POST.get("week") or current_week).strip()
        amount_raw = (request.POST.get("amount") or "0").replace(",", "")
        proyecto_id = request.POST.get("project_select") or ""

        # puede levantar DoesNotExist si no existe
        technician = User.objects.get(pk=tech_id)
        proyecto = Proyecto.objects.filter(
            pk=proyecto_id).first() if proyecto_id else None

        try:
            amount = Decimal(str(amount_raw))
        except (InvalidOperation, TypeError):
            amount = Decimal("0")

        # mapear datos ligeros desde Proyecto
        project_name = proyecto.nombre if proyecto else ""
        client_name = (proyecto.mandante or "") if proyecto else ""
        project_code = str(proyecto.pk) if proyecto else ""

        AdjustmentEntry.objects.create(
            technician=technician,
            week=week,
            adjustment_type=adj_type,
            amount=amount,
            # ligeros (solo visuales)
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
        "editing": False,
        "selected_project_id": None,
    })


@login_required
def adjustment_edit(request, pk):
    """
    Editar AdjustmentEntry:
      - Muestra el mismo <select> de proyectos que en "new"
      - Al guardar, vuelve a mapear datos ligeros desde Proyecto
    """
    adj = get_object_or_404(AdjustmentEntry, pk=pk)

    if request.method == "POST":
        # básicos
        tech_id = request.POST.get("technician") or adj.technician_id
        adj.technician_id = int(tech_id)
        adj.adjustment_type = request.POST.get(
            "adjustment_type") or adj.adjustment_type
        adj.week = (request.POST.get("week") or adj.week).strip()

        amount_raw = (request.POST.get("amount") or "").replace(",", "")
        try:
            adj.amount = Decimal(
                amount_raw) if amount_raw != "" else adj.amount
        except (InvalidOperation, TypeError):
            pass  # deja el valor anterior

        # proyecto (viene del <select>)
        proyecto_id = request.POST.get("project_select") or ""
        proyecto = Proyecto.objects.filter(
            pk=proyecto_id).first() if proyecto_id else None

        # Remapear datos ligeros (igual que en "new")
        adj.project = proyecto.nombre if proyecto else ""
        adj.client = (proyecto.mandante or "") if proyecto else ""
        adj.project_id = str(proyecto.pk) if proyecto else ""
        # si quieres limpiar city/office como en "new":
        adj.city = ""
        adj.office = ""

        adj.save()
        return redirect("operaciones:produccion_admin")

    # GET → precargar opciones y selección actual
    techs = User.objects.filter(is_active=True).order_by(
        "first_name", "last_name", "username")
    projects = Proyecto.objects.all().order_by("nombre")

    # intentar preseleccionar por el project_id “ligero” guardado
    try:
        selected_project_id = int(adj.project_id) if adj.project_id else None
    except ValueError:
        selected_project_id = None

    return render(
        request,
        "operaciones/adjustment_new.html",
        {
            "current_week": adj.week,
            "techs": techs,
            "projects": projects,
            "editing": True,
            "adj": adj,
            "selected_project_id": selected_project_id,
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
