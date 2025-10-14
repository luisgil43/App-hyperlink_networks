# operaciones/management/commands/reconstruir_evidencias_wasabi.py

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils.text import slugify
from operaciones.models import SesionBilling, SesionBillingTecnico, EvidenciaFotoBilling
import boto3


def _is_safe_key(k: str) -> bool:
    return bool(k) and ".." not in k and not k.startswith("/")


class Command(BaseCommand):
    help = "Reconstruye EvidenciaFotoBilling de una sesión leyendo archivos existentes en Wasabi."

    def add_arguments(self, parser):
        parser.add_argument("sesion_id", type=int, help="ID de SesionBilling")
        parser.add_argument(
            "--prefix", default="operaciones/reporte_fotografico", help="Prefijo base en el bucket")

    def handle(self, *args, **opts):
        sesion_id = opts["sesion_id"]
        base = opts["prefix"]

        try:
            s = SesionBilling.objects.get(pk=sesion_id)
        except SesionBilling.DoesNotExist:
            raise CommandError("Sesión no encontrada")

        proj_slug = slugify(s.proyecto_id or "project") or "project"

        # Configuración de conexión a Wasabi usando boto3
        session = boto3.session.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )
        s3 = session.client(
            "s3",
            endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL", None),
        )
        bucket = settings.AWS_STORAGE_BUCKET_NAME

        creadas = 0
        for ts in s.tecnicos_sesion.select_related("tecnico").all():
            name = getattr(ts.tecnico, "get_full_name", lambda: "")(
            ) or ts.tecnico.username or f"user-{ts.tecnico_id}"
            tech_slug = slugify(name) or f"user-{ts.tecnico_id}"
            prefix = f"{base}/{proj_slug}/{tech_slug}/evidencia/"

            cont = None
            while True:
                resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, ContinuationToken=cont) if cont else \
                    s3.list_objects_v2(Bucket=bucket, Prefix=prefix)

                for obj in resp.get("Contents", []):
                    key = obj["Key"]
                    if not _is_safe_key(key):
                        continue
                    # Verifica si ya existe en BD
                    if not EvidenciaFotoBilling.objects.filter(tecnico_sesion=ts, imagen=key).exists():
                        EvidenciaFotoBilling.objects.create(
                            tecnico_sesion=ts,
                            requisito=None,
                            imagen=key,
                            nota="(rehidratada)",
                        )
                        creadas += 1

                if resp.get("IsTruncated"):
                    cont = resp.get("NextContinuationToken")
                else:
                    break

        self.stdout.write(self.style.SUCCESS(
            f"OK. Evidencias creadas: {creadas}"))
