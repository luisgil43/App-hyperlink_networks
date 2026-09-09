from django.conf import settings
from django.db import models
from django.db.models import Q


class BillingAssignmentQueue(models.Model):
    """
    Estado administrativo de cola de una asignación Billing/Técnico.

    IMPORTANTE:
    - No reemplaza SesionBillingTecnico.
    - No modifica el workflow actual de estados.
    - La cola es individual por técnico.
    """

    assignment = models.OneToOneField(
        "operaciones.SesionBillingTecnico",
        on_delete=models.CASCADE,
        related_name="queue_state",
    )

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_queue_entries",
        db_index=True,
    )

    queue_position = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
    )

    is_released = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether this assignment is currently visible/actionable for the technician.",
    )

    released_manually = models.BooleanField(
        default=False,
        db_index=True,
    )

    released_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_queue_releases",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ("technician_id", "queue_position", "id")
        constraints = [
            models.UniqueConstraint(
                fields=["technician", "queue_position"],
                name="uniq_billing_queue_position_per_technician",
            ),
        ]
        indexes = [
            models.Index(
                fields=["technician", "is_released"],
                name="billq_tech_release_idx",
            ),
            models.Index(
                fields=["technician", "queue_position"],
                name="billq_tech_pos_idx",
            ),
        ]

    def __str__(self):
        return (
            f"Queue assignment {self.assignment_id} / "
            f"tech {self.technician_id} / "
            f"#{self.queue_position or '-'}"
        )


class BillingWorkSession(models.Model):
    """
    Segmento real de trabajo.

    Cada Start/Resume abre un segmento.
    Cada cambio de Billing, Pause o Finish cierra el segmento.

    No reemplaza aceptado_en/finalizado_en.
    """

    assignment = models.ForeignKey(
        "operaciones.SesionBillingTecnico",
        on_delete=models.CASCADE,
        related_name="work_sessions",
    )

    technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="billing_work_sessions",
        db_index=True,
    )

    started_at = models.DateTimeField(
        db_index=True,
    )

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ("started_at", "id")

        constraints = [
            models.UniqueConstraint(
                fields=["technician"],
                condition=Q(ended_at__isnull=True),
                name="uniq_open_billing_timer_per_technician",
            ),
        ]

        indexes = [
            models.Index(
                fields=["technician", "ended_at"],
                name="billwork_tech_end_idx",
            ),
            models.Index(
                fields=["assignment", "started_at"],
                name="billwork_asg_start_idx",
            ),
        ]

    @property
    def duration_seconds(self):
        if not self.ended_at:
            return None

        return max(
            0,
            int((self.ended_at - self.started_at).total_seconds()),
        )

    def __str__(self):
        status = "running" if self.ended_at is None else "closed"

        return (
            f"Work assignment {self.assignment_id} / "
            f"tech {self.technician_id} / {status}"
        )
