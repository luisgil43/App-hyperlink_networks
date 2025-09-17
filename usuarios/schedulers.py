# usuarios/schedulers.py
from django.core.files.storage import default_storage as storage
from django.core.files import File
from django.db import transaction, connection
from django.utils import timezone
from django.utils.text import slugify
from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from datetime import timedelta

from operaciones.models import (
    ReporteFotograficoJob,
    SesionBilling,
    EvidenciaFotoBilling,
)

# ---------------- Scheduler ----------------


def iniciar_scheduler():
    """
    Arranca un BackgroundScheduler y lo cuelga en settings.APP_SCHEDULER.
    apps.py se encarga de que no se arranque dos veces con el autoreloader.
    """
    scheduler = BackgroundScheduler()
    scheduler.start()
    settings.APP_SCHEDULER = scheduler
    return scheduler

# ---------------- Helpers ----------------


def _stable_report_key(s: SesionBilling) -> str:
    """
    Key estable en el storage para el XLSX final del proyecto.
    """
    proj_slug = slugify(
        s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    return f"operaciones/reporte_fotografico/{proj_slug}-{s.id}/project/{proj_slug}-{s.id}.xlsx"


class ReportCancelled(Exception):
    """Señal de cancelación por el usuario durante la generación."""
    pass


def _make_should_cancel(job_id: int):
    """
    Devuelve una función que consulta con 'throttling' si cancel_requested=True.
    """
    last_check = {"n": 0}

    def should_cancel(n_processed: int = 0) -> bool:
        # consulta cada 10 fotos (o en la llamada inicial n==0)
        if n_processed and (n_processed - last_check["n"] < 10):
            return False
        last_check["n"] = n_processed
        return (
            ReporteFotograficoJob.objects.filter(
                pk=job_id, cancel_requested=True
            ).values_list("cancel_requested", flat=True).first()
            or False
        )

    return should_cancel

# ---------------- FINAL (reporte definitivo) ----------------


def _compute_next_monday_iso_week(now=None) -> str:
    """
    Devuelve 'YYYY-W##' correspondiente al próximo lunes desde 'now'.
    Útil para rellenar semana_pago_real cuando aún no está definida.
    """
    now = now or timezone.now()
    # weekday(): 0 = lunes ... 6 = domingo
    days_to_next_monday = (7 - now.weekday()) % 7 or 7
    next_monday = now + timedelta(days=days_to_next_monday)
    y, w, _ = next_monday.isocalendar()
    return f"{int(y)}-W{int(w):02d}"


# Alias público sin guión bajo
compute_next_monday_iso_week = _compute_next_monday_iso_week


def procesar_reporte_fotografico_job(job_id: int):
    """
    Genera el XLSX FINAL del proyecto y lo guarda en el FileField estable.
    Maneja progreso, cancelación y actualización de estados.
    """
    # Asegura que este hilo no herede una conexión/txn abierta
    try:
        connection.close()
    except Exception:
        pass

    # Import perezoso para evitar ciclo con operaciones.views_billing_exec
    from operaciones.views_billing_exec import _xlsx_path_reporte_fotografico_qs

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
    should_cancel = _make_should_cancel(job.id)

    try:
        # progreso: guarda cada 10 y al final
        def _progress(n: int):
            if should_cancel(n):
                raise ReportCancelled()
            if n == 1 or n % 10 == 0 or n == job.total:
                ReporteFotograficoJob.objects.filter(
                    pk=job.pk).update(procesadas=n)

        # Genera XLSX a disco (streaming)
        xlsx_path = _xlsx_path_reporte_fotografico_qs(
            s, ev_qs=None, progress_cb=_progress, should_cancel=should_cancel
        )

        # ---------------- Reemplazo FINAL con limpieza defensiva ----------------
        stable_key = _stable_report_key(s)

        # Borra por key directo en el storage (aunque el FileField no lo apunte)
        try:
            storage.delete(stable_key)
        except Exception:
            pass

        # Intenta también borrar lo que apunte el FileField
        try:
            if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
                s.reporte_fotografico.delete(save=False)
        except Exception:
            # No bloquear por errores de borrado
            pass

        # Guarda el nuevo XLSX con el mismo key estable (ruta optimizada)
        with open(xlsx_path, "rb") as f:
            s.reporte_fotografico.save(stable_key, File(f), save=True)
        # -----------------------------------------------------------------------

        now = timezone.now()

        # Actualiza estados del proyecto y asignaciones
        with transaction.atomic():
            s.estado = "aprobado_supervisor"
            if not s.semana_pago_real:
                s.semana_pago_real = _compute_next_monday_iso_week(now)
            s.save(update_fields=["reporte_fotografico",
                   "estado", "semana_pago_real"])

            for a in s.tecnicos_sesion.all():
                a.estado = "aprobado_supervisor"
                a.supervisor_revisado_en = now
                a.reintento_habilitado = False
                a.save(
                    update_fields=[
                        "estado",
                        "supervisor_revisado_en",
                        "reintento_habilitado",
                    ]
                )

        # Finaliza job
        job.resultado_key = stable_key
        job.procesadas = job.total
        job.estado = "ok"
        job.terminado_en = timezone.now()
        job.save(update_fields=["resultado_key",
                 "procesadas", "estado", "terminado_en"])

    except ReportCancelled:
        job.error = "Cancelled by user"
        job.estado = "error"
        job.terminado_en = timezone.now()
        job.save(update_fields=["error", "estado", "terminado_en"])
    except Exception as e:
        job.error = str(e)
        job.estado = "error"
        job.terminado_en = timezone.now()
        job.save(update_fields=["error", "estado", "terminado_en"])


def enqueue_reporte_fotografico(job_id: int):
    """
    Encola o ejecuta inmediatamente el job FINAL según haya scheduler.
    """
    scheduler = getattr(settings, "APP_SCHEDULER", None)
    if scheduler is None:
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

# ---------------- PARCIAL (reporte parcial) ----------------


def procesar_reporte_parcial_job(job_id: int):
    """
    Genera un XLSX PARCIAL y guarda la ruta local en resultado_key.
    Respeta cancel_requested y va escribiendo progreso.
    """
    # Asegura que este hilo no herede una conexión/txn abierta
    try:
        connection.close()
    except Exception:
        pass

    # Import perezoso para evitar ciclo con operaciones.views_billing_exec
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

    # Throttle de escritura y chequeo de cancelación
    last_flush = {"n": 0}

    def _on_progress(n: int):
        # cancelar si el usuario lo pidió
        if ReporteFotograficoJob.objects.filter(pk=job.pk, cancel_requested=True).exists():
            job.log = (job.log or "") + "[partial] cancel requested\n"
            job.error = "Cancelled by user"
            job.estado = "error"  # o 'cancelado' si tienes ese estado
            job.terminado_en = timezone.now()
            job.save(update_fields=["log", "error", "estado", "terminado_en"])
            raise RuntimeError("cancelled")  # abortar generación

        job.procesadas = n
        if n - last_flush["n"] >= 10 or n == job.total:
            ReporteFotograficoJob.objects.filter(
                pk=job.pk).update(procesadas=n)
            last_flush["n"] = n

    try:
        # genera XLSX a DISCO – siempre un archivo nuevo
        xlsx_path = _xlsx_path_reporte_fotografico_qs(
            s, ev_qs=None, progress_cb=_on_progress
        )

        job.resultado_key = xlsx_path  # ruta local del archivo parcial
        job.estado = "ok"
        job.terminado_en = timezone.now()
        job.log = (job.log or "") + "[partial] done\n"
        job.save(
            update_fields=[
                "resultado_key",
                "estado",
                "terminado_en",
                "log",
                "procesadas",
            ]
        )

    except RuntimeError:
        # cancelado: ya se guardó arriba
        pass
    except Exception as e:
        job.error = str(e)
        job.estado = "error"
        job.terminado_en = timezone.now()
        job.log = (job.log or "") + f"[partial] error: {e}\n"
        job.save(update_fields=["error", "estado", "terminado_en", "log"])


def enqueue_reporte_parcial(job_id: int):
    """
    Encola o ejecuta inmediatamente el job PARCIAL según haya scheduler.
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
