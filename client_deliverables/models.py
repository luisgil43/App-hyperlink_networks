# Create your models here.
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

try:
    from facturacion.models import Proyecto
except Exception:
    Proyecto = None

try:
    from operaciones.models import SesionBilling
except Exception:
    SesionBilling = None


class ClientProjectAssignment(models.Model):
    """
    Asigna Project IDs específicos a usuarios con rol cliente.

    Regla:
    El cliente NO ve todos los proyectos de una empresa/cliente.
    Solo ve los Project IDs asignados explícitamente a su usuario.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="client_project_assignments",
    )

    proyecto = models.ForeignKey(
        "facturacion.Proyecto",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_assignments",
    )

    project_id = models.CharField(
        max_length=120,
        db_index=True,
        help_text="Project ID visible en Billing/operaciones.",
    )

    client_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Nombre del cliente/empresa para referencia.",
    )

    is_active = models.BooleanField(default=True)

    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_project_assignments_created",
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client Project Assignment"
        verbose_name_plural = "Client Project Assignments"
        indexes = [
            models.Index(fields=["user", "project_id"]),
            models.Index(fields=["project_id", "is_active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "project_id"],
                name="unique_client_project_assignment",
            )
        ]

    def __str__(self):
        return f"{self.user} → {self.project_id}"


class DeliveryPackage(models.Model):

    STATUS_DRAFT = "draft"

    STATUS_PUBLISHED = "published"

    STATUS_EXPIRED = "expired"

    STATUS_REVOKED = "revoked"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_REVOKED, "Revoked"),
    ]

    EXPIRATION_NONE = "none"

    EXPIRATION_DAYS = "days"

    EXPIRATION_DATE = "date"

    EXPIRATION_CHOICES = [
        (EXPIRATION_NONE, "No expiration"),
        (EXPIRATION_DAYS, "Expires in days"),
        (EXPIRATION_DATE, "Specific expiration date"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)

    token = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        editable=False,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_packages_created",
    )

    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_packages_published",
    )

    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_packages_revoked",
    )

    expiration_mode = models.CharField(
        max_length=20,
        choices=EXPIRATION_CHOICES,
        default=EXPIRATION_NONE,
    )

    expiration_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Cantidad de días de vigencia si expiration_mode=days.",
    )

    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Si está vacío, el link no expira.",
    )

    requires_access_key = models.BooleanField(
        default=False,
        help_text="Si está activo, el cliente debe ingresar una clave para abrir el link.",
    )

    access_key_hash = models.CharField(
        max_length=255,
        blank=True,
        help_text="Clave hasheada. Se usa para validar la clave de acceso.",
    )

    access_key_plain = models.CharField(
        max_length=50,
        blank=True,
        help_text="Clave visible para copiar y enviar al cliente por otro canal.",
    )

    access_key_hint = models.CharField(
        max_length=120,
        blank=True,
        help_text="Referencia opcional, no debe revelar la clave.",
    )

    failed_attempts = models.PositiveIntegerField(default=0)

    locked_until = models.DateTimeField(null=True, blank=True)

    message = models.TextField(
        blank=True,
        help_text="Mensaje interno/opcional para el paquete.",
    )

    published_at = models.DateTimeField(null=True, blank=True)

    revoked_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:

        verbose_name = "Delivery Package"

        verbose_name_plural = "Delivery Packages"

        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["token"]),
        ]

    def __str__(self):

        return self.name

    def save(self, *args, **kwargs):

        if not self.token:

            self.token = self.generate_token()

        if self.expiration_mode == self.EXPIRATION_NONE:

            self.expiration_days = None

            self.expires_at = None

        super().save(*args, **kwargs)

    @staticmethod
    def generate_token():

        return secrets.token_urlsafe(48)

    @staticmethod
    def generate_access_key(length=6):
        """

        Genera una clave numérica simple para enviar por otro canal.

        Similar al flujo de seguridad del onboarding.

        """

        digits = "0123456789"

        return "".join(secrets.choice(digits) for _ in range(length))

    def set_access_key(self, raw_key):

        raw_key = str(raw_key or "").strip()

        if raw_key:

            self.access_key_hash = make_password(raw_key)

            self.access_key_plain = raw_key

            self.requires_access_key = True

        else:

            self.access_key_hash = ""

            self.access_key_plain = ""

            self.requires_access_key = False

    def check_access_key(self, raw_key):

        if not self.requires_access_key:

            return True

        if not self.access_key_hash:

            return False

        return check_password(raw_key or "", self.access_key_hash)

    def is_expired(self):

        if self.status == self.STATUS_REVOKED:

            return False

        if not self.expires_at:

            return False

        return timezone.now() > self.expires_at

    def is_locked(self):

        return bool(self.locked_until and timezone.now() < self.locked_until)

    def can_be_opened(self):

        if self.status != self.STATUS_PUBLISHED:

            return False

        if self.is_expired():

            return False

        if self.is_locked():

            return False

        return True

    def publish(self, user=None):

        now = timezone.now()

        if self.expiration_mode == self.EXPIRATION_DAYS and self.expiration_days:

            self.expires_at = now + timedelta(days=int(self.expiration_days))

        self.status = self.STATUS_PUBLISHED

        self.published_at = now

        self.published_by = user

        self.revoked_at = None

        self.revoked_by = None

    def revoke(self, user=None):

        self.status = self.STATUS_REVOKED

        self.revoked_at = timezone.now()

        self.revoked_by = user

    def register_failed_attempt(self):

        self.failed_attempts = (self.failed_attempts or 0) + 1

        if self.failed_attempts >= 5:

            self.locked_until = timezone.now() + timedelta(minutes=15)

    def reset_failed_attempts(self):

        self.failed_attempts = 0

        self.locked_until = None

    def project_ids(self):

        return list(
            self.files.exclude(project_id="")
            .values_list("project_id", flat=True)
            .distinct()
        )

    def public_url_path(self):

        return f"/client-deliverables/p/{self.token}/"


class DeliveryPackageFile(models.Model):
    FILE_CLIENT_REPORT = "client_report"
    FILE_PHOTO_REPORT = "photo_report"
    FILE_PHOTOS_ZIP = "photos_zip"
    FILE_LIGHT_LEVELS = "light_levels"
    FILE_OPERATIONAL_REPORT = "operational_report"
    FILE_MANUAL = "manual_file"
    FILE_OTHER = "other"

    FILE_TYPE_CHOICES = [
        (FILE_CLIENT_REPORT, "Client Report"),
        (FILE_PHOTO_REPORT, "Photo Report"),
        (FILE_PHOTOS_ZIP, "Photos ZIP"),
        (FILE_LIGHT_LEVELS, "Light Levels"),
        (FILE_OPERATIONAL_REPORT, "Operational Report"),
        (FILE_MANUAL, "Manual File"),
        (FILE_OTHER, "Other"),
    ]

    package = models.ForeignKey(
        DeliveryPackage,
        on_delete=models.CASCADE,
        related_name="files",
    )

    billing_session = models.ForeignKey(
        "operaciones.SesionBilling",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_files",
    )

    project_id = models.CharField(max_length=120, db_index=True)

    file_type = models.CharField(
        max_length=40,
        choices=FILE_TYPE_CHOICES,
        default=FILE_OTHER,
    )

    display_name = models.CharField(max_length=255)

    file = models.FileField(
        upload_to="client_deliverables/%Y/%m/",
        null=True,
        blank=True,
    )

    source_url = models.TextField(
        blank=True,
        help_text="URL interna o externa desde donde se puede generar/descargar el archivo.",
    )

    source_key = models.TextField(
        blank=True,
        help_text="Storage key opcional si el archivo vive en Wasabi/S3.",
    )

    size_bytes = models.PositiveBigIntegerField(null=True, blank=True)

    order = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_files_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Delivery Package File"
        verbose_name_plural = "Delivery Package Files"
        ordering = ["project_id", "order", "id"]
        indexes = [
            models.Index(fields=["package", "project_id"]),
            models.Index(fields=["project_id", "file_type"]),
        ]

    def __str__(self):
        return f"{self.project_id} - {self.display_name}"

    def safe_filename(self):
        base = self.display_name or self.get_file_type_display() or "deliverable"
        base = slugify(base).replace("-", "_") or "deliverable"

        ext = ""

        if self.file and self.file.name:
            name = self.file.name
            if "." in name:
                ext = "." + name.rsplit(".", 1)[-1].lower()

        if not ext:
            if self.file_type == self.FILE_CLIENT_REPORT:
                ext = ".xlsx"
            elif self.file_type == self.FILE_OPERATIONAL_REPORT:
                ext = ".xlsx"
            elif self.file_type == self.FILE_LIGHT_LEVELS:
                ext = ".xlsx"
            elif self.file_type == self.FILE_PHOTO_REPORT:
                ext = ".pdf"
            elif self.file_type == self.FILE_PHOTOS_ZIP:
                ext = ".zip"

        return f"{base}_{self.project_id}{ext}"


class DeliveryAccessLog(models.Model):
    ACTION_VIEW = "view"
    ACTION_UNLOCK_SUCCESS = "unlock_success"
    ACTION_UNLOCK_FAILED = "unlock_failed"
    ACTION_DOWNLOAD_FILE = "download_file"
    ACTION_DOWNLOAD_ALL = "download_all"
    ACTION_REVOKED_ACCESS = "revoked_access"
    ACTION_EXPIRED_ACCESS = "expired_access"

    ACTION_CHOICES = [
        (ACTION_VIEW, "View"),
        (ACTION_UNLOCK_SUCCESS, "Unlock success"),
        (ACTION_UNLOCK_FAILED, "Unlock failed"),
        (ACTION_DOWNLOAD_FILE, "Download file"),
        (ACTION_DOWNLOAD_ALL, "Download all"),
        (ACTION_REVOKED_ACCESS, "Revoked access"),
        (ACTION_EXPIRED_ACCESS, "Expired access"),
    ]

    package = models.ForeignKey(
        DeliveryPackage,
        on_delete=models.CASCADE,
        related_name="access_logs",
    )

    file = models.ForeignKey(
        DeliveryPackageFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="access_logs",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_access_logs",
    )

    action = models.CharField(max_length=40, choices=ACTION_CHOICES)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    extra = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Delivery Access Log"
        verbose_name_plural = "Delivery Access Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["package", "action"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.package} - {self.action} - {self.created_at:%Y-%m-%d %H:%M}"


class DeliveryZipJob(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_READY, "Ready"),
        (STATUS_FAILED, "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    package = models.ForeignKey(
        DeliveryPackage,
        on_delete=models.CASCADE,
        related_name="zip_jobs",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    zip_file = models.FileField(
        upload_to="client_deliverables/zips/%Y/%m/",
        null=True,
        blank=True,
    )

    filename = models.CharField(max_length=255, blank=True)

    total_files = models.PositiveIntegerField(default=0)
    files_added = models.PositiveIntegerField(default=0)
    files_failed = models.PositiveIntegerField(default=0)

    error_message = models.TextField(blank=True)
    errors = models.JSONField(default=list, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_zip_jobs_requested",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Delivery ZIP Job"
        verbose_name_plural = "Delivery ZIP Jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["package", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.package} - {self.status}"

    def is_ready(self):
        return self.status == self.STATUS_READY and bool(self.zip_file)

    def is_failed(self):
        return self.status == self.STATUS_FAILED
