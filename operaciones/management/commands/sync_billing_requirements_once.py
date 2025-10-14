from collections import Counter, defaultdict
from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify
from django.db import transaction
from django.conf import settings

from operaciones.models import (
    SesionBilling,
    RequisitoFotoBilling,
    EvidenciaFotoBilling,
)


def _slug(s: str) -> str:
    return slugify((s or "").strip())


def _evid_count(req_id: int) -> int:
    return EvidenciaFotoBilling.objects.filter(requisito_id=req_id).count()


def sync_session(session_id: int, dry_run: bool = False) -> dict:
    s = SesionBilling.objects.get(pk=session_id)

    asign_ids = list(s.tecnicos_sesion.values_list("id", flat=True))
    reqs_qs = (
        RequisitoFotoBilling.objects
        .filter(tecnico_sesion_id__in=asign_ids)
        .select_related("tecnico_sesion")
    )
    if not reqs_qs.exists():
        return {"moved_photos": 0, "created_reqs": 0, "merged_dups": 0, "updated_reqs": 0, "dry_run": dry_run}

    by_slug = defaultdict(list)
    for r in reqs_qs:
        by_slug[_slug(r.titulo)].append(r)

    canon = {}
    for gslug, lst in by_slug.items():
        t_counts = Counter([(r.titulo or "").strip() for r in lst])
        titulo = max(t_counts.items(), key=lambda kv: (
            kv[1], len(kv[0])))[0] or gslug
        ord_counts = Counter([getattr(r, "orden", 0) for r in lst])
        orden = ord_counts.most_common(1)[0][0]
        obl_counts = Counter(
            [bool(getattr(r, "obligatorio", True)) for r in lst])
        obligatorio = obl_counts.most_common(1)[0][0]
        canon[gslug] = (titulo, orden, obligatorio)

    moved_photos = created_reqs = merged_dups = updated_reqs = 0

    with transaction.atomic():
        for asign_id in asign_ids:
            bucket = defaultdict(list)
            for r in reqs_qs.filter(tecnico_sesion_id=asign_id):
                bucket[_slug(r.titulo)].append(r)

            for gslug, (titulo, orden, obligatorio) in canon.items():
                lst = bucket.get(gslug, [])
                if not lst:
                    if not dry_run:
                        RequisitoFotoBilling.objects.create(
                            tecnico_sesion_id=asign_id,
                            titulo=titulo,
                            descripcion="",
                            obligatorio=obligatorio,
                            orden=orden,
                        )
                    created_reqs += 1
                    continue

                lst_counts = sorted([(r, _evid_count(r.id))
                                    for r in lst], key=lambda t: (t[1], -t[0].id))
                keeper = lst_counts[-1][0] if lst_counts else lst[0]

                changed = False
                if (keeper.titulo or "").strip() != titulo.strip():
                    keeper.titulo = titulo
                    changed = True
                if getattr(keeper, "orden", 0) != orden:
                    keeper.orden = orden
                    changed = True
                if bool(getattr(keeper, "obligatorio", True)) != bool(obligatorio):
                    keeper.obligatorio = obligatorio
                    changed = True
                if changed and not dry_run:
                    keeper.save(update_fields=[
                                "titulo", "orden", "obligatorio"])
                if changed:
                    updated_reqs += 1

                for dup in [r for r in lst if r.id != keeper.id]:
                    q = EvidenciaFotoBilling.objects.filter(
                        requisito_id=dup.id)
                    moved = q.count()
                    if moved and not dry_run:
                        q.update(requisito_id=keeper.id)
                    moved_photos += moved
                    if not dry_run:
                        dup.delete()
                    merged_dups += 1

        if dry_run:
            transaction.set_rollback(True)

    return {
        "moved_photos": moved_photos,
        "created_reqs": created_reqs,
        "merged_dups": merged_dups,
        "updated_reqs": updated_reqs,
        "dry_run": dry_run,
    }


class Command(BaseCommand):
    help = "Sincroniza requisitos/evidencias por sesión (idempotente). Útil para reparar sesiones desincronizadas."

    def add_arguments(self, parser):
        parser.add_argument("--session", type=int, default=174)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--only-if-prod", action="store_true")

    def handle(self, *args, **opts):
        session_id = opts["session"]
        dry = bool(opts["dry_run"])
        only_if_prod = bool(opts["only_if_prod"])

        if only_if_prod and settings.DEBUG:
            raise CommandError("Abortado: DEBUG=True y --only-if-prod activo.")

        res = sync_session(session_id, dry_run=dry)
        self.stdout.write(self.style.SUCCESS(str(res)))
