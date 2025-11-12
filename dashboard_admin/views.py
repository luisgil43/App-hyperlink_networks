import re

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from dashboard.models import ProduccionTecnico
from facturacion.models import Proyecto
from rrhh.forms import FeriadoForm
from rrhh.models import Feriado
from usuarios.decoradores import rol_requerido
from usuarios.models import CustomUser
from usuarios.models import CustomUser as User
from usuarios.models import Notificacion, Rol

# Intentamos ubicar el modelo de asignación (si ya existe con through)
try:
    from usuarios.models import ProyectoAsignacion  # through recomendado
except Exception:
    try:
        from facturacion.models import ProyectoAsignacion
    except Exception:
        ProyectoAsignacion = None  # fallback si aún no creas el through
User = get_user_model()


@login_required(login_url='usuarios:login')
def admin_dashboard_view(request):
    # Cargar datos para la plantilla principal del admin dashboard
    return render(request, 'dashboard_admin/base.html')


@login_required(login_url='usuarios:login_unificado')
def logout_view(request):
    logout(request)
    messages.info(request, "You have successfully logged out.")
    return redirect(reverse('usuarios:login_unificado'))


def inicio_admin(request):
    queryset = Notificacion.objects.filter(
        usuario=request.user).order_by('leido', '-fecha')
    notificaciones = queryset[:10]
    no_leidas = queryset.filter(leido=False).count()

    return render(request, 'dashboard_admin/inicio_admin.html', {
        'notificaciones': notificaciones,
        'notificaciones_no_leidas': no_leidas,
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'supervisor')
def produccion_tecnico(request):
    produccion = ProduccionTecnico.objects.filter(tecnico__user=request.user)
    return render(request, 'dashboard/produccion_tecnico.html', {
        'produccion': produccion
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def grupos_view(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        grupo_id = request.POST.get('grupo_id')

        if 'add_group' in request.POST:
            if nombre:
                grupo, creado = Group.objects.get_or_create(name=nombre)
                if creado:
                    messages.success(request, f'Group "{nombre}" created successfully.')
                else:
                    messages.warning(request, f'Group "{nombre}" already exists.')
            else:
                messages.error(request, "You must enter a group name.")
            return redirect('dashboard_admin:grupos')

        elif 'delete_group' in request.POST and grupo_id:
            try:
                grupo = Group.objects.get(id=grupo_id)
                grupo.delete()
                messages.success(request, f'Group "{grupo.name}" deleted successfully.')
            except Group.DoesNotExist:
                messages.error(request, 'Group does not exist.')
            return redirect('dashboard_admin:grupos')

    grupos = Group.objects.all().order_by('name')
    return render(request, 'dashboard_admin/grupos.html', {'grupos': grupos})

@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def editar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)
    grupos = Group.objects.all()
    roles_disponibles = Rol.objects.all()

    if request.method == 'POST':
        # --- update basic data ---
        usuario.username = request.POST.get('username', usuario.username)
        usuario.first_name = request.POST.get('first_name', usuario.first_name)
        usuario.last_name = request.POST.get('last_name', usuario.last_name)
        usuario.email = request.POST.get('email', usuario.email)
        usuario.is_active = 'is_active' in request.POST
        usuario.is_staff = 'is_staff' in request.POST
        usuario.is_superuser = 'is_superuser' in request.POST
        usuario.identidad = request.POST.get('identidad', usuario.identidad)

        # --- groups ---
        grupo_ids = request.POST.getlist('groups')
        usuario.groups.set(grupo_ids)

        # --- roles (M2M) ---
        roles_ids = request.POST.getlist('roles')
        usuario.roles.set(roles_ids)

        # --- password (optional) ---
        password1 = request.POST.get('password1') or ''
        password2 = request.POST.get('password2') or ''
        if password1 or password2:
            if password1 != password2:
                messages.error(request, 'Passwords do not match.')
                return render(request, 'dashboard_admin/editar_usuario.html', {
                    'usuario': usuario,
                    'grupos': grupos,
                    'roles': roles_disponibles,
                    'roles_seleccionados': set(map(int, roles_ids)),
                    'grupo_ids_post': set(map(int, grupo_ids)),
                })
            usuario.set_password(password1)

        # --- Proyectos seleccionados y modo visibilidad ---
        proy_ids = [int(pid) for pid in request.POST.getlist('proyectos')]
        visibility_mode = (request.POST.get('project_visibility') or 'history').strip()
        start_date_str = (request.POST.get('project_start_date') or '').strip()
        start_dt = None
        if visibility_mode == 'from_now':
            try:
                start_dt = timezone.make_aware(timezone.datetime.fromisoformat(start_date_str)) if start_date_str else timezone.now()
            except Exception:
                start_dt = timezone.now()

        usuario.save()

        # --- Guardar asignaciones de proyecto ---
        if ProyectoAsignacion:
            ProyectoAsignacion.objects.filter(usuario=usuario).delete()
            include_history = (visibility_mode == 'history')
            objetos = []
            for pid in proy_ids:
                objetos.append(ProyectoAsignacion(
                    usuario=usuario,
                    proyecto_id=pid,
                    include_history=include_history,
                    start_at=None if include_history else (start_dt or timezone.now()),
                ))
            if objetos:
                ProyectoAsignacion.objects.bulk_create(objetos)
        elif hasattr(usuario, 'proyectos'):
            usuario.proyectos.set(proy_ids)

        messages.success(request, "User updated successfully.")
        return redirect('dashboard_admin:listar_usuarios')

    # --- GET: preload current selections ---
    roles_seleccionados = set(usuario.roles.values_list('id', flat=True))
    grupo_ids_post = set(usuario.groups.values_list('id', flat=True))

    # Precarga de proyectos + modo/fecha visibilidad
    proyectos_all = Proyecto.objects.all().order_by('nombre')
    proyectos_seleccionados = []
    project_visibility = 'history'
    project_start_date = ''

    if ProyectoAsignacion:
        asignaciones = list(
            ProyectoAsignacion.objects.filter(usuario=usuario).select_related('proyecto')
        )
        proyectos_seleccionados = [a.proyecto_id for a in asignaciones]
        any_from_now = any(not a.include_history for a in asignaciones)
        project_visibility = 'from_now' if any_from_now else 'history'
        if any_from_now:
            fechas = [a.start_at for a in asignaciones if a.start_at]
            if fechas:
                project_start_date = fechas and fechas[0].date().isoformat()
    elif hasattr(usuario, 'proyectos'):
        proyectos_seleccionados = list(usuario.proyectos.values_list('id', flat=True))

    return render(request, 'dashboard_admin/editar_usuario.html', {
        'usuario': usuario,
        'grupos': grupos,
        'roles': roles_disponibles,
        'roles_seleccionados': roles_seleccionados,
        'grupo_ids_post': grupo_ids_post,
        'proyectos': proyectos_all,
        'proyectos_seleccionados': proyectos_seleccionados,
        'project_visibility': project_visibility,
        'project_start_date': project_start_date,
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def crear_usuario_view(request, identidad=None):
    grupos = Group.objects.all()
    usuario = get_object_or_404(User, identidad=identidad) if identidad else None

    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        first_name = request.POST['first_name']
        last_name = request.POST['last_name']
        is_active = request.POST.get('is_active') == 'on'
        is_staff = 'is_staff' in request.POST
        is_superuser = 'is_superuser' in request.POST
        grupo_ids = [int(gid) for gid in request.POST.getlist('groups')]
        identidad_post = request.POST.get('identidad')
        roles_ids = request.POST.getlist('roles')

        # --- Proyectos seleccionados y modo de visibilidad ---
        proy_ids = [int(pid) for pid in request.POST.getlist('proyectos')]
        visibility_mode = (request.POST.get('project_visibility') or 'history').strip()  # 'history' | 'from_now'
        start_date_str = (request.POST.get('project_start_date') or '').strip()
        # Si es "from_now" y viene una fecha válida, úsala; si no, usa now()
        start_dt = None
        if visibility_mode == 'from_now':
            try:
                # Formato esperado 'YYYY-MM-DD' (ajusta si tu form envía datetime)
                start_dt = timezone.make_aware(timezone.datetime.fromisoformat(start_date_str)) if start_date_str else timezone.now()
            except Exception:
                start_dt = timezone.now()

        # Campos jerárquicos (se mantienen)
        def get_user_or_none(uid):
            return CustomUser.objects.filter(id=uid).first() if uid else None

        supervisor = get_user_or_none(request.POST.get('supervisor'))
        pm = get_user_or_none(request.POST.get('pm'))
        rrhh_encargado = get_user_or_none(request.POST.get('rrhh_encargado'))
        prevencionista = get_user_or_none(request.POST.get('prevencionista'))
        logistica_encargado = get_user_or_none(request.POST.get('logistica_encargado'))
        encargado_flota = get_user_or_none(request.POST.get('encargado_flota'))
        encargado_subcontrato = get_user_or_none(request.POST.get('encargado_subcontrato'))
        encargado_facturacion = get_user_or_none(request.POST.get('encargado_facturacion'))

        # Validaciones
        if password1 or password2:
            if password1 != password2:
                messages.error(request, 'Passwords do not match.')
                return redirect(request.path)

        if identidad_post and not re.match(r'^[A-Za-z0-9\.\-]+$', identidad_post):
            messages.error(request, 'ID may contain only letters, numbers, dots, or hyphens.')
            return redirect(request.path)

        if usuario:
            # Edición
            usuario.username = username
            usuario.email = email
            usuario.first_name = first_name
            usuario.last_name = last_name
            usuario.is_active = is_active
            usuario.is_staff = is_staff
            usuario.is_superuser = is_superuser
            usuario.identidad = identidad_post
            usuario.groups.set(grupo_ids)
            usuario.roles.set(roles_ids)

            # Jerarquías
            usuario.supervisor = supervisor
            usuario.pm = pm
            usuario.rrhh_encargado = rrhh_encargado
            usuario.prevencionista = prevencionista
            usuario.logistica_encargado = logistica_encargado
            usuario.encargado_flota = encargado_flota
            usuario.encargado_subcontrato = encargado_subcontrato
            usuario.encargado_facturacion = encargado_facturacion

            if password1:
                usuario.set_password(password1)
            usuario.save()

            # --- Asignación de proyectos (reemplaza actuales por POST) ---
            if ProyectoAsignacion:
                ProyectoAsignacion.objects.filter(usuario=usuario).delete()
                objetos = []
                include_history = (visibility_mode == 'history')
                for pid in proy_ids:
                    objetos.append(ProyectoAsignacion(
                        usuario=usuario,
                        proyecto_id=pid,
                        include_history=include_history,
                        start_at=None if include_history else (start_dt or timezone.now()),
                    ))
                if objetos:
                    ProyectoAsignacion.objects.bulk_create(objetos)
            elif hasattr(usuario, 'proyectos'):
                usuario.proyectos.set(proy_ids)

            messages.success(request, 'User updated successfully.')
        else:
            # Creación
            if User.objects.filter(username=username).exists():
                messages.error(request, 'Username already exists.')
                return redirect('dashboard_admin:crear_usuario')

            if identidad_post and User.objects.filter(identidad=identidad_post).exists():
                messages.error(request, 'ID number is already registered.')
                return redirect('dashboard_admin:crear_usuario')

            usuario = User.objects.create_user(
                username=username,
                email=email,
                password=password1,
                first_name=first_name,
                last_name=last_name,
                is_active=is_active,
                is_staff=is_staff,
                is_superuser=is_superuser,
                identidad=identidad_post,
                supervisor=supervisor,
                pm=pm,
                rrhh_encargado=rrhh_encargado,
                prevencionista=prevencionista,
                logistica_encargado=logistica_encargado,
                encargado_flota=encargado_flota,
                encargado_subcontrato=encargado_subcontrato,
                encargado_facturacion=encargado_facturacion
            )
            usuario.groups.set(grupo_ids)
            usuario.roles.set(roles_ids)

            # --- Asignación de proyectos al crear ---
            if ProyectoAsignacion:
                include_history = (visibility_mode == 'history')
                objetos = []
                for pid in proy_ids:
                    objetos.append(ProyectoAsignacion(
                        usuario=usuario,
                        proyecto_id=pid,
                        include_history=include_history,
                        start_at=None if include_history else (start_dt or timezone.now()),
                    ))
                if objetos:
                    ProyectoAsignacion.objects.bulk_create(objetos)
            elif hasattr(usuario, 'proyectos'):
                usuario.proyectos.set(proy_ids)

            messages.success(request, 'User created successfully.')

        return redirect('dashboard_admin:listar_usuarios')

    # --- GET: precarga de selecciones ---
    grupo_ids_post = request.POST.getlist('groups') if request.method == 'POST' else []
    if not grupo_ids_post and usuario:
        grupo_ids_post = [str(g.id) for g in usuario.groups.all()]

    roles_disponibles = Rol.objects.all()
    roles_seleccionados = usuario.roles.values_list('id', flat=True) if usuario else []
    roles_seleccionados = [str(id) for id in roles_seleccionados]

    usuarios_activos = CustomUser.objects.filter(is_active=True).order_by('first_name', 'last_name')

    # --- Proyectos para el form + preselección + modo/fecha visibilidad ---
    proyectos_all = Proyecto.objects.all().order_by('nombre')
    proyectos_seleccionados = []
    project_visibility = 'history'
    project_start_date = ''

    if usuario:
        if ProyectoAsignacion:
            asignaciones = list(
                ProyectoAsignacion.objects.filter(usuario=usuario)
                .select_related('proyecto')
            )
            proyectos_seleccionados = [a.proyecto_id for a in asignaciones]
            # Si TODAS son history => 'history'; si hay alguna from_now => 'from_now'
            any_from_now = any(not a.include_history for a in asignaciones)
            project_visibility = 'from_now' if any_from_now else 'history'
            if any_from_now:
                # Usa la mínima start_at como sugerencia (si existe)
                fechas = [a.start_at for a in asignaciones if a.start_at]
                if fechas:
                    project_start_date = fechas and fechas[0].date().isoformat()
        elif hasattr(usuario, 'proyectos'):
            proyectos_seleccionados = list(usuario.proyectos.values_list('id', flat=True))

    contexto = {
        'grupos': grupos,
        'grupo_ids_post': grupo_ids_post,
        'usuario': usuario,
        'roles': roles_disponibles,
        'roles_seleccionados': roles_seleccionados,
        'usuarios': usuarios_activos,  # para los selects jerárquicos
        'proyectos': proyectos_all,  # lista completa para el <select multiple>
        'proyectos_seleccionados': proyectos_seleccionados,  # ids preseleccionados
        'project_visibility': project_visibility,  # 'history' | 'from_now'
        'project_start_date': project_start_date,  # YYYY-MM-DD si aplica
    }
    return render(request, 'dashboard_admin/crear_usuario.html', contexto)


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def listar_usuarios(request):
    # Delete (POST)
    if request.method == "POST" and "delete_user" in request.POST:
        user_id = request.POST.get("user_id")
        try:
            usuario = User.objects.get(id=user_id)
            username = usuario.username
            usuario.delete()
            messages.success(request, f'User "{username}" deleted successfully.')
        except User.DoesNotExist:
            messages.error(request, "User not found.")
        return redirect('dashboard_admin:listar_usuarios')

    # Filtros
    rol_filtrado = (request.GET.get('rol') or '').strip()
    first_q = (request.GET.get('first') or '').strip()
    last_q  = (request.GET.get('last') or '').strip()
    id_q    = (request.GET.get('id') or '').strip()

    qs = User.objects.all().order_by('id').prefetch_related('roles', 'groups')

    # Prefetch de proyectos según exista through o M2M directo
    if ProyectoAsignacion:
        qs = qs.prefetch_related(
            Prefetch(
                'proyectoasignacion_set',
                queryset=ProyectoAsignacion.objects.select_related('proyecto'),
            )
        )
    elif hasattr(User, 'proyectos'):
        qs = qs.prefetch_related('proyectos')

    if rol_filtrado:
        qs = qs.filter(roles__nombre=rol_filtrado).distinct()
    if first_q:
        qs = qs.filter(first_name__icontains=first_q)
    if last_q:
        qs = qs.filter(last_name__icontains=last_q)
    if id_q:
        qs = qs.filter(identidad__icontains=id_q)

    # Pagination (soporta per_page=all/todos y el hidden "cantidad")
    per_page_raw = str(request.GET.get('per_page', request.GET.get('cantidad', '20'))).strip().lower()
    if per_page_raw in ('all', 'todos'):
        per_page = max(qs.count(), 1)
    else:
        try:
            per_page = int(per_page_raw or 20)
        except ValueError:
            per_page = 20
        per_page = max(5, min(per_page, 100))

    paginator = Paginator(qs, per_page)
    page_number = request.GET.get('page', 1)
    try:
        usuarios_page = paginator.get_page(page_number)
    except (PageNotAnInteger, EmptyPage):
        usuarios_page = paginator.get_page(1)

    # Preserva querystring (excepto page) para mantener filtros
    params = request.GET.copy()
    params.pop('page', None)
    querystring = params.urlencode()

    roles_disponibles = Rol.objects.all()

    return render(request, 'dashboard_admin/listar_usuarios.html', {
        'usuarios': usuarios_page,
        'page_obj': usuarios_page,
        'roles': roles_disponibles,
        'rol_filtrado': rol_filtrado,
        'per_page': per_page,
        'querystring': querystring,
        # mantener valores en inputs
        'first_q': first_q,
        'last_q': last_q,
        'id_q': id_q,
        # (opcional) reflejar el valor mostrado en el selector
        'cantidad': request.GET.get('cantidad', None),
    })


@login_required(login_url='usuarios:login')
@rol_requerido('admin', 'pm', 'rrhh')
def eliminar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        usuario.delete()
        messages.success(
            request, f'Usuario {usuario.username} eliminado correctamente.'
        )
        return redirect('dashboard_admin:listar_usuarios')

    # GET → mostrar confirmación
    return render(request, 'dashboard_admin/eliminar_usuario_confirmacion.html', {'usuario': usuario})


# Vista para usuarios no autorizados
def no_autorizado(request):
    return render(request, 'dashboard_admin/no_autorizado.html')


@login_required
def redireccionar_vacaciones(request):
    user = request.user
    if user.es_supervisor:
        return redirect('rrhh:revisar_supervisor')
    elif user.es_pm:
        return redirect('rrhh:revisar_pm')
    elif user.es_rrhh or user.es_admin_general:  # 👈 Aquí
        return redirect('rrhh:revisar_rrhh')
    else:
        return redirect('dashboard_admin:inicio_admin')


@login_required
@rol_requerido('rrhh')
def listar_feriados(request):
    feriados = Feriado.objects.order_by('fecha')
    form = FeriadoForm()

    if request.method == 'POST':
        form = FeriadoForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('dashboard_admin:listar_feriados')

    return render(request, 'dashboard_admin/listar_feriados.html', {
        'feriados': feriados,
        'form': form
    })


@login_required
@rol_requerido('rrhh')
def eliminar_feriado(request, pk):
    feriado = get_object_or_404(Feriado, pk=pk)
    feriado.delete()
    messages.success(
        request, f'El feriado "{feriado.nombre}" fue eliminado con éxito.')
    return redirect('dashboard_admin:listar_feriados')


def redirigir_a_login_unificado(request):
    return redirect('usuarios:login_unificado')
