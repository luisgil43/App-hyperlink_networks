from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ============================================================
# Constantes generales
# ============================================================

DEFAULT_SMARTSHEET_FORM_URL = (
    "https://app.smartsheet.com/b/form/" "a45ea2b5dbce49d2b5fcd8177d5be815"
)


# ============================================================
# Batch / lote de envíos
# ============================================================


class ClientSubmissionBatch(models.Model):
    """
    Representa un grupo de proyectos seleccionados desde Ready to Invoice.

    Ejemplo:
        El usuario selecciona 100 invoices y crea un Batch.

    El Batch almacena:
    - La configuración común del formulario.
    - El modo de ejecución.
    - El estado global del proceso.
    - La referencia al usuario que inició el proceso.

    Cada proyecto se almacena individualmente en ClientSubmission.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending to start"
        RUNNING = "running", "Processing"
        AWAITING_VERIFICATION = (
            "awaiting_verification",
            "Awaiting verification",
        )
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_ERRORS = (
            "completed_with_errors",
            "Completed with errors",
        )
        CANCELLED = "cancelled", "Cancelled"

    class ExecutionMode(models.TextChoices):
        DRY_RUN = "dry_run", "Dry Run"
        LIVE = "live", "Live submission"

    id = models.BigAutoField(primary_key=True)

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="client_submission_batches_created",
    )

    # --------------------------------------------------------
    # Configuración general
    # --------------------------------------------------------

    name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Optional internal name for this submission batch.",
    )

    form_url = models.URLField(
        max_length=1000,
        default=DEFAULT_SMARTSHEET_FORM_URL,
    )

    execution_mode = models.CharField(
        max_length=20,
        choices=ExecutionMode.choices,
        default=ExecutionMode.LIVE,
        db_index=True,
    )

    status = models.CharField(
        max_length=40,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    # --------------------------------------------------------
    # Datos comunes del formulario Smartsheet
    # --------------------------------------------------------

    submitted_by_email = models.EmailField(
        default="l.suarez@hyperlink-networks.com",
    )

    send_copy_of_responses = models.BooleanField(
        default=True,
    )

    copy_email = models.EmailField(
        default="l.suarez@hyperlink-networks.com",
    )

    additional_copy_emails = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Additional internal recipients. "
            "These are not necessarily entered in the Smartsheet form."
        ),
    )

    is_subcontractor = models.BooleanField(
        default=True,
    )

    subcontractor_name = models.CharField(
        max_length=255,
        default="Hyperlink",
    )

    production_completed_date = models.DateField(
        null=True,
        blank=True,
    )

    market = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    # --------------------------------------------------------
    # Tipo de trabajo
    # --------------------------------------------------------

    fiber_placed = models.BooleanField(
        default=False,
    )

    splicing = models.BooleanField(
        default=False,
    )

    testing = models.BooleanField(
        default=False,
    )

    aerial_case = models.BooleanField(
        default=False,
    )

    re_entry = models.BooleanField(
        default=False,
    )

    # --------------------------------------------------------
    # Control del worker
    # --------------------------------------------------------

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    paused_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    worker_identifier = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Identifier of the worker currently processing the batch.",
    )

    current_submission = models.ForeignKey(
        "ClientSubmission",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # --------------------------------------------------------
    # Control administrativo
    # --------------------------------------------------------

    notes = models.TextField(
        blank=True,
        default="",
    )

    last_error = models.TextField(
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "created_at"],
                name="cs_batch_status_created_idx",
            ),
            models.Index(
                fields=["execution_mode", "status"],
                name="cs_batch_mode_status_idx",
            ),
        ]

    def __str__(self):
        return f"Client Submission Batch #{self.pk}"

    # --------------------------------------------------------
    # Propiedades
    # --------------------------------------------------------

    @property
    def is_dry_run(self):
        return self.execution_mode == self.ExecutionMode.DRY_RUN

    @property
    def is_live(self):
        return self.execution_mode == self.ExecutionMode.LIVE

    @property
    def can_start(self):
        return self.status in {
            self.Status.DRAFT,
            self.Status.PENDING,
            self.Status.PAUSED,
        }

    @property
    def can_pause(self):
        return self.status == self.Status.RUNNING

    @property
    def can_cancel(self):
        return self.status not in {
            self.Status.COMPLETED,
            self.Status.COMPLETED_WITH_ERRORS,
            self.Status.CANCELLED,
        }

    @property
    def total_submissions(self):
        return self.submissions.count()

    @property
    def sent_count(self):
        return self.submissions.filter(
            status=ClientSubmission.Status.SENT_TO_CLIENT
        ).count()

    @property
    def pending_count(self):
        return self.submissions.filter(
            status=ClientSubmission.Status.PENDING_CLIENT_SUBMISSION
        ).count()

    @property
    def failed_count(self):
        return self.submissions.filter(status=ClientSubmission.Status.FAILED).count()

    @property
    def awaiting_verification_count(self):
        return self.submissions.filter(
            status=ClientSubmission.Status.AWAITING_VERIFICATION
        ).count()

    @property
    def processing_count(self):
        return self.submissions.filter(
            status__in=[
                ClientSubmission.Status.PREPARING,
                ClientSubmission.Status.SUBMITTING,
                ClientSubmission.Status.AWAITING_EMAIL_CONFIRMATION,
            ]
        ).count()

    # --------------------------------------------------------
    # Métodos de estado
    # --------------------------------------------------------

    def mark_running(self, save=True):
        now = timezone.now()

        self.status = self.Status.RUNNING
        self.last_activity_at = now
        self.paused_at = None

        if not self.started_at:
            self.started_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "last_activity_at",
                    "paused_at",
                    "started_at",
                    "updated_at",
                ]
            )

    def mark_awaiting_verification(self, save=True):
        now = timezone.now()

        self.status = self.Status.AWAITING_VERIFICATION
        self.paused_at = now
        self.last_activity_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "paused_at",
                    "last_activity_at",
                    "updated_at",
                ]
            )

    def mark_paused(self, save=True):
        now = timezone.now()

        self.status = self.Status.PAUSED
        self.paused_at = now
        self.last_activity_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "paused_at",
                    "last_activity_at",
                    "updated_at",
                ]
            )

    def mark_cancelled(self, save=True):
        now = timezone.now()

        self.status = self.Status.CANCELLED
        self.cancelled_at = now
        self.finished_at = now
        self.last_activity_at = now
        self.current_submission = None

        if save:
            self.save(
                update_fields=[
                    "status",
                    "cancelled_at",
                    "finished_at",
                    "last_activity_at",
                    "current_submission",
                    "updated_at",
                ]
            )

    def refresh_final_status(self, save=True):
        """
        Calcula el estado final del Batch según sus proyectos.
        """

        submissions = self.submissions.all()

        if not submissions.exists():
            return self.status

        unfinished = submissions.exclude(
            status__in=[
                ClientSubmission.Status.SENT_TO_CLIENT,
                ClientSubmission.Status.DRY_RUN_COMPLETED,
                ClientSubmission.Status.FAILED,
                ClientSubmission.Status.CANCELLED,
            ]
        ).exists()

        if unfinished:
            return self.status

        has_errors = submissions.filter(status=ClientSubmission.Status.FAILED).exists()

        self.status = (
            self.Status.COMPLETED_WITH_ERRORS if has_errors else self.Status.COMPLETED
        )

        self.finished_at = timezone.now()
        self.last_activity_at = timezone.now()
        self.current_submission = None

        if save:
            self.save(
                update_fields=[
                    "status",
                    "finished_at",
                    "last_activity_at",
                    "current_submission",
                    "updated_at",
                ]
            )

        return self.status


# ============================================================
# Envío individual
# ============================================================


class ClientSubmission(models.Model):
    """
    Representa un único formulario que será enviado al cliente.

    Un ClientSubmission corresponde normalmente a una SesionBilling
    seleccionada desde Ready to Invoice.
    """

    class Status(models.TextChoices):
        PENDING_CLIENT_SUBMISSION = (
            "pending_client_submission",
            "Pending client submission",
        )

        PREPARING = (
            "preparing_client_submission",
            "Preparing client submission",
        )

        AWAITING_VERIFICATION = (
            "awaiting_verification",
            "Awaiting verification",
        )

        SUBMITTING = (
            "submitting_to_client",
            "Submitting to client",
        )

        AWAITING_EMAIL_CONFIRMATION = (
            "awaiting_email_confirmation",
            "Awaiting email confirmation",
        )

        SENT_TO_CLIENT = (
            "sent_to_client",
            "Sent to client",
        )

        DRY_RUN_COMPLETED = (
            "dry_run_completed",
            "Dry Run completed",
        )

        FAILED = (
            "client_submission_failed",
            "Client submission failed",
        )

        CANCELLED = (
            "cancelled",
            "Cancelled",
        )

    id = models.BigAutoField(primary_key=True)

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )

    batch = models.ForeignKey(
        ClientSubmissionBatch,
        on_delete=models.CASCADE,
        related_name="submissions",
    )

    # --------------------------------------------------------
    # Relación con Billing
    # --------------------------------------------------------

    billing_session = models.ForeignKey(
        "operaciones.SesionBilling",
        on_delete=models.PROTECT,
        related_name="client_submissions",
    )

    # --------------------------------------------------------
    # Identificación del proyecto
    # --------------------------------------------------------

    project_id = models.CharField(
        max_length=255,
        db_index=True,
    )

    dfn_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    access_point_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    # --------------------------------------------------------
    # Estado
    # --------------------------------------------------------

    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.PENDING_CLIENT_SUBMISSION,
        db_index=True,
    )

    sequence_number = models.PositiveIntegerField(
        default=0,
        help_text="Processing order within the batch.",
    )

    # --------------------------------------------------------
    # Datos específicos que se enviarán al formulario
    # --------------------------------------------------------

    form_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Resolved form data for this project. "
            "This is a snapshot of what will be submitted."
        ),
    )

    billing_codes_snapshot = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Snapshot of Billing items and quantities used "
            "to build the Smartsheet form."
        ),
    )

    # --------------------------------------------------------
    # ZIP / archivo adjunto
    # --------------------------------------------------------

    zip_file = models.FileField(
        upload_to="client_submissions/zips/%Y/%m/",
        null=True,
        blank=True,
    )

    zip_source_url = models.URLField(
        max_length=2000,
        blank=True,
        default="",
        help_text=(
            "Original URL of the ZIP when the file is stored "
            "outside Django default storage."
        ),
    )

    zip_filename = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    zip_size = models.BigIntegerField(
        null=True,
        blank=True,
    )

    zip_available = models.BooleanField(
        default=False,
        db_index=True,
    )

    # --------------------------------------------------------
    # Validación previa
    # --------------------------------------------------------

    validation_ok = models.BooleanField(
        default=False,
        db_index=True,
    )

    validation_errors = models.JSONField(
        default=list,
        blank=True,
    )

    validation_warnings = models.JSONField(
        default=list,
        blank=True,
    )

    validated_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # --------------------------------------------------------
    # Ejecución del navegador
    # --------------------------------------------------------

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    form_loaded_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    form_completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    verification_required_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    verification_completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    submit_clicked_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    browser_confirmation_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    email_confirmation_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # --------------------------------------------------------
    # Confirmaciones
    # --------------------------------------------------------

    browser_confirmation_received = models.BooleanField(
        default=False,
        db_index=True,
    )

    email_confirmation_received = models.BooleanField(
        default=False,
        db_index=True,
    )

    confirmation_reference = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    # --------------------------------------------------------
    # Control de reintentos
    # --------------------------------------------------------

    attempt_count = models.PositiveIntegerField(
        default=0,
    )

    max_attempts = models.PositiveIntegerField(
        default=3,
    )

    # --------------------------------------------------------
    # Error actual
    # --------------------------------------------------------

    last_error_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    last_error_message = models.TextField(
        blank=True,
        default="",
    )

    last_error_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # --------------------------------------------------------
    # Estado del navegador para reanudación
    # --------------------------------------------------------

    browser_session_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    browser_state = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Non-sensitive automation state needed to continue "
            "the current submission."
        ),
    )

    # --------------------------------------------------------
    # Auditoría
    # --------------------------------------------------------

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "sequence_number",
            "id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "batch",
                    "billing_session",
                ],
                name="cs_unique_billing_per_batch",
            ),
        ]

        indexes = [
            models.Index(
                fields=["batch", "status", "sequence_number"],
                name="cs_sub_batch_status_seq_idx",
            ),
            models.Index(
                fields=["status", "created_at"],
                name="cs_sub_status_created_idx",
            ),
            models.Index(
                fields=["project_id", "status"],
                name="cs_sub_project_status_idx",
            ),
        ]

    def __str__(self):
        return f"{self.project_id} - {self.get_status_display()}"

    # --------------------------------------------------------
    # Propiedades
    # --------------------------------------------------------

    @property
    def is_terminal(self):
        return self.status in {
            self.Status.SENT_TO_CLIENT,
            self.Status.DRY_RUN_COMPLETED,
            self.Status.CANCELLED,
        }

    @property
    def can_retry(self):
        return (
            self.status == self.Status.FAILED and self.attempt_count < self.max_attempts
        )

    @property
    def requires_human_verification(self):
        return self.status == self.Status.AWAITING_VERIFICATION

    @property
    def has_zip(self):
        return bool(self.zip_file or self.zip_source_url)

    @property
    def fully_confirmed(self):
        return self.browser_confirmation_received and self.email_confirmation_received

    # --------------------------------------------------------
    # Validación
    # --------------------------------------------------------

    def clean(self):
        errors = {}

        if not self.project_id:
            errors["project_id"] = "Project ID is required."

        if not self.dfn_name:
            errors["dfn_name"] = "DFN Name is required."

        if not self.access_point_id:
            errors["access_point_id"] = "Access Point ID is required."

        if errors:
            raise ValidationError(errors)

    # --------------------------------------------------------
    # Métodos de estado
    # --------------------------------------------------------

    def mark_preparing(self, save=True):
        now = timezone.now()

        self.status = self.Status.PREPARING

        if not self.started_at:
            self.started_at = now

        self.last_error_code = ""
        self.last_error_message = ""
        self.last_error_at = None

        if save:
            self.save(
                update_fields=[
                    "status",
                    "started_at",
                    "last_error_code",
                    "last_error_message",
                    "last_error_at",
                    "updated_at",
                ]
            )

    def mark_awaiting_verification(self, save=True):
        now = timezone.now()

        self.status = self.Status.AWAITING_VERIFICATION
        self.verification_required_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "verification_required_at",
                    "updated_at",
                ]
            )

    def mark_verification_completed(self, save=True):
        now = timezone.now()

        self.verification_completed_at = now
        self.status = self.Status.SUBMITTING

        if save:
            self.save(
                update_fields=[
                    "verification_completed_at",
                    "status",
                    "updated_at",
                ]
            )

    def mark_submitting(self, save=True):
        self.status = self.Status.SUBMITTING

        if save:
            self.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

    def mark_browser_confirmed(
        self,
        reference="",
        save=True,
    ):
        """
        Marca el proyecto como enviado cuando Smartsheet confirma
        visualmente que el formulario fue recibido.

        La confirmación por correo se conserva como una validación
        posterior y no bloquea el cierre operativo del proyecto.
        """

        now = timezone.now()

        self.browser_confirmation_received = True
        self.browser_confirmation_at = now

        if reference:
            self.confirmation_reference = reference

        self.status = self.Status.SENT_TO_CLIENT
        self.submitted_at = self.submitted_at or now
        self.finished_at = now

        if save:
            self.save(
                update_fields=[
                    "browser_confirmation_received",
                    "browser_confirmation_at",
                    "confirmation_reference",
                    "status",
                    "submitted_at",
                    "finished_at",
                    "updated_at",
                ]
            )

    def mark_email_confirmed(self, save=True):
        now = timezone.now()

        self.email_confirmation_received = True
        self.email_confirmation_at = now

        if self.browser_confirmation_received:
            self.status = self.Status.SENT_TO_CLIENT
            self.submitted_at = self.submitted_at or now
            self.finished_at = now
        else:
            self.status = self.Status.AWAITING_EMAIL_CONFIRMATION

        if save:
            self.save(
                update_fields=[
                    "email_confirmation_received",
                    "email_confirmation_at",
                    "status",
                    "submitted_at",
                    "finished_at",
                    "updated_at",
                ]
            )

    def mark_sent_to_client(self, save=True):
        """
        Método de cierre final.

        Se utilizará cuando ambas confirmaciones estén disponibles
        o cuando una regla administrativa permita confirmar manualmente.
        """

        now = timezone.now()

        self.status = self.Status.SENT_TO_CLIENT
        self.submitted_at = self.submitted_at or now
        self.finished_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "submitted_at",
                    "finished_at",
                    "updated_at",
                ]
            )

    def mark_dry_run_completed(self, save=True):
        now = timezone.now()

        self.status = self.Status.DRY_RUN_COMPLETED
        self.finished_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "finished_at",
                    "updated_at",
                ]
            )

    def mark_failed(
        self,
        message,
        code="",
        save=True,
    ):
        now = timezone.now()

        self.status = self.Status.FAILED
        self.last_error_code = code or ""
        self.last_error_message = str(message or "")
        self.last_error_at = now
        self.finished_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "last_error_code",
                    "last_error_message",
                    "last_error_at",
                    "finished_at",
                    "updated_at",
                ]
            )

    def reset_for_retry(self, save=True):
        """
        Prepara nuevamente el proyecto después de un error.

        Nunca debe utilizarse automáticamente sobre un proyecto
        que ya esté confirmado como enviado.
        """

        if self.status == self.Status.SENT_TO_CLIENT:
            raise ValidationError(
                "A submission already sent to the client cannot be retried."
            )

        self.status = self.Status.PENDING_CLIENT_SUBMISSION

        self.started_at = None
        self.form_loaded_at = None
        self.form_completed_at = None

        self.verification_required_at = None
        self.verification_completed_at = None

        self.submit_clicked_at = None
        self.browser_confirmation_at = None

        self.finished_at = None

        self.last_error_code = ""
        self.last_error_message = ""
        self.last_error_at = None

        self.browser_session_key = ""
        self.browser_state = {}

        if save:
            self.save(
                update_fields=[
                    "status",
                    "started_at",
                    "form_loaded_at",
                    "form_completed_at",
                    "verification_required_at",
                    "verification_completed_at",
                    "submit_clicked_at",
                    "browser_confirmation_at",
                    "finished_at",
                    "last_error_code",
                    "last_error_message",
                    "last_error_at",
                    "browser_session_key",
                    "browser_state",
                    "updated_at",
                ]
            )


# ============================================================
# Intentos de envío
# ============================================================


class ClientSubmissionAttempt(models.Model):
    """
    Historial completo de cada intento de automatización.

    Nunca sobrescribimos los intentos anteriores.
    """

    class Result(models.TextChoices):
        STARTED = "started", "Started"
        FORM_COMPLETED = "form_completed", "Form completed"
        AWAITING_VERIFICATION = (
            "awaiting_verification",
            "Awaiting verification",
        )
        BROWSER_CONFIRMED = (
            "browser_confirmed",
            "Browser confirmed",
        )
        DRY_RUN_COMPLETED = (
            "dry_run_completed",
            "Dry Run completed",
        )
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.BigAutoField(primary_key=True)

    submission = models.ForeignKey(
        ClientSubmission,
        on_delete=models.CASCADE,
        related_name="attempts",
    )

    attempt_number = models.PositiveIntegerField()

    result = models.CharField(
        max_length=40,
        choices=Result.choices,
        default=Result.STARTED,
        db_index=True,
    )

    started_at = models.DateTimeField(
        default=timezone.now,
    )

    finished_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # --------------------------------------------------------
    # Snapshot del intento
    # --------------------------------------------------------

    form_url = models.URLField(
        max_length=1000,
        blank=True,
        default="",
    )

    form_payload_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    browser_url = models.URLField(
        max_length=2000,
        blank=True,
        default="",
    )

    browser_title = models.CharField(
        max_length=1000,
        blank=True,
        default="",
    )

    # --------------------------------------------------------
    # Evidencia
    # --------------------------------------------------------

    screenshot = models.ImageField(
        upload_to="client_submissions/screenshots/%Y/%m/",
        max_length=500,
        null=True,
        blank=True,
    )

    screenshot_url = models.URLField(
        max_length=2000,
        blank=True,
        default="",
    )

    page_html_snapshot = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Optional sanitized HTML fragment for troubleshooting. "
            "Avoid storing secrets or unnecessary personal data."
        ),
    )

    # --------------------------------------------------------
    # Error
    # --------------------------------------------------------

    error_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    error_message = models.TextField(
        blank=True,
        default="",
    )

    error_details = models.JSONField(
        default=dict,
        blank=True,
    )

    # --------------------------------------------------------
    # IA aplicada al formulario
    # --------------------------------------------------------

    ai_used = models.BooleanField(
        default=False,
    )

    ai_result = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "AI-assisted interpretation of form structure or automation errors. "
            "AI is not used to analyze project photos."
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-attempt_number",
            "-created_at",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "submission",
                    "attempt_number",
                ],
                name="cs_unique_attempt_number",
            ),
        ]

        indexes = [
            models.Index(
                fields=["submission", "result"],
                name="cs_attempt_sub_result_idx",
            ),
        ]

    def __str__(self):
        return f"{self.submission.project_id} " f"- Attempt {self.attempt_number}"

    def mark_finished(
        self,
        result,
        save=True,
    ):
        self.result = result
        self.finished_at = timezone.now()

        if save:
            self.save(
                update_fields=[
                    "result",
                    "finished_at",
                    "updated_at",
                ]
            )


# ============================================================
# Confirmación por correo
# ============================================================


class ClientSubmissionConfirmation(models.Model):
    """
    Guarda una confirmación externa asociada con un envío.

    Inicialmente la fuente principal será el correo que llega
    después de enviar el formulario Smartsheet.
    """

    class Source(models.TextChoices):
        EMAIL = "email", "Email"
        BROWSER = "browser", "Browser"
        MANUAL = "manual", "Manual"

    id = models.BigAutoField(primary_key=True)

    submission = models.ForeignKey(
        ClientSubmission,
        on_delete=models.CASCADE,
        related_name="confirmations",
    )

    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        db_index=True,
    )

    external_id = models.CharField(
        max_length=500,
        blank=True,
        default="",
        db_index=True,
        help_text=("External identifier such as an email message ID."),
    )

    sender = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    recipient = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    subject = models.CharField(
        max_length=1000,
        blank=True,
        default="",
    )

    project_id_detected = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    dfn_name_detected = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    access_point_id_detected = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    raw_metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    confirmed_at = models.DateTimeField(
        default=timezone.now,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "-confirmed_at",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "source",
                    "external_id",
                ],
                condition=~models.Q(external_id=""),
                name="cs_unique_confirmation_external_id",
            ),
        ]

        indexes = [
            models.Index(
                fields=["submission", "source"],
                name="cs_confirm_sub_source_idx",
            ),
        ]

    def __str__(self):
        return f"{self.submission.project_id} " f"- {self.get_source_display()}"


# ============================================================
# Registro de eventos
# ============================================================


class ClientSubmissionEvent(models.Model):
    """
    Timeline técnico y administrativo del proceso.

    Ejemplos:
    - Batch created
    - Validation completed
    - ZIP found
    - Form opened
    - Verification required
    - Browser confirmation received
    - Email confirmation received
    """

    class Level(models.TextChoices):
        INFO = "info", "Info"
        SUCCESS = "success", "Success"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    id = models.BigAutoField(primary_key=True)

    batch = models.ForeignKey(
        ClientSubmissionBatch,
        on_delete=models.CASCADE,
        related_name="events",
    )

    submission = models.ForeignKey(
        ClientSubmission,
        on_delete=models.CASCADE,
        related_name="events",
        null=True,
        blank=True,
    )

    level = models.CharField(
        max_length=20,
        choices=Level.choices,
        default=Level.INFO,
        db_index=True,
    )

    event_type = models.CharField(
        max_length=100,
        db_index=True,
    )

    message = models.TextField()

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=["batch", "created_at"],
                name="cs_event_batch_created_idx",
            ),
            models.Index(
                fields=["submission", "created_at"],
                name="cs_event_sub_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.event_type}: {self.message[:80]}"
