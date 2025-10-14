# utils/rehidratacion.py (por ejemplo)
from utils.rehidratacion import backfill_from_s3_metadata
from django.core.management.base import BaseCommand
import boto3
from django.conf import settings
from django.utils.dateparse import parse_datetime
from operaciones.models import EvidenciaFotoBilling


def backfill_from_s3_metadata(ev: EvidenciaFotoBilling) -> bool:
    """
    Intenta recuperar titulo_manual, direccion_manual, client_taken_at, lat, lng
    desde los metadatos S3 del objeto ev.imagen (si existen).
    Retorna True si se actualizó algo.
    """
    if not ev or not getattr(ev.imagen, "name", ""):
        return False

    s3 = boto3.client(
        "s3",
        endpoint_url=getattr(settings, "WASABI_ENDPOINT_URL",
                             "https://s3.us-east-1.wasabisys.com"),
        region_name=getattr(settings, "WASABI_REGION_NAME", "us-east-1"),
        aws_access_key_id=getattr(settings, "WASABI_ACCESS_KEY_ID"),
        aws_secret_access_key=getattr(settings, "WASABI_SECRET_ACCESS_KEY"),
    )
    bucket = getattr(settings, "WASABI_BUCKET_NAME")
    key = ev.imagen.name

    try:
        resp = s3.head_object(Bucket=bucket, Key=key)
    except Exception:
        return False

    md = {k.lower(): v for k, v in (resp.get("Metadata") or {}).items()}
    changed = False

    # address -> direccion_manual
    addr = (md.get("address") or "").strip()
    if addr and not (ev.direccion_manual or "").strip():
        ev.direccion_manual = addr
        changed = True

    # title -> titulo_manual (si es proyecto especial + extra)
    title = (md.get("title") or "").strip()
    if title and (ev.requisito_id is None) and not (ev.titulo_manual or "").strip():
        ev.titulo_manual = title
        changed = True

    # taken_at -> client_taken_at
    taken = (md.get("taken_at") or "").strip()
    if taken and not ev.client_taken_at:
        dt = parse_datetime(taken)
        if dt:
            ev.client_taken_at = dt
            changed = True

    # lat/lng
    def _to_float(s):
        try:
            return float(s)
        except Exception:
            return None

    if ev.lat is None and md.get("lat"):
        v = _to_float(md.get("lat"))
        if v is not None:
            ev.lat = v
            changed = True

    if ev.lng is None and md.get("lng"):
        v = _to_float(md.get("lng"))
        if v is not None:
            ev.lng = v
            changed = True

    if changed:
        ev.save(update_fields=["direccion_manual",
                "titulo_manual", "client_taken_at", "lat", "lng"])
    return changed


# management command ej.: python manage.py backfill_evidencias


class Command(BaseCommand):
    help = "Rellena titulo_manual/direccion_manual/fecha/lat/lng desde metadatos S3."

    def handle(self, *args, **kwargs):
        qs = EvidenciaFotoBilling.objects.filter(
            # típicamente las especiales son requisito nulo
            requisito__isnull=True
        ).filter(
            # sin título o sin dirección
            # (ajusta a tus criterios)
        )
        ok = fail = 0
        for ev in qs.iterator():
            try:
                if backfill_from_s3_metadata(ev):
                    ok += 1
            except Exception:
                fail += 1
        self.stdout.write(self.style.SUCCESS(
            f"Actualizadas: {ok}, fallidas: {fail}"))
