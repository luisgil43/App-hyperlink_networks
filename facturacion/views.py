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
    import re
    from datetime import datetime as py_datetime

    from django.contrib import messages
    from django.core.paginator import Paginator
    from django.db.models import Case, CharField, IntegerField, Q, Value, When
    from django.db.models.functions import Cast

    # --- Cantidad/paginación (mantén string para la UI)
    cantidad_param = request.GET.get('cantidad', '10')

    # Limitamos a máx. 100 (y "todos" también se interpreta como 100)
    if cantidad_param == 'todos':
        page_size = 100
    else:
        try:
            page_size = max(5, min(int(cantidad_param), 100))
        except ValueError:
            page_size = 10
            cantidad_param = '10'

    # ---------- Filtros tipo Excel recibidos por GET ----------
    params = request.GET.copy()
    excel_filters_raw = (params.get('excel_filters') or '').strip()
    try:
        excel_filters = json.loads(excel_filters_raw) if excel_filters_raw else {}
    except json.JSONDecodeError:
        excel_filters = {}

    # --- Filtros (string trimming)
    usuario = (request.GET.get('usuario') or '').strip()
    fecha_txt = (request.GET.get('fecha') or '').strip()
    proyecto = (request.GET.get('proyecto') or '').strip()
    categoria = (request.GET.get('categoria') or '').strip()
    tipo = (request.GET.get('tipo') or '').strip()
    rut_factura = (request.GET.get('rut_factura') or '').strip()
    estado = (request.GET.get('estado') or '').strip()

    # --- Base queryset (✅ FIX PERFORMANCE: evita N+1 con select_related)
    movimientos = (
        CartolaMovimiento.objects
        .select_related(
            "usuario",
            "proyecto",
            "tipo",
            "aprobado_por_supervisor",
            "aprobado_por_pm",
            "aprobado_por_finanzas",
        )
    )

    # Anotar fecha como texto ISO (YYYY-MM-DD...) para poder usar icontains
    movimientos = movimientos.annotate(
        fecha_iso=Cast('fecha', CharField())
    )

    # --- Filtro usuario: username / nombres / apellidos
    if usuario:
        movimientos = movimientos.filter(
            Q(usuario__username__icontains=usuario) |
            Q(usuario__first_name__icontains=usuario) |
            Q(usuario__last_name__icontains=usuario)
        )

    # --- Filtro fecha (permite parcial: DD, DD-MM, DD-MM-YYYY)
    if fecha_txt:
        # Permitimos 08-12-2025, 08/12/2025, 8-12, 29, etc.
        fecha_normalizada = fecha_txt.replace('/', '-').strip()

        # Patrones permitidos: "D", "DD", "DD-MM", "DD-M", "DD-MM-YYYY"
        m = re.match(r'^(\d{1,2})(?:-(\d{1,2}))?(?:-(\d{1,4}))?$', fecha_normalizada)
        if m:
            dia_str = m.group(1)
            mes_str = m.group(2)
            anio_str = m.group(3)

            try:
                q_fecha = Q()

                # Siempre filtramos por día
                dia = int(dia_str)
                q_fecha &= Q(fecha__day=dia)

                # Si hay mes, también filtramos por mes
                if mes_str:
                    mes = int(mes_str)
                    q_fecha &= Q(fecha__month=mes)

                # Si hay año completo (4 dígitos), filtramos por año
                if anio_str and len(anio_str) == 4:
                    anio = int(anio_str)
                    q_fecha &= Q(fecha__year=anio)

                movimientos = movimientos.filter(q_fecha)

            except ValueError:
                messages.warning(
                    request,
                    "Formato de fecha inválido. Use DD, DD-MM o DD-MM-YYYY."
                )
        else:
            messages.warning(
                request,
                "Formato de fecha inválido. Use DD, DD-MM o DD-MM-YYYY."
            )

    # --- Otros filtros
    if proyecto:
        movimientos = movimientos.filter(proyecto__nombre__icontains=proyecto)
    if categoria:
        movimientos = movimientos.filter(tipo__categoria__icontains=categoria)
    if tipo:
        movimientos = movimientos.filter(tipo__nombre__icontains=tipo)
    if rut_factura:
        movimientos = movimientos.filter(rut_factura__icontains=rut_factura)
    if estado:
        movimientos = movimientos.filter(status=estado)

    # ✅ SOLO MOSTRAR: pendientes por finanzas + aprobados por finanzas
    movimientos = movimientos.filter(status__in=['aprobado_pm', 'aprobado_finanzas'])

    # --- ORDEN personalizado (se mantiene igual)
    movimientos = movimientos.annotate(
        prioridad=Case(
            When(status='aprobado_pm', then=Value(0)),
            When(status__startswith='pendiente', then=Value(1)),
            When(status__startswith='rechazado', then=Value(2)),
            When(status__startswith='aprobado', then=Value(3)),
            default=Value(4),
            output_field=IntegerField(),
        )
    ).order_by('prioridad', '-fecha')

    # ============================================================
    #  Traemos TODO a memoria:
    #  - aplicar filtros Excel (Python)
    #  - construir excel_global (todos los registros filtrados)
    #  - luego paginar
    # ============================================================
    movimientos_list = list(movimientos)

    # Helpers de formato CLP (para que los valores del panel se vean igual que la tabla)
    def format_clp(n):
        try:
            n = 0 if n is None else n
            n_int = int(n)
            return f"${n_int:,}".replace(",", ".")
        except Exception:
            return "$0"

    # ---------- Aplicar filtros Excel ----------
    if excel_filters:
        def matches_excel_filters(m):
            for col, values in excel_filters.items():
                if not values:
                    continue
                values_set = set(values)

                # índices según tabla GZ:
                # 0 Usuario
                # 1 Fecha
                # 2 Fecha real del gasto
                # 3 Proyecto
                # 4 Categoría
                # 5 Tipo
                # 6 RUT factura
                # 7 Tipo de documento
                # 8 N° Documento
                # 9 Observaciones
                # 10 N° Transferencia
                # 11 Comprobante
                # 12 Cargos
                # 13 Abonos
                # 14 Status
                if col == "0":
                    label = str(m.usuario) if m.usuario else ""

                elif col == "1":
                    d = getattr(m, "fecha", None)
                    label = d.strftime("%d-%m-%Y") if d else ""

                elif col == "2":
                    d = getattr(m, "fecha_transaccion", None) or getattr(m, "fecha", None)
                    label = d.strftime("%d-%m-%Y") if d else ""

                elif col == "3":
                    label = str(m.proyecto) if m.proyecto else ""

                elif col == "4":
                    if m.tipo and getattr(m.tipo, "categoria", None):
                        label = (m.tipo.categoria or "").title()
                    else:
                        label = ""

                elif col == "5":
                    label = str(m.tipo) if m.tipo else ""

                elif col == "6":
                    label = (getattr(m, "rut_factura", None) or "—").strip() or "—"

                elif col == "7":
                    label = (getattr(m, "tipo_doc", None) or "—").strip() or "—"

                elif col == "8":
                    label = (getattr(m, "numero_doc", None) or "—").strip() or "—"

                elif col == "9":
                    label = (getattr(m, "observaciones", None) or "").strip()

                elif col == "10":
                    label = (getattr(m, "numero_transferencia", None) or "—").strip() or "—"

                elif col == "11":
                    label = "Ver" if getattr(m, "comprobante", None) else "—"

                elif col == "12":
                    label = format_clp(getattr(m, "cargos", 0) or 0)

                elif col == "13":
                    label = format_clp(getattr(m, "abonos", 0) or 0)

                elif col == "14":
                    label = m.get_status_display() if getattr(m, "status", None) else ""

                else:
                    continue

                if label not in values_set:
                    return False

            return True

        movimientos_list = [m for m in movimientos_list if matches_excel_filters(m)]

    # ---------- Distinct globales para filtros tipo Excel ----------
    excel_global = {}

    # 0 Usuario
    excel_global[0] = sorted({str(m.usuario) for m in movimientos_list if m.usuario})

    # 1 Fecha
    excel_global[1] = sorted({
        m.fecha.strftime("%d-%m-%Y")
        for m in movimientos_list
        if getattr(m, "fecha", None)
    })

    # 2 Fecha real del gasto
    excel_global[2] = sorted({
        (getattr(m, "fecha_transaccion", None) or getattr(m, "fecha", None)).strftime("%d-%m-%Y")
        for m in movimientos_list
        if (getattr(m, "fecha_transaccion", None) or getattr(m, "fecha", None))
    })

    # 3 Proyecto
    excel_global[3] = sorted({str(m.proyecto) for m in movimientos_list if m.proyecto})

    # 4 Categoría
    excel_global[4] = sorted({
        (m.tipo.categoria or "").title()
        for m in movimientos_list
        if m.tipo and getattr(m.tipo, "categoria", None)
    })

    # 5 Tipo
    excel_global[5] = sorted({str(m.tipo) for m in movimientos_list if m.tipo})

    # 6 RUT factura
    excel_global[6] = sorted({
        (getattr(m, "rut_factura", None) or "—").strip() or "—"
        for m in movimientos_list
    })

    # 7 Tipo doc
    excel_global[7] = sorted({
        (getattr(m, "tipo_doc", None) or "—").strip() or "—"
        for m in movimientos_list
    })

    # 8 N° doc
    excel_global[8] = sorted({
        (getattr(m, "numero_doc", None) or "—").strip() or "—"
        for m in movimientos_list
    })

    # 9 Observaciones
    excel_global[9] = sorted({
        (getattr(m, "observaciones", None) or "").strip()
        for m in movimientos_list
    })

    # 10 N° transferencia
    excel_global[10] = sorted({
        (getattr(m, "numero_transferencia", None) or "—").strip() or "—"
        for m in movimientos_list
    })

    # 11 Comprobante
    excel_global[11] = sorted({
        "Ver" if getattr(m, "comprobante", None) else "—"
        for m in movimientos_list
    })

    # 12 Cargos
    excel_global[12] = sorted({
        format_clp(getattr(m, "cargos", 0) or 0)
        for m in movimientos_list
    })

    # 13 Abonos
    excel_global[13] = sorted({
        format_clp(getattr(m, "abonos", 0) or 0)
        for m in movimientos_list
    })

    # 14 Status (display)
    estado_map = dict(CartolaMovimiento.ESTADOS)
    status_codes = {m.status for m in movimientos_list if getattr(m, "status", None)}
    excel_global[14] = sorted(estado_map.get(c, c) for c in status_codes)

    excel_global_json = json.dumps(excel_global)

    # --- Paginación (después de filtros Excel)
    paginator = Paginator(movimientos_list, page_size)
    page_number = request.GET.get('page')
    pagina = paginator.get_page(page_number)

    # --- Estado choices y eco de filtros a la plantilla
    estado_choices = CartolaMovimiento.ESTADOS
    filtros = {
        'usuario': usuario,
        'fecha': fecha_txt,
        'proyecto': proyecto,
        'categoria': categoria,
        'tipo': tipo,
        'rut_factura': rut_factura,
        'estado': estado,
    }

    # qs helpers (igual estilo Hyperlink, por si luego quieres usar)
    params_no_page = params.copy()
    params_no_page.pop('page', None)
    base_qs = params_no_page.urlencode()
    full_qs = params.urlencode()

    return render(
        request,
        'facturacion/listar_cartola.html',
        {
            'pagina': pagina,
            'cantidad': cantidad_param,
            'estado_choices': estado_choices,
            'filtros': filtros,
            'excel_global_json': excel_global_json,
            'base_qs': base_qs,
            'full_qs': full_qs,
        }
    )


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
@rol_requerido("admin")
@require_POST
def toggle_tipo(request, pk):
    """
    Activa/Desactiva un TipoGasto usando is_active.
    - POST only
    - Si es AJAX: devuelve html del tbody actualizado
    - Si no: redirect a crear_tipo
    """
    tipo = get_object_or_404(TipoGasto, pk=pk)
    tipo.is_active = not tipo.is_active
    tipo.save(update_fields=["is_active"])

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        tipos = TipoGasto.objects.all().order_by("-id")
        html = render_to_string(
            "facturacion/partials/tipo_gasto_table.html",
            {"tipos": tipos},
            request=request,
        )
        return JsonResponse(
            {
                "success": True,
                "html": html,
                "is_active": tipo.is_active,
                "msg": f'Expense type "{tipo.nombre}" {"activated" if tipo.is_active else "deactivated"} successfully.',
            }
        )

    messages.success(
        request,
        f'Expense type "{tipo.nombre}" {"activated" if tipo.is_active else "deactivated"} successfully.',
    )
    return redirect("facturacion:crear_tipo")


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
    from decimal import Decimal

    from django.core.paginator import Paginator
    from django.db.models import (Case, DecimalField, ExpressionWrapper, F, Q,
                                  Sum, Value, When)
    from django.db.models.functions import Coalesce

    from core.permissions import filter_queryset_by_assignment_history

    from .models import CartolaMovimiento

    cantidad = request.GET.get('cantidad', '5')

    USER_PENDING = ['pendiente_abono_usuario']
    SUP_PENDING  = ['pendiente_supervisor']
    PM_PENDING   = ['aprobado_supervisor']
    FIN_PENDING  = ['aprobado_pm']

    DEC = DecimalField(max_digits=12, decimal_places=2)
    V0  = Value(Decimal('0.00'), output_field=DEC)

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

    base = CartolaMovimiento.objects.all()

    # ✅ BLINDADO: proyecto + include_history/start_at también para agregados
    base = filter_queryset_by_assignment_history(
        base,
        request.user,
        project_field="proyecto_id",
        date_field="fecha",
    )

    qs = (
        base
        .values('usuario__id', 'usuario__first_name', 'usuario__last_name', 'usuario__email')
        .annotate(
            monto_rendido = Coalesce(Sum('cargos'), V0, output_field=DEC),
            monto_asignado = Coalesce(Sum('abonos'), V0, output_field=DEC),

            pend_user = pend_user_abonos,

            _pend_sup_abonos = pend_sup_abonos,
            _pend_sup_cargos = pend_sup_cargos,
            _pend_pm_abonos  = pend_pm_abonos,
            _pend_pm_cargos  = pend_pm_cargos,
            _pend_fin_abonos = pend_fin_abonos,
            _pend_fin_cargos = pend_fin_cargos,
        )
        .annotate(
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
    from datetime import datetime, time, timedelta

    import xlwt
    from django.db import models
    from django.db.models import Q
    from django.http import HttpResponse
    from django.utils import timezone
    from django.utils.timezone import is_aware

    from core.permissions import filter_queryset_by_assignment_history

    from .models import CartolaMovimiento

    def _parse_date_any(s: str):
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass
        return None

    params = request.GET

    du        = (params.get('du') or '').strip()
    fecha_str = (params.get('fecha') or '').strip()
    proyecto  = (params.get('proyecto') or '').strip()
    categoria = (params.get('categoria') or '').strip()
    tipo      = (params.get('tipo') or '').strip()
    estado    = (params.get('estado') or '').strip()
    rut       = (params.get('rut_factura') or '').strip()

    movimientos = (
        CartolaMovimiento.objects.all()
        .select_related('usuario', 'proyecto', 'tipo')
        .order_by('-fecha')
    )

    # ✅ BLINDADO: proyecto + include_history/start_at
    movimientos = filter_queryset_by_assignment_history(
        movimientos,
        request.user,
        project_field="proyecto_id",
        date_field="fecha",
    )

    if du:
        movimientos = movimientos.filter(
            Q(usuario__username__icontains=du) |
            Q(usuario__first_name__icontains=du) |
            Q(usuario__last_name__icontains=du)
        )

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
        ws.write(row_num, 0, str(mov.usuario))

        fecha_excel = getattr(mov, 'fecha', None)
        if isinstance(fecha_excel, datetime):
            if is_aware(fecha_excel):
                fecha_excel = fecha_excel.astimezone().replace(tzinfo=None)
            fecha_excel = fecha_excel.date()
        ws.write(row_num, 1, fecha_excel, date_style)

        ws.write(row_num, 2, str(getattr(mov, 'proyecto', '') or ''))

        rcd = getattr(mov, 'real_consumption_date', None)
        if isinstance(rcd, datetime):
            if is_aware(rcd):
                rcd = rcd.astimezone().replace(tzinfo=None)
            rcd = rcd.date()
        if rcd:
            ws.write(row_num, 3, rcd, date_style)
        else:
            ws.write(row_num, 3, "")

        cat = (getattr(getattr(mov, 'tipo', None), 'categoria', '') or '')
        tipo_txt = str(getattr(mov, 'tipo', '') or '')
        ws.write(row_num, 4, str(cat).title())
        ws.write(row_num, 5, tipo_txt)

        ws.write(row_num, 6, mov.observaciones or "")
        ws.write(row_num, 7, mov.numero_transferencia or "")

        try:
            ws.write(row_num, 8, float(mov.kilometraje) if mov.kilometraje is not None else "")
        except Exception:
            ws.write(row_num, 8, "")

        ws.write(row_num, 9, float(mov.cargos or 0))
        ws.write(row_num, 10, float(mov.abonos or 0))
        ws.write(row_num, 11, mov.get_status_display())

    wb.save(response)
    return response


@login_required
@rol_requerido('facturacion', 'admin')
def exportar_saldos(request):
    from datetime import datetime

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

    # ✅ Limitar también por fecha (ventana ProyectoAsignacion) - SOLO SE AGREGA ESTO
    # Si include_history=True => todo; si start_at => desde start_at en adelante
    try:
        from usuarios.models import ProyectoAsignacion
    except Exception:
        ProyectoAsignacion = None

    can_view_legacy_history = (
        request.user.is_superuser or
        getattr(request.user, "es_usuario_historial", False)
    )

    if (ProyectoAsignacion is not None) and (not can_view_legacy_history):
        try:
            # OJO: aquí base ya está filtrado por proyectos visibles
            proyecto_ids_visibles = list(
                base.values_list("proyecto_id", flat=True).distinct()
            )
        except Exception:
            proyecto_ids_visibles = []

        try:
            asignaciones = list(
                ProyectoAsignacion.objects
                .filter(usuario=request.user, proyecto_id__in=proyecto_ids_visibles)
            )
        except Exception:
            asignaciones = []

        if asignaciones:
            access_by_pk = {}
            for a in asignaciones:
                if a.include_history or not a.start_at:
                    access_by_pk[a.proyecto_id] = {"include_history": True, "start_at": None}
                else:
                    access_by_pk[a.proyecto_id] = {"include_history": False, "start_at": a.start_at}

            ids_ok = []
            # usamos values_list para no romper con select_related/deferred
            for mid, pid, fecha in base.values_list("id", "proyecto_id", "fecha"):
                if pid is None:
                    continue
                access = access_by_pk.get(pid)
                if not access:
                    continue
                if access["include_history"] or access["start_at"] is None:
                    ids_ok.append(mid)
                    continue
                if not fecha:
                    continue

                start_at = access["start_at"]
                # normaliza a date (evita datetime vs date)
                if isinstance(start_at, datetime):
                    start_date = start_at.date()
                else:
                    start_date = start_at

                if isinstance(fecha, datetime):
                    fecha_date = fecha.date()
                else:
                    fecha_date = fecha

                if fecha_date >= start_date:
                    ids_ok.append(mid)

            base = base.filter(id__in=ids_ok)

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
      - 'open': todo lo que realmente está en Finanzas
      - 'paid': solo cobrados
      - 'all': todo lo de Finanzas

    PERFORMANCE:
      - Primero filtra con queryset liviano.
      - Luego pagina IDs.
      - Solo carga relaciones pesadas para la página visible.
    """
    import json
    from datetime import date as _date
    from datetime import datetime
    from urllib.parse import urlencode

    from django.core.paginator import Paginator
    from django.db.models import Prefetch, Q
    from django.utils import timezone

    from facturacion.models import Proyecto
    from operaciones.models import (BillingPayWeekSnapshot,
                                    EvidenciaFotoBilling, ItemBilling,
                                    ItemBillingTecnico, SesionBilling,
                                    SesionBillingTecnico)

    user = request.user
    scope = (request.GET.get("scope") or "open").strip()

    can_view_legacy_history = user.is_superuser or getattr(
        user, "es_usuario_historial", False
    )

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
            nombre = (getattr(p, "nombre", "") or "").strip()
            if nombre:
                allowed_keys.add(nombre)

            codigo = getattr(p, "codigo", None)
            if codigo:
                allowed_keys.add(str(codigo).strip())

            allowed_keys.add(str(p.id).strip())
    else:
        allowed_keys = set()

    # ============================================================
    # Query base liviana.
    # NO hacemos prefetch_related aquí para no cargar todo antes
    # de paginar.
    # ============================================================
    qs = SesionBilling.objects.all().order_by("-creado_en")

    finance_open_base = [
        "discount_applied",
        "sent",
        "in_review",
        "pending",
        "rejected",
    ]

    if scope == "paid":
        qs = qs.filter(finance_status="paid")

    elif scope == "all":
        qs = qs.exclude(
            Q(finance_status__in=["none", ""])
            | Q(finance_status__isnull=True)
            | (Q(finance_status="review_discount") & Q(finance_sent_at__isnull=True))
        )

    else:
        qs = qs.filter(
            Q(finance_status__in=finance_open_base)
            | (Q(finance_status="review_discount") & Q(finance_sent_at__isnull=False))
        ).exclude(finance_status="paid")

    if not can_view_legacy_history:
        if allowed_keys:
            qs = qs.filter(proyecto__in=allowed_keys)
        else:
            qs = SesionBilling.objects.none()

    # ============================================================
    # Limitar por historial de ProyectoAsignacion
    # ============================================================
    try:
        from usuarios.models import ProyectoAsignacion
    except Exception:
        ProyectoAsignacion = None

    if (ProyectoAsignacion is not None) and (not can_view_legacy_history):
        try:
            asignaciones = list(
                ProyectoAsignacion.objects.filter(
                    usuario=user,
                    proyecto__in=proyectos_user,
                ).select_related("proyecto")
            )
        except Exception:
            asignaciones = []

        if asignaciones:
            access_by_key = {}

            for a in asignaciones:
                p = getattr(a, "proyecto", None)
                if not p:
                    continue

                if getattr(a, "include_history", False) or not getattr(
                    a, "start_at", None
                ):
                    access = {"include_history": True, "start_at": None}
                else:
                    access = {"include_history": False, "start_at": a.start_at}

                for k in (
                    getattr(p, "nombre", None),
                    getattr(p, "codigo", None),
                    getattr(p, "id", None),
                ):
                    if k is None:
                        continue
                    ks = str(k).strip()
                    if ks:
                        access_by_key[ks.lower()] = access

            ids_ok = []

            for sid, proj_txt, creado_en in qs.values_list(
                "id",
                "proyecto",
                "creado_en",
            ):
                key = str(proj_txt).strip().lower() if proj_txt else ""
                if not key:
                    continue

                access = access_by_key.get(key)
                if not access:
                    continue

                if access["include_history"] or access["start_at"] is None:
                    ids_ok.append(sid)
                    continue

                if not creado_en:
                    continue

                start_at = access["start_at"]
                start_date = (
                    start_at.date() if isinstance(start_at, datetime) else start_at
                )

                if isinstance(creado_en, datetime):
                    creado_date = (
                        timezone.localtime(creado_en).date()
                        if timezone.is_aware(creado_en)
                        else creado_en.date()
                    )
                else:
                    creado_date = creado_en

                if creado_date >= start_date:
                    ids_ok.append(sid)

            qs = qs.filter(id__in=ids_ok)

    # ============================================================
    # Filtros rápidos
    # ============================================================
    f = {
        "tech": (request.GET.get("f_tech") or "").strip(),
        "projid": (request.GET.get("f_projid") or "").strip(),
        "week": (request.GET.get("f_week") or "").strip(),
        "client": (request.GET.get("f_client") or "").strip(),
        "finish": (request.GET.get("f_finish") or "").strip(),
    }

    qs_filtered = qs

    if f["projid"]:
        qs_filtered = qs_filtered.filter(proyecto_id__icontains=f["projid"])

    if f["week"]:
        qs_filtered = qs_filtered.filter(
            Q(semana_pago_proyectada__icontains=f["week"])
            | Q(semana_pago_real__icontains=f["week"])
            | Q(discount_week__icontains=f["week"])
            | Q(pay_week_snapshots__semana_resultado__icontains=f["week"])
            | Q(pay_week_snapshots__semana_base__icontains=f["week"])
        )

    if f["tech"]:
        qs_filtered = qs_filtered.filter(
            Q(tecnicos_sesion__tecnico__first_name__icontains=f["tech"])
            | Q(tecnicos_sesion__tecnico__last_name__icontains=f["tech"])
            | Q(tecnicos_sesion__tecnico__username__icontains=f["tech"])
            | Q(pay_week_snapshots__tecnico__first_name__icontains=f["tech"])
            | Q(pay_week_snapshots__tecnico__last_name__icontains=f["tech"])
            | Q(pay_week_snapshots__tecnico__username__icontains=f["tech"])
        )

    if f["client"]:
        qs_filtered = qs_filtered.filter(cliente__icontains=f["client"])

    if f["finish"]:
        try:
            d_fin = _date.fromisoformat(f["finish"])
            qs_filtered = qs_filtered.filter(finance_finish_date=d_fin)
        except ValueError:
            pass

    qs_filtered = qs_filtered.distinct()

    # ============================================================
    # Helpers livianos para labels
    # ============================================================
    def money_label(n):
        try:
            return f"${float(n or 0):.2f}"
        except Exception:
            return "$0.00"

    def session_status_label_light(s):
        if getattr(s, "is_direct_discount", False):
            return "Direct discount"
        if s.estado == "aprobado_pm":
            return "Approved by PM"
        if s.estado == "rechazado_pm":
            return "Rejected by PM"
        if s.estado == "aprobado_supervisor":
            return "Approved by supervisor"
        if s.estado == "rechazado_supervisor":
            return "Rejected by supervisor"
        if s.estado == "en_revision_supervisor":
            return "In supervisor review"
        if s.estado == "finalizado":
            return "Finished (pending review)"
        if s.estado == "en_proceso":
            return "In progress"
        return "Assigned"

    def finance_status_label_light(s):
        if s.finance_status == "review_discount":
            return "Review discount"
        if s.finance_status == "discount_applied":
            return "Discount applied"
        if s.finance_status == "sent":
            return "Sent to client"
        if s.finance_status == "in_review":
            return "In review"
        if s.finance_status == "rejected":
            return "Rejected"
        if s.finance_status == "pending":
            return "Pending payment"
        if s.finance_status == "paid":
            return "Collected"
        return "—"

    def resolve_project_labels_for_sessions(sessions):
        proj_ids = set()
        proj_texts = set()

        for s in sessions:
            raw_proyecto = getattr(s, "proyecto", None)
            if raw_proyecto not in (None, "", "-"):
                txt = str(raw_proyecto).strip()
                if txt:
                    proj_texts.add(txt)
                    try:
                        proj_ids.add(int(txt))
                    except Exception:
                        pass

            raw_proyecto_id = getattr(s, "proyecto_id", None)
            if raw_proyecto_id not in (None, "", "-"):
                txt2 = str(raw_proyecto_id).strip()
                if txt2:
                    proj_texts.add(txt2)
                    try:
                        proj_ids.add(int(txt2))
                    except Exception:
                        pass

        proj_q = Q()

        if proj_ids:
            proj_q |= Q(id__in=proj_ids)

        if proj_texts:
            proj_q |= Q(nombre__in=proj_texts) | Q(codigo__in=proj_texts)

        if proj_q:
            proyectos = Proyecto.objects.filter(proj_q).only("id", "nombre", "codigo")
        else:
            proyectos = Proyecto.objects.none()

        by_id = {str(p.id): p.nombre for p in proyectos}
        by_code = {
            (p.codigo or "").strip().lower(): p.nombre
            for p in proyectos
            if getattr(p, "codigo", None)
        }
        by_name = {
            (p.nombre or "").strip().lower(): p.nombre
            for p in proyectos
            if getattr(p, "nombre", None)
        }

        for s in sessions:
            raw = str(getattr(s, "proyecto", "") or "").strip()
            raw_id = str(getattr(s, "proyecto_id", "") or "").strip()

            label = ""

            if raw:
                label = (
                    by_id.get(raw)
                    or by_code.get(raw.lower())
                    or by_name.get(raw.lower())
                    or raw
                )

            if not label and raw_id:
                label = (
                    by_id.get(raw_id)
                    or by_code.get(raw_id.lower())
                    or by_name.get(raw_id.lower())
                    or raw_id
                )

            s.project_label = label

        return sessions

    # ============================================================
    # Query liviana para filtros Excel y paginación
    # ============================================================
    light_qs = qs_filtered.only(
        "id",
        "creado_en",
        "proyecto_id",
        "direccion_proyecto",
        "semana_pago_proyectada",
        "estado",
        "is_direct_discount",
        "cliente",
        "ciudad",
        "proyecto",
        "oficina",
        "subtotal_tecnico",
        "subtotal_empresa",
        "finance_daily_number",
        "finance_finish_date",
        "real_company_billing",
        "finance_status",
        "semana_pago_real",
        "discount_week",
        "finance_note",
    )

    light_rows = list(light_qs)
    resolve_project_labels_for_sessions(light_rows)

    def excel_value_for_invoice_light(s, col):
        if col == "0":
            d = getattr(s, "creado_en", None)
            return d.strftime("%Y-%m-%d %H:%M") if d else ""

        if col == "1":
            return str(getattr(s, "proyecto_id", "") or "")

        if col == "2":
            return str(getattr(s, "direccion_proyecto", "") or "")

        if col == "3":
            return str(getattr(s, "semana_pago_proyectada", "") or "—")

        if col == "4":
            return session_status_label_light(s)

        if col == "5":
            # Por performance no cargamos técnicos globales aquí.
            # La tabla visible sí los muestra bien.
            return "—"

        if col == "6":
            return str(getattr(s, "cliente", "") or "")

        if col == "7":
            return str(getattr(s, "ciudad", "") or "")

        if col == "8":
            return str(getattr(s, "project_label", "") or "")

        if col == "9":
            return str(getattr(s, "oficina", "") or "")

        if col == "10":
            return money_label(getattr(s, "subtotal_tecnico", 0))

        if col == "11":
            return money_label(getattr(s, "subtotal_empresa", 0))

        if col == "12":
            return str(getattr(s, "finance_daily_number", "") or "—")

        if col == "13":
            d = getattr(s, "finance_finish_date", None)
            return d.strftime("%Y-%m-%d") if d else "—"

        if col == "14":
            real = getattr(s, "real_company_billing", None)
            return "—" if real is None else money_label(real)

        if col == "15":
            real = getattr(s, "real_company_billing", None)
            subtotal = getattr(s, "subtotal_empresa", None)

            if real is None or subtotal is None:
                return "—"

            try:
                diff = float(subtotal or 0) - float(real or 0)
            except Exception:
                return "—"

            if diff > 0:
                return f"+ ${abs(diff):.2f}"
            if diff < 0:
                return f"- ${abs(diff):.2f}"
            return "$0.00"

        if col == "16":
            return finance_status_label_light(s)

        if col == "17":
            week = (
                (getattr(s, "semana_pago_real", "") or "").strip()
                or (getattr(s, "discount_week", "") or "").strip()
                or (getattr(s, "semana_pago_proyectada", "") or "").strip()
                or "—"
            )
            return week

        if col == "18":
            # Por performance no cargamos comentarios globales aquí.
            # La tabla visible sí los muestra bien.
            return "—"

        return ""

    # ============================================================
    # Filtros Excel
    # ============================================================
    excel_filters_raw = (request.GET.get("excel_filters") or "").strip()

    try:
        excel_filters = json.loads(excel_filters_raw) if excel_filters_raw else {}
    except json.JSONDecodeError:
        excel_filters = {}

    if excel_filters:
        filtered_light_rows = []

        for s in light_rows:
            ok = True

            for col, values in excel_filters.items():
                values_set = set(values or [])
                if not values_set:
                    continue

                label = excel_value_for_invoice_light(s, col)

                if label not in values_set:
                    ok = False
                    break

            if ok:
                filtered_light_rows.append(s)

        light_rows = filtered_light_rows

    # ============================================================
    # Opciones globales para filtros Excel
    # ============================================================
    excel_global = {}

    for col in range(19):
        vals = set()

        for s in light_rows:
            vals.add(excel_value_for_invoice_light(s, str(col)) or "(Vacías)")

        excel_global[col] = sorted(vals)

    excel_global_json = json.dumps(excel_global)

    # ============================================================
    # Paginación sobre IDs
    # ============================================================
    raw_cantidad = request.GET.get("cantidad", "10")

    try:
        per_page = int(raw_cantidad)
    except Exception:
        per_page = 10

    if per_page < 5:
        per_page = 5

    if per_page > 100:
        per_page = 100

    cantidad = str(per_page)

    filtered_ids = [s.id for s in light_rows]

    paginator = Paginator(filtered_ids, per_page)
    pagina_ids = paginator.get_page(request.GET.get("page"))

    page_ids = list(pagina_ids.object_list)
    order_map = {pk: idx for idx, pk in enumerate(page_ids)}

    # ============================================================
    # Cargar relaciones pesadas SOLO para la página visible
    # ============================================================
    page_rows = list(
        SesionBilling.objects.filter(id__in=page_ids).prefetch_related(
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
                queryset=SesionBillingTecnico.objects.select_related(
                    "tecnico"
                ).prefetch_related(
                    Prefetch(
                        "evidencias",
                        queryset=EvidenciaFotoBilling.objects.only(
                            "id",
                            "imagen",
                            "tecnico_sesion_id",
                            "requisito_id",
                        ).order_by("-id"),
                    )
                ),
            ),
            Prefetch(
                "pay_week_snapshots",
                queryset=BillingPayWeekSnapshot.objects.select_related(
                    "tecnico",
                    "item",
                )
                .filter(is_adjustment=False)
                .order_by(
                    "tecnico__first_name",
                    "tecnico__last_name",
                    "tecnico__username",
                    "tipo_trabajo",
                    "codigo_trabajo",
                    "id",
                ),
            ),
        )
    )

    page_rows.sort(key=lambda s: order_map.get(s.id, 999999))
    resolve_project_labels_for_sessions(page_rows)

    # ============================================================
    # Helpers que usan relaciones, solo para página visible
    # ============================================================
    def legacy_paid_flag(s):
        note = getattr(s, "finance_note", "") or ""

        try:
            tech_ids = list(
                s.tecnicos_sesion.all().values_list("tecnico_id", flat=True)
            )
        except Exception:
            tech_ids = []

        possible_weeks = [
            (getattr(s, "semana_pago_real", "") or "").strip().upper(),
            (getattr(s, "semana_pago_proyectada", "") or "").strip().upper(),
            (getattr(s, "discount_week", "") or "").strip().upper(),
        ]
        possible_weeks = [w for w in possible_weeks if w]

        for tech_id in tech_ids:
            for wk in possible_weeks:
                marker = f"[TECH_WEEKLY_PAYMENT_PAID:{tech_id}:{wk}]"
                if marker in note:
                    return True

        return False

    def build_payweek_groups(s):
        groups_map = {}

        snaps = (
            list(getattr(s, "pay_week_snapshots", []).all())
            if hasattr(s, "pay_week_snapshots")
            else []
        )

        if snaps:
            for snap in snaps:
                tech_name = (
                    snap.tecnico.get_full_name().strip()
                    if getattr(snap, "tecnico", None) and snap.tecnico.get_full_name()
                    else getattr(snap.tecnico, "username", "")
                    or f"User {snap.tecnico_id}"
                )

                grp = groups_map.setdefault(
                    tech_name,
                    {
                        "tech_name": tech_name,
                        "weeks_summary": "",
                        "lines": [],
                    },
                )

                work_type = (
                    (snap.tipo_trabajo or "").strip()
                    or (getattr(snap.item, "tipo_trabajo", "") or "").strip()
                    or "Legacy"
                )

                week = (
                    (snap.semana_resultado or "").strip()
                    or (snap.semana_base or "").strip()
                    or (getattr(s, "semana_pago_real", "") or "").strip()
                    or (getattr(s, "discount_week", "") or "").strip()
                    or (getattr(s, "semana_pago_proyectada", "") or "").strip()
                    or "—"
                )

                is_paid_line = bool(
                    getattr(snap, "paid_at", None) or getattr(snap, "is_paid", False)
                )

                grp["lines"].append(
                    {
                        "work_type": work_type,
                        "codigo_trabajo": (snap.codigo_trabajo or "").strip(),
                        "week": week,
                        "is_legacy": False,
                        "snapshot_id": snap.id,
                        "is_paid": is_paid_line,
                    }
                )

            groups = list(groups_map.values())

            for grp in groups:
                weeks = []

                for line in grp["lines"]:
                    wk = (line.get("week") or "").strip()
                    if wk and wk not in weeks:
                        weeks.append(wk)

                grp["weeks_summary"] = ", ".join(weeks) if weeks else "—"

            return groups

        asignaciones = (
            list(s.tecnicos_sesion.all()) if hasattr(s, "tecnicos_sesion") else []
        )

        base_week = (
            (getattr(s, "semana_pago_real", "") or "").strip()
            or (getattr(s, "discount_week", "") or "").strip()
            or (getattr(s, "semana_pago_proyectada", "") or "").strip()
            or "—"
        )

        legacy_is_paid = legacy_paid_flag(s)

        tech_names = []

        for asig in asignaciones:
            tech_name = (
                asig.tecnico.get_full_name().strip()
                if getattr(asig, "tecnico", None) and asig.tecnico.get_full_name()
                else getattr(asig.tecnico, "username", "") or f"User {asig.tecnico_id}"
            )

            if tech_name and tech_name not in tech_names:
                tech_names.append(tech_name)

        tech_label = ", ".join(tech_names) if tech_names else "—"

        return [
            {
                "tech_name": tech_label,
                "weeks_summary": base_week,
                "lines": [
                    {
                        "work_type": "Legacy",
                        "codigo_trabajo": "",
                        "week": base_week,
                        "is_legacy": True,
                        "session_id": s.id,
                        "dom_id": f"{s.id}-legacy",
                        "is_paid": legacy_is_paid,
                    }
                ],
            }
        ]

    def payweek_snapshot_label(s):
        groups = build_payweek_groups(s)

        if not groups:
            return str(getattr(s, "semana_pago_real", "") or "—")

        rows = []

        for grp in groups:
            tech_name = grp.get("tech_name") or "—"

            for line in grp.get("lines", []):
                work_type = (line.get("work_type") or "").strip() or "Work type"
                week = (line.get("week") or "").strip() or "—"
                suffix = " [Paid]" if line.get("is_paid") else ""
                rows.append(f"{tech_name} — {work_type} → {week}{suffix}")

        return (
            " | ".join(rows) if rows else str(getattr(s, "semana_pago_real", "") or "—")
        )

    # ============================================================
    # Preparar objetos visibles para template
    # ============================================================
    for s in page_rows:
        comentarios = []

        try:
            for st in s.tecnicos_sesion.all():
                txt = (getattr(st, "tecnico_comentario", "") or "").strip()
                if txt:
                    comentarios.append(st)
        except Exception:
            pass

        s.comentarios_tecnicos = comentarios
        s.payweek_groups = build_payweek_groups(s)
        s.payweek_snapshot_label = payweek_snapshot_label(s)

    pagina_ids.object_list = page_rows
    pagina = pagina_ids

    # ============================================================
    # Mantener querystring
    # ============================================================
    keep_params = {
        "scope": scope,
        "cantidad": cantidad,
        "f_tech": f["tech"],
        "f_projid": f["projid"],
        "f_week": f["week"],
        "f_client": f["client"],
        "f_finish": f["finish"],
    }

    if excel_filters_raw:
        keep_params["excel_filters"] = excel_filters_raw

    qs_keep = urlencode({k: v for k, v in keep_params.items() if v not in ("", None)})

    return render(
        request,
        "facturacion/invoices_list.html",
        {
            "pagina": pagina,
            "cantidad": cantidad,
            "scope": scope,
            "can_edit_real_week": _can_edit_real_week(request.user),
            "f_tech": f["tech"],
            "f_projid": f["projid"],
            "f_week_input": f["week"],
            "f_client": f["client"],
            "f_finish": f["finish"],
            "qs_keep": qs_keep,
            "excel_global_json": excel_global_json,
        },
    )


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
    Mark invoice as Collected.
    - Requiere real_company_billing
    - Si hay diferencia positiva, pide confirmación
    - Este flujo es SOLO de finanzas / cobro cliente
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    s = get_object_or_404(SesionBilling, pk=pk)
    if s.real_company_billing is None:
        return HttpResponseBadRequest(
            "Real Company Billing is required before marking as collected."
        )

    difference = (s.subtotal_empresa or Decimal("0")) - s.real_company_billing
    force = (request.POST.get("force") or "") == "1"

    if difference > 0 and not force:
        return JsonResponse(
            {
                "ok": False,
                "confirm": True,
                "message": "You are collecting less than expected. Do you still want to mark it as collected?",
            },
            status=409,
        )

    with transaction.atomic():
        s.finance_status = "paid"  # interno
        if not s.finance_finish_date:
            s.finance_finish_date = timezone.localdate()
        s.save(
            update_fields=[
                "finance_status",
                "finance_finish_date",
                "finance_updated_at",
            ]
        )

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
    - Si es descuento directo -> vuelve a 'review_discount'
    - Si NO es descuento directo -> vuelve a 'none'
    - No toca status operativo
    - No borra markers legacy de pago técnico
    """
    s = (
        SesionBilling.objects.only(
            "id",
            "is_direct_discount",
            "finance_status",
            "finance_note",
        )
        .filter(pk=pk)
        .first()
    )

    if not s:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "Not found"}, status=404)
        messages.error(request, "Billing not found.")
        return redirect(
            request.META.get("HTTP_REFERER") or reverse("facturacion:invoices")
        )

    new_status = "review_discount" if s.is_direct_discount else "none"

    # Preservar markers técnicos legacy
    old_note = getattr(s, "finance_note", "") or ""
    keep_lines = []

    for ln in old_note.splitlines():
        txt = (ln or "").strip()
        if txt.startswith("[TECH_WEEKLY_PAYMENT_PAID:") and txt.endswith("]"):
            keep_lines.append(txt)

    preserved_note = "\n".join(keep_lines).strip()

    SesionBilling.objects.filter(pk=s.pk).update(
        finance_status=new_status,
        finance_note=preserved_note,
        finance_sent_at=None,
        finance_finish_date=None,
        finance_updated_at=timezone.now(),
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "id": s.pk, "finance_status": new_status})

    if new_status == "review_discount":
        messages.success(request, f"Direct discount #{s.pk} returned to Operations.")
    else:
        messages.success(request, f"Billing #{s.pk} removed from Finance queue.")

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
    from datetime import datetime
    from decimal import Decimal

    from django.db.models import Prefetch, Q
    from django.http import HttpResponse
    from django.utils import timezone
    from openpyxl import Workbook

    from facturacion.models import Proyecto
    from operaciones.models import (EvidenciaFotoBilling, ItemBilling,
                                    ItemBillingTecnico, SesionBilling,
                                    SesionBillingTecnico)

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

    # ✅ Limitar también por fecha (ventana ProyectoAsignacion) - SOLO SE AGREGA ESTO
    # Si include_history=True => todo; si start_at => desde start_at en adelante
    try:
        from usuarios.models import ProyectoAsignacion
    except Exception:
        ProyectoAsignacion = None

    if (ProyectoAsignacion is not None) and (not can_view_legacy_history):
        try:
            asignaciones = list(
                ProyectoAsignacion.objects
                .filter(usuario=user, proyecto__in=proyectos_user)
                .select_related("proyecto")
            )
        except Exception:
            asignaciones = []

        if asignaciones:
            access_by_key = {}
            for a in asignaciones:
                p = getattr(a, "proyecto", None)
                if not p:
                    continue

                if a.include_history or not a.start_at:
                    access = {"include_history": True, "start_at": None}
                else:
                    access = {"include_history": False, "start_at": a.start_at}

                for k in (getattr(p, "nombre", None), getattr(p, "codigo", None), getattr(p, "id", None)):
                    if k is None:
                        continue
                    ks = str(k).strip()
                    if ks:
                        access_by_key[ks.lower()] = access

            ids_ok = []
            for sid, proj_txt, creado_en in qs.values_list("id", "proyecto", "creado_en"):
                key = (str(proj_txt).strip().lower() if proj_txt else "")
                if not key:
                    continue

                access = access_by_key.get(key)
                if not access:
                    continue

                if access["include_history"] or access["start_at"] is None:
                    ids_ok.append(sid)
                    continue

                if not creado_en:
                    continue

                start_at = access["start_at"]
                # normaliza a date (evita datetime vs date)
                if isinstance(start_at, datetime):
                    start_date = start_at.date()
                else:
                    start_date = start_at

                if isinstance(creado_en, datetime):
                    creado_date = timezone.localtime(creado_en).date() if timezone.is_aware(creado_en) else creado_en.date()
                else:
                    creado_date = creado_en

                if creado_date >= start_date:
                    ids_ok.append(sid)

            qs = qs.filter(id__in=ids_ok)

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

    # ✅ NUEVO: comentario (como el comentario del técnico)
    def comments_string(sesion):
        parts = []
        for st in sesion.tecnicos_sesion.all():
            txt = (getattr(st, "tecnico_comentario", "") or "").strip()
            if not txt:
                continue
            tech = getattr(st, "tecnico", None)
            name = (tech.get_full_name() or tech.username) if tech else "—"
            parts.append(f"{name}: {txt}")
        return "\n".join(parts)

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
        "Comment",
        "Job Code", "Work Type", "Description", "UOM", "Quantity",
        "Technical Rate", "Company Rate", "Subtotal Technical", "Subtotal Company",
    ]
    ws.append(headers)

    for s in qs:
        status_label = "Direct discount" if getattr(s, "is_direct_discount", False) else status_map.get(s.estado, "Assigned")
        finance_label = finance_map.get(s.finance_status, "—")

        real_week = s.semana_pago_real or ""
        disc_week = getattr(s, "discount_week", "") or getattr(s, "semana_descuento", "") or ""
        pay_or_disc = f"{real_week} / {disc_week}" if (real_week and disc_week) else (real_week or disc_week)

        project_label = _resolve_project_label(s)
        comment_text = comments_string(s)

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
            comment_text,
        ]

        items = getattr(s, "items", None).all() if hasattr(s, "items") else []
        if not items:
            ws.append(head_common + ["", "", "", "", 0.0, 0.0, 0.0, 0.0, 0.0])
            continue

        for it in items:
            qty_total = Decimal(str(it.cantidad or 0))
            comp_rate = Decimal(str(it.precio_empresa or 0))
            desglose = list(getattr(it, "desglose_tecnico", []).all()) if hasattr(it, "desglose_tecnico") else []

            if desglose:
                for bd in desglose:
                    pct = Decimal(str(bd.porcentaje or 0)) / Decimal("100")
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
                sub_comp_item = Decimal(str(it.subtotal_empresa or (comp_rate * qty_total)))
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
