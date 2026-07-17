# Create your models here.
from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ============================================================
# Sesión remota del navegador
# ============================================================


class RemoteBrowserSession(models.Model):
    """
    Representa una consola remota asociada con un único
    ClientSubmission.

    Esta tabla no modifica ClientSubmission ni ninguna otra
    tabla existente. Toda la información nueva queda aislada
    dentro de esta aplicación.

    El worker de Playwright:

    - crea o reactiva una sesión;
    - captura la pantalla;
    - procesa acciones pendientes;
    - actualiza el estado;
    - cierra la sesión al terminar.

    La interfaz web:

    - consulta esta sesión;
    - muestra la captura;
    - registra acciones;
    - nunca accede directamente al navegador Playwright.
    """

    class Status(models.TextChoices):
        STARTING = "starting", "Starting"
        ACTIVE = "active", "Active"
        PROCESSING_ACTION = (
            "processing_action",
            "Processing action",
        )
        WAITING_FOR_USER = (
            "waiting_for_user",
            "Waiting for user",
        )
        VERIFICATION_PASSED = (
            "verification_passed",
            "Verification passed",
        )
        SUBMISSION_CONFIRMED = (
            "submission_confirmed",
            "Submission confirmed",
        )
        RESTART_REQUESTED = (
            "restart_requested",
            "Restart requested",
        )
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        CLOSED = "closed", "Closed"
        FAILED = "failed", "Failed"

    class CaptchaStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        CHALLENGE_ACTIVE = (
            "challenge_active",
            "Challenge active",
        )
        DYNAMIC_CHALLENGE = (
            "dynamic_challenge",
            "Dynamic challenge",
        )
        NEW_CHALLENGE_DETECTED = (
            "new_challenge_detected",
            "New challenge detected",
        )
        VERIFICATION_PROCESSING = (
            "verification_processing",
            "Verification processing",
        )
        VERIFICATION_PASSED = (
            "verification_passed",
            "Verification passed",
        )
        VERIFICATION_FAILED = (
            "verification_failed",
            "Verification failed",
        )
        NOT_VISIBLE = "not_visible", "Not visible"

    id = models.BigAutoField(
        primary_key=True,
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )

    submission = models.ForeignKey(
        "client_submissions.ClientSubmission",
        on_delete=models.CASCADE,
        related_name="remote_browser_sessions",
    )

    attempt = models.ForeignKey(
        "client_submissions.ClientSubmissionAttempt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="remote_browser_sessions",
    )

    # --------------------------------------------------------
    # Estado general
    # --------------------------------------------------------

    status = models.CharField(
        max_length=40,
        choices=Status.choices,
        default=Status.STARTING,
        db_index=True,
    )

    captcha_status = models.CharField(
        max_length=40,
        choices=CaptchaStatus.choices,
        default=CaptchaStatus.UNKNOWN,
        db_index=True,
    )

    stage = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Automation stage where the remote session started. "
            "Examples: form_load, before_form_fill, after_submit."
        ),
    )

    # --------------------------------------------------------
    # Identificación del worker
    # --------------------------------------------------------

    worker_identifier = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    browser_session_key = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    # --------------------------------------------------------
    # Viewport fijo de Playwright
    # --------------------------------------------------------

    viewport_width = models.PositiveIntegerField(
        default=1440,
    )

    viewport_height = models.PositiveIntegerField(
        default=1100,
    )

    device_scale_factor = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=1,
    )

    # --------------------------------------------------------
    # Captura actual
    # --------------------------------------------------------

    screenshot = models.ImageField(
        upload_to="client_submission_remote/screenshots/%Y/%m/",
        null=True,
        blank=True,
    )

    screenshot_version = models.PositiveBigIntegerField(
        default=0,
    )

    screenshot_width = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    screenshot_height = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    screenshot_captured_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    screenshot_error = models.TextField(
        blank=True,
        default="",
    )

    # --------------------------------------------------------
    # Estado visible del navegador
    # --------------------------------------------------------

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

    page_scroll_x = models.IntegerField(
        default=0,
    )

    page_scroll_y = models.IntegerField(
        default=0,
    )

    page_document_width = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    page_document_height = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    # --------------------------------------------------------
    # Control de rondas CAPTCHA
    # --------------------------------------------------------

    round_number = models.PositiveIntegerField(
        default=1,
    )

    max_rounds = models.PositiveIntegerField(
        default=10,
    )

    action_count = models.PositiveIntegerField(
        default=0,
    )

    last_action_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
    )

    last_action_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_result = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    # --------------------------------------------------------
    # Control de usuario
    # --------------------------------------------------------

    controller_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="controlled_remote_browser_sessions",
    )

    controller_acquired_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    controller_last_activity_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    # --------------------------------------------------------
    # Mensajes y errores
    # --------------------------------------------------------

    message = models.TextField(
        blank=True,
        default="",
    )

    error_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    error_message = models.TextField(
        blank=True,
        default="",
    )

    metadata = models.JSONField(
        default=dict,
        blank=True,
    )

    # --------------------------------------------------------
    # Fechas
    # --------------------------------------------------------

    started_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    closed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    last_worker_activity_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

        indexes = [
            models.Index(
                fields=[
                    "submission",
                    "status",
                ],
                name="csr_session_sub_status_idx",
            ),
            models.Index(
                fields=[
                    "status",
                    "expires_at",
                ],
                name="csr_session_status_exp_idx",
            ),
            models.Index(
                fields=[
                    "worker_identifier",
                    "status",
                ],
                name="csr_session_worker_idx",
            ),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "submission",
                ],
                condition=models.Q(
                    status__in=[
                        "starting",
                        "active",
                        "processing_action",
                        "waiting_for_user",
                        "restart_requested",
                    ]
                ),
                name="csr_one_open_session_per_sub",
            ),
        ]

    def __str__(self):
        return f"{self.submission.project_id} " f"- Remote session {self.public_id}"

    # --------------------------------------------------------
    # Propiedades
    # --------------------------------------------------------

    @property
    def is_open(self):
        return self.status in {
            self.Status.STARTING,
            self.Status.ACTIVE,
            self.Status.PROCESSING_ACTION,
            self.Status.WAITING_FOR_USER,
            self.Status.RESTART_REQUESTED,
        }

    @property
    def is_expired(self):
        return bool(self.expires_at and self.expires_at <= timezone.now())

    @property
    def has_screenshot(self):
        return bool(self.screenshot)

    @property
    def can_accept_actions(self):
        return (
            self.is_open
            and not self.is_expired
            and self.status
            not in {
                self.Status.PROCESSING_ACTION,
                self.Status.RESTART_REQUESTED,
            }
        )

    # --------------------------------------------------------
    # Validación
    # --------------------------------------------------------

    def clean(self):
        errors = {}

        if self.viewport_width < 320:
            errors["viewport_width"] = "Viewport width must be at least 320 pixels."

        if self.viewport_height < 320:
            errors["viewport_height"] = "Viewport height must be at least 320 pixels."

        if self.max_rounds <= 0:
            errors["max_rounds"] = "Maximum CAPTCHA rounds must be greater than zero."

        if self.round_number <= 0:
            errors["round_number"] = "CAPTCHA round number must be greater than zero."

        if self.round_number > self.max_rounds:
            errors["round_number"] = (
                "CAPTCHA round number cannot exceed the configured maximum."
            )

        if errors:
            raise ValidationError(errors)

    # --------------------------------------------------------
    # Métodos de estado
    # --------------------------------------------------------

    def mark_active(
        self,
        *,
        message="",
        save=True,
    ):
        now = timezone.now()

        self.status = self.Status.ACTIVE
        self.last_worker_activity_at = now

        if message:
            self.message = message

        if save:
            self.save(
                update_fields=[
                    "status",
                    "last_worker_activity_at",
                    "message",
                    "updated_at",
                ]
            )

    def mark_waiting_for_user(
        self,
        *,
        captcha_status=None,
        message="",
        save=True,
    ):
        now = timezone.now()

        self.status = self.Status.WAITING_FOR_USER
        self.last_worker_activity_at = now

        if captcha_status:
            self.captcha_status = captcha_status

        if message:
            self.message = message

        if save:
            self.save(
                update_fields=[
                    "status",
                    "captcha_status",
                    "last_worker_activity_at",
                    "message",
                    "updated_at",
                ]
            )

    def mark_processing_action(
        self,
        *,
        action_type="",
        save=True,
    ):
        now = timezone.now()

        self.status = self.Status.PROCESSING_ACTION
        self.last_action_type = action_type
        self.last_action_at = now
        self.last_worker_activity_at = now

        if save:
            self.save(
                update_fields=[
                    "status",
                    "last_action_type",
                    "last_action_at",
                    "last_worker_activity_at",
                    "updated_at",
                ]
            )

    def mark_closed(
        self,
        *,
        status=None,
        message="",
        save=True,
    ):
        now = timezone.now()

        self.status = status or self.Status.CLOSED
        self.closed_at = now
        self.last_worker_activity_at = now

        if message:
            self.message = message

        if save:
            self.save(
                update_fields=[
                    "status",
                    "closed_at",
                    "last_worker_activity_at",
                    "message",
                    "updated_at",
                ]
            )


# ============================================================
# Acciones enviadas al navegador remoto
# ============================================================


class RemoteBrowserAction(models.Model):
    """
    Acción solicitada desde la consola web.

    La vista crea la acción en PostgreSQL.

    El worker toma acciones pendientes usando select_for_update
    y las ejecuta en Playwright.

    Nunca se ejecuta código arbitrario ni navegación libre.
    """

    class ActionType(models.TextChoices):
        CLICK = "click", "Click"
        DOUBLE_CLICK = "double_click", "Double click"
        MULTI_CLICK = "multi_click", "Multiple clicks"
        SCROLL = "scroll", "Scroll"
        REFRESH_SCREENSHOT = (
            "refresh_screenshot",
            "Refresh screenshot",
        )
        VERIFY = "verify", "Verify CAPTCHA"
        CONTINUE = "continue", "Continue verification"
        RESTART = "restart", "Restart CAPTCHA"
        CANCEL = "cancel", "Cancel submission"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.BigAutoField(
        primary_key=True,
    )

    public_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
    )

    session = models.ForeignKey(
        RemoteBrowserSession,
        on_delete=models.CASCADE,
        related_name="actions",
    )

    action_type = models.CharField(
        max_length=40,
        choices=ActionType.choices,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Validated action data such as click coordinates " "or scroll distance."
        ),
    )

    screenshot_version = models.PositiveBigIntegerField(
        default=0,
        help_text=(
            "Screenshot version visible to the user when " "the action was requested."
        ),
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="remote_browser_actions_requested",
    )

    requested_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )

    result = models.JSONField(
        default=dict,
        blank=True,
    )

    error_code = models.CharField(
        max_length=100,
        blank=True,
        default="",
    )

    error_message = models.TextField(
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
        ordering = [
            "requested_at",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "session",
                    "status",
                    "requested_at",
                ],
                name="csr_action_sess_status_idx",
            ),
            models.Index(
                fields=[
                    "status",
                    "requested_at",
                ],
                name="csr_action_status_req_idx",
            ),
        ]

        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    screenshot_version__gte=0,
                ),
                name="csr_action_screen_ver_gte_0",
            ),
        ]

    def __str__(self):
        return (
            f"{self.session.submission.project_id} "
            f"- {self.get_action_type_display()}"
        )

    # --------------------------------------------------------
    # Propiedades
    # --------------------------------------------------------

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def is_terminal(self):
        return self.status in {
            self.Status.COMPLETED,
            self.Status.FAILED,
            self.Status.CANCELLED,
        }

    # --------------------------------------------------------
    # Validación
    # --------------------------------------------------------

    def clean(self):
        errors = {}

        payload = (
            self.payload
            if isinstance(
                self.payload,
                dict,
            )
            else {}
        )

        if self.action_type in {
            self.ActionType.CLICK,
            self.ActionType.DOUBLE_CLICK,
            self.ActionType.VERIFY,
        }:
            x = payload.get("x")
            y = payload.get("y")

            if not isinstance(x, (int, float)):
                errors["payload"] = "Click action requires a numeric x coordinate."

            elif x < 0:
                errors["payload"] = "Click x coordinate cannot be negative."

            if not isinstance(y, (int, float)):
                errors["payload"] = "Click action requires a numeric y coordinate."

            elif y < 0:
                errors["payload"] = "Click y coordinate cannot be negative."

        elif self.action_type == self.ActionType.MULTI_CLICK:
            points = payload.get("points")

            if not isinstance(points, list) or not points:
                errors["payload"] = (
                    "Multiple click action requires a non-empty points list."
                )

            else:
                for index, point in enumerate(points):
                    if not isinstance(point, dict):
                        errors["payload"] = f"Point #{index + 1} must be an object."
                        break

                    x = point.get("x")
                    y = point.get("y")

                    if (
                        not isinstance(x, (int, float))
                        or not isinstance(y, (int, float))
                        or x < 0
                        or y < 0
                    ):
                        errors["payload"] = (
                            f"Point #{index + 1} has invalid coordinates."
                        )
                        break

        elif self.action_type == self.ActionType.SCROLL:
            delta_x = payload.get("delta_x", 0)
            delta_y = payload.get("delta_y", 0)

            if not isinstance(delta_x, (int, float)):
                errors["payload"] = "Scroll delta_x must be numeric."

            if not isinstance(delta_y, (int, float)):
                errors["payload"] = "Scroll delta_y must be numeric."

            if delta_x == 0 and delta_y == 0:
                errors["payload"] = "Scroll action requires a non-zero distance."

        elif self.action_type in {
            self.ActionType.REFRESH_SCREENSHOT,
            self.ActionType.CONTINUE,
            self.ActionType.RESTART,
            self.ActionType.CANCEL,
        }:
            pass

        else:
            errors["action_type"] = "Unsupported remote browser action."

        if errors:
            raise ValidationError(errors)

    # --------------------------------------------------------
    # Métodos de estado
    # --------------------------------------------------------

    def mark_processing(
        self,
        save=True,
    ):
        self.status = self.Status.PROCESSING
        self.processing_started_at = timezone.now()

        if save:
            self.save(
                update_fields=[
                    "status",
                    "processing_started_at",
                    "updated_at",
                ]
            )

    def mark_completed(
        self,
        *,
        result=None,
        save=True,
    ):
        self.status = self.Status.COMPLETED
        self.processed_at = timezone.now()
        self.result = result if isinstance(result, dict) else {}
        self.error_code = ""
        self.error_message = ""

        if save:
            self.save(
                update_fields=[
                    "status",
                    "processed_at",
                    "result",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )

    def mark_failed(
        self,
        *,
        message,
        code="",
        save=True,
    ):
        self.status = self.Status.FAILED
        self.processed_at = timezone.now()
        self.error_code = str(code or "")
        self.error_message = str(message or "")

        if save:
            self.save(
                update_fields=[
                    "status",
                    "processed_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
