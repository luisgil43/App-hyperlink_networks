from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Dict, List, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from operaciones.models import (
    SesionBilling,
    SesionBillingTecnico,
    RequisitoFotoBilling,
    EvidenciaFotoBilling,
)


def _slug(s: str) -> str:
    return slugify((s or "").strip())


def _evid_count(req_id: int) -> int:
    return EvidenciaFotoBilling.objects.filter(requisito_id=req_id).count()


def _build_canon(reqs: List[RequisitoFotoBilling]) -> Dict[str, Tuple[str, int, bool]]:
    by_slug: Dict[str, List[RequisitoFotoBilling]] = defaultdict(list)
    for r in reqs:
        by_slug[_slug(r.titulo)].append(r)
    canon: Dict[str, Tuple[str, int, bool]] = {}
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
    return canon


def hard_sync_requirements(session_id: int) -> Dict[str, int]:
    s = SesionBilling.objects.get(pk=session_id)
    asign_ids = list(s.tecnicos_sesion.values_list("id", flat=True))
    reqs_qs = RequisitoFotoBilling.objects.filter(
        tecnico_sesion_id__in=asign_ids).select_related("tecnico_sesion")
    if not reqs_qs.exists():
        return dict(moved_photos=0, created_reqs=0, merged_dups=0, updated_reqs=0)

    canon = _build_canon(list(reqs_qs))
    moved_photos = created_reqs = merged_dups = updated_reqs = 0

    with transaction.atomic():
        for asign_id in asign_ids:
            bucket: Dict[str, List[RequisitoFotoBilling]] = defaultdict(list)
            for r in reqs_qs.filter(tecnico_sesion_id=asign_id):
                bucket[_slug(r.titulo)].append(r)

            for gslug, (titulo, orden, obligatorio) in canon.items():
                lst = bucket.get(gslug, [])
                if not lst:
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
                if changed:
                    keeper.save(update_fields=[
                                "titulo", "orden", "obligatorio"])
                    updated_reqs += 1

                for dup in [r for r in lst if r.id != keeper.id]:
                    q = EvidenciaFotoBilling.objects.filter(
                        requisito_id=dup.id)
                    moved = q.count()
                    if moved:
                        q.update(requisito_id=keeper.id)
                    moved_photos += moved
                    dup.delete()
                    merged_dups += 1

    return dict(moved_photos=moved_photos, created_reqs=created_reqs, merged_dups=merged_dups, updated_reqs=updated_reqs)


def reattach_orphans_by_hints(session_id: int, dry_run: bool) -> Dict[str, object]:
    s = SesionBilling.objects.get(pk=session_id)
    asign_ids = list(s.tecnicos_sesion.values_list("id", flat=True))
    if not asign_ids:
        return {"moved": 0, "skipped": 0, "ambiguous": 0, "dry_run": dry_run}

    reqs = list(RequisitoFotoBilling.objects.filter(
        tecnico_sesion_id__in=asign_ids))
    by_assign_slug: Dict[int, Dict[str, int]] = defaultdict(dict)
    for r in reqs:
        by_assign_slug[r.tecnico_sesion_id][_slug(r.titulo)] = r.id

    evs = EvidenciaFotoBilling.objects.filter(
        requisito__isnull=True, tecnico_sesion_id__in=asign_ids).order_by("id")
    moved = skipped = ambiguous = 0

    with transaction.atomic():
        for e in evs:
            candidates: List[Tuple[str, str, int]] = []

            note_s = _slug(getattr(e, "nota", ""))
            title_s = _slug(getattr(e, "titulo_manual", ""))
            try:
                fname = PurePosixPath(
                    getattr(e, "imagen", "").name or "").name.lower()
            except Exception:
                fname = (getattr(e, "imagen", "") or "").lower()

            slugs_map = by_assign_slug.get(e.tecnico_sesion_id, {})

            if note_s and note_s in slugs_map:
                candidates.append(("note", note_s, slugs_map[note_s]))
            if title_s and title_s in slugs_map:
                candidates.append(("manual", title_s, slugs_map[title_s]))
            for sl, rid in slugs_map.items():
                if sl and sl in fname:
                    candidates.append(("file", sl, rid))

            target = None
            for typ in ("note", "manual", "file"):
                opts = [(t, sl, rid)
                        for (t, sl, rid) in candidates if t == typ]
                uniq = list({rid: (t, sl, rid)
                            for (t, sl, rid) in opts}.values())
                if len(uniq) == 1:
                    target = uniq[0]
                    break
                if len(uniq) > 1:
                    target = ("ambiguous", typ, [u[2] for u in uniq])
                    break

            if target and target[0] != "ambiguous":
                _, sl, rid = target
                if not dry_run:
                    e.requisito_id = rid
                    e.save(update_fields=["requisito"])
                moved += 1
            elif target and target[0] == "ambiguous":
                ambiguous += 1
            else:
                skipped += 1

        if dry_run:
            transaction.set_rollback(True)

    return {"moved": moved, "skipped": skipped, "ambiguous": ambiguous, "dry_run": dry_run}


class Command(BaseCommand):
    help = "Arregla sesión: hard-sync de requisitos y reatacha evidencias huérfanas por nota/título/filename. Idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--session", type=int, default=174)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--only-if-prod", action="store_true")

    def handle(self, *args, **opts):
        session_id: int = opts["session"]
        dry = bool(opts["dry_run"])
        only_if_prod = bool(opts["only_if_prod"])

        if only_if_prod and settings.DEBUG:
            raise CommandError("Abortado: DEBUG=True y --only-if-prod activo.")

        # 1) Hard-sync requisitos
        r1 = hard_sync_requirements(session_id)
        self.stdout.write(self.style.WARNING(f"[hard-sync] {r1}"))

        # 2) Reattach de huérfanas (usa dry-run si se pide)
        r2 = reattach_orphans_by_hints(session_id, dry_run=dry)
        self.stdout.write(self.style.WARNING(f"[reattach] {r2}"))

        self.stdout.write(self.style.SUCCESS("OK"))
