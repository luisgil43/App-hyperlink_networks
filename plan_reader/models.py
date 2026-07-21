from django.conf import settings
from django.db import models

# =============================================================================
# RUTAS DE ARCHIVOS
# =============================================================================


def plan_reader_pdf_upload_path(instance, filename):
    job_id = instance.id or "new"
    return f"plan_reader/jobs/{job_id}/pdf/{filename}"


def plan_reader_excel_upload_path(instance, filename):
    job_id = instance.id or "new"
    return f"plan_reader/jobs/{job_id}/excel/{filename}"


def plan_reader_material_request_pdf_upload_path(instance, filename):
    """
    Ruta del PDF generado para un Material Request.

    Ejemplo:

        plan_reader/jobs/7/material_requests/3/
        0913R_05_material_request.pdf
    """
    job_id = instance.job_id or "new"
    request_id = instance.id or "new"

    return f"plan_reader/jobs/{job_id}/material_requests/" f"{request_id}/{filename}"


# =============================================================================
# PLAN READER JOB
# =============================================================================


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

    original_filename = models.CharField(
        max_length=255,
        blank=True,
    )

    pdf_file = models.FileField(
        upload_to=plan_reader_pdf_upload_path,
    )

    client = models.CharField(
        max_length=120,
        blank=True,
    )

    # Campos usados para llenar directamente el Excel Bulk Billing.
    city = models.CharField(
        max_length=120,
        blank=True,
    )

    project = models.CharField(
        max_length=255,
        blank=True,
    )

    co = models.CharField(
        "CO",
        max_length=50,
        blank=True,
    )

    dfn = models.CharField(
        "DFN",
        max_length=50,
        blank=True,
    )

    office = models.CharField(
        max_length=120,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    total_pages = models.PositiveIntegerField(
        default=0,
    )

    processed_pages = models.PositiveIntegerField(
        default=0,
    )

    failed_pages = models.PositiveIntegerField(
        default=0,
    )

    generated_excel = models.FileField(
        upload_to=plan_reader_excel_upload_path,
        blank=True,
        null=True,
    )

    error_message = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    started_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    completed_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "-created_at",
        ]

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

        return min(
            100,
            percent,
        )


# =============================================================================
# PLAN READER PAGE
# =============================================================================


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

    sheet_name = models.CharField(
        max_length=50,
        blank=True,
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    extracted_json = models.JSONField(
        blank=True,
        null=True,
    )

    raw_ai_response = models.TextField(
        blank=True,
    )

    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    error_message = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    processed_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    class Meta:
        ordering = [
            "job_id",
            "page_number",
        ]

        unique_together = [
            [
                "job",
                "page_number",
            ]
        ]

        verbose_name = "Plan Reader Page"
        verbose_name_plural = "Plan Reader Pages"

    def __str__(self):
        return f"Job #{self.job_id} - " f"Page {self.page_number}"


# =============================================================================
# PLAN READER ITEM
# =============================================================================


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

    sheet = models.CharField(
        max_length=50,
        blank=True,
    )

    co = models.CharField(
        "CO",
        max_length=50,
        blank=True,
    )

    dfn = models.CharField(
        "DFN",
        max_length=50,
        blank=True,
    )

    project_name = models.CharField(
        max_length=120,
        blank=True,
    )

    primary_feed = models.CharField(
        max_length=120,
        blank=True,
    )

    visible_type = models.CharField(
        max_length=120,
        blank=True,
    )

    detected_box_type = models.CharField(
        max_length=120,
        blank=True,
    )

    # Campos legacy.
    # Se mantienen por compatibilidad con producción
    # y con la lógica existente.
    has_p = models.BooleanField(
        default=False,
    )

    s_splitter = models.CharField(
        max_length=50,
        blank=True,
    )

    t_splitter = models.CharField(
        max_length=50,
        blank=True,
    )

    # Nueva fuente principal de información de splitters.
    #
    # Ejemplo:
    #
    # [
    #     {
    #         "level": "P",
    #         "ratio": "1:8",
    #         "raw_text": "P-1:8(P0049)",
    #     },
    #     {
    #         "level": "S",
    #         "ratio": "1:2",
    #         "raw_text": "S-1:2(P0049:S3)",
    #     },
    #     {
    #         "level": "T",
    #         "ratio": "1:4",
    #         "raw_text": "T-1:4(P0049,S3:T1)",
    #     },
    # ]
    splitter_lines = models.JSONField(
        default=list,
        blank=True,
    )

    splice_count = models.PositiveIntegerField(
        default=0,
    )

    calculated_box_type = models.CharField(
        max_length=120,
        blank=True,
    )

    c108_ug = models.PositiveIntegerField(
        default=0,
    )

    c109_splices = models.PositiveIntegerField(
        default=0,
    )

    c110_splitters = models.PositiveIntegerField(
        default=0,
    )

    observation = models.TextField(
        blank=True,
    )

    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    needs_review = models.BooleanField(
        default=True,
    )

    is_duplicate = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "sheet",
            "project_name",
            "primary_feed",
        ]

        verbose_name = "Plan Reader Item"
        verbose_name_plural = "Plan Reader Items"

    def __str__(self):
        project = self.project_name or "No project"
        feed = self.primary_feed or "No feed"

        return f"{project} - {feed}"


# =============================================================================
# MATERIAL REQUEST — CATÁLOGO
# =============================================================================


class MaterialCatalogItem(models.Model):
    """
    Catálogo maestro de materiales utilizados por el cliente.

    Este catálogo contiene las filas base del formulario, por ejemplo:

    - SPLICE CASE - OFDC-A4-S2/44-14-N-12
    - SPLICE CASE - OFDC-B8G-S2/84-14-NN-72
    - SPLITTER - 1x4 without connectors
    - SPLICE SLEEVE - 40MM
    - TDS LABEL
    - 24ct Fiber
    - 48ct Fiber
    - 72ct Fiber

    Cuando se crea un Material Request, cada material se copia a una línea
    editable. De esta manera, cambiar el catálogo en el futuro no altera
    solicitudes antiguas ya guardadas.

    Reglas de cantidades:

    - Splice cases:
      cantidad exacta detectada, sin porcentaje adicional.

    - TDS Labels:
      cantidad de cajas del tipo correspondiente más 10%.

    - Splitters:
      cantidad detectada más 10%.

    - Splice sleeves:
      cantidad de splices más 10%.

    - Cable y otros materiales:
      ingreso manual.
    """

    RULE_MANUAL = "manual"

    # -------------------------------------------------------------------------
    # Splice cases
    # Cantidad exacta detectada. No llevan 10% adicional.
    # -------------------------------------------------------------------------

    RULE_SPLICE_CASE_A4 = "splice_case_a4"
    RULE_SPLICE_CASE_B8G_EMPTY = "splice_case_b8g_empty"
    RULE_SPLICE_CASE_B8G_1X2 = "splice_case_b8g_1x2"
    RULE_SPLICE_CASE_B8G_1X4 = "splice_case_b8g_1x4"
    RULE_SPLICE_CASE_B8G_1X8 = "splice_case_b8g_1x8"
    RULE_SPLICE_CASE_C12 = "splice_case_c12"

    # -------------------------------------------------------------------------
    # TDS Labels
    # Se calculan según la cantidad de cajas del tipo correspondiente
    # y llevan 10% adicional.
    # -------------------------------------------------------------------------

    RULE_TDS_LABEL_A4 = "tds_label_a4"
    RULE_TDS_LABEL_B8G_EMPTY = "tds_label_b8g_empty"
    RULE_TDS_LABEL_B8G_1X2 = "tds_label_b8g_1x2"
    RULE_TDS_LABEL_B8G_1X4 = "tds_label_b8g_1x4"
    RULE_TDS_LABEL_B8G_1X8 = "tds_label_b8g_1x8"
    RULE_TDS_LABEL_C12 = "tds_label_c12"

    # -------------------------------------------------------------------------
    # Splitters
    # Se utilizarán los materiales "without connectors".
    # Llevan 10% adicional.
    # -------------------------------------------------------------------------

    RULE_SPLITTER_1X2 = "splitter_1x2"
    RULE_SPLITTER_1X4 = "splitter_1x4"
    RULE_SPLITTER_1X6 = "splitter_1x6"
    RULE_SPLITTER_1X8 = "splitter_1x8"

    # -------------------------------------------------------------------------
    # Splice sleeves
    # Llevan 10% adicional.
    # -------------------------------------------------------------------------

    RULE_SPLICE_SLEEVE_40MM = "splice_sleeve_40mm"
    RULE_SPLICE_SLEEVE_60MM = "splice_sleeve_60mm"

    RULE_CHOICES = [
        (
            RULE_MANUAL,
            "Manual",
        ),
        # Splice cases.
        (
            RULE_SPLICE_CASE_A4,
            "Splice case — A4",
        ),
        (
            RULE_SPLICE_CASE_B8G_EMPTY,
            "Splice case — B8G without splitter",
        ),
        (
            RULE_SPLICE_CASE_B8G_1X2,
            "Splice case — B8G with 1x2",
        ),
        (
            RULE_SPLICE_CASE_B8G_1X4,
            "Splice case — B8G with 1x4",
        ),
        (
            RULE_SPLICE_CASE_B8G_1X8,
            "Splice case — B8G with 1x8",
        ),
        (
            RULE_SPLICE_CASE_C12,
            "Splice case — C12",
        ),
        # TDS Labels.
        (
            RULE_TDS_LABEL_A4,
            "TDS Label — A4",
        ),
        (
            RULE_TDS_LABEL_B8G_EMPTY,
            "TDS Label — B8G without splitter",
        ),
        (
            RULE_TDS_LABEL_B8G_1X2,
            "TDS Label — B8G with 1x2",
        ),
        (
            RULE_TDS_LABEL_B8G_1X4,
            "TDS Label — B8G with 1x4",
        ),
        (
            RULE_TDS_LABEL_B8G_1X8,
            "TDS Label — B8G with 1x8",
        ),
        (
            RULE_TDS_LABEL_C12,
            "TDS Label — C12",
        ),
        # Splitters.
        (
            RULE_SPLITTER_1X2,
            "Splitter without connectors — 1x2",
        ),
        (
            RULE_SPLITTER_1X4,
            "Splitter without connectors — 1x4",
        ),
        (
            RULE_SPLITTER_1X6,
            "Splitter without connectors — 1x6",
        ),
        (
            RULE_SPLITTER_1X8,
            "Splitter without connectors — 1x8",
        ),
        # Splice sleeves.
        (
            RULE_SPLICE_SLEEVE_40MM,
            "Splice sleeve — 40MM",
        ),
        (
            RULE_SPLICE_SLEEVE_60MM,
            "Splice sleeve — 60MM",
        ),
    ]

    code = models.SlugField(
        max_length=120,
        unique=True,
        help_text=("Internal stable identifier used by the Material Request builder."),
    )

    material_type = models.CharField(
        "Type",
        max_length=120,
        blank=True,
    )

    category = models.CharField(
        max_length=120,
        blank=True,
    )

    material_name = models.CharField(
        "Material",
        max_length=500,
    )

    uom = models.CharField(
        "UOM",
        max_length=30,
        blank=True,
        default="EA",
    )

    auto_rule = models.CharField(
        max_length=60,
        choices=RULE_CHOICES,
        default=RULE_MANUAL,
        db_index=True,
    )

    display_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
    )

    is_active = models.BooleanField(
        default=True,
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
            "display_order",
            "id",
        ]

        indexes = [
            models.Index(
                fields=[
                    "is_active",
                    "display_order",
                ],
                name="pr_matcat_active_order_idx",
            ),
            models.Index(
                fields=[
                    "auto_rule",
                    "is_active",
                ],
                name="pr_matcat_rule_active_idx",
            ),
        ]

        verbose_name = "Material Catalog Item"
        verbose_name_plural = "Material Catalog Items"

    def __str__(self):
        return self.material_name


# =============================================================================
# MATERIAL REQUEST — ENCABEZADO
# =============================================================================


class PlanReaderMaterialRequest(models.Model):
    """
    Encabezado editable de una solicitud de materiales.

    Cada PlanReaderJob puede tener dos solicitudes diferentes:

    - Splicing Material Request
    - Cable Material Request

    Ambas solicitudes utilizan el mismo catálogo y el mismo formato PDF,
    pero tienen comportamientos diferentes:

    Splicing:
        Las cantidades correspondientes a cajas, TDS Labels, splitters
        y splice sleeves se calculan automáticamente desde Plan Reader.

    Cable:
        Los datos básicos se completan automáticamente, pero las cantidades
        de materiales son ingresadas manualmente por el usuario.

    Todos los materiales del catálogo se copian a cada solicitud para que
    el formulario pueda mostrarse e imprimirse completo, incluso cuando
    muchas cantidades permanezcan en cero.
    """

    STATUS_DRAFT = "draft"
    STATUS_GENERATED = "generated"

    STATUS_CHOICES = [
        (
            STATUS_DRAFT,
            "Draft",
        ),
        (
            STATUS_GENERATED,
            "PDF generated",
        ),
    ]

    REQUEST_TYPE_SPLICING = "splicing"
    REQUEST_TYPE_CABLE = "cable"

    REQUEST_TYPE_CHOICES = [
        (
            REQUEST_TYPE_SPLICING,
            "Splicing",
        ),
        (
            REQUEST_TYPE_CABLE,
            "Cable",
        ),
    ]

    job = models.ForeignKey(
        PlanReaderJob,
        on_delete=models.CASCADE,
        related_name="material_requests",
    )

    request_type = models.CharField(
        max_length=30,
        choices=REQUEST_TYPE_CHOICES,
        default=REQUEST_TYPE_SPLICING,
        db_index=True,
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    subcontractor = models.CharField(
        max_length=255,
        default="Hyperlink Networks LLC",
        blank=True,
    )

    request_date = models.DateField(
        blank=True,
        null=True,
    )

    market = models.CharField(
        max_length=120,
        blank=True,
    )

    dfn = models.CharField(
        "DFN",
        max_length=120,
        blank=True,
    )

    contractor_employee_name = models.CharField(
        max_length=255,
        blank=True,
    )

    contractor_employee_signature = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Typed signature or employee name to display " "in the generated PDF."
        ),
    )

    notes = models.TextField(
        blank=True,
    )

    generated_pdf = models.FileField(
        upload_to=plan_reader_material_request_pdf_upload_path,
        blank=True,
        null=True,
    )

    generated_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_plan_reader_material_requests",
    )

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_plan_reader_material_requests",
        blank=True,
        null=True,
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

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "job",
                    "request_type",
                ],
                name="unique_material_request_type_per_job",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "job",
                    "request_type",
                ],
                name="pr_matreq_job_type_idx",
            ),
            models.Index(
                fields=[
                    "request_type",
                    "status",
                ],
                name="pr_matreq_type_status_idx",
            ),
        ]

        verbose_name = "Plan Reader Material Request"
        verbose_name_plural = "Plan Reader Material Requests"

    def __str__(self):
        request_type_label = self.get_request_type_display()

        return (
            f"{request_type_label} Material Request #{self.id} - "
            f"Plan Reader Job #{self.job_id}"
        )

    @property
    def is_splicing_request(self):
        return self.request_type == self.REQUEST_TYPE_SPLICING

    @property
    def is_cable_request(self):
        return self.request_type == self.REQUEST_TYPE_CABLE

    @property
    def requested_items_count(self):
        return self.items.filter(
            quantity_requested__gt=0,
        ).count()

    @property
    def total_requested_quantity(self):
        total = self.items.aggregate(
            total=models.Sum(
                "quantity_requested",
            )
        )["total"]

        return total or 0


# =============================================================================
# MATERIAL REQUEST — DETALLE
# =============================================================================


class PlanReaderMaterialRequestItem(models.Model):
    """
    Línea editable del Material Request.

    Los datos de Type, Category, Material y UOM se copian desde el catálogo.
    Se guardan como snapshot para conservar exactamente la información usada
    al momento de generar el PDF.

    La línea puede continuar existiendo aunque el catálogo sea modificado
    o desactivado posteriormente.
    """

    SOURCE_MANUAL = "manual"
    SOURCE_AUTOMATIC = "automatic"
    SOURCE_AUTOMATIC_EDITED = "automatic_edited"

    SOURCE_CHOICES = [
        (
            SOURCE_MANUAL,
            "Manual",
        ),
        (
            SOURCE_AUTOMATIC,
            "Automatic",
        ),
        (
            SOURCE_AUTOMATIC_EDITED,
            "Automatic — edited by user",
        ),
    ]

    material_request = models.ForeignKey(
        PlanReaderMaterialRequest,
        on_delete=models.CASCADE,
        related_name="items",
    )

    catalog_item = models.ForeignKey(
        MaterialCatalogItem,
        on_delete=models.SET_NULL,
        related_name="material_request_items",
        blank=True,
        null=True,
    )

    material_type = models.CharField(
        "Type",
        max_length=120,
        blank=True,
    )

    category = models.CharField(
        max_length=120,
        blank=True,
    )

    material_name = models.CharField(
        "Material",
        max_length=500,
    )

    uom = models.CharField(
        "UOM",
        max_length=30,
        blank=True,
        default="EA",
    )

    quantity_requested = models.DecimalField(
        "QTY Requested",
        max_digits=14,
        decimal_places=2,
        default=0,
    )

    quantity_received = models.DecimalField(
        "QTY Received",
        max_digits=14,
        decimal_places=2,
        blank=True,
        null=True,
    )

    source = models.CharField(
        max_length=30,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
        db_index=True,
    )

    auto_rule = models.CharField(
        max_length=60,
        choices=MaterialCatalogItem.RULE_CHOICES,
        default=MaterialCatalogItem.RULE_MANUAL,
        db_index=True,
    )

    automatic_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text=("Last quantity calculated automatically from Plan Reader."),
    )

    calculation_note = models.TextField(
        blank=True,
        help_text=("Explanation of how the automatic quantity was calculated."),
    )

    display_order = models.PositiveIntegerField(
        default=0,
        db_index=True,
    )

    is_active = models.BooleanField(
        default=True,
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
            "display_order",
            "id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "material_request",
                    "catalog_item",
                ],
                condition=models.Q(
                    catalog_item__isnull=False,
                ),
                name="unique_catalog_item_per_material_request",
            ),
        ]

        indexes = [
            models.Index(
                fields=[
                    "material_request",
                    "display_order",
                ],
                name="pr_matreq_item_order_idx",
            ),
            models.Index(
                fields=[
                    "material_request",
                    "source",
                ],
                name="pr_matreq_item_source_idx",
            ),
            models.Index(
                fields=[
                    "material_request",
                    "auto_rule",
                ],
                name="pr_matreq_item_rule_idx",
            ),
        ]

        verbose_name = "Plan Reader Material Request Item"
        verbose_name_plural = "Plan Reader Material Request Items"

    def __str__(self):
        return (
            f"Material Request #{self.material_request_id} - " f"{self.material_name}"
        )

    @property
    def was_automatically_modified(self):
        """
        Indica si la cantidad que ve el usuario ya no coincide con
        la última cantidad calculada por Plan Reader.
        """
        if self.source == self.SOURCE_MANUAL:
            return False

        return self.quantity_requested != self.automatic_quantity
