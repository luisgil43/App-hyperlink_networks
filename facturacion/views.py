# ajusta si tu decorador está en otro módulo
import datetime
import datetime as dt
import re
import traceback
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

import openpyxl
import pdfplumber
import xlwt
from dateutil import parser
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import (Case, DecimalField, ExpressionWrapper, F,
                              Prefetch, Q, Subquery, Sum, Value, When)
from django.db.models.functions import Coalesce
from django.http import (HttpResponse, HttpResponseBadRequest,
                         HttpResponseForbidden, HttpResponseNotAllowed,
                         JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.timezone import is_aware
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from facturacion.models import CartolaMovimiento, Proyecto
from operaciones.forms import MovimientoUsuarioForm
from operaciones.models import (EvidenciaFotoBilling, ItemBilling,
                                ItemBillingTecnico, SesionBilling,
                                SesionBillingTecnico)
from usuarios.decoradores import rol_requerido
from usuarios.models import ProyectoAsignacion

from .forms import (CartolaAbonoForm, CartolaGastoForm,
                    CartolaMovimientoCompletoForm, ProyectoForm, TipoGastoForm)
from .models import CartolaMovimiento, Proyecto, TipoGasto

User = get_user_model()

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import IntegerField
from django.db.models.functions import Cast, Substr
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

# ⬇️ agrega junto a tus imports
from core.decorators import project_object_access_required
from core.permissions import (filter_queryset_by_access, projects_ids_for_user,
                              user_has_project_access)

from .models import CartolaMovimiento, Proyecto


def suggest_next_project_code(prefix: str = "PRJ-", width: int = 6) -> str:
    pattern = rf"^{re.escape(prefix)}\d{{{width}}}$"
    start = len(prefix) + 1  # Substr es 1-indexado

    qs = (Proyecto.objects
          .filter(codigo__regex=pattern)
          .annotate(num=Cast(Substr('codigo', start, width), IntegerField()))
          .order_by('-num'))

    last = qs.values_list('num', flat=True).first() or 0
    return f"{prefix}{last + 1:0{width}d}"

def _next_project_code():
    # extrae los 6 dígitos y calcula el siguiente
    qs = (Proyecto.objects
          .filter(codigo__regex=r'^PRJ-\d{6}$')
          .annotate(n=Cast(Substr('codigo', 5, 6), IntegerField()))
          .order_by('-n'))
    last = qs.first().n if qs.exists() else 0
    return f"PRJ-{last + 1:06d}"

@login_required
@rol_requerido('facturacion', 'admin')
def listar_cartola(request):
    import json
    from datetime import datetime
    from datetime import datetime as py_datetime
    from datetime import time, timedelta

    from django.contrib import messages
    from django.core.paginator import Paginator
    from django.db import models
    from django.db.models import Q
    from django.utils import timezone

    from .models import CartolaMovimiento

    def parse_date_any(s: str):
        """Devuelve date para varios formatos comunes o None."""
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    params = request.GET.copy()

    # ---------- Filtros tipo Excel recibidos por GET ----------
    excel_filters_raw = params.get('excel_filters', '').strip()
    try:
        excel_filters = json.loads(excel_filters_raw) if excel_filters_raw else {}
    except json.JSONDecodeError:
        excel_filters = {}

    # ==========================
    # Manejo de 'cantidad'
    # ==========================
    raw_cantidad = params.get('cantidad', '10')
    page_number = params.get('page', '1')

    MAX_PAGE_SIZE = 100

    try:
        cantidad_int = int(raw_cantidad)
    except (TypeError, ValueError):
        cantidad_int = 10

    if cantidad_int < 5:
        cantidad_int = 5
    if cantidad_int > MAX_PAGE_SIZE:
        cantidad_int = MAX_PAGE_SIZE

    cantidad = str(cantidad_int)

    # ==========================
    # Filtros por GET
    # ==========================
    du        = params.get('du', '').strip()
    fecha_str = params.get('fecha', '').strip()
    proyecto  = params.get('proyecto', '').strip()
    categoria = params.get('categoria', '').strip()
    tipo      = params.get('tipo', '').strip()
    estado    = params.get('estado', '').strip()

    # Query base
    movimientos_qs = (
        CartolaMovimiento.objects.all()
        .select_related('usuario', 'proyecto', 'tipo')
        .order_by('-fecha')
    )
    movimientos_qs = filter_queryset_by_access(movimientos_qs, request.user, 'proyecto_id')

    # Usuario
    if du:
        movimientos_qs = movimientos_qs.filter(
            Q(usuario__username__icontains=du) |
            Q(usuario__first_name__icontains=du) |
            Q(usuario__last_name__icontains=du)
        )

    # ===== Filtro de FECHA =====
    if fecha_str:
        solo_digitos = fecha_str.isdigit()
        if solo_digitos and 1 <= int(fecha_str) <= 31:
            dia = int(fecha_str)
            movimientos_qs = movimientos_qs.filter(fecha__day=dia)
        else:
            fecha_valida = parse_date_any(fecha_str)
            if not fecha_valida:
                messages.warning(
                    request,
                    "Invalid date. Use DD-MM-YYYY or only the day (e.g. 20)."
                )
            else:
                campo_fecha = CartolaMovimiento._meta.get_field('fecha')
                if isinstance(campo_fecha, models.DateTimeField):
                    tz = timezone.get_current_timezone()
                    start = timezone.make_aware(
                        datetime.combine(fecha_valida, time.min), tz
                    )
                    end = start + timedelta(days=1)
                    movimientos_qs = movimientos_qs.filter(
                        fecha__gte=start, fecha__lt=end
                    )
                else:
                    movimientos_qs = movimientos_qs.filter(fecha=fecha_valida)

    if proyecto:
        movimientos_qs = movimientos_qs.filter(proyecto__nombre__icontains=proyecto)
    if categoria:
        movimientos_qs = movimientos_qs.filter(tipo__categoria__icontains=categoria)
    if tipo:
        movimientos_qs = movimientos_qs.filter(tipo__nombre__icontains=tipo)
    if estado:
        movimientos_qs = movimientos_qs.filter(status=estado)

    # ============================================================
    #  Traemos TODO el queryset a memoria:
    #  - luego aplicamos filtros Excel (sobre Python)
    #  - luego paginamos
    # ============================================================
    movimientos_list = list(movimientos_qs)

    # ---------- Aplicar filtros Excel sobre la lista ----------
    if excel_filters:
        def matches_excel_filters(m):
            for col, values in excel_filters.items():
                if not values:
                    continue
                values_set = set(values)

                if col == "0":      # User
                    label = str(m.usuario) if m.usuario else ""

                elif col == "1":    # Date (dd-mm-YYYY)
                    dt = getattr(m, "fecha", None)
                    if not dt:
                        label = ""
                    else:
                        if isinstance(dt, py_datetime):
                            dt = timezone.localtime(dt)
                        label = dt.strftime("%d-%m-%Y")

                elif col == "2":    # Project
                    label = str(m.proyecto) if m.proyecto else ""

                elif col == "3":    # Real consumption date (dd/mm/YYYY o —)
                    d = getattr(m, "real_consumption_date", None)
                    if not d:
                        label = "—"
                    else:
                        if isinstance(d, py_datetime):
                            d = timezone.localtime(d)
                        label = d.strftime("%d/%m/%Y")

                elif col == "4":    # Category (title)
                    if m.tipo and m.tipo.categoria:
                        label = m.tipo.categoria.title()
                    else:
                        label = ""

                elif col == "5":    # Type
                    label = str(m.tipo) if m.tipo else ""

                elif col == "12":   # Status (display)
                    label = m.get_status_display() if m.status else ""

                else:
                    # otras columnas (numéricas, etc.) las ignoramos por ahora
                    continue

                if label not in values_set:
                    return False
            return True

        movimientos_list = [m for m in movimientos_list if matches_excel_filters(m)]

    # -------- Distinct globales para filtros tipo Excel ----------
    excel_global = {}

    # Col 0: User
    users_set = {str(m.usuario) for m in movimientos_list if m.usuario}
    excel_global[0] = sorted(users_set)

    # Col 1: Date (DD-MM-YYYY)
    dates_set = set()
    for m in movimientos_list:
        if not m.fecha:
            continue
        dt = m.fecha
        if isinstance(dt, py_datetime):
            dt = timezone.localtime(dt)
        dates_set.add(dt.strftime("%d-%m-%Y"))
    excel_global[1] = sorted(dates_set)

    # Col 2: Project
    projects_set = {str(m.proyecto) for m in movimientos_list if m.proyecto}
    excel_global[2] = sorted(projects_set)

    # Col 3: Real consumption date (DD/MM/YYYY)
    rcd_set = set()
    for m in movimientos_list:
        d = getattr(m, "real_consumption_date", None)
        if not d:
            continue
        if isinstance(d, py_datetime):
            d = timezone.localtime(d)
        rcd_set.add(d.strftime("%d/%m/%Y"))
    excel_global[3] = sorted(rcd_set)

    # Col 4: Category
    cat_set = {
        (m.tipo.categoria or "").title()
        for m in movimientos_list
        if m.tipo and m.tipo.categoria
    }
    excel_global[4] = sorted(cat_set)

    # Col 5: Type
    type_set = {str(m.tipo) for m in movimientos_list if m.tipo}
    excel_global[5] = sorted(type_set)

    # Col 12: Status
    estado_map = dict(CartolaMovimiento.ESTADOS)
    status_codes = {m.status for m in movimientos_list if m.status}
    excel_global[12] = sorted(estado_map.get(c, c) for c in status_codes)

    excel_global_json = json.dumps(excel_global)

    # ==========================
    # Paginación
    # ==========================
    paginator = Paginator(movimientos_list, cantidad_int)
    pagina = paginator.get_page(page_number)

    params_no_page = params.copy()
    params_no_page.pop('page', None)
    base_qs = params_no_page.urlencode()
    full_qs = params.urlencode()

    estado_choices = CartolaMovimiento.ESTADOS
    filtros = {
        'du': du,
        'fecha': fecha_str,
        'proyecto': proyecto,
        'categoria': categoria,
        'tipo': tipo,
        'estado': estado,
    }

    ctx = {
        'pagina': pagina,
        'cantidad': cantidad,
        'estado_choices': estado_choices,
        'filtros': filtros,
        'base_qs': base_qs,
        'full_qs': full_qs,
        'excel_global_json': excel_global_json,
    }
    return render(request, 'facturacion/listar_cartola.html', ctx)


@login_required
@rol_requerido('facturacion', 'admin')
def registrar_abono(request):
    # Detectar el usuario “destinatario” del abono (viene en el form)
    target_user = None
    user_field_names = ('usuario', 'user', 'tecnico')
    if request.method == 'POST':
        for fn in user_field_names:
            uid = request.POST.get(fn)
            if uid:
                try:
                    target_user = User.objects.get(pk=uid)
                except User.DoesNotExist:
                    target_user = None
                break
    else:
        for fn in user_field_names:
            uid = request.GET.get(fn)
            if uid:
                try:
                    target_user = User.objects.get(pk=uid)
                except User.DoesNotExist:
                    target_user = None
                break

    form = CartolaAbonoForm(request.POST or None, request.FILES or None)

    # 🔒 Restringir el combo de proyectos del formulario
    if hasattr(form, 'fields') and 'proyecto' in form.fields:
        # Si ya eligieron un usuario destino, mostrar SOLO proyectos donde él participa.
        # Si no, mostrar (por defecto) los proyectos a los que el actor (tú) tiene acceso.
        allowed_ids = projects_ids_for_user(target_user) if target_user else projects_ids_for_user(request.user)
        form.fields['proyecto'].queryset = Proyecto.objects.filter(id__in=allowed_ids).order_by('nombre')

    if request.method == 'POST':
        if form.is_valid():
            movimiento = form.save(commit=False)

            # Si el form NO setea el usuario y lo detectamos arriba, lo fijamos.
            if target_user and not getattr(movimiento, 'usuario_id', None):
                movimiento.usuario = target_user

            proj_id = getattr(getattr(movimiento, 'proyecto', None), 'id', None)
            if not proj_id:
                messages.error(request, "You must choose a project.")
                return render(request, 'facturacion/registrar_abono.html', {'form': form})

            # 🔒 1) El actor debe tener acceso al proyecto
            if not user_has_project_access(request.user, proj_id):
                messages.error(request, "You don't have access to the selected project.")
                return render(request, 'facturacion/registrar_abono.html', {'form': form})

            # 🔒 2) El usuario destino debe participar en ese proyecto
            if getattr(movimiento, 'usuario_id', None):
                target_allowed = projects_ids_for_user(movimiento.usuario)
                if proj_id not in target_allowed:
                    messages.error(request, "The selected user is not assigned to that project.")
                    return render(request, 'facturacion/registrar_abono.html', {'form': form})

            # Forzar categoría como abono
            tipo_abono = TipoGasto.objects.filter(categoria='abono').first()
            movimiento.tipo = tipo_abono
            movimiento.cargos = 0

            if 'comprobante' in request.FILES:
                movimiento.comprobante = request.FILES['comprobante']

            movimiento.save()
            messages.success(request, "Transaction registered successfully.")
            return redirect('facturacion:listar_cartola')
        else:
            messages.error(request, "Please correct the errors before proceeding.")

    return render(request, 'facturacion/registrar_abono.html', {'form': form})


@login_required
@rol_requerido('facturacion', 'admin')
def crear_tipo(request):
    if request.method == 'POST':
        form = TipoGastoForm(request.POST)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                tipos = TipoGasto.objects.all().order_by('-id')
                html = render_to_string(
                    'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
                return JsonResponse({'success': True, 'html': html})
            messages.success(request, "Expense type created successfully.")
            return redirect('facturacion:crear_tipo')
    else:
        form = TipoGastoForm()
    tipos = TipoGasto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_tipo.html', {'form': form, 'tipos': tipos})


@login_required
@rol_requerido('admin')
def editar_tipo(request, pk):
    tipo = get_object_or_404(TipoGasto, pk=pk)
    if request.method == 'POST':
        form = TipoGastoForm(request.POST, instance=tipo)
        if form.is_valid():
            form.save()
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                tipos = TipoGasto.objects.all().order_by('-id')
                html = render_to_string(
                    'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
                return JsonResponse({'success': True, 'html': html})
            messages.success(request, "Expense type updated successfully.")
            return redirect('facturacion:crear_tipo')
    else:
        form = TipoGastoForm(instance=tipo)
    tipos = TipoGasto.objects.all().order_by('-id')
    # Usamos el mismo template que crear
    return render(request, 'facturacion/crear_tipo.html', {'form': form, 'tipos': tipos, 'editando': True})


@login_required
@rol_requerido('admin')
def eliminar_tipo(request, pk):
    tipo = get_object_or_404(TipoGasto, pk=pk)
    tipo.delete()
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        tipos = TipoGasto.objects.all().order_by('-id')
        html = render_to_string(
            'facturacion/partials/tipo_gasto_table.html', {'tipos': tipos})
        return JsonResponse({'success': True, 'html': html})
    messages.success(request, "Expense type deleted successfully.")
    return redirect('facturacion:crear_tipo')




@login_required
@rol_requerido('facturacion', 'admin')
def crear_proyecto(request):
    if request.method == 'POST':
        form = ProyectoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Project created successfully.")
            return redirect('facturacion:crear_proyecto')
    else:
        next_code = _next_project_code()
        form = ProyectoForm(initial={'codigo': next_code, 'activo': '1'})  # preselecciona Active

    proyectos = Proyecto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_proyecto.html', {
        'form': form,
        'proyectos': proyectos,
        'next_code': next_code if request.method != 'POST' else None,
    })



# Editar
@login_required
@rol_requerido('admin')
def editar_proyecto(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        form = ProyectoForm(request.POST, instance=proyecto)
        if form.is_valid():
            form.save()
            messages.success(request, "Project updated successfully.")
            return redirect('facturacion:crear_proyecto')
    else:
        form = ProyectoForm(instance=proyecto)
    proyectos = Proyecto.objects.all().order_by('-id')
    return render(request, 'facturacion/crear_proyecto.html', {
        'form': form,
        'proyectos': proyectos
    })

# Eliminar


@login_required
@rol_requerido('admin')
def eliminar_proyecto(request, pk):
    proyecto = get_object_or_404(Proyecto, pk=pk)
    if request.method == 'POST':
        proyecto.delete()
        messages.success(request, "Project deleted successfully.")
        return redirect('facturacion:crear_proyecto')
    return redirect('facturacion:crear_proyecto')


@login_required
@rol_requerido('facturacion', 'supervisor', 'pm', 'admin')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def aprobar_movimiento(request, pk):
    """
    Aprueba un movimiento.

    - Si es petición AJAX → responde JSON (sin redirect).
    - Si es petición normal → mantiene el redirect a listar_cartola.
    """
    mov = get_object_or_404(CartolaMovimiento, pk=pk)

    ok = False
    prev_status = mov.status
    new_status = mov.status

    if mov.tipo and mov.tipo.categoria != "abono":
        # Asignar aprobador según el rol / estado actual
        if getattr(request.user, "es_supervisor", False) and mov.status == 'pendiente_supervisor':
            mov.status = 'aprobado_supervisor'
            mov.aprobado_por_supervisor = request.user
        elif getattr(request.user, "es_pm", False) and mov.status == 'aprobado_supervisor':
            mov.status = 'aprobado_pm'
            mov.aprobado_por_pm = request.user
        elif getattr(request.user, "es_facturacion", False) and mov.status == 'aprobado_pm':
            mov.status = 'aprobado_finanzas'
            mov.aprobado_por_finanzas = request.user

        if mov.status != prev_status:
            mov.motivo_rechazo = ''  # Limpiar cualquier rechazo previo
            mov.save()
            ok = True
            new_status = mov.status

    # --- Si es AJAX, devolvemos JSON ---
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        if not ok:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Movement cannot be approved in the current state or category.",
                    "status": mov.status,
                    "status_display": mov.get_status_display(),
                },
                status=400,
            )

        return JsonResponse(
            {
                "ok": True,
                "id": mov.pk,
                "status": new_status,
                "status_display": mov.get_status_display(),
            }
        )

    # --- Flujo normal (no AJAX) ---
    if ok:
        messages.success(request, "Expense approved successfully.")
    else:
        messages.error(request, "Expense cannot be approved in the current state.")
    return redirect('facturacion:listar_cartola')


@login_required
@rol_requerido('facturacion', 'supervisor', 'pm', 'admin')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def rechazar_movimiento(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    if request.method == 'POST':
        motivo = request.POST.get('motivo_rechazo', '').strip()
        if mov.tipo and mov.tipo.categoria != "abono":
            if request.user.es_supervisor and mov.status == 'pendiente_supervisor':
                mov.status = 'rechazado_supervisor'
                mov.aprobado_por_supervisor = request.user
            elif request.user.es_pm and mov.status == 'aprobado_supervisor':
                mov.status = 'rechazado_pm'
                mov.aprobado_por_pm = request.user
            elif request.user.es_facturacion and mov.status == 'aprobado_pm':
                mov.status = 'rechazado_finanzas'
                mov.aprobado_por_finanzas = request.user  # Usuario de finanzas

            mov.motivo_rechazo = motivo
            mov.save()
            messages.success(request, "Expense rejected successfully.")
    return redirect('facturacion:listar_cartola')

@login_required
@rol_requerido('admin')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def aprobar_abono_como_usuario(request, pk):
    mov = get_object_or_404(CartolaMovimiento, pk=pk)
    if mov.tipo and mov.tipo.categoria == "abono" and mov.status == "pendiente_abono_usuario":
        mov.status = "aprobado_abono_usuario"
        mov.save()
        messages.success(request, "Deposit approved as user.")
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER') or reverse('facturacion:listar_cartola')
    return redirect(next_url)


from django import forms  # 👈 agregar este import arriba


@login_required
@rol_requerido('facturacion', 'admin')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def editar_movimiento(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)

    # ¿Es abono o gasto?
    es_abono = bool(movimiento.tipo and movimiento.tipo.categoria == "abono")
    FormClass = CartolaAbonoForm if es_abono else MovimientoUsuarioForm
    estado_restaurado = 'pendiente_abono_usuario' if es_abono else 'pendiente_supervisor'

    def ensure_real_consumption_date_field(form):
        """
        Asegura que el form tenga real_consumption_date para precargar y guardar,
        sin depender de cómo esté declarado el ModelForm.
        """
        if hasattr(movimiento, "real_consumption_date") and "real_consumption_date" not in form.fields:
            form.fields["real_consumption_date"] = forms.DateField(
                required=False,
                widget=forms.DateInput(attrs={"type": "date"})
            )
            # precarga
            if movimiento.real_consumption_date:
                val = movimiento.real_consumption_date
                # por si fuera datetime
                if hasattr(val, "date"):
                    try:
                        val = timezone.localtime(val).date()
                    except Exception:
                        val = val.date()
                form.initial["real_consumption_date"] = val
        return form

    if request.method == 'POST':
        form = FormClass(request.POST, request.FILES, instance=movimiento)
        form = ensure_real_consumption_date_field(form)

        if form.is_valid():
            movimiento = form.save(commit=False)

            # ✅ guardar nuevo campo si viene en el form
            if "real_consumption_date" in form.cleaned_data:
                movimiento.real_consumption_date = form.cleaned_data.get("real_consumption_date")

            # Reemplazo explícito del comprobante si viene un archivo nuevo
            if 'comprobante' in request.FILES:
                movimiento.comprobante = request.FILES['comprobante']

            # Reemplazo explícito de la foto del tablero (solo si viene)
            if 'foto_tablero' in request.FILES:
                movimiento.foto_tablero = request.FILES['foto_tablero']

            # ----- Reset de estado -----
            if es_abono and movimiento.status == 'rechazado_abono_usuario':
                movimiento.status = 'pendiente_abono_usuario'
                movimiento.motivo_rechazo = ""
            elif form.changed_data:
                movimiento.status = estado_restaurado
                movimiento.motivo_rechazo = ""

            movimiento.save()
            messages.success(request, "Expense updated successfully.")

            next_url = (
                request.GET.get('next')
                or request.POST.get('next')
                or request.META.get('HTTP_REFERER')
                or reverse('facturacion:listar_cartola')
            )
            return redirect(next_url)
    else:
        form = FormClass(instance=movimiento)
        form = ensure_real_consumption_date_field(form)

    return render(request, 'facturacion/editar_movimiento.html', {
        'form': form,
        'movimiento': movimiento
    })


@login_required
@rol_requerido('admin')
@project_object_access_required(model='facturacion.CartolaMovimiento', object_kw='pk', project_attr='proyecto_id')
def eliminar_movimiento(request, pk):
    movimiento = get_object_or_404(CartolaMovimiento, pk=pk)

    if request.method == 'POST':
        movimiento.delete()
        messages.success(request, "Expense deleted successfully.")

        # 👇 RESPETAR FILTROS (next con todos los params)
        next_url = (
            request.GET.get('next')
            or request.POST.get('next')
            or request.META.get('HTTP_REFERER')
            or reverse('facturacion:listar_cartola')
        )
        return redirect(next_url)

    # GET: muestra la pantalla de confirmación
    return render(request, 'facturacion/eliminar_movimiento.html', {'movimiento': movimiento})


@login_required
@rol_requerido('facturacion', 'admin')
def listar_saldos_usuarios(request):
    cantidad = request.GET.get('cantidad', '5')

    # Estados según tu modelo (pendientes por etapa)
    USER_PENDING = ['pendiente_abono_usuario']
    SUP_PENDING  = ['pendiente_supervisor']
    PM_PENDING   = ['aprobado_supervisor']   # esperando PM
    FIN_PENDING  = ['aprobado_pm']           # esperando Finanzas

    # Constante decimal tipada
    DEC = DecimalField(max_digits=12, decimal_places=2)
    V0  = Value(Decimal('0.00'), output_field=DEC)

    # Sumas condicionadas (usar V0 en default)
    pend_user_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=USER_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_sup_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=SUP_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_sup_cargos = Sum(
        Case(
            When(Q(cargos__gt=0) & Q(status__in=SUP_PENDING), then=F('cargos')),
            default=V0, output_field=DEC,
        )
    )
    pend_pm_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=PM_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_pm_cargos = Sum(
        Case(
            When(Q(cargos__gt=0) & Q(status__in=PM_PENDING), then=F('cargos')),
            default=V0, output_field=DEC,
        )
    )
    pend_fin_abonos = Sum(
        Case(
            When(Q(abonos__gt=0) & Q(status__in=FIN_PENDING), then=F('abonos')),
            default=V0, output_field=DEC,
        )
    )
    pend_fin_cargos = Sum(
        Case(
            When(Q(cargos__gt=0) & Q(status__in=FIN_PENDING), then=F('cargos')),
            default=V0, output_field=DEC,
        )
    )

    # 🔒 Filtra por proyectos a los que el usuario tiene acceso
    base = CartolaMovimiento.objects.all()
    base = filter_queryset_by_access(base, request.user, 'proyecto_id')

    qs = (
        base
        .values('usuario__id', 'usuario__first_name', 'usuario__last_name', 'usuario__email')
        .annotate(
            # Totales base (Coalesce con V0 decimal)
            monto_rendido = Coalesce(Sum('cargos'), V0, output_field=DEC),
            monto_asignado = Coalesce(Sum('abonos'), V0, output_field=DEC),

            # Pendiente por usuario (solo abonos)
            pend_user = pend_user_abonos,

            # Parciales por etapa (abonos/cargos separados)
            _pend_sup_abonos = pend_sup_abonos,
            _pend_sup_cargos = pend_sup_cargos,
            _pend_pm_abonos  = pend_pm_abonos,
            _pend_pm_cargos  = pend_pm_cargos,
            _pend_fin_abonos = pend_fin_abonos,
            _pend_fin_cargos = pend_fin_cargos,
        )
        .annotate(
            # Combinar abonos+cargos en SQL (usa Coalesce(..., V0))
            pend_sup = ExpressionWrapper(
                Coalesce(F('_pend_sup_abonos'), V0, output_field=DEC) +
                Coalesce(F('_pend_sup_cargos'), V0, output_field=DEC),
                output_field=DEC,
            ),
            pend_pm = ExpressionWrapper(
                Coalesce(F('_pend_pm_abonos'), V0, output_field=DEC) +
                Coalesce(F('_pend_pm_cargos'), V0, output_field=DEC),
                output_field=DEC,
            ),
            pend_fin = ExpressionWrapper(
                Coalesce(F('_pend_fin_abonos'), V0, output_field=DEC) +
                Coalesce(F('_pend_fin_cargos'), V0, output_field=DEC),
                output_field=DEC,
            ),
            # Disponible: asignado - rendido (todo decimal)
            monto_disponible = ExpressionWrapper(
                Coalesce(F('monto_asignado'), V0, output_field=DEC) -
                Coalesce(F('monto_rendido'), V0, output_field=DEC),
                output_field=DEC,
            ),
        )
        .order_by('usuario__first_name', 'usuario__last_name')
    )

    paginator = Paginator(qs, qs.count() or 1) if cantidad == 'todos' else Paginator(qs, int(cantidad))
    pagina = paginator.get_page(request.GET.get('page'))

    return render(request, 'facturacion/listar_saldos_usuarios.html', {
        'saldos': pagina,
        'pagina': pagina,
        'cantidad': cantidad,
    })

from datetime import datetime, time, timedelta

from django.db import models
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.timezone import is_aware


def _parse_date_any(s: str):
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

@login_required
@rol_requerido('facturacion', 'admin')
def exportar_cartola(request):
    import xlwt
    params = request.GET

    du        = (params.get('du') or '').strip()
    fecha_str = (params.get('fecha') or '').strip()
    proyecto  = (params.get('proyecto') or '').strip()
    categoria = (params.get('categoria') or '').strip()
    tipo      = (params.get('tipo') or '').strip()
    estado    = (params.get('estado') or '').strip()
    rut       = (params.get('rut_factura') or '').strip()  # opcional

    # 🔒 Limitar a proyectos con acceso del usuario
    base = CartolaMovimiento.objects.all()
    base = filter_queryset_by_access(base, request.user, 'proyecto_id')

    movimientos = (
        base
        .select_related('usuario', 'proyecto', 'tipo')
        .order_by('-fecha')
    )

    # Usuario: igual que en listar_cartola (du => username/nombre/apellido)
    if du:
        movimientos = movimientos.filter(
            Q(usuario__username__icontains=du) |
            Q(usuario__first_name__icontains=du) |
            Q(usuario__last_name__icontains=du)
        )

    # Fecha: igual que en listar_cartola (día suelto o fecha completa; DateTime→rango)
    if fecha_str:
        if fecha_str.isdigit() and 1 <= int(fecha_str) <= 31:
            dia = int(fecha_str)
            movimientos = movimientos.filter(fecha__day=dia)
        else:
            f = _parse_date_any(fecha_str)
            if f:
                campo_fecha = CartolaMovimiento._meta.get_field('fecha')
                if isinstance(campo_fecha, models.DateTimeField):
                    tz = timezone.get_current_timezone()
                    start = timezone.make_aware(datetime.combine(f, time.min), tz)
                    end = start + timedelta(days=1)
                    movimientos = movimientos.filter(fecha__gte=start, fecha__lt=end)
                else:
                    movimientos = movimientos.filter(fecha=f)

    if proyecto:
        movimientos = movimientos.filter(proyecto__nombre__icontains=proyecto)
    if categoria:
        movimientos = movimientos.filter(tipo__categoria__icontains=categoria)
    if tipo:
        movimientos = movimientos.filter(tipo__nombre__icontains=tipo)
    if rut:
        movimientos = movimientos.filter(rut_factura__icontains=rut)
    if estado:
        movimientos = movimientos.filter(status=estado)

    # ----- Excel -----
    response = HttpResponse(content_type='application/vnd.ms-excel')
    now_str = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    response['Content-Disposition'] = f'attachment; filename="transactions_ledger_{now_str}.xls"'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Transactions')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    date_style   = xlwt.easyxf(num_format_str='DD-MM-YYYY')

    columns = [
        "User", "Date", "Project", "Real consumption date", "Category", "Type", "Remarks",
        "Transfer Number", "Odometer (km)", "Debits", "Credits", "Status"
    ]
    for col_num, title in enumerate(columns):
        ws.write(0, col_num, title, header_style)

    if not movimientos.exists():
        ws.write(1, 0, "Sin resultados para los filtros aplicados.")
        wb.save(response)
        return response

    for row_num, mov in enumerate(movimientos, start=1):
        # User
        ws.write(row_num, 0, str(mov.usuario))

        # Date → date naive para xlwt
        fecha_excel = getattr(mov, 'fecha', None)
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()
        ws.write(row_num, 1, fecha_excel, date_style)

        # Project
        ws.write(row_num, 2, str(getattr(mov, 'proyecto', '') or ''))

        # ✅ Real consumption date (nuevo)
        rcd = getattr(mov, 'real_consumption_date', None)
        if isinstance(rcd, datetime):
            if is_aware(rcd):
                rcd = rcd.astimezone().replace(tzinfo=None)
            rcd = rcd.date()
        if rcd:
            ws.write(row_num, 3, rcd, date_style)
        else:
            ws.write(row_num, 3, "")

        # Category / Type (protegido contra None)
        cat = (getattr(getattr(mov, 'tipo', None), 'categoria', '') or '')
        tipo_txt = str(getattr(mov, 'tipo', '') or '')
        ws.write(row_num, 4, str(cat).title())
        ws.write(row_num, 5, tipo_txt)

        # Remarks / Transfer
        ws.write(row_num, 6, mov.observaciones or "")
        ws.write(row_num, 7, mov.numero_transferencia or "")

        # Odometer
        try:
            ws.write(row_num, 8, float(mov.kilometraje) if mov.kilometraje is not None else "")
        except Exception:
            ws.write(row_num, 8, "")

        # Debits / Credits
        ws.write(row_num, 9, float(mov.cargos or 0))
        ws.write(row_num, 10, float(mov.abonos or 0))

        # Status (display)
        ws.write(row_num, 11, mov.get_status_display())

    wb.save(response)
    return response


@login_required
@rol_requerido('facturacion', 'admin')
def exportar_saldos(request):
    import xlwt
    from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
    from django.http import HttpResponse

    USER_PENDING = ['pendiente_usuario',
                    'pendiente_aprobacion_usuario', 'pendiente_abono_usuario']
    SUP_PENDING = ['pendiente_supervisor']
    PM_PENDING  = ['aprobado_supervisor', 'pendiente_pm']
    FIN_PENDING = ['aprobado_pm', 'pendiente_finanzas']

    def _sum_pending_abonos(status_list):
        return Sum(
            Case(
                When(Q(abonos__gt=0) & Q(status__in=status_list), then=F('abonos')),
                default=Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )

    def _sum_pending_cargos(status_list):
        return Sum(
            Case(
                When(Q(cargos__gt=0) & Q(status__in=status_list), then=F('cargos')),
                default=Value(0),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )

    # 🔒 Limitar a proyectos con acceso del usuario
    base = CartolaMovimiento.objects.all()
    base = filter_queryset_by_access(base, request.user, 'proyecto_id')

    balances = (
        base
        .values('usuario__first_name', 'usuario__last_name')
        .annotate(
            rendered_amount = Sum('cargos', default=0),
            assigned_amount = Sum('abonos', default=0),
            available_amount = Sum(F('abonos') - F('cargos'), default=0),

            pending_user = _sum_pending_abonos(USER_PENDING),

            sup_abonos = _sum_pending_abonos(SUP_PENDING),
            sup_cargos = _sum_pending_cargos(SUP_PENDING),

            pm_abonos  = _sum_pending_abonos(PM_PENDING),
            pm_cargos  = _sum_pending_cargos(PM_PENDING),

            fin_abonos = _sum_pending_abonos(FIN_PENDING),
            fin_cargos = _sum_pending_cargos(FIN_PENDING),
        )
        .order_by('usuario__first_name', 'usuario__last_name')
    )

    response = HttpResponse(content_type='application/octet-stream')
    response['Content-Disposition'] = 'attachment; filename="available_balances.xls"'
    response['X-Content-Type-Options'] = 'nosniff'

    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Available Balances')

    header_style = xlwt.easyxf('font: bold on; align: horiz center')
    currency_style = xlwt.easyxf(num_format_str='$#,##0.00')

    columns = [
        "User", "Amount Rendered", "Assigned Amount", "Available Amount",
        "Pending (User)", "Pending (Supervisor)", "Pending (PM)", "Pending (Finance)"
    ]
    for col, title in enumerate(columns):
        ws.write(0, col, title, header_style)

    for r, b in enumerate(balances, start=1):
        pend_sup = float((b['sup_abonos'] or 0) + (b['sup_cargos'] or 0))
        pend_pm  = float((b['pm_abonos']  or 0) + (b['pm_cargos']  or 0))
        pend_fin = float((b['fin_abonos'] or 0) + (b['fin_cargos'] or 0))

        ws.write(r, 0, f"{b['usuario__first_name']} {b['usuario__last_name']}")
        ws.write(r, 1, float(b['rendered_amount'] or 0), currency_style)
        ws.write(r, 2, float(b['assigned_amount'] or 0), currency_style)
        ws.write(r, 3, float(b['available_amount'] or 0), currency_style)
        ws.write(r, 4, float(b['pending_user'] or 0), currency_style)
        ws.write(r, 5, pend_sup, currency_style)
        ws.write(r, 6, pend_pm,  currency_style)
        ws.write(r, 7, pend_fin, currency_style)

    wb.save(response)
    return response


def _parse_decimal(val: str | None) -> Decimal | None:
    if val is None:
        return None
    s = (str(val) or "").strip().replace(",", "")
    if s == "":
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _can_edit_real_week(user) -> bool:
    """
    PM / Finance / Admin can edit the real pay week.
    Ajusta esto a tu lógica de roles si usas permisos/grupos.
    """
    try:
        from usuarios.utils import user_has_any_role  # opcional
        return user_has_any_role(user, ["pm", "facturacion", "admin"])
    except Exception:
        # Fallback: usa el decorador que ya aplicamos a la vista
        return True












def _limit_invoices_by_assignment_and_history(qs, user):
    """
    Lógica de historial igual que en producción admin, usando ProyectoAsignacion:

      - Toma las ProyectoAsignacion del usuario.
      - Si include_history=True -> ve TODO el historial de ese proyecto.
      - Si include_history=False y tiene start_at -> solo ve desde start_at.
      - Resultado: solo invoices de proyectos donde está asignado.

    Si el usuario no tiene asignaciones, devuelve qs.none().
    """
    asignaciones = ProyectoAsignacion.objects.filter(usuario=user)
    if not asignaciones.exists():
        return qs.none()

    cond = Q()
    for asig in asignaciones:
        pid_str = str(asig.proyecto_id)
        if asig.include_history or not asig.start_at:
            cond |= Q(proyecto_id=pid_str)
        else:
            cond |= Q(proyecto_id=pid_str, creado_en__gte=asig.start_at)

    return qs.filter(cond)


def _attach_project_label(page_obj):
    """
    Crea un atributo 'project_label' en cada SesionBilling de la página,
    usando la MISMA filosofía que en produccion_admin:

      - Usa Proyecto (id, código, nombre).
      - Intenta resolver primero por 'proyecto' (texto),
        luego por 'proyecto_id'.
      - Si encuentra Proyecto -> usa p.nombre.
      - Si no encuentra, hace fallback a s.proyecto o s.proyecto_id.
    """
    sessions = list(page_obj.object_list)
    if not sessions:
        return page_obj

    # --- Recolectamos textos/ids candidatos de esta página ---
    raw_texts = set()
    raw_ids = set()

    for s in sessions:
        proj_text = (getattr(s, "proyecto", "") or "").strip()
        proj_id = getattr(s, "proyecto_id", None)

        for raw in (proj_text, proj_id):
            if raw in (None, "", "-"):
                continue
            txt = str(raw).strip()
            if not txt:
                continue
            raw_texts.add(txt)
            try:
                raw_ids.add(int(txt))
            except (TypeError, ValueError):
                # no es un entero, puede ser código o nombre
                pass

    # Si no hay nada, solo ponemos fallback y salimos
    if not raw_texts and not raw_ids:
        for s in sessions:
            s.project_label = getattr(s, "proyecto", None) or getattr(s, "proyecto_id", "") or ""
        page_obj.object_list = sessions
        return page_obj

    # --- Cargamos solo los proyectos relevantes ---
    proj_q = Q()
    if raw_ids:
        proj_q |= Q(pk__in=raw_ids)
    if raw_texts:
        proj_q |= Q(codigo__in=raw_texts) | Q(nombre__in=raw_texts)

    proyectos = Proyecto.objects.filter(proj_q).distinct()

    by_id = {p.id: p for p in proyectos}
    by_code = {
        (p.codigo or "").strip().lower(): p
        for p in proyectos
        if getattr(p, "codigo", None)
    }
    by_name = {
        (p.nombre or "").strip().lower(): p
        for p in proyectos
        if getattr(p, "nombre", None)
    }

    def _resolve_for_session(s):
        proj_text = (getattr(s, "proyecto", "") or "").strip()
        proj_id = getattr(s, "proyecto_id", None)

        proyecto_sel = None

        # 1) intentar con proj_text (igual que en produccion_admin)
        if proj_text:
            try:
                pid = int(proj_text)
            except (TypeError, ValueError):
                key = proj_text.lower()
                proyecto_sel = by_code.get(key) or by_name.get(key)
            else:
                proyecto_sel = by_id.get(pid)

        # 2) si no, probar con proyecto_id
        if not proyecto_sel and proj_id not in (None, "", "-"):
            txt = str(proj_id).strip()
            try:
                pid2 = int(txt)
            except (TypeError, ValueError):
                key2 = txt.lower()
                proyecto_sel = by_code.get(key2) or by_name.get(key2)
            else:
                proyecto_sel = by_id.get(pid2)

        # 3) Construir label
        if proyecto_sel:
            return getattr(proyecto_sel, "nombre", str(proyecto_sel))

        # Fallback: lo que ya teníamos en la sesión
        if proj_text:
            return proj_text
        if proj_id not in (None, "", "-"):
            return str(proj_id)
        return ""

    for s in sessions:
        s.project_label = _resolve_for_session(s)

    page_obj.object_list = sessions
    return page_obj


@login_required
@rol_requerido("facturacion", "admin")
def invoices_list(request):
    """
    Finanzas:
      - 'open': todo lo que realmente está en Finanzas (incluye descuentos directos
                SOLO si fueron ENVIADOS -> finance_sent_at no nulo).
      - 'paid': solo pagados.
      - 'all' : todo lo de Finanzas (enviado, en revisión, pendiente, rechazado, pagado,
                y descuentos directos ENVIADOS). Excluye 'none', vacío y nulos.

    Visibilidad:
      - Usuarios normales: solo ven invoices de proyectos a los que tienen acceso
        (según ProyectoAsignacion / filter_queryset_by_access).
      - Usuarios privilegiados (superuser o es_usuario_historial): pueden ver TODO
        el historial de Finanzas (sin restringir por proyectos asignados).
    """
    user = request.user
    scope = request.GET.get("scope", "open")  # open | all | paid

    # ---------------- Usuarios privilegiados (historial completo) ----------------
    can_view_legacy_history = (
        user.is_superuser or
        getattr(user, "es_usuario_historial", False)
    )

    # ---------------- Proyectos visibles para el usuario ----------------
    try:
        proyectos_user = filter_queryset_by_access(
            Proyecto.objects.all(),
            user,
            "id",
        )
    except Exception:
        proyectos_user = Proyecto.objects.none()

    if proyectos_user.exists():
        allowed_keys = set()
        for p in proyectos_user:
            # nombre legible del proyecto
            nombre = (getattr(p, "nombre", "") or "").strip()
            if nombre:
                allowed_keys.add(nombre)

            # compatibilidad: código y id
            codigo = getattr(p, "codigo", None)
            if codigo:
                allowed_keys.add(str(codigo).strip())
            allowed_keys.add(str(p.id).strip())
    else:
        # sin proyectos asignados → no ve nada (para usuarios normales)
        allowed_keys = set()

    # -------------------- Query base + prefetch --------------------
    qs = (
        SesionBilling.objects
        .prefetch_related(
            Prefetch(
                "items",
                queryset=ItemBilling.objects.prefetch_related(
                    Prefetch(
                        "desglose_tecnico",
                        queryset=ItemBillingTecnico.objects.select_related("tecnico"),
                    )
                ),
            ),
            Prefetch(
                "tecnicos_sesion",
                queryset=SesionBillingTecnico.objects
                .select_related("tecnico")
                .prefetch_related(
                    Prefetch(
                        "evidencias",
                        queryset=EvidenciaFotoBilling.objects.only(
                            "id", "imagen", "tecnico_sesion_id", "requisito_id"
                        ).order_by("-id"),
                    )
                ),
            ),
        )
        .order_by("-creado_en")
    )

    # -------------------- Alcance Finanzas (open / all / paid) --------------------
    FINANCE_OPEN_BASE = ["discount_applied", "sent", "in_review", "pending", "rejected"]

    if scope == "paid":
        qs = qs.filter(finance_status="paid")

    elif scope == "all":
        qs = qs.exclude(
            Q(finance_status__in=["none", ""]) |
            Q(finance_status__isnull=True) |
            (Q(finance_status="review_discount") & Q(finance_sent_at__isnull=True))
        )

    else:  # "open"
        qs = qs.filter(
            Q(finance_status__in=FINANCE_OPEN_BASE) |
            (Q(finance_status="review_discount") & Q(finance_sent_at__isnull=False))
        ).exclude(finance_status="paid")

    # ---------------- 🔒 Limitar por proyectos asignados (solo usuarios NO historial) ----------------
    if not can_view_legacy_history:
        if allowed_keys:
            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            qs = SesionBilling.objects.none()

    # -------------------- Filtros por GET (ligeros) --------------------
    date_s = (request.GET.get("date") or "").strip()
    projid_s = (request.GET.get("projid") or "").strip()
    week_s = (request.GET.get("week") or "").strip()
    tech_s = (request.GET.get("tech") or "").strip()
    client_s = (request.GET.get("client") or "").strip()
    status_s = (request.GET.get("status") or "").strip().lower()

    # 🔹 nuevos filtros específicos
    f_finish = (request.GET.get("f_finish") or "").strip()
    f_projid = (request.GET.get("f_projid") or "").strip()
    f_client = (request.GET.get("f_client") or "").strip()

    # Date (YYYY-MM-DD). Si viene mal formado, se ignora.
    if date_s:
        try:
            d = datetime.strptime(date_s, "%Y-%m-%d").date()
            qs = qs.filter(creado_en__date=d)
        except ValueError:
            pass

    if projid_s:
        qs = qs.filter(proyecto_id__icontains=projid_s)

    if week_s:
        qs = qs.filter(semana_pago_proyectada__icontains=week_s)

    if tech_s:
        qs = qs.filter(
            Q(tecnicos_sesion__tecnico__first_name__icontains=tech_s) |
            Q(tecnicos_sesion__tecnico__last_name__icontains=tech_s) |
            Q(tecnicos_sesion__tecnico__username__icontains=tech_s)
        )

    if client_s:
        qs = qs.filter(cliente__icontains=client_s)

    # Status (palabras clave simples)
    if status_s:
        if "direct" in status_s or "descuento" in status_s:
            qs = qs.filter(is_direct_discount=True)
        elif ("supervisor" in status_s) and ("aprob" in status_s or "approved" in status_s):
            qs = qs.filter(estado="aprobado_supervisor")
        elif ("pm" in status_s) and ("aprob" in status_s or "approved" in status_s):
            qs = qs.filter(estado="aprobado_pm")
        elif "rechaz" in status_s or "rejected" in status_s:
            qs = qs.filter(estado__startswith="rechazado")
        elif "review" in status_s or "revisi" in status_s:
            qs = qs.filter(estado="en_revision_supervisor")
        elif "finished" in status_s or "finaliz" in status_s:
            qs = qs.filter(estado="finalizado")
        elif "progress" in status_s or "proceso" in status_s:
            qs = qs.filter(estado="en_proceso")
        elif "assigned" in status_s or "asignado" in status_s:
            qs = qs.filter(estado="asignado")

    # 🔹 Nuevo: filtro por fecha de finalización (finance_end_date, formato YYYY-MM-DD)
    if f_finish:
        try:
            d_fin = datetime.strptime(f_finish, "%Y-%m-%d").date()
            qs = qs.filter(finance_end_date__date=d_fin)
        except ValueError:
            pass

    # 🔹 Nuevo: filtro por Project ID (extra, además del viejo 'projid')
    if f_projid:
        qs = qs.filter(proyecto_id__icontains=f_projid)

    # 🔹 Nuevo: filtro por Client (extra, además del viejo 'client')
    if f_client:
        qs = qs.filter(cliente__icontains=f_client)

    # Evitar duplicados por joins con tecnicos_sesion/items
    qs = qs.distinct()

    # -------------------- Paginación --------------------
    raw_cantidad = request.GET.get("cantidad", "10")
    page_number = request.GET.get("page")

    MAX_PAGE_SIZE = 100  # 👈 límite duro

    if raw_cantidad == "todos":
        # si viene "todos", en realidad mostramos como máximo 100
        per_page = MAX_PAGE_SIZE
        cantidad = "todos"
    else:
        try:
            per_page = int(raw_cantidad)
            cantidad = raw_cantidad
        except Exception:
            per_page = 10
            cantidad = "10"

        # mínimos / máximos
        if per_page < 5:
            per_page = 5
        if per_page > MAX_PAGE_SIZE:
            per_page = MAX_PAGE_SIZE

    pagina = Paginator(qs, per_page).get_page(page_number)

    # Adjuntamos etiqueta legible de proyecto (project_label) a cada sesión
    pagina = _attach_project_label(pagina)

    ctx = {
        "pagina": pagina,
        "cantidad": cantidad,
        "scope": scope,
        "can_edit_real_week": _can_edit_real_week(request.user),

        # para rellenar inputs y reconstruir enlaces
        "date_s": date_s,
        "projid_s": projid_s,
        "week_s": week_s,
        "tech_s": tech_s,
        "client_s": client_s,
        "status_s": status_s,

        # nuevos filtros (para el template)
        "f_finish": f_finish,
        "f_projid": f_projid,
        "f_client": f_client,
    }
    return render(request, "facturacion/invoices_list.html", ctx)

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST


@require_POST
def invoice_update_real(request, pk):
    # Solo AJAX
    if request.headers.get('x-requested-with') != 'XMLHttpRequest':
        return HttpResponseForbidden('AJAX only')

    s = get_object_or_404(SesionBilling, pk=pk)

    real_raw  = request.POST.get('real', None)
    week_raw  = request.POST.get('week', None)
    daily_raw = request.POST.get('daily_number', None)

    with transaction.atomic():
        updated_fields = []

        # ----- Real Company Billing -----
        if real_raw is not None:
            raw = (real_raw or '').strip()

            # Vacío o guiones => NULL en DB
            if raw in ('', '-', '—', 'null', 'None'):
                s.real_company_billing = None
                updated_fields.append('real_company_billing')
            else:
                # normaliza $ , espacios y miles
                txt = raw.replace('$', '').replace(',', '').replace(' ', '')
                try:
                    s.real_company_billing = Decimal(txt)
                    updated_fields.append('real_company_billing')
                    # si estaba “sent/in_review” y ahora hay número => pending
                    if s.finance_status in ('sent', 'in_review'):
                        s.finance_status = 'pending'
                        updated_fields.append('finance_status')
                except (InvalidOperation, ValueError):
                    return JsonResponse({'error': 'Invalid amount.'}, status=400)

        # ----- Real pay week (permite vacío) -----
        if week_raw is not None:
            s.semana_pago_real = (week_raw or '').strip()
            updated_fields.append('semana_pago_real')

        # ----- Daily Number (permite vacío) -----
        if daily_raw is not None:
            value = (daily_raw or '').strip()
            s.finance_daily_number = value or None
            updated_fields.append('finance_daily_number')

        # Guardar solo si algo cambió
        if updated_fields:
            updated_fields.append('finance_updated_at')  # tu campo auto_now
            s.save(update_fields=updated_fields)

    # difference solo si hay real
    diff = None
    if s.real_company_billing is not None:
        diff = (s.subtotal_empresa or Decimal('0')) - s.real_company_billing

    return JsonResponse({
        'ok': True,
        'real': (
            None
            if s.real_company_billing is None
            else f'{s.real_company_billing:.2f}'
        ),
        'week': s.semana_pago_real or '',
        'daily_number': s.finance_daily_number or '',
        'difference': '' if diff is None else f'{diff:.2f}',
        'finance_status': s.finance_status,
    })

@login_required
@rol_requerido("facturacion", "admin")
def invoice_mark_paid(request, pk: int):
    """
    Mark invoice as Paid.
    - Requires real_company_billing not null
    - If difference > 0 (we receive less than company billing), require client-side confirmation (force=1)
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    s = get_object_or_404(SesionBilling, pk=pk)
    if s.real_company_billing is None:
        return HttpResponseBadRequest("Real Company Billing is required before marking as paid.")

    difference = (s.subtotal_empresa or Decimal("0")) - s.real_company_billing
    force = (request.POST.get("force") or "") == "1"

    if difference > 0 and not force:
        return JsonResponse({
            "ok": False,
            "confirm": True,
            "message": "You are collecting less than expected. Do you still want to mark it as paid?"
        }, status=409)

    with transaction.atomic():
        s.finance_status = "paid"
        if not s.semana_pago_real:
            y, w, _ = timezone.localdate().isocalendar()
            s.semana_pago_real = f"{y}-W{int(w):02d}"
        s.save(update_fields=["finance_status", "semana_pago_real"])

    return JsonResponse({"ok": True})


@login_required
@rol_requerido("facturacion", "admin")
def invoice_reject(request, pk: int):
    """
    Reject invoice back to Operations with a reason.
    Sets finance_status='rejected' and stores the note.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        return HttpResponseBadRequest("A rejection reason is required.")

    s = get_object_or_404(SesionBilling, pk=pk)
    with transaction.atomic():
        s.finance_status = "rejected"
        s.finance_note = reason
        s.save(update_fields=["finance_status", "finance_note"])

    return JsonResponse({"ok": True})


@login_required
@rol_requerido("facturacion", "admin")
@require_POST
@transaction.atomic
def invoice_remove(request, pk: int):
    """
    Saca la sesión de la cola de Finanzas (NO borra el billing).

    Reglas:
    - Si es descuento directo -> vuelve a 'review_discount' (se mantiene visible en Billing).
    - Si NO es descuento directo -> vuelve a 'none'.
    - Si está 'paid' -> no permitir remover.
    """
    # Trae solo lo necesario y evita invocar save() (usaremos .update)
    s = (
        SesionBilling.objects
        .only("id", "is_direct_discount", "finance_status")
        .filter(pk=pk)
        .first()
    )

    # Not found
    if not s:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Not found"}, status=404)
        messages.error(request, "Billing not found.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("facturacion:invoices"))

    # No se puede remover si ya está pagado
    if s.finance_status == "paid":
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Already paid"}, status=409)
        messages.error(
            request, "This billing is already paid and cannot be removed from Finance.")
        return redirect(request.META.get("HTTP_REFERER") or reverse("facturacion:invoices"))

    # Estado de retorno según sea descuento directo o no
    new_status = "review_discount" if s.is_direct_discount else "none"

    # Actualización atómica y sin disparar save()
    SesionBilling.objects.filter(pk=s.pk).update(
        finance_status=new_status,
        finance_note="",
        finance_sent_at=None,
        finance_updated_at=timezone.now(),
    )

    # Respuesta
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "id": s.pk, "finance_status": new_status})

    if new_status == "review_discount":
        messages.success(
            request, f"Direct discount #{s.pk} returned to Operations.")
    else:
        messages.success(
            request, f"Billing #{s.pk} removed from Finance queue.")
    return redirect(request.META.get("HTTP_REFERER") or reverse("facturacion:invoices"))


@rol_requerido('facturacion', 'admin', 'pm')
@require_POST
def invoice_discount_verified(request, pk):
    s = get_object_or_404(SesionBilling, pk=pk)

    if not s.is_direct_discount:
        return JsonResponse({"ok": False, "error": "NOT_DIRECT_DISCOUNT"}, status=400)

    note = (request.POST.get("note") or "").strip()

    # Primer estado de descuentos → discount_applied
    s.finance_status = "discount_applied"
    if note:
        # apendea (igual que en otros flujos)
        prefix = f"{timezone.now():%Y-%m-%d %H:%M} Finance: "
        s.finance_note = ((s.finance_note + "\n")
                          if s.finance_note else "") + prefix + note

    s.save(update_fields=[
        "finance_status",
        *(["finance_note"] if note else []),
        "finance_updated_at",  # auto_now se actualiza
    ])

    return JsonResponse({"ok": True, "finance_status": s.finance_status})


def _to_excel_dt(value):
    if not value:
        return ""
    try:
        return timezone.make_naive(value) if timezone.is_aware(value) else value
    except Exception:
        return value


@rol_requerido('facturacion', 'admin', 'pm')
def invoices_export(request):
    """
    Exporta a Excel los invoices de Finanzas.

    - Respeta el scope: open | paid | all (igual que invoices_list).
    - Usa la MISMA lógica de visibilidad por proyecto que invoices_list:
        * Usuarios normales: solo proyectos a los que están asignados.
        * Usuarios con historial (is_superuser o es_usuario_historial): ven todo.
    - En la columna 'Project' se escribe el NOMBRE del Proyecto cuando se puede resolver.
    """
    from decimal import Decimal

    from django.db.models import Q
    from django.http import HttpResponse
    from django.utils import timezone
    from openpyxl import Workbook

    from facturacion.models import Proyecto

    user = request.user
    scope = request.GET.get("scope", "open")  # open | all | paid

    # --------- Usuarios privilegiados (historial completo) ---------
    can_view_legacy_history = (
        user.is_superuser or
        getattr(user, "es_usuario_historial", False)
    )

    # --------- Proyectos visibles para el usuario (igual que invoices_list) ---------
    try:
        proyectos_user = filter_queryset_by_access(
            Proyecto.objects.all(),
            user,
            "id",
        )
    except Exception:
        proyectos_user = Proyecto.objects.none()

    proyectos_list = list(proyectos_user)

    if proyectos_list:
        allowed_keys = set()
        for p in proyectos_list:
            # nombre legible del proyecto
            nombre = (getattr(p, "nombre", "") or "").strip()
            if nombre:
                allowed_keys.add(nombre)

            # compatibilidad: código y id
            codigo = getattr(p, "codigo", None)
            if codigo:
                allowed_keys.add(str(codigo).strip())
            allowed_keys.add(str(p.id).strip())
    else:
        allowed_keys = set()

    # --------- Query base + prefetch (igual patrón que invoices_list) ---------
    qs = (
        SesionBilling.objects
        .prefetch_related(
            Prefetch(
                "items",
                queryset=ItemBilling.objects.prefetch_related(
                    Prefetch(
                        "desglose_tecnico",
                        queryset=ItemBillingTecnico.objects.select_related("tecnico"),
                    )
                ),
            ),
            Prefetch(
                "tecnicos_sesion",
                queryset=SesionBillingTecnico.objects
                .select_related("tecnico")
                .prefetch_related(
                    Prefetch(
                        "evidencias",
                        queryset=EvidenciaFotoBilling.objects.only(
                            "id", "imagen", "tecnico_sesion_id", "requisito_id"
                        ).order_by("-id"),
                    )
                ),
            ),
        )
        .order_by("-creado_en")
    )

    # --------- Alcance Finanzas (open / all / paid) – igual que invoices_list ---------
    FINANCE_OPEN_BASE = ["discount_applied", "sent", "in_review", "pending", "rejected"]

    if scope == "paid":
        qs = qs.filter(finance_status="paid")

    elif scope == "all":
        qs = qs.exclude(
            Q(finance_status__in=["none", ""]) |
            Q(finance_status__isnull=True) |
            (Q(finance_status="review_discount") & Q(finance_sent_at__isnull=True))
        )

    else:  # "open"
        qs = qs.filter(
            Q(finance_status__in=FINANCE_OPEN_BASE) |
            (Q(finance_status="review_discount") & Q(finance_sent_at__isnull=False))
        ).exclude(finance_status="paid")

    # --------- 🔒 Limitar por proyectos asignados (solo usuarios NO historial) ---------
    if not can_view_legacy_history:
        if allowed_keys:
            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            qs = SesionBilling.objects.none()

    # No usamos filtros adicionales por GET aquí (date, projid, etc.),
    # pero si quieres se pueden copiar de invoices_list.

    qs = qs.distinct()

    # --------- Mapas de Proyecto para obtener el nombre ---------
    # Para poner en la columna "Project" el nombre del Proyecto
    # cuando es posible resolverlo.
    if can_view_legacy_history:
        # Para usuarios de historial, intentamos tener todos los proyectos
        proyectos_all = Proyecto.objects.all()
        proyectos_list = list(proyectos_all)
    # si no es historial, ya tenemos proyectos_list con los asignados

    by_id = {p.id: p for p in proyectos_list}
    by_code = {
        (p.codigo or "").strip().lower(): p
        for p in proyectos_list
        if getattr(p, "codigo", None)
    }
    by_name = {
        (p.nombre or "").strip().lower(): p
        for p in proyectos_list
        if getattr(p, "nombre", None)
    }

    def _resolve_project_label(s):
        """
        Devuelve el nombre legible del proyecto para el Excel:

        - Si SesionBilling.proyecto guarda ID numérico → busca en by_id.
        - Si guarda nombre/código → intenta mapear a Proyecto.
        - Si no encuentra nada, devuelve el texto original o el project_id.
        """
        proj_text = (getattr(s, "proyecto", "") or "").strip()
        proj_id = getattr(s, "proyecto_id", None)

        proyecto_sel = None

        # 1) intentar interpretar proj_text como PK
        if proj_text:
            try:
                pid = int(proj_text)
            except (TypeError, ValueError):
                key = proj_text.lower()
                proyecto_sel = by_code.get(key) or by_name.get(key)
            else:
                proyecto_sel = by_id.get(pid)

        # 2) intentar con proyecto_id (NB6790, etc.)
        if not proyecto_sel and proj_id not in (None, "", "-"):
            try:
                pid2 = int(proj_id)
            except (TypeError, ValueError):
                key2 = str(proj_id).strip().lower()
                proyecto_sel = by_code.get(key2) or by_name.get(key2)
            else:
                proyecto_sel = by_id.get(pid2)

        if proyecto_sel:
            return getattr(proyecto_sel, "nombre", str(proyecto_sel))

        if proj_text:
            return proj_text
        if proj_id not in (None, "", "-"):
            return str(proj_id)
        return ""

    # --------- Mapas de estado (igual que el export anterior) ---------
    status_map = {
        "aprobado_pm": "Approved by PM",
        "rechazado_pm": "Rejected by PM",
        "aprobado_supervisor": "Approved by supervisor",
        "rechazado_supervisor": "Rejected by supervisor",
        "en_revision_supervisor": "In supervisor review",
        "finalizado": "Finished (pending review)",
        "en_proceso": "In progress",
        "asignado": "Assigned",
    }
    finance_map = {
        "none": "—",
        "review_discount": "Review discount",
        "discount_applied": "Discount applied",
        "sent": "Sent to client",
        "pending": "Pending payment",
        "in_review": "In review",
        "rejected": "Rejected",
        "paid": "Paid",
    }

    def techs_string(sesion):
        parts = []
        for st in sesion.tecnicos_sesion.all():
            tech = st.tecnico
            name = (tech.get_full_name() or tech.username) if tech else "—"
            parts.append(f"{name} ({st.porcentaje:.2f}%)")
        return ", ".join(parts)

    # --------- Crear Excel ---------
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"

    headers = [
        "Date", "Project ID", "Project address", "Projected week",
        "Status", "Technicians", "Client", "City", "Project", "Office",
        "Technical Billing", "Company Billing",
        "Daily Number", "Finish date", "Real Company Billing",
        "Difference", "Finance status", "Finance note",
        "Pay week / Discount week",
        "Job Code", "Work Type", "Description", "UOM", "Quantity",
        "Technical Rate", "Company Rate", "Subtotal Technical", "Subtotal Company",
    ]
    ws.append(headers)

    for s in qs:
        status_label = "Direct discount" if getattr(
            s, "is_direct_discount", False) else status_map.get(s.estado, "Assigned")
        finance_label = finance_map.get(s.finance_status, "—")

        real_week = s.semana_pago_real or ""
        disc_week = getattr(s, "discount_week", "") or getattr(
            s, "semana_descuento", "") or ""
        pay_or_disc = f"{real_week} / {disc_week}" if (
            real_week and disc_week) else (real_week or disc_week)

        project_label = _resolve_project_label(s)

        head_common = [
            _to_excel_dt(s.creado_en),
            s.proyecto_id,
            s.direccion_proyecto,
            s.semana_pago_proyectada or "",
            status_label,
            techs_string(s),
            s.cliente or "",
            s.ciudad or "",
            project_label or "",
            s.oficina or "",
            float(s.subtotal_tecnico or 0),
            float(s.subtotal_empresa or 0),
            (s.finance_daily_number or ""),
            _to_excel_dt(s.finance_finish_date) if getattr(s, "finance_finish_date", None) else "",
            float(s.real_company_billing or 0),
            float((s.diferencia or 0)),
            finance_label,
            (s.finance_note or ""),
            pay_or_disc,
        ]

        items = getattr(s, "items", None).all() if hasattr(s, "items") else []
        if not items:
            ws.append(head_common + ["", "", "", "", 0.0, 0.0, 0.0, 0.0, 0.0])
            continue

        for it in items:
            qty_total = Decimal(str(it.cantidad or 0))
            comp_rate = Decimal(str(it.precio_empresa or 0))
            desglose = list(getattr(it, "desglose_tecnico", []).all()) if hasattr(
                it, "desglose_tecnico") else []

            if desglose:
                for bd in desglose:
                    pct = Decimal(str(bd.porcentaje or 0)) / Decimal('100')
                    qty_tec = (qty_total * pct)
                    base_rate = Decimal(str(getattr(bd, "tarifa_base", 0) or 0))
                    sub_tec = base_rate * qty_tec
                    sub_comp = comp_rate * qty_tec

                    row = head_common + [
                        it.codigo_trabajo or "",
                        it.tipo_trabajo or "",
                        it.descripcion or "",
                        it.unidad_medida or "",
                        float(qty_tec),
                        float(base_rate),
                        float(comp_rate),
                        float(sub_tec),
                        float(sub_comp),
                    ]
                    ws.append(row)
            else:
                sub_tec_item = Decimal(str(it.subtotal_tecnico or 0))
                sub_comp_item = Decimal(
                    str(it.subtotal_empresa or (comp_rate * qty_total)))
                row = head_common + [
                    it.codigo_trabajo or "",
                    it.tipo_trabajo or "",
                    it.descripcion or "",
                    it.unidad_medida or "",
                    float(qty_total),
                    float(Decimal("0.00")),  # sin desglose no hay rate técnico
                    float(comp_rate),
                    float(sub_tec_item),
                    float(sub_comp_item),
                ]
                ws.append(row)

    now_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    resp = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    resp["Content-Disposition"] = f'attachment; filename="invoices_{now_str}.xlsx"'
    wb.save(resp)
    return resp