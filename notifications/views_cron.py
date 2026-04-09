# notifications/views_cron.py
from __future__ import annotations

import logging
import zlib
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.test import RequestFactory
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import CronDailyRun

logger = logging.getLogger(__name__)


def _try_pg_advisory_lock(lock_key: int) -> bool:
    """
    Lock cross-process sin migraciones (solo Postgres).
    Retorna True si tomó el lock; False si ya hay otro proceso corriendo.
    """
    if connection.vendor != "postgresql":
        return True
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s);", [lock_key])
        row = cur.fetchone()
        return bool(row and row[0])


def _pg_advisory_unlock(lock_key: int) -> None:
    if connection.vendor != "postgresql":
        return
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s);", [lock_key])
    except Exception:
        logger.exception("CRON general: fallo liberando advisory lock")


def _call_fleet_subcron(force: bool = False):
    """
    Llama al subcron de Fleet (sin HTTP real, usando RequestFactory).
    """
    from fleet.views_cron import \
        cron_fleet_maintenances  # import local para evitar circular

    rf = RequestFactory()
    fleet_token = (getattr(settings, "FLOTA_CRON_TOKEN", "") or "").strip()

    qs = f"token={fleet_token}"
    if force:
        qs += "&force=1"

    req = rf.get(f"/fleet/cron/mantenciones/?{qs}")
    # Importante: el view de fleet ya valida token y maneja horario/force.
    return cron_fleet_maintenances(req)


@require_http_methods(["GET", "HEAD"])
def cron_daily_general(request):
    """
    CRON GENERAL (por ahora solo Flota)
    URL: /cron/diario/?token=...
    - Token único: CRON_GENERAL_TOKEN
    - Una vez por día (CronDailyRun)
    - No antes de las 08:00 local (salvo force=1)
    - Lock cross-process (advisory lock en Postgres)
    - Llama subcrons (hoy solo flota)
    """
    token_recibido = (request.GET.get("token") or "").strip()
    token_esperado = (getattr(settings, "CRON_GENERAL_TOKEN", "") or "").strip()
    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    hoy = ahora.date()
    force_run = (request.GET.get("force") or "").strip() == "1"

    if ahora.hour < 8 and not force_run:
        return JsonResponse(
            {"status": "before-8am", "detail": "Not yet 08:00 local time"},
            status=200,
        )

    # Advisory lock estable (no usar hash())
    lock_key = zlib.crc32(f"cron_general:{hoy.isoformat()}".encode("utf-8"))
    if not _try_pg_advisory_lock(lock_key):
        return JsonResponse(
            {"status": "already-running", "detail": "Another process is running"},
            status=200,
        )

    job_name = "cron_general"

    try:
        if force_run:
            CronDailyRun.objects.filter(name=job_name, run_date=hoy).delete()

        # Lock diario por DB
        try:
            with transaction.atomic():
                _obj, created = CronDailyRun.objects.get_or_create(
                    name=job_name,
                    run_date=hoy,
                    defaults={"ok": True, "log": ""},
                )
        except IntegrityError:
            created = False

        if not created and not force_run:
            return JsonResponse(
                {"status": "already-run", "detail": "Already executed today"},
                status=200,
            )

        summary = {
            "fleet": None,
        }
        ok = True
        logs = []

        # ======================
        # 1) Fleet subcron
        # ======================
        try:
            resp = _call_fleet_subcron(force=force_run)
            # resp es JsonResponse del subcron
            try:
                data = getattr(resp, "json", None)
                if callable(data):
                    payload = resp.json()
                else:
                    # Django JsonResponse no siempre trae .json()
                    import json

                    payload = json.loads(resp.content.decode("utf-8") or "{}")
            except Exception:
                payload = {"status": "unknown", "detail": "Could not parse response"}

            summary["fleet"] = payload
            if payload.get("status") not in (
                "ok",
                "partial-error",
                "already-run",
                "before-8am",
                "already-running",
            ):
                ok = False
        except Exception as e:
            ok = False
            summary["fleet"] = {"status": "error", "error": e.__class__.__name__}
            logger.exception("CRON general: error calling fleet subcron")
            logs.append(f"fleet error: {e.__class__.__name__}")

        # Persistir resultado
        try:
            CronDailyRun.objects.filter(name=job_name, run_date=hoy).update(
                ok=bool(ok),
                log="\n".join(logs)[:10000],
            )
        except Exception:
            logger.exception("CRON general: failed saving CronDailyRun status")

        return JsonResponse(
            {
                "status": "ok" if ok else "partial-error",
                "date": str(hoy),
                "force": force_run,
                "subcrons": summary,
            },
            status=200,
        )

    finally:
        _pg_advisory_unlock(lock_key)
