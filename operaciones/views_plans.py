from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.http import FileResponse, HttpResponseForbidden, Http404
from django.views.decorators.http import require_http_methods
from django.conf import settings
import mimetypes

from .models import SesionBilling, ProjectPlan
# ajusta si tus decoradores viven en otro módulo
from usuarios.decoradores import rol_requerido
from django.contrib.auth.decorators import login_required

# ---------- Helpers ----------


def _next_plan_number(sesion: SesionBilling) -> int:
    last = sesion.plans.order_by("-plan_number").first()
    return (last.plan_number + 1) if last else 1


def _is_pdf(plan: ProjectPlan) -> bool:
    mt, _ = mimetypes.guess_type(plan.file.name)
    return (mt == "application/pdf") or plan.file.name.lower().endswith(".pdf")

# ---------- List + Upload ----------


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_http_methods(["GET", "POST"])
def list_plans(request, sesion_id: int):
    """
    GET: lista los planos (Plan 1, Plan 2, ...)
    POST: multi-upload (PDF, DWG, XLS, XLSX). No reemplaza; agrega Plan N+1, N+2...
    """
    sesion = get_object_or_404(SesionBilling, pk=sesion_id)
    plans = sesion.plans.all().order_by("plan_number", "id")

    if request.method == "POST":
        files = request.FILES.getlist("plans")  # <input name="plans" multiple>
        if not files:
            messages.warning(
                request, "Please select one or more files to upload.")
            return redirect("operaciones:list_plans", sesion_id=sesion.id)

        current_n = _next_plan_number(sesion)
        created = 0

        for f in files:
            low = (f.name or "").lower()
            if not (low.endswith(".pdf") or low.endswith(".dwg") or low.endswith(".xlsx") or low.endswith(".xls")):
                messages.error(request, f"Unsupported file type: {f.name}")
                continue

            ProjectPlan.objects.create(
                sesion=sesion,
                plan_number=current_n,
                file=f,
                original_name=f.name or "",
            )
            created += 1
            current_n += 1

        if created:
            messages.success(
                request, f"{created} plan(s) uploaded successfully.")
        else:
            messages.error(request, "No files were uploaded.")

        return redirect("operaciones:list_plans", sesion_id=sesion.id)

    return render(
        request,
        "operaciones/plans_list.html",
        {
            "sesion": sesion,
            "plans": plans,
            "can_import": True,
            "can_delete_plans": True,
        },
    )


# ---------- View (inline for PDF, download otherwise) ----------


@login_required
@rol_requerido("supervisor", "admin", "pm")
def view_plan(request, plan_id: int):
    """
    Devuelve el archivo. Si es PDF lo renderiza inline (visualizable); otros tipos los descarga.
    """
    plan = get_object_or_404(ProjectPlan, pk=plan_id)
    # Opcional: chequeo que el usuario tenga permisos sobre esa sesión
    # if not user_can_access(request.user, plan.sesion): return HttpResponseForbidden()

    mt, _ = mimetypes.guess_type(plan.file.name)
    mt = mt or "application/octet-stream"

    if _is_pdf(plan):
        # Mostrar inline
        resp = FileResponse(plan.file.open("rb"), content_type=mt)
        resp["Content-Disposition"] = f'inline; filename="{plan.original_name or plan.file.name}"'
        return resp
    else:
        # Forzar descarga para DWG / Excel (usualmente el navegador no los previsualiza)
        resp = FileResponse(plan.file.open("rb"), content_type=mt)
        resp["Content-Disposition"] = f'attachment; filename="{plan.original_name or plan.file.name}"'
        return resp

# ---------- Delete ----------


@login_required
@rol_requerido("supervisor", "admin", "pm")
@require_http_methods(["POST"])
def delete_plan(request, plan_id: int):
    plan = get_object_or_404(ProjectPlan, pk=plan_id)
    sesion_id = plan.sesion_id
    # if not user_can_access(request.user, plan.sesion): return HttpResponseForbidden()

    # Borrado físico del objeto y del archivo en storage
    storage = plan.file.storage
    name = plan.file.name
    plan.delete()
    try:
        if name and storage.exists(name):
            storage.delete(name)
    except Exception:
        pass

    messages.success(request, "Plan deleted.")
    return redirect("operaciones:list_plans", sesion_id=sesion_id)


@login_required
@rol_requerido("tecnico", "supervisor", "admin", "pm")  # incluye técnicos
@require_http_methods(["GET"])
def list_plans_readonly(request, sesion_id: int):
    """
    Solo lista y permite ver/descargar. Sin importar ni eliminar.
    """
    sesion = get_object_or_404(SesionBilling, pk=sesion_id)
    plans = sesion.plans.all().order_by("plan_number", "id")
    return render(
        request,
        "operaciones/plans_list.html",
        {
            "sesion": sesion,
            "plans": plans,
            "can_import": False,
            "can_delete_plans": False,
        },
    )
