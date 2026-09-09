from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from operaciones.models import BillingAssignmentQueue, SesionBillingTecnico

QUEUE_STATES = {
    "asignado",
    "en_proceso",
}


class Command(BaseCommand):
    help = (
        "Registers legacy active Billing assignments as unmanaged "
        "queue entries. Legacy assignments keep queue_position=NULL "
        "and remain visible."
    )

    def add_arguments(
        self,
        parser,
    ):
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Actually create legacy queue entries. "
                "Without --apply this command is dry-run only."
            ),
        )

    def handle(
        self,
        *args,
        **options,
    ):
        apply_changes = bool(options["apply"])

        qs = SesionBillingTecnico.objects.filter(
            estado__in=QUEUE_STATES,
            sesion__is_direct_discount=False,
        ).select_related(
            "tecnico",
            "sesion",
        )

        try:
            SesionBillingTecnico._meta.get_field("is_active")
            qs = qs.filter(is_active=True)
        except Exception:
            pass

        existing_assignment_ids = set(
            BillingAssignmentQueue.objects.values_list(
                "assignment_id",
                flat=True,
            )
        )

        candidates = [
            assignment
            for assignment in qs
            if assignment.id not in existing_assignment_ids
        ]

        grouped = defaultdict(list)

        for assignment in candidates:
            grouped[assignment.tecnico_id].append(assignment)

        self.stdout.write("")
        self.stdout.write("=" * 72)
        self.stdout.write("BILLING QUEUE LEGACY BACKFILL")
        self.stdout.write("=" * 72)

        self.stdout.write(f"Mode: " f"{'APPLY' if apply_changes else 'DRY-RUN'}")
        self.stdout.write(
            "Legacy candidates without queue entry: " f"{len(candidates)}"
        )
        self.stdout.write("Technicians affected: " f"{len(grouped)}")

        if not candidates:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Nothing to backfill."))
            return

        created_total = 0

        for technician_id in sorted(grouped):
            assignments = grouped[technician_id]

            assignments.sort(
                key=lambda assignment: (
                    assignment.sesion.creado_en,
                    assignment.id,
                )
            )

            technician = assignments[0].tecnico

            self.stdout.write("")
            self.stdout.write(f"TECH {technician_id} | " f"{technician.username}")

            for assignment in assignments:
                project = getattr(
                    assignment.sesion,
                    "proyecto_id",
                    None,
                )

                self.stdout.write(
                    "  "
                    "PRIORITY=UNMANAGED "
                    f"| ASG={assignment.id} "
                    f"| BILLING={assignment.sesion_id} "
                    f"| PROJECT={project} "
                    f"| STATE={assignment.estado} "
                    "| RELEASED=True"
                )

                if not apply_changes:
                    continue

                with transaction.atomic():
                    entry, created = BillingAssignmentQueue.objects.get_or_create(
                        assignment=assignment,
                        defaults={
                            "technician": (assignment.tecnico),
                            "queue_position": None,
                            "is_released": True,
                            "released_manually": False,
                            "released_at": None,
                            "released_by": None,
                        },
                    )

                    if created:
                        created_total += 1
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                "    SKIPPED: queue entry " "already exists."
                            )
                        )

        self.stdout.write("")
        self.stdout.write("=" * 72)

        if apply_changes:
            self.stdout.write(
                self.style.SUCCESS(
                    "Created legacy unmanaged " f"queue entries: {created_total}"
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING("DRY-RUN ONLY. " "No database rows were created.")
            )
            self.stdout.write("Run with --apply only after " "reviewing this output.")

        self.stdout.write("=" * 72)
