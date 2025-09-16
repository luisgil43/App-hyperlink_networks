# usuarios/schedulers.py

from django.core.files.storage import default_storage as storage
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings

from operaciones.views_billing_exec import _xlsx_path_reporte_fotografico_qs
from operaciones.models import (
    ReporteFotograficoJob,
    SesionBilling,
    EvidenciaFotoBilling,
)
from usuarios.utils import enviar_notificaciones_documentos_vencidos


# ============================
# Scheduler bootstrap
# ============================

def iniciar_scheduler():
    """
    Arranca un APScheduler de fondo, registra jobs recurrentes
    y lo expone en settings.APP_SCHEDULER para poder encolar trabajos puntuales.
    """
    scheduler = BackgroundScheduler()

    # Ejemplo de job recurrente (diario)
    scheduler.add_job(
        enviar_notificaciones_documentos_vencidos,
        trigger="interval",
        days=1,
        id="notificaciones_documentos",
        replace_existing=True,
    )

    scheduler.start()

    # 🔴 dejarlo accesible para enqueue_*()
    settings.APP_SCHEDULER = scheduler
    return scheduler


# ============================
# Helpers
# ============================

def _stable_report_key(s: SesionBilling) -> str:
    proj_slug = slugify(
        s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    return f"operaciones/reporte_fotografico/{proj_slug}-{s.id}/project/{proj_slug}-{s.id}.xlsx"


def _stable_partial_key(s: SesionBilling) -> str:
    proj_slug = slugify(
        s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    return f"operaciones/reporte_fotografico/{proj_slug}-{s.id}/partial/{proj_slug}-{s.id}.xlsx"


def _compute_next_monday_iso_week(now=None) -> str:
    """
    Devuelve 'YYYY-W##' de la semana del próximo lunes desde 'now'.
    Útil para sellar semana de pago real si aún no está definida.
    """
    now = now or timezone.now()
    # weekday(): 0=Lunes ... 6=Domingo
    days_to_next_monday = (7 - now.weekday()) % 7 or 7
    next_monday = now + timezone.timedelta(days=days_to_next_monday)
    y, w, _ = next_monday.isocalendar()
    return f"{int(y)}-W{int(w):02d}"


# ============================
# Photographic report (FINAL)
# ============================

def procesar_reporte_fotografico_job(job_id: int):
    job = ReporteFotograficoJob.objects.select_related("sesion").get(pk=job_id)
    if job.estado in ("procesando", "ok"):
        return

    job.estado = "procesando"
    job.iniciado_en = timezone.now()
    job.total = EvidenciaFotoBilling.objects.filter(
        tecnico_sesion__sesion=job.sesion
    ).count()
    job.procesadas = 0
    job.save(update_fields=["estado", "iniciado_en", "total", "procesadas"])

    s = job.sesion
    try:
        # 🔔 callback de progreso
        def _progress(n):
            # guarda cada 5 y al final
            if n == 1 or n % 5 == 0 or n == job.total:
                ReporteFotograficoJob.objects.filter(
                    pk=job.pk).update(procesadas=n)

        xlsx_path = _xlsx_path_reporte_fotografico_qs(
            s, ev_qs=None, progress_cb=_progress)

        stable_key = _stable_report_key(s)
        try:
            if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
                s.reporte_fotografico.delete(save=False)
        except Exception:
            pass

        with open(xlsx_path, "rb") as f:
            s.reporte_fotografico.save(stable_key, File(f), save=True)

        now = timezone.now()
        with transaction.atomic():
            if not s.semana_pago_real:
                s.semana_pago_real = _compute_next_monday_iso_week(now)
            s.estado = "aprobado_supervisor"
            s.save(update_fields=["reporte_fotografico",
                   "estado", "semana_pago_real"])
            for a in s.tecnicos_sesion.all():
                a.estado = "aprobado_supervisor"
                a.supervisor_revisado_en = now
                a.reintento_habilitado = False
                a.save(update_fields=[
                       "estado", "supervisor_revisado_en", "reintento_habilitado"])

        job.resultado_key = stable_key
        job.procesadas = job.total
        job.estado = "ok"
        job.terminado_en = timezone.now()
        job.save(update_fields=["resultado_key",
                 "procesadas", "estado", "terminado_en"])

    except Exception as e:
        job.error = str(e)
        job.estado = "error"
        job.terminado_en = timezone.now()
        job.save(update_fields=["error", "estado", "terminado_en"])


def enqueue_reporte_fotografico(job_id: int):
    """
    Encola el job FINAL para correr inmediatamente usando settings.APP_SCHEDULER.
    Si no hay scheduler expuesto, ejecuta inline como fallback.
    """
    scheduler = getattr(settings, "APP_SCHEDULER", None)
    if scheduler is None:
        # Fallback síncrono
        procesar_reporte_fotografico_job(job_id)
        return

    scheduler.add_job(
        func=procesar_reporte_fotografico_job,
        args=[job_id],
        id=f"repfoto-{job_id}",
        replace_existing=True,
        misfire_grace_time=300,
        max_instances=1,
        coalesce=True,
        next_run_time=timezone.now(),
    )


# ============================
# Photographic report (PARCIAL)
# ============================

def procesar_reporte_parcial_job(job_id: int):
    """
    Genera el REPORTE FOTOGRÁFICO PARCIAL en background.
    - No cambia estados.
    - NO sube a Wasabi; deja un archivo temporal local y guarda su path en job.resultado_key.
    - Actualiza progreso (job.procesadas) durante el build.
    """
    from operaciones.views_billing_exec import _xlsx_path_reporte_fotografico_qs

    job = ReporteFotograficoJob.objects.select_related("sesion").get(pk=job_id)
    if job.estado in ("procesando", "ok"):
        return

    job.estado = "procesando"
    job.iniciado_en = timezone.now()
    job.log = (job.log or "") + "[partial] start\n"
    job.total = EvidenciaFotoBilling.objects.filter(
        tecnico_sesion__sesion=job.sesion
    ).count()
    job.procesadas = 0
    job.save(update_fields=["estado", "iniciado_en",
             "log", "total", "procesadas"])

    s = job.sesion

    # callback de progreso (throttle para no escribir en DB en cada foto)
    last_flush = {"n": 0}

    def _on_progress(n: int):
        job.procesadas = n
        # guarda cada 5 fotos o al final
        if n - last_flush["n"] >= 5 or n == job.total:
            ReporteFotograficoJob.objects.filter(
                pk=job.pk).update(procesadas=n)
            last_flush["n"] = n

    try:
        # genera XLSX a disco con progreso
        xlsx_path = _xlsx_path_reporte_fotografico_qs(
            s, ev_qs=None, progress_cb=_on_progress)

        # NO subir a Wasabi: guardar path local (sirve para descargar luego)
        job.resultado_key = xlsx_path
        job.estado = "ok"
        job.terminado_en = timezone.now()
        job.log = (job.log or "") + "[partial] done\n"
        job.save(update_fields=["resultado_key",
                 "estado", "terminado_en", "log", "procesadas"])

    except Exception as e:
        job.error = str(e)
        job.estado = "error"
        job.terminado_en = timezone.now()
        job.log = (job.log or "") + f"[partial] error: {e}\n"
        job.save(update_fields=["error", "estado", "terminado_en", "log"])


def enqueue_reporte_parcial(job_id: int):
    """
    Encola el job PARCIAL para correr ahora (usa settings.APP_SCHEDULER).
    Si no hay scheduler, ejecuta inline como fallback.
    """
    scheduler = getattr(settings, "APP_SCHEDULER", None)
    if scheduler is None:
        procesar_reporte_parcial_job(job_id)
        return

    scheduler.add_job(
        func=procesar_reporte_parcial_job,
        args=[job_id],
        id=f"repfoto-partial-{job_id}",
        replace_existing=True,
        misfire_grace_time=300,
        max_instances=1,
        coalesce=True,
        next_run_time=timezone.now(),
    )
