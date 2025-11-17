# operations/views_adjustments.py
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from facturacion.models import Proyecto
from usuarios.models import ProyectoAsignacion

from .models import AdjustmentEntry, SesionBilling

User = get_user_model()


# ========================== HELPERS DE VISIBILIDAD ========================== #

def _visible_tech_ids_for_user(user):
    """
    Devuelve:
      - None  => sin restricción (ve a todos)
      - set() => IDs de técnicos que el usuario puede ver

    Regla (igual filosofía que en weekly_payments):
      - admin / superuser -> todos
      - facturación SIN ser pm/supervisor -> todos
      - pm / supervisor (aunque tengan facturación) -> solo usuarios
        que comparten al menos un proyecto con ellos (+ ellos mismos)
      - otros -> solo ellos mismos
    """
    ids = {user.id}

    tiene_rol = getattr(user, "tiene_rol", None)
    if not callable(tiene_rol):
        return ids

    # 1) Admin / superuser -> sin filtro
    if user.is_superuser or getattr(user, "es_admin_general", False):
        return None

    # 2) Facturación pura (NO pm/supervisor) -> sin filtro
    if getattr(user, "es_facturacion", False) and not (
        tiene_rol("pm") or tiene_rol("supervisor")
    ):
        return None

    # 3) Si NO es pm ni supervisor -> sólo él mismo
    if not (tiene_rol("pm") or tiene_rol("supervisor")):
        return ids

    # 4) pm / supervisor -> técnicos con proyectos en común
    my_project_ids = ProyectoAsignacion.objects.filter(
        usuario=user
    ).values_list("proyecto_id", flat=True)

    if not my_project_ids:
        return ids  # sólo él mismo si no tiene proyectos asignados

    others = ProyectoAsignacion.objects.filter(
        proyecto_id__in=my_project_ids
    ).values_list("usuario_id", flat=True).distinct()

    ids.update(others)
    return ids


def _projects_for_user(user):
    """
    Proyectos que el usuario puede usar en ajustes:
      - admin / superuser -> todos
      - facturación pura -> todos
      - pm / supervisor / otros -> solo sus proyectos asignados
    """
    tiene_rol = getattr(user, "tiene_rol", None)

    if user.is_superuser or getattr(user, "es_admin_general", False):
        return Proyecto.objects.all()

    if getattr(user, "es_facturacion", False) and callable(tiene_rol) and not (
        tiene_rol("pm") or tiene_rol("supervisor")
    ):
        return Proyecto.objects.all()

    # Resto: solo proyectos asignados
    my_project_ids = ProyectoAsignacion.objects.filter(
        usuario=user
    ).values_list("proyecto_id", flat=True)

    return Proyecto.objects.filter(pk__in=my_project_ids)


def _user_can_use_project_for_tech(user, technician, proyecto):
    """
    Seguridad backend: ¿puede 'user' crear/editar un ajuste para 'technician' en 'proyecto'?

    - Admin / superuser -> siempre sí
    - Facturación pura -> siempre sí
    - pm / supervisor / otros:
        * el proyecto debe estar asignado al usuario
        * y también asignado al técnico
    """
    if proyecto is None:
        # Permitir ajustes sin proyecto explícito
        return True

    tiene_rol = getattr(user, "tiene_rol", None)

    if user.is_superuser or getattr(user, "es_admin_general", False):
        return True

    if getattr(user, "es_facturacion", False) and callable(tiene_rol) and not (
        tiene_rol("pm") or tiene_rol("supervisor")
    ):
        return True

    # Para pm / supervisor / otros: validar proyectos en común
    if not ProyectoAsignacion.objects.filter(
        usuario=user, proyecto=proyecto
    ).exists():
        return False

    if not ProyectoAsignacion.objects.filter(
        usuario=technician, proyecto=proyecto
    ).exists():
        return False

    return True


def _build_proj_tech_map(projects_qs, techs_qs):
    """
    Construye un mapa {project_id_str: [user_id, ...]} para usar en el JS
    que filtra proyectos según el técnico seleccionado.
    Solo incluye combinaciones proyecto–técnico realmente asignadas.
    """
    proj_ids = list(projects_qs.values_list("id", flat=True))
    tech_ids = list(techs_qs.values_list("id", flat=True))

    if not proj_ids or not tech_ids:
        return {}

    asigs = ProyectoAsignacion.objects.filter(
        proyecto_id__in=proj_ids,
        usuario_id__in=tech_ids,
    ).values_list("proyecto_id", "usuario_id")

    mapping = {}
    for p_id, u_id in asigs:
        mapping.setdefault(str(p_id), set()).add(u_id)

    return {pid: list(uids) for pid, uids in mapping.items()}


# ========================== VISTAS DE AJUSTES ========================== #


@login_required
def adjustment_new(request):
    """
    Crear AdjustmentEntry:
      - Project es un <select> de facturacion.Proyecto
      - Técnicos filtrados:
          * si PM/supervisor -> solo técnicos con proyectos en común
          * si admin/facturación -> todos
      - Según el técnico elegido, el <select> de proyecto mostrará SOLO
        los proyectos en común entre el creador y ese técnico (JS).
    """
    y, w, _ = timezone.now().isocalendar()
    current_week = f"{y}-W{int(w):02d}"

    # ---------- POST: crear ajuste ----------
    if request.method == "POST":
        tech_id = request.POST.get("technician")
        adj_type = request.POST.get("adjustment_type")
        week = (request.POST.get("week") or current_week).strip()
        amount_raw = (request.POST.get("amount") or "0").replace(",", "")
        proyecto_id = request.POST.get("project_select") or ""

        if not tech_id:
            messages.error(request, "Select a technician.")
            return redirect("operaciones:adjustment_new")

        technician = get_object_or_404(User, pk=tech_id)
        proyecto = Proyecto.objects.filter(pk=proyecto_id).first() if proyecto_id else None

        try:
            amount = Decimal(str(amount_raw))
        except (InvalidOperation, TypeError):
            amount = Decimal("0")

        # Seguridad: validar que el usuario pueda usar ese proyecto para ese técnico
        if not _user_can_use_project_for_tech(request.user, technician, proyecto):
            messages.error(
                request,
                "You cannot create adjustments for this technician and project."
            )
            return redirect("operaciones:produccion_admin")

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

    # ---------- GET: formulario ----------
    visible_tech_ids = _visible_tech_ids_for_user(request.user)
    if visible_tech_ids is None:
        techs = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
    else:
        techs = User.objects.filter(
            is_active=True,
            id__in=visible_tech_ids
        ).order_by("first_name", "last_name", "username")

    projects_qs = _projects_for_user(request.user).order_by("nombre")

    proj_tech_map = _build_proj_tech_map(projects_qs, techs)
    proj_tech_map_json = json.dumps(proj_tech_map)

    return render(request, "operaciones/adjustment_new.html", {
        "techs": techs,
        "projects": projects_qs,
        "current_week": current_week,
        "editing": False,
        "selected_project_id": None,
        "proj_tech_map_json": proj_tech_map_json,
    })


@login_required
def adjustment_edit(request, pk):
    """
    Editar AdjustmentEntry:
      - Muestra el mismo <select> de proyectos que en "new"
      - Al guardar, vuelve a mapear datos ligeros desde Proyecto
      - Respeta las mismas reglas de visibilidad y seguridad
    """
    adj = get_object_or_404(AdjustmentEntry, pk=pk)

    # ---------- POST: guardar ----------
    if request.method == "POST":
        tech_id = request.POST.get("technician") or adj.technician_id
        technician = get_object_or_404(User, pk=tech_id)

        adj.technician_id = int(tech_id)
        adj.adjustment_type = request.POST.get("adjustment_type") or adj.adjustment_type
        adj.week = (request.POST.get("week") or adj.week).strip()

        amount_raw = (request.POST.get("amount") or "").replace(",", "")
        try:
            adj.amount = Decimal(amount_raw) if amount_raw != "" else adj.amount
        except (InvalidOperation, TypeError):
            pass  # deja el valor anterior

        proyecto_id = request.POST.get("project_select") or ""
        proyecto = Proyecto.objects.filter(pk=proyecto_id).first() if proyecto_id else None

        # Seguridad: validar que el usuario pueda usar ese proyecto para ese técnico
        if not _user_can_use_project_for_tech(request.user, technician, proyecto):
            messages.error(
                request,
                "You cannot edit this adjustment for this technician and project."
            )
            return redirect("operaciones:produccion_admin")

        # Remapear datos ligeros (igual que en "new")
        adj.project = proyecto.nombre if proyecto else ""
        adj.client = (proyecto.mandante or "") if proyecto else ""
        adj.project_id = str(proyecto.pk) if proyecto else ""
        adj.city = ""
        adj.office = ""

        adj.save()
        return redirect("operaciones:produccion_admin")

    # ---------- GET: formulario ----------
    visible_tech_ids = _visible_tech_ids_for_user(request.user)
    if visible_tech_ids is None:
        techs = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
    else:
        techs = User.objects.filter(
            is_active=True,
            id__in=visible_tech_ids
        ).order_by("first_name", "last_name", "username")

    projects_qs = _projects_for_user(request.user).order_by("nombre")

    # intentar preseleccionar por el project_id “ligero” guardado
    try:
        selected_project_id = int(adj.project_id) if adj.project_id else None
    except ValueError:
        selected_project_id = None

    # Si el proyecto actual no está en la lista visible, lo agregamos para no perderlo en el edit
    projects = list(projects_qs)
    if selected_project_id and not any(p.id == selected_project_id for p in projects):
        extra = Proyecto.objects.filter(pk=selected_project_id).first()
        if extra:
            projects.append(extra)

    proj_tech_map = _build_proj_tech_map(projects_qs, techs)
    # Aseguramos que la combinación actual esté presente en el mapa (por si no hay asignación)
    if selected_project_id and adj.technician_id:
        key = str(selected_project_id)
        ids = set(proj_tech_map.get(key, []))
        ids.add(adj.technician_id)
        proj_tech_map[key] = list(ids)

    proj_tech_map_json = json.dumps(proj_tech_map)

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
            "proj_tech_map_json": proj_tech_map_json,
        },
    )


@login_required
@require_POST
def adjustment_delete(request):
    import json as _json
    try:
        payload = _json.loads(request.body.decode("utf-8"))
        pk = int(payload.get("id"))
    except Exception:
        return HttpResponseBadRequest("Invalid payload")
    adj = get_object_or_404(AdjustmentEntry, pk=pk)
    adj.delete()
    return JsonResponse({"ok": True})