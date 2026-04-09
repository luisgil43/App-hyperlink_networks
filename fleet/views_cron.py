# fleet/views_cron.py
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (FlotaAlertaEnviada, FlotaCronDiarioEjecutado,
                     VehicleNotificationConfig, VehicleService,
                     VehicleServiceType)

logger = logging.getLogger(__name__)


def _get_logo_url() -> str:
    return getattr(
        settings,
        "PLANIX_LOGO_URL",
        "https://res.cloudinary.com/dm6gqg4fb/image/upload/v1751574704/planixb_a4lorr.jpg",
    )


def _split_csv_emails(raw: str) -> list[str]:
    if not raw:
        return []
    parts = [p.strip() for p in (raw or "").replace("\n", ",").split(",") if p.strip()]
    # dedupe
    seen = set()
    out = []
    for e in parts:
        k = e.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _build_recipients(cfg: VehicleNotificationConfig):
    to_emails = _split_csv_emails(cfg.extra_emails_to)
    cc_emails = _split_csv_emails(cfg.extra_emails_cc)

    if cfg.include_assigned_driver:
        asg = (
            cfg.vehicle.assignments.select_related("user")
            .filter(is_active=True)
            .order_by("-assigned_at")
            .first()
        )
        if asg and getattr(asg.user, "email", None):
            em = (asg.user.email or "").strip()
            if em:
                to_emails = [em] + to_emails

    # dedupe final
    def dedupe(lst):
        seen = set()
        out = []
        for e in lst:
            k = (e or "").strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(e.strip())
        return out

    return dedupe(to_emails), dedupe(cc_emails)


def _latest_service_for_type(vehicle_id: int, st_type: VehicleServiceType):
    return (
        VehicleService.objects.filter(
            vehicle_id=vehicle_id, service_type_obj_id=st_type.id
        )
        .order_by("-service_date", "-created_at", "-pk")
        .first()
    )


def _try_pg_advisory_lock(lock_key: int) -> bool:
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
        logger.exception("Fleet CRON: failed unlocking advisory lock")


def _send_email(
    *,
    subject: str,
    to_emails: list[str],
    cc_emails: list[str],
    text_body: str,
    html_body: str,
):
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=to_emails,
        cc=cc_emails or None,
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)


@require_http_methods(["GET", "HEAD"])
def cron_fleet_maintenances(request):
    """
    Token: ?token=FLOTA_CRON_TOKEN
    - 1 vez por día (FlotaCronDiarioEjecutado)
    - no antes de las 08:00 local (salvo force=1)
    - PRE: una vez por base_service + threshold
    - OVERDUE: una vez por día (sent_on = hoy)
    """
    token_recibido = (request.GET.get("token") or "").strip()
    token_esperado = (getattr(settings, "FLOTA_CRON_TOKEN", "") or "").strip()
    if not token_esperado or token_recibido != token_esperado:
        return HttpResponseForbidden("Forbidden")

    ahora = timezone.localtime()
    hoy = ahora.date()
    force_run = (request.GET.get("force") or "").strip() == "1"

    if ahora.hour < 8 and not force_run:
        return JsonResponse(
            {"status": "before-8am", "detail": "Not yet 08:00"}, status=200
        )

    import zlib

    lock_key = zlib.crc32(f"fleet_maintenances:{hoy.isoformat()}".encode("utf-8"))

    if not _try_pg_advisory_lock(lock_key):
        return JsonResponse(
            {"status": "already-running", "detail": "Another process is running"},
            status=200,
        )

    job_name = "fleet_maintenances"

    try:
        if force_run:
            FlotaCronDiarioEjecutado.objects.filter(nombre=job_name, fecha=hoy).delete()

        try:
            with transaction.atomic():
                _obj, created = FlotaCronDiarioEjecutado.objects.get_or_create(
                    nombre=job_name, fecha=hoy
                )
        except IntegrityError:
            created = False

        if not created and not force_run:
            return JsonResponse(
                {"status": "already-run", "detail": "Already executed today"},
                status=200,
            )

        sent = 0
        skipped = 0
        send_errors = 0
        logic_errors = 0
        last_error = None

        logo_url = _get_logo_url()

        cfgs = VehicleNotificationConfig.objects.select_related("vehicle").filter(
            enabled=True
        )

        tipos = VehicleServiceType.objects.filter(is_active=True).order_by("name")

        for cfg in cfgs:
            v = cfg.vehicle
            to_emails, cc_emails = _build_recipients(cfg)

            if not to_emails and not cc_emails:
                skipped += 1
                continue

            odo_now = int(v.kilometraje_actual or 0)

            for t in tipos:
                has_km = bool((t.interval_km or 0) > 0)
                has_days = bool((t.interval_days or 0) > 0)
                if not has_km and not has_days:
                    continue

                last = _latest_service_for_type(v.id, t)
                if not last:
                    continue

                # ======================
                # A) KM (miles)
                # ======================
                if has_km and last.kilometraje_declarado is not None:
                    try:
                        due_km = int(last.kilometraje_declarado) + int(t.interval_km)
                        remaining_km = due_km - odo_now

                        # overdue km daily
                        if remaining_km <= 0:
                            if t.notify_on_overdue:
                                already = FlotaAlertaEnviada.objects.filter(
                                    vehicle_id=v.id,
                                    service_type_id=t.id,
                                    base_service_id=last.id,
                                    mode="overdue_km",
                                    threshold=0,
                                    sent_on=hoy,
                                ).exists()

                                if not already:
                                    subject = f"[Hyperlink] Maintenance overdue (miles) - {t.name} - {v.patente}"
                                    text_body = (
                                        "Hello,\n\n"
                                        f"Vehicle {v.patente} has an overdue maintenance.\n"
                                        f"Type: {t.name}\n"
                                        f"Odometer: {odo_now}\n"
                                        f"Due at: {due_km}\n\n"
                                        "Generated automatically by Planix.\n"
                                    )

                                    html_body = f"""\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#f4f6f8;padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;box-shadow:0 5px 15px rgba(0,0,0,.10);font-family:system-ui,-apple-system,Segoe UI,Arial;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">⚠️ Maintenance overdue (miles)</h2>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehicle:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Type:</strong> {t.name}</li>
        <li><strong>Odometer:</strong> {odo_now:,}</li>
        <li><strong>Due at:</strong> {due_km:,}</li>
        <li><strong>Last service:</strong> {last.service_date:%Y-%m-%d} (odo {int(last.kilometraje_declarado):,})</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">Auto-generated. Do not reply.</p>
  </div>
</body>
</html>
"""
                                    try:
                                        _send_email(
                                            subject=subject,
                                            to_emails=to_emails or cc_emails,
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        send_errors += 1
                                        last_error = e.__class__.__name__
                                        logger.exception(
                                            "Fleet cron send error overdue_km veh=%s type=%s",
                                            v.id,
                                            t.id,
                                        )
                                    else:
                                        try:
                                            FlotaAlertaEnviada.objects.get_or_create(
                                                vehicle_id=v.id,
                                                service_type_id=t.id,
                                                base_service_id=last.id,
                                                mode="overdue_km",
                                                threshold=0,
                                                sent_on=hoy,
                                            )
                                        except IntegrityError:
                                            pass
                                        sent += 1
                            continue

                        # pre km steps
                        for threshold in t.get_km_steps() or []:
                            if remaining_km <= int(threshold):
                                already = FlotaAlertaEnviada.objects.filter(
                                    vehicle_id=v.id,
                                    service_type_id=t.id,
                                    base_service_id=last.id,
                                    mode="pre_km",
                                    threshold=int(threshold),
                                    sent_on__isnull=True,
                                ).exists()

                                if not already:
                                    subject = f"[Hyperlink] Maintenance due soon (miles) - {t.name} - {v.patente}"
                                    text_body = (
                                        "Hello,\n\n"
                                        f"Vehicle {v.patente} has maintenance due soon.\n"
                                        f"Type: {t.name}\n"
                                        f"Odometer: {odo_now}\n"
                                        f"Due at: {due_km}\n"
                                        f"Remaining: {remaining_km}\n\n"
                                        "Generated automatically by Planix.\n"
                                    )
                                    html_body = f"""\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#f4f6f8;padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;box-shadow:0 5px 15px rgba(0,0,0,.10);font-family:system-ui,-apple-system,Segoe UI,Arial;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">🔔 Maintenance due soon (miles)</h2>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehicle:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Type:</strong> {t.name}</li>
        <li><strong>Odometer:</strong> {odo_now:,}</li>
        <li><strong>Due at:</strong> {due_km:,}</li>
        <li><strong>Remaining:</strong> {remaining_km:,} (threshold {int(threshold):,})</li>
        <li><strong>Last service:</strong> {last.service_date:%Y-%m-%d}</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">Auto-generated. Do not reply.</p>
  </div>
</body>
</html>
"""
                                    try:
                                        _send_email(
                                            subject=subject,
                                            to_emails=to_emails or cc_emails,
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        send_errors += 1
                                        last_error = e.__class__.__name__
                                        logger.exception(
                                            "Fleet cron send error pre_km veh=%s type=%s",
                                            v.id,
                                            t.id,
                                        )
                                    else:
                                        try:
                                            FlotaAlertaEnviada.objects.get_or_create(
                                                vehicle_id=v.id,
                                                service_type_id=t.id,
                                                base_service_id=last.id,
                                                mode="pre_km",
                                                threshold=int(threshold),
                                                sent_on=None,
                                            )
                                        except IntegrityError:
                                            pass
                                        sent += 1

                                break

                    except Exception as e:
                        logic_errors += 1
                        last_error = e.__class__.__name__
                        logger.exception(
                            "Fleet cron logic error km veh=%s type=%s", v.id, t.id
                        )

                # ======================
                # B) DAYS
                # ======================
                if has_days:
                    try:
                        due_date = last.service_date + timedelta(
                            days=int(t.interval_days)
                        )
                        remaining_days = (due_date - hoy).days

                        if remaining_days <= 0:
                            if t.notify_on_overdue:
                                already = FlotaAlertaEnviada.objects.filter(
                                    vehicle_id=v.id,
                                    service_type_id=t.id,
                                    base_service_id=last.id,
                                    mode="overdue_days",
                                    threshold=0,
                                    sent_on=hoy,
                                ).exists()

                                if not already:
                                    subject = f"[Hyperlink] Maintenance overdue (days) - {t.name} - {v.patente}"
                                    text_body = (
                                        "Hello,\n\n"
                                        f"Vehicle {v.patente} has an overdue maintenance.\n"
                                        f"Type: {t.name}\n"
                                        f"Due date: {due_date:%Y-%m-%d}\n\n"
                                        "Generated automatically by Planix.\n"
                                    )

                                    html_body = f"""\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#f4f6f8;padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;box-shadow:0 5px 15px rgba(0,0,0,.10);font-family:system-ui,-apple-system,Segoe UI,Arial;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">⚠️ Maintenance overdue (days)</h2>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehicle:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Type:</strong> {t.name}</li>
        <li><strong>Due date:</strong> {due_date:%Y-%m-%d}</li>
        <li><strong>Last service:</strong> {last.service_date:%Y-%m-%d}</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">Auto-generated. Do not reply.</p>
  </div>
</body>
</html>
"""
                                    try:
                                        _send_email(
                                            subject=subject,
                                            to_emails=to_emails or cc_emails,
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        send_errors += 1
                                        last_error = e.__class__.__name__
                                        logger.exception(
                                            "Fleet cron send error overdue_days veh=%s type=%s",
                                            v.id,
                                            t.id,
                                        )
                                    else:
                                        try:
                                            FlotaAlertaEnviada.objects.get_or_create(
                                                vehicle_id=v.id,
                                                service_type_id=t.id,
                                                base_service_id=last.id,
                                                mode="overdue_days",
                                                threshold=0,
                                                sent_on=hoy,
                                            )
                                        except IntegrityError:
                                            pass
                                        sent += 1
                            continue

                        for threshold in t.get_day_steps() or []:
                            if remaining_days <= int(threshold):
                                already = FlotaAlertaEnviada.objects.filter(
                                    vehicle_id=v.id,
                                    service_type_id=t.id,
                                    base_service_id=last.id,
                                    mode="pre_days",
                                    threshold=int(threshold),
                                    sent_on__isnull=True,
                                ).exists()

                                if not already:
                                    subject = f"[Hyperlink] Maintenance due soon (days) - {t.name} - {v.patente}"
                                    text_body = (
                                        "Hello,\n\n"
                                        f"Vehicle {v.patente} has maintenance due soon.\n"
                                        f"Type: {t.name}\n"
                                        f"Due date: {due_date:%Y-%m-%d}\n"
                                        f"Remaining: {remaining_days} day(s)\n\n"
                                        "Generated automatically by Planix.\n"
                                    )

                                    html_body = f"""\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#f4f6f8;padding:20px;">
  <div style="max-width:640px;margin:auto;background:#fff;padding:28px;border-radius:12px;box-shadow:0 5px 15px rgba(0,0,0,.10);font-family:system-ui,-apple-system,Segoe UI,Arial;">
    <div style="text-align:center;margin-bottom:18px;">
      <img src="{logo_url}" alt="Planix" style="max-width:180px;height:auto;">
    </div>
    <h2 style="font-size:20px;margin:0 0 10px;color:#111827;">🔔 Maintenance due soon (days)</h2>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 18px;font-size:13px;color:#374151;">
      <ul style="margin:0;padding-left:18px;">
        <li><strong>Vehicle:</strong> {v.marca} {v.modelo} ({v.patente})</li>
        <li><strong>Type:</strong> {t.name}</li>
        <li><strong>Due date:</strong> {due_date:%Y-%m-%d}</li>
        <li><strong>Remaining:</strong> {remaining_days} day(s) (threshold {int(threshold)})</li>
        <li><strong>Last service:</strong> {last.service_date:%Y-%m-%d}</li>
      </ul>
    </div>
    <p style="font-size:12px;color:#9ca3af;margin-top:20px;text-align:center;">Auto-generated. Do not reply.</p>
  </div>
</body>
</html>
"""
                                    try:
                                        _send_email(
                                            subject=subject,
                                            to_emails=to_emails or cc_emails,
                                            cc_emails=cc_emails if to_emails else [],
                                            text_body=text_body,
                                            html_body=html_body,
                                        )
                                    except Exception as e:
                                        send_errors += 1
                                        last_error = e.__class__.__name__
                                        logger.exception(
                                            "Fleet cron send error pre_days veh=%s type=%s",
                                            v.id,
                                            t.id,
                                        )
                                    else:
                                        try:
                                            FlotaAlertaEnviada.objects.get_or_create(
                                                vehicle_id=v.id,
                                                service_type_id=t.id,
                                                base_service_id=last.id,
                                                mode="pre_days",
                                                threshold=int(threshold),
                                                sent_on=None,
                                            )
                                        except IntegrityError:
                                            pass
                                        sent += 1

                                break

                    except Exception as e:
                        logic_errors += 1
                        last_error = e.__class__.__name__
                        logger.exception(
                            "Fleet cron logic error days veh=%s type=%s", v.id, t.id
                        )

        ok = send_errors == 0 and logic_errors == 0

        return JsonResponse(
            {
                "status": "ok" if ok else "partial-error",
                "date": str(hoy),
                "force": force_run,
                "sent": sent,
                "skipped": skipped,
                "send_errors": send_errors,
                "logic_errors": logic_errors,
                "last_error": last_error,
            },
            status=200,
        )

    finally:
        _pg_advisory_unlock(lock_key)
