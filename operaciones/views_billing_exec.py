# operaciones/views_billing_exec.py
from decimal import Decimal
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import (
    Count, Sum, Subquery, OuterRef, DecimalField, Value
)
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import (
    SesionBilling, SesionBillingTecnico, ItemBillingTecnico,
    RequisitoFotoBilling, EvidenciaFotoBilling
)
from usuarios.decoradores import rol_requerido


# ============================
# UTIL
# ============================

def storage_file_exists(filefield) -> bool:
    if not filefield or not getattr(filefield, "name", ""):
        return False
    try:
        return filefield.storage.exists(filefield.name)
    except Exception:
        return False


# ============================
# TÉCNICO
# ============================

@login_required
@rol_requerido('usuario', 'admin', 'pm', 'supervisor')
def mis_assignments(request):
    base_qs = (
        SesionBillingTecnico.objects
        .select_related("sesion")
        .filter(tecnico=request.user)
        .order_by("-id")
    )

    # Subquery: total del técnico para cada sesión
    ibt = (
        ItemBillingTecnico.objects
        .filter(item__sesion=OuterRef("sesion_id"), tecnico=request.user)
        .values("tecnico")
        .annotate(total=Sum("subtotal"))
        .values("total")
    )

    dec_field = DecimalField(max_digits=12, decimal_places=2)

    asignaciones = base_qs.annotate(
        my_total=Coalesce(
            Subquery(ibt, output_field=dec_field),
            Value(Decimal("0.00"), output_field=dec_field),
            output_field=dec_field
        )
    )

    return render(
        request,
        "operaciones/billing_mis_asignaciones.html",
        {"asignaciones": asignaciones}
    )


@login_required
@rol_requerido('usuario')
def detalle_assignment(request, pk):
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)
    items = (ItemBillingTecnico.objects
             .filter(item__sesion=a.sesion, tecnico=request.user)
             .select_related("item")
             .order_by("item__id"))
    return render(request, "operaciones/billing_detalle_asignacion.html", {
        "a": a, "items": items
    })


@login_required
@rol_requerido('usuario')
@require_POST
def start_assignment(request, pk):
    """
    El técnico acepta la tarea y la pone en 'en_proceso'.
    El proyecto pasa a 'en_proceso' si estaba 'rechazado_supervisor' o 'asignado'.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    if a.estado not in {"asignado", "rechazado_supervisor"} and not a.reintento_habilitado:
        messages.error(request, "This assignment cannot be started.")
        return redirect("operaciones:mis_assignments")

    a.estado = "en_proceso"
    a.aceptado_en = timezone.now()
    a.reintento_habilitado = False
    a.save(update_fields=["estado", "aceptado_en", "reintento_habilitado"])

    s = a.sesion
    if s.estado in {"rechazado_supervisor", "asignado"}:
        s.estado = "en_proceso"
        s.save(update_fields=["estado"])

    messages.success(request, "Assignment started.")
    return redirect("operaciones:mis_assignments")


@login_required
@rol_requerido('usuario')
def upload_evidencias(request, pk):
    """
    Carga de evidencias para una asignación.
    Se permite subir si la asignación está:
      - en_proceso, o
      - rechazada_por_supervisor con reintento_habilitado=True.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    # ¿Se puede subir en el estado actual?
    puede_subir = (a.estado == "en_proceso") or (
        a.estado == "rechazado_supervisor" and a.reintento_habilitado
    )
    if not puede_subir:
        messages.info(request, "This assignment is not open for uploads.")
        return redirect("operaciones:mis_assignments")

    if request.method == "POST":
        req_id = request.POST.get("req_id") or None
        nota = (request.POST.get("nota") or "").strip()
        files = request.FILES.getlist("imagenes[]")

        lat = request.POST.get("lat") or None
        lng = request.POST.get("lng") or None
        acc = request.POST.get("acc") or None
        taken = request.POST.get("client_taken_at")
        taken_dt = parse_datetime(taken) if taken else None

        n = 0
        for f in files:
            EvidenciaFotoBilling.objects.create(
                tecnico_sesion=a,
                requisito_id=req_id,
                imagen=f,
                nota=nota,
                lat=lat,
                lng=lng,
                gps_accuracy_m=acc,
                client_taken_at=taken_dt,
            )
            n += 1

        if n:
            messages.success(request, f"{n} photo(s) uploaded.")
        else:
            messages.info(request, "No files selected.")
        return redirect("operaciones:upload_evidencias", pk=a.pk)

    # Requisitos con cantidad de evidencias
    requisitos = (
        a.requisitos
         .annotate(uploaded=Count("evidencias"))
         .order_by("orden", "id")
    )
    faltantes = requisitos.filter(obligatorio=True, uploaded=0)

    # El botón Finish solo cuando está en proceso y sin faltantes
    can_finish = (a.estado == "en_proceso" and not faltantes.exists())

    # Evidencias ya subidas (se listan con opción de borrar si puede_subir=True)
    evidencias = (
        a.evidencias
         .select_related("requisito")
         .order_by("requisito__orden", "tomada_en", "id")
    )

    # Bandera para mostrar la ✕ de eliminar en el template
    can_delete = puede_subir

    return render(
        request,
        "operaciones/billing_upload_evidencias.html",
        {
            "a": a,
            "requisitos": requisitos,
            "evidencias": evidencias,
            "faltantes": faltantes,
            "can_finish": can_finish,
            "can_delete": can_delete,
        },
    )


@login_required
@rol_requerido('usuario')
def finish_assignment(request, pk):
    """
    El técnico finaliza su parte. Si TODOS los requisitos obligatorios del proyecto
    (sumando todos los técnicos) están listos, el estado del PROYECTO sube a
    'en_revision_supervisor'. Si no, queda 'en_proceso'.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk, tecnico=request.user)

    if a.estado != "en_proceso":
        messages.error(request, "This assignment is not in progress.")
        return redirect("operaciones:mis_assignments")

    # Validar requisitos obligatorios del TÉCNICO (server-side)
    faltan = []
    for r in a.requisitos.filter(obligatorio=True):
        if not r.evidencias.exists():
            faltan.append(r.titulo)
    if faltan:
        messages.error(request, "Missing required photos: " +
                       ", ".join(faltan))
        return redirect("operaciones:upload_evidencias", pk=a.pk)

    # Marcar la asignación como lista para supervisor
    a.estado = "en_revision_supervisor"
    a.finalizado_en = timezone.now()
    a.save(update_fields=["estado", "finalizado_en"])

    # ¿El PROYECTO completo (todos los obligatorios de todos los técnicos) está listo?
    s = a.sesion
    reqs_proj = (
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion__sesion=s, obligatorio=True)
        .annotate(n=Count("evidencias"))
    )
    todos_listos = not reqs_proj.filter(n=0).exists()

    if todos_listos:
        s.estado = "en_revision_supervisor"
        s.save(update_fields=["estado"])
    else:
        if s.estado in {"rechazado_supervisor", "asignado"}:
            s.estado = "en_proceso"
            s.save(update_fields=["estado"])

    messages.success(request, "Sent to supervisor review.")
    return redirect("operaciones:mis_assignments")


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def revisar_assignment(request, pk):
    """
    Compat: antes se revisaba por asignación.
    Ahora redirigimos a la revisión unificada por PROYECTO.
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk)
    return redirect("operaciones:revisar_sesion", sesion_id=a.sesion_id)


# ============================
# SUPERVISOR — Revisión POR PROYECTO (unificada)
# ============================

def _project_report_key(sesion: SesionBilling) -> str:
    """
    Ruta determinística para el reporte único por PROYECTO.
    Ej: operaciones/reporte_fotografico/<proj>/project/<proj>.xlsx
    """
    proj_slug = slugify(
        sesion.proyecto_id or f"billing-{sesion.id}") or f"billing-{sesion.id}"
    return f"operaciones/reporte_fotografico/{proj_slug}/project/{proj_slug}.xlsx"


@login_required
@rol_requerido('supervisor', 'admin', 'pm')
@transaction.atomic
def revisar_sesion(request, sesion_id):
    """
    Revisión unificada POR PROYECTO.
    - Supervisor aprueba/rechaza.
    - Al aprobar se genera y guarda UN Excel con imágenes embebidas.
    - Botones visibles si el proyecto está 'en_revision_supervisor'.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)

    asignaciones = (
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("evidencias__requisito")
         .all()
    )

    # Sincroniza estado del proyecto con sus asignaciones
    s.recomputar_estado_desde_asignaciones()

    can_review = s.estado in {"en_revision_supervisor"}

    if request.method == "POST":
        accion = request.POST.get("accion")
        comentario = (request.POST.get("comentario") or "").strip()

        if not can_review:
            messages.error(
                request, "This project is not ready for supervisor review.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        if accion == "aprobar":
            # Generar XLSX con imágenes embebidas
            try:
                bytes_excel = _bytes_excel_reporte_fotografico(s)
            except Exception as e:
                messages.error(request, f"No se pudo generar el informe: {e}")
                return redirect("operaciones:revisar_sesion", sesion_id=s.id)

            # Reemplazar archivo anterior si existía
            try:
                if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
                    s.reporte_fotografico.delete(save=False)
            except Exception:
                pass

            filename = f"REPORTE FOTOGRAFICO {s.proyecto_id}.xlsx"
            s.reporte_fotografico.save(
                filename, ContentFile(bytes_excel), save=False)

            # Actualizar estados
            s.estado = "aprobado_supervisor"
            s.save(update_fields=["reporte_fotografico", "estado"])

            now = timezone.now()
            for a in asignaciones:
                a.estado = "aprobado_supervisor"
                a.supervisor_comentario = comentario
                a.supervisor_revisado_en = now
                a.reintento_habilitado = False
                a.save(update_fields=[
                    "estado", "supervisor_comentario", "supervisor_revisado_en", "reintento_habilitado"
                ])

            messages.success(
                request, "Project approved by Supervisor. Photo report generated.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        elif accion == "rechazar":
            s.estado = "rechazado_supervisor"
            s.save(update_fields=["estado"])

            now = timezone.now()
            for a in asignaciones:
                a.estado = "rechazado_supervisor"
                a.supervisor_comentario = comentario or "Rejected."
                a.supervisor_revisado_en = now
                a.reintento_habilitado = True
                a.save(update_fields=[
                    "estado", "supervisor_comentario", "supervisor_revisado_en", "reintento_habilitado"
                ])

            messages.warning(
                request, "Project rejected. Reupload enabled for technicians.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

        else:
            messages.error(request, "Acción no reconocida.")
            return redirect("operaciones:revisar_sesion", sesion_id=s.id)

    # Para template
    evidencias_por_asig = []
    for a in asignaciones:
        evs = (a.evidencias
               .select_related("requisito")
               .order_by("requisito__orden", "tomada_en", "id"))
        evidencias_por_asig.append((a, evs))

    project_report_exists = bool(
        s.reporte_fotografico and getattr(s.reporte_fotografico, "name", "")
    )

    return render(request, "operaciones/billing_revisar_sesion.html", {
        "s": s,
        "evidencias_por_asig": evidencias_por_asig,
        "can_review": can_review,
        "project_report_exists": project_report_exists,
        "project_report_url": s.reporte_fotografico.url if project_report_exists else "",
    })


# ============================
# REPORTE FOTOGRÁFICO — PROYECTO
# ============================

@login_required
def descargar_reporte_fotos_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    # Permisos: supervisor/pm/admin o técnicos asignados al proyecto
    allowed = (getattr(request.user, "rol", "") in ("supervisor", "pm", "admin")) \
        or s.tecnicos_sesion.filter(tecnico=request.user).exists()
    if not allowed:
        raise Http404()

    if not s.reporte_fotografico or not storage_file_exists(s.reporte_fotografico):
        messages.warning(
            request, "El informe fotográfico no está disponible. Puedes generarlo nuevamente.")
        return redirect("operaciones:regenerar_reporte_fotografico_proyecto", sesion_id=s.id)

    return FileResponse(s.reporte_fotografico.open("rb"), as_attachment=True, filename="photo_report.xlsx")


def _bytes_excel_reporte_fotografico(sesion: SesionBilling) -> bytes:
    """
    XLSX con imágenes embebidas (2 por fila), sin notas.
    - Encabezado del bloque = nombre del requisito (centrado).
    - Imagen centrada en su recuadro y con borde.
    - Debajo: Taken at / Lat / Lng.
    - Gridlines ocultas.
    """
    import io
    import xlsxwriter
    from .models import EvidenciaFotoBilling

    # Todas las evidencias del proyecto en orden
    evs = (EvidenciaFotoBilling.objects
           .filter(tecnico_sesion__sesion=sesion)
           .select_related("requisito")
           .order_by("requisito__orden", "tomada_en", "id"))

    bio = io.BytesIO()
    wb = xlsxwriter.Workbook(bio, {"in_memory": True})
    ws = wb.add_worksheet("Reporte fotografico")

    # Ocultar cuadrícula (pantalla e impresión)
    ws.hide_gridlines(2)

    # ====== Formatos ======
    fmt_title = wb.add_format({
        "bold": True, "align": "center", "valign": "vcenter",
        "border": 1, "bg_color": "#E8EEF7"
    })
    fmt_head = wb.add_format({
        "border": 1, "align": "center", "valign": "vcenter",   # ← centrado
        "bold": True, "text_wrap": True, "bg_color": "#F5F7FB", "font_size": 11
    })
    fmt_box = wb.add_format({"border": 1})  # borde del recuadro de la imagen
    fmt_info = wb.add_format({
        "border": 1, "align": "center", "valign": "vcenter",
        "text_wrap": True, "font_size": 9
    })

    # ====== Layout (2 por fila) ======
    BLOCK_COLS = 6   # columnas por bloque
    SEP_COLS = 1   # columna separadora
    LEFT_COL = 0
    RIGHT_COL = LEFT_COL + BLOCK_COLS + SEP_COLS  # 7

    # Anchos de columnas
    for c in range(LEFT_COL, LEFT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)
    ws.set_column(LEFT_COL + BLOCK_COLS, LEFT_COL + BLOCK_COLS, 2)  # separador
    for c in range(RIGHT_COL, RIGHT_COL + BLOCK_COLS):
        ws.set_column(c, c, 13)

    # Alturas por bloque
    HEAD_ROWS = 1
    ROWS_IMG = 12
    ROW_INFO = 1
    ROW_SPACE = 1
    BLOCK_ROWS = HEAD_ROWS + ROWS_IMG + ROW_INFO

    # Título hoja
    ws.merge_range(0, 0, 0, RIGHT_COL + BLOCK_COLS - 1,
                   f"ID PROJECT: {sesion.proyecto_id}", fmt_title)

    cur_row = 2

    def draw_block(r, c, ev):
        # Encabezado del bloque: SOLO el nombre del requisito (centrado)
        titulo_req = (getattr(ev.requisito, "titulo", "") or "Extra").strip()
        ws.merge_range(r, c, r + HEAD_ROWS - 1, c + BLOCK_COLS - 1,
                       titulo_req, fmt_head)
        for rr in range(r, r + HEAD_ROWS):
            ws.set_row(rr, 20)

        # Área para la imagen (con borde)
        img_top = r + HEAD_ROWS
        for rr in range(img_top, img_top + ROWS_IMG):
            ws.set_row(rr, 18)
        ws.merge_range(img_top, c, img_top + ROWS_IMG -
                       1, c + BLOCK_COLS - 1, "", fmt_box)

        # Dimensiones del contenedor aprox (px)
        max_w_px = BLOCK_COLS * 60
        max_h_px = ROWS_IMG * 18

        # Leer imagen, escalar y centrar
        image_data = None
        x_scale = y_scale = 1.0
        scaled_w = scaled_h = None
        try:
            from PIL import Image
            ev.imagen.open("rb")
            raw = ev.imagen.read()
            image_data = io.BytesIO(raw)
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
            sx = max_w_px / float(w)
            sy = max_h_px / float(h)
            scale = min(sx, sy, 1.0)
            x_scale = y_scale = scale
            scaled_w = int(w * scale)
            scaled_h = int(h * scale)
        except Exception:
            try:
                ev.imagen.open("rb")
                image_data = io.BytesIO(ev.imagen.read())
                scaled_w = max_w_px
                scaled_h = max_h_px
            except Exception:
                image_data = None

        if image_data:
            x_off = max((max_w_px - (scaled_w or max_w_px)) // 2, 0)
            y_off = max((max_h_px - (scaled_h or max_h_px)) // 2, 0)
            ws.insert_image(img_top, c, ev.imagen.name, {
                "image_data": image_data,
                "x_scale": x_scale, "y_scale": y_scale,
                "x_offset": x_off, "y_offset": y_off,
                "object_position": 1,
            })

        # Fila de info: Taken / Lat / Lng
        info_row = img_top + ROWS_IMG
        t1c0, t1c1 = c,     c + 1
        t2c0, t2c1 = c + 2, c + 3
        t3c0, t3c1 = c + 4, c + 5

        dt = ev.client_taken_at or ev.tomada_en
        taken_txt = dt.strftime("%Y-%m-%d %H:%M") if dt else ""
        lat_txt = f"{float(ev.lat):.6f}" if ev.lat is not None else ""
        lng_txt = f"{float(ev.lng):.6f}" if ev.lng is not None else ""

        ws.merge_range(info_row, t1c0, info_row, t1c1,
                       f"Taken at\n{taken_txt}", fmt_info)
        ws.merge_range(info_row, t2c0, info_row, t2c1,
                       f"Lat\n{lat_txt}", fmt_info)
        ws.merge_range(info_row, t3c0, info_row, t3c1,
                       f"Lng\n{lng_txt}", fmt_info)
        ws.set_row(info_row, 30)

    # Pintar 2 por fila
    idx = 0
    for ev in evs:
        if idx % 2 == 0:
            draw_block(cur_row, LEFT_COL, ev)
        else:
            draw_block(cur_row, RIGHT_COL, ev)
            cur_row += BLOCK_ROWS + ROW_SPACE
        idx += 1
    if idx % 2 == 1:
        cur_row += BLOCK_ROWS + ROW_SPACE

    wb.close()
    return bio.getvalue()


@login_required
def regenerar_reporte_fotografico_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    # Solo supervisor/pm/admin
    if getattr(request.user, "rol", "") not in ("supervisor", "pm", "admin"):
        raise Http404()

    try:
        bytes_excel = _bytes_excel_reporte_fotografico(s)

        if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
            try:
                s.reporte_fotografico.delete(save=False)
            except Exception:
                pass

        filename = f"REPORTE FOTOGRAFICO {s.proyecto_id}.xlsx"
        s.reporte_fotografico.save(
            filename, ContentFile(bytes_excel), save=True)
        messages.success(
            request, "Informe fotográfico del proyecto regenerado.")
        return redirect("operaciones:descargar_reporte_fotos_proyecto", sesion_id=s.id)

    except Exception as e:
        messages.error(request, f"No se pudo generar el informe: {e}")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)


# ============================
# CONFIGURAR REQUISITOS (¡la que faltaba!)
# ============================

@login_required
@rol_requerido('supervisor', 'admin', 'pm')
def configurar_requisitos(request, sesion_id):
    """
    Crea/edita los requisitos de fotos por TÉCNICO dentro de un PROYECTO.
    Actualiza sin borrar todo:
      - Crea nuevos (id vacío)
      - Actualiza existentes (id presente)
      - Elimina solo los marcados en t<tecnico_id>_delete_id[]
    Espera arrays por técnico usando prefijo t<tecnico_id>_ en los names.
    """
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    # Traer requisitos para precarga ordenados por 'orden'
    asignaciones = (
        s.tecnicos_sesion
         .select_related("tecnico")
         .prefetch_related("requisitos")  # related_name='requisitos'
         .all()
    )

    if request.method == "POST":
        try:
            with transaction.atomic():
                for a in asignaciones:
                    prefix = f"t{a.tecnico_id}_"

                    # 1) Eliminar solo los que se marcaron para borrar
                    delete_ids = request.POST.getlist(prefix + "delete_id[]")
                    if delete_ids:
                        # filtrar a enteros válidos
                        del_ids = [int(x)
                                   for x in delete_ids if str(x).isdigit()]
                        if del_ids:
                            RequisitoFotoBilling.objects.filter(
                                tecnico_sesion=a, id__in=del_ids
                            ).delete()

                    # 2) Recibir arrays alineados por índice
                    ids = request.POST.getlist(prefix + "id[]")
                    titulos = request.POST.getlist(prefix + "titulo[]")
                    descs = request.POST.getlist(prefix + "desc[]")
                    obls = request.POST.getlist(prefix + "obl[]")   # "0" / "1"
                    ords = request.POST.getlist(prefix + "ord[]")

                    n = len(titulos)
                    for i in range(n):
                        titulo = (titulos[i] or "").strip()
                        if not titulo:
                            # si vino vacío, lo ignoramos (no crear/actualizar)
                            continue

                        rid = (ids[i] if i < len(ids) else "").strip()
                        descripcion = (descs[i].strip() if i < len(
                            descs) and descs[i] else "")
                        obligatorio = (obls[i] == "1") if i < len(
                            obls) else True
                        try:
                            orden = int(ords[i]) if i < len(ords) else i
                        except Exception:
                            orden = i

                        if rid and str(rid).isdigit():
                            # 2.1) Actualizar existente (si pertenece a la misma asignación)
                            RequisitoFotoBilling.objects.filter(
                                pk=int(rid), tecnico_sesion=a
                            ).update(
                                titulo=titulo,
                                descripcion=descripcion,
                                obligatorio=obligatorio,
                                orden=orden,
                            )
                        else:
                            # 2.2) Crear nuevo
                            RequisitoFotoBilling.objects.create(
                                tecnico_sesion=a,
                                titulo=titulo,
                                descripcion=descripcion,
                                obligatorio=obligatorio,
                                orden=orden,
                            )

            messages.success(request, "Photo requirements saved.")
            return redirect("operaciones:listar_billing")

        except Exception as e:
            messages.error(request, f"Could not save requirements: {e}")

    # GET: render con requisitos cargados
    # (si prefieres ordenados: en el template usa r.orden o ordena en el queryset)
    return render(
        request,
        "operaciones/billing_configurar_requisitos.html",
        {"sesion": s, "asignaciones": asignaciones},
    )


# ============================
# PM — Aprobación/Rechazo PROYECTO
# ============================

@login_required
@rol_requerido('pm', 'admin')
def pm_aprobar_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    if s.estado not in ("aprobado_supervisor",):
        messages.error(
            request, "El proyecto aún no está aprobado por Supervisor.")
        return redirect("operaciones:revisar_sesion", sesion_id=s.id)
    s.estado = "aprobado_pm"
    s.save(update_fields=["estado"])
    messages.success(request, "Proyecto aprobado por PM.")
    return redirect("operaciones:listar_billing")


@login_required
@rol_requerido('pm', 'admin')
def pm_rechazar_proyecto(request, sesion_id):
    s = get_object_or_404(SesionBilling, pk=sesion_id)
    s.estado = "rechazado_pm"
    s.save(update_fields=["estado"])
    messages.warning(request, "Proyecto rechazado por PM.")
    return redirect("operaciones:revisar_sesion", sesion_id=s.id)


# ============================
# ELIMINAR EVIDENCIA
# ============================

@login_required
@rol_requerido('usuario', 'supervisor', 'admin', 'pm')
@require_POST
def eliminar_evidencia(request, pk, evidencia_id):
    """
    El técnico puede borrar en 'en_proceso' o si fue rechazado con reintento.
    Supervisor/Admin/PM pueden borrar siempre (p. ej. desde la revisión).
    """
    a = get_object_or_404(SesionBillingTecnico, pk=pk)

    # ¿Quién es?
    is_owner = (a.tecnico_id == request.user.id)
    is_staff_role = getattr(request.user, "rol", None) in {
        "supervisor", "admin", "pm"}

    # Reglas para técnico
    can_owner_delete = (
        a.estado == "en_proceso" or
        (a.estado == "rechazado_supervisor" and a.reintento_habilitado)
    )

    if not (is_staff_role or (is_owner and can_owner_delete)):
        return HttpResponseForbidden("You can't delete photos at this stage.")

    ev = get_object_or_404(EvidenciaFotoBilling,
                           pk=evidencia_id, tecnico_sesion=a)

    # eliminar archivo físico si existe
    try:
        ev.imagen.delete(save=False)
    except Exception:
        pass
    ev.delete()

    messages.success(request, "Photo deleted.")

    next_url = request.POST.get("next") or reverse(
        "operaciones:upload_evidencias", args=[a.pk])
    return redirect(next_url)
