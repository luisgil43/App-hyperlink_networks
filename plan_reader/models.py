from django.conf import settings
from django.db import models


def plan_reader_pdf_upload_path(instance, filename):
    job_id = instance.id or "new"
    return f"plan_reader/jobs/{job_id}/pdf/{filename}"


def plan_reader_excel_upload_path(instance, filename):
    job_id = instance.id or "new"
    return f"plan_reader/jobs/{job_id}/excel/{filename}"


class PlanReaderJob(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_NEEDS_REVIEW = "needs_review"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_NEEDS_REVIEW, "Needs review"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="plan_reader_jobs",
    )

    original_filename = models.CharField(max_length=255, blank=True)
    pdf_file = models.FileField(upload_to=plan_reader_pdf_upload_path)

    client = models.CharField(max_length=120, blank=True)

    # Nuevos campos para llenar directamente el Excel Bulk Billing.
    # No reemplazan ni renombran campos existentes.
    city = models.CharField(max_length=120, blank=True)
    project = models.CharField(max_length=255, blank=True)

    co = models.CharField("CO", max_length=50, blank=True)
    dfn = models.CharField("DFN", max_length=50, blank=True)
    office = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    total_pages = models.PositiveIntegerField(default=0)
    processed_pages = models.PositiveIntegerField(default=0)
    failed_pages = models.PositiveIntegerField(default=0)

    generated_excel = models.FileField(
        upload_to=plan_reader_excel_upload_path,
        blank=True,
        null=True,
    )

    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Plan Reader Job"
        verbose_name_plural = "Plan Reader Jobs"

    def __str__(self):
        name = self.original_filename or self.pdf_file.name or "PDF"
        return f"Plan Reader Job #{self.id} - {name}"

    @property
    def progress_percent(self):
        if not self.total_pages:
            return 0

        percent = round((self.processed_pages / self.total_pages) * 100)
        return min(100, percent)


class PlanReaderPage(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
    ]

    job = models.ForeignKey(
        PlanReaderJob,
        on_delete=models.CASCADE,
        related_name="pages",
    )

    page_number = models.PositiveIntegerField()
    sheet_name = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    extracted_json = models.JSONField(blank=True, null=True)
    raw_ai_response = models.TextField(blank=True)

    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["job_id", "page_number"]
        unique_together = [["job", "page_number"]]
        verbose_name = "Plan Reader Page"
        verbose_name_plural = "Plan Reader Pages"

    def __str__(self):
        return f"Job #{self.job_id} - Page {self.page_number}"


class PlanReaderItem(models.Model):
    job = models.ForeignKey(
        PlanReaderJob,
        on_delete=models.CASCADE,
        related_name="items",
    )

    page = models.ForeignKey(
        PlanReaderPage,
        on_delete=models.SET_NULL,
        related_name="items",
        blank=True,
        null=True,
    )

    sheet = models.CharField(max_length=50, blank=True)
    co = models.CharField("CO", max_length=50, blank=True)
    dfn = models.CharField("DFN", max_length=50, blank=True)

    project_name = models.CharField(max_length=120, blank=True)
    primary_feed = models.CharField(max_length=120, blank=True)

    visible_type = models.CharField(max_length=120, blank=True)
    detected_box_type = models.CharField(max_length=120, blank=True)

    has_p = models.BooleanField(default=False)
    s_splitter = models.CharField(max_length=50, blank=True)
    t_splitter = models.CharField(max_length=50, blank=True)

    splice_count = models.PositiveIntegerField(default=0)

    calculated_box_type = models.CharField(max_length=120, blank=True)

    c108_ug = models.PositiveIntegerField(default=0)
    c109_splices = models.PositiveIntegerField(default=0)
    c110_splitters = models.PositiveIntegerField(default=0)

    observation = models.TextField(blank=True)

    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    needs_review = models.BooleanField(default=True)
    is_duplicate = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sheet", "project_name", "primary_feed"]
        verbose_name = "Plan Reader Item"
        verbose_name_plural = "Plan Reader Items"

    def __str__(self):
        project = self.project_name or "No project"
        feed = self.primary_feed or "No feed"
        return f"{project} - {feed}"
