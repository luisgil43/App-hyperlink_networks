# usuarios/schedulers.py
import os
import threading
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage as storage
from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from operaciones.models import (EvidenciaFotoBilling, ReporteFotograficoJob,
                                SesionBilling)

# ---------------- Scheduler ----------------


def iniciar_scheduler():
    """
    Arranca un BackgroundScheduler y lo cuelga en settings.APP_SCHEDULER.
    apps.py se encarga de no iniciarlo dos veces con el autoreloader.
    """
    scheduler = BackgroundScheduler()
    scheduler.start()
    settings.APP_SCHEDULER = scheduler
    return scheduler


# ---------------- Helpers ----------------


def _stable_report_key(s: SesionBilling) -> str:
    proj_slug = slugify(s.proyecto_id or f"billing-{s.id}") or f"billing-{s.id}"
    return f"operaciones/reporte_fotografico/{proj_slug}-{s.id}/project/{proj_slug}-{s.id}.xlsx"


class ReportCancelled(Exception):
    pass


def _make_should_cancel(job_id: int):
    last_check = {"n": 0}

    def should_cancel(n_processed: int = 0) -> bool:
        if n_processed and (n_processed - last_check["n"] < 10):
            return False
        last_check["n"] = n_processed
        return (
            ReporteFotograficoJob.objects.filter(pk=job_id, cancel_requested=True)
            .values_list("cancel_requested", flat=True)
            .first()
        ) or False

    return should_cancel


def _compute_next_monday_iso_week(now=None) -> str:
    now = now or timezone.now()
    days_to_next_monday = (7 - now.weekday()) % 7 or 7
    next_monday = now + timedelta(days=days_to_next_monday)
    y, w, _ = next_monday.isocalendar()
    return f"{int(y)}-W{int(w):02d}"


compute_next_monday_iso_week = _compute_next_monday_iso_week

# ---------------- FINAL ----------------


def procesar_reporte_fotografico_job(job_id: int):
    """Genera el XLSX FINAL… (igual que tenías)"""
    try:
        connection.close()
    except Exception:
        pass

    from operaciones.views_billing_exec import \
        _xlsx_path_reporte_fotografico_qs

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

        def _progress(n: int):
            if should_cancel(n):
                raise ReportCancelled()
            if n == 1 or n % 10 == 0 or n == job.total:
                ReporteFotograficoJob.objects.filter(pk=job.pk).update(procesadas=n)

        xlsx_path = _xlsx_path_reporte_fotografico_qs(
            s,
            ev_qs=None,
            progress_cb=_progress,
            should_cancel=should_cancel,
        )

        stable_key = _stable_report_key(s)
        try:
            storage.delete(stable_key)
        except Exception:
            pass
        try:
            if s.reporte_fotografico and getattr(s.reporte_fotografico, "name", ""):
                s.reporte_fotografico.delete(save=False)
        except Exception:
            pass

        with open(xlsx_path, "rb") as f:
            s.reporte_fotografico.save(stable_key, File(f), save=True)

        now = timezone.now()
        with transaction.atomic():
            s.estado = "aprobado_supervisor"
            if not s.semana_pago_real:
                s.semana_pago_real = _compute_next_monday_iso_week(now)
            s.save(update_fields=["reporte_fotografico", "estado", "semana_pago_real"])
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

        job.resultado_key = stable_key
        job.procesadas = job.total
        job.estado = "ok"
        job.terminado_en = timezone.now()
        job.save(
            update_fields=[
                "resultado_key",
                "procesadas",
                "estado",
                "terminado_en",
            ]
        )

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


def _run_in_thread(fn, *args):
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


def enqueue_reporte_fotografico(job_id: int):
    """
    Intenta APScheduler; si no hay, cae a un hilo en background.
    Así el request responde de inmediato SIEMPRE.
    """
    scheduler = getattr(settings, "APP_SCHEDULER", None)
    if scheduler:
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
    else:
        _run_in_thread(procesar_reporte_fotografico_job, job_id)


# ---------------- PARCIAL ----------------


def procesar_reporte_parcial_job(job_id: int):
    try:
        connection.close()
    except Exception:
        pass

    from operaciones.views_billing_exec import \
        _xlsx_path_reporte_fotografico_qs

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
    job.save(update_fields=["estado", "iniciado_en", "log", "total", "procesadas"])

    last_flush = {"n": 0}

    def _on_progress(n: int):
        if ReporteFotograficoJob.objects.filter(
            pk=job.pk, cancel_requested=True
        ).exists():
            job.log = (job.log or "") + "[partial] cancel requested\n"
            job.error = "Cancelled by user"
            job.estado = "error"
            job.terminado_en = timezone.now()
            job.save(update_fields=["log", "error", "estado", "terminado_en"])
            raise RuntimeError("cancelled")
        if n - last_flush["n"] >= 10 or n == job.total:
            ReporteFotograficoJob.objects.filter(pk=job.pk).update(procesadas=n)
            last_flush["n"] = n

    try:
        xlsx_path = _xlsx_path_reporte_fotografico_qs(
            job.sesion,
            ev_qs=None,
            progress_cb=_on_progress,
        )
        job.resultado_key = xlsx_path
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
        pass
    except Exception as e:
        job.error = str(e)
        job.estado = "error"
        job.terminado_en = timezone.now()
        job.log = (job.log or "") + f"[partial] error: {e}\n"
        job.save(update_fields=["error", "estado", "terminado_en", "log"])


def enqueue_reporte_parcial(job_id: int):
    scheduler = getattr(settings, "APP_SCHEDULER", None)
    if scheduler:
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
    else:
        _run_in_thread(procesar_reporte_parcial_job, job_id)


# ---------------- CABLE FINAL ----------------


def procesar_cable_photo_report_job(job_id: int):
    try:
        connection.close()
    except Exception:
        pass

    job = None
    tmp_path = None

    try:
        from cable_installation.views_revision_admin import (
            CableReportCancelled, _cable_report_evidences_qs,
            _cable_report_project_key, _xlsx_path_cable_photo_report)

        job = ReporteFotograficoJob.objects.select_related("sesion").get(pk=job_id)
        if job.estado in ("procesando", "ok"):
            return

        billing = job.sesion

        job.estado = "procesando"
        job.iniciado_en = timezone.now()
        job.total = _cable_report_evidences_qs(billing).count()
        job.procesadas = 0
        job.error = ""
        job.save(
            update_fields=["estado", "iniciado_en", "total", "procesadas", "error"]
        )

        def progress_cb(done, total_count):
            fresh = ReporteFotograficoJob.objects.get(pk=job.pk)
            if getattr(fresh, "cancel_requested", False):
                raise CableReportCancelled()
            fresh.procesadas = done
            fresh.total = total_count
            fresh.save(update_fields=["procesadas", "total"])

        def should_cancel(_done):
            return (
                ReporteFotograficoJob.objects.filter(pk=job.pk, cancel_requested=True)
                .values_list("cancel_requested", flat=True)
                .first()
            ) or False

        tmp_path = _xlsx_path_cable_photo_report(
            billing,
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )

        key_name = _cable_report_project_key(billing)

        try:
            storage.delete(key_name)
        except Exception:
            pass

        try:
            if billing.reporte_fotografico and getattr(
                billing.reporte_fotografico, "name", ""
            ):
                billing.reporte_fotografico.delete(save=False)
        except Exception:
            pass

        with open(tmp_path, "rb") as fh:
            billing.reporte_fotografico.save(
                key_name,
                File(fh),
                save=False,
            )

        now = timezone.now()
        with transaction.atomic():
            billing.estado = "aprobado_supervisor"
            if not billing.semana_pago_real:
                billing.semana_pago_real = _compute_next_monday_iso_week(now)
            billing.save(
                update_fields=["reporte_fotografico", "estado", "semana_pago_real"]
            )

            for a in billing.tecnicos_sesion.all():
                update_fields = ["estado"]
                a.estado = "aprobado_supervisor"

                if hasattr(a, "supervisor_revisado_en"):
                    a.supervisor_revisado_en = now
                    update_fields.append("supervisor_revisado_en")

                if hasattr(a, "reintento_habilitado"):
                    a.reintento_habilitado = False
                    update_fields.append("reintento_habilitado")

                a.save(update_fields=update_fields)

        job.estado = "ok"
        job.terminado_en = timezone.now()
        job.resultado_key = getattr(billing.reporte_fotografico, "name", "") or key_name
        job.procesadas = job.total
        job.save(
            update_fields=["estado", "terminado_en", "resultado_key", "procesadas"]
        )

    except Exception as e:
        if job:
            job.estado = "error"
            job.terminado_en = timezone.now()
            job.error = str(e)
            job.save(update_fields=["estado", "terminado_en", "error"])

    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


def enqueue_cable_photo_report(job_id: int):
    scheduler = getattr(settings, "APP_SCHEDULER", None)
    if scheduler:
        scheduler.add_job(
            func=procesar_cable_photo_report_job,
            args=[job_id],
            id=f"cable-repfoto-{job_id}",
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
            next_run_time=timezone.now(),
        )
    else:
        _run_in_thread(procesar_cable_photo_report_job, job_id)
