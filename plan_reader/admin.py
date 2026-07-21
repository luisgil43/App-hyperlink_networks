from django.contrib import admin

from .models import (MaterialCatalogItem, PlanReaderItem, PlanReaderJob,
                     PlanReaderMaterialRequest, PlanReaderMaterialRequestItem,
                     PlanReaderPage)

# =============================================================================
# PLAN READER JOBS
# =============================================================================


@admin.register(PlanReaderJob)
class PlanReaderJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_filename",
        "client",
        "city",
        "project",
        "office",
        "co",
        "dfn",
        "status",
        "total_pages",
        "processed_pages",
        "failed_pages",
        "uploaded_by",
        "created_at",
    )

    list_filter = (
        "status",
        "client",
        "city",
        "project",
        "office",
        "co",
        "dfn",
        "created_at",
    )

    search_fields = (
        "id",
        "original_filename",
        "client",
        "city",
        "project",
        "office",
        "co",
        "dfn",
        "uploaded_by__username",
        "uploaded_by__first_name",
        "uploaded_by__last_name",
        "uploaded_by__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "progress_percent_display",
    )

    list_select_related = ("uploaded_by",)

    ordering = ("-created_at",)

    fieldsets = (
        (
            "Uploaded plan",
            {
                "fields": (
                    "uploaded_by",
                    "original_filename",
                    "pdf_file",
                    "generated_excel",
                )
            },
        ),
        (
            "Project information",
            {
                "fields": (
                    "client",
                    "city",
                    "project",
                    "office",
                    "co",
                    "dfn",
                    "notes",
                )
            },
        ),
        (
            "Processing",
            {
                "fields": (
                    "status",
                    "total_pages",
                    "processed_pages",
                    "failed_pages",
                    "progress_percent_display",
                    "error_message",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "created_at",
                    "started_at",
                    "completed_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Progress")
    def progress_percent_display(self, obj):
        if not obj:
            return "0%"

        return f"{obj.progress_percent}%"


# =============================================================================
# PLAN READER PAGES
# =============================================================================


@admin.register(PlanReaderPage)
class PlanReaderPageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "page_number",
        "sheet_name",
        "status",
        "confidence",
        "processed_at",
    )

    list_filter = (
        "status",
        "sheet_name",
        "processed_at",
        "created_at",
    )

    search_fields = (
        "job__id",
        "job__original_filename",
        "job__co",
        "job__dfn",
        "sheet_name",
        "error_message",
    )

    readonly_fields = (
        "created_at",
        "processed_at",
    )

    list_select_related = ("job",)

    ordering = (
        "-job_id",
        "page_number",
    )

    fieldsets = (
        (
            "Page",
            {
                "fields": (
                    "job",
                    "page_number",
                    "sheet_name",
                    "status",
                    "confidence",
                )
            },
        ),
        (
            "OpenAI extraction",
            {
                "classes": ("collapse",),
                "fields": (
                    "extracted_json",
                    "raw_ai_response",
                ),
            },
        ),
        (
            "Processing result",
            {
                "fields": (
                    "error_message",
                    "created_at",
                    "processed_at",
                )
            },
        ),
    )


# =============================================================================
# PLAN READER ITEMS
# =============================================================================


@admin.register(PlanReaderItem)
class PlanReaderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "sheet",
        "project_name",
        "primary_feed",
        "calculated_box_type",
        "splice_count",
        "c108_ug",
        "c109_splices",
        "c110_splitters",
        "needs_review",
        "is_duplicate",
    )

    list_filter = (
        "needs_review",
        "is_duplicate",
        "calculated_box_type",
        "detected_box_type",
        "visible_type",
        "sheet",
        "created_at",
    )

    search_fields = (
        "id",
        "job__id",
        "job__original_filename",
        "project_name",
        "primary_feed",
        "sheet",
        "co",
        "dfn",
        "visible_type",
        "detected_box_type",
        "calculated_box_type",
        "observation",
    )

    readonly_fields = ("created_at",)

    list_select_related = (
        "job",
        "page",
    )

    ordering = (
        "-job_id",
        "sheet",
        "project_name",
        "primary_feed",
    )

    fieldsets = (
        (
            "Source",
            {
                "fields": (
                    "job",
                    "page",
                    "sheet",
                    "co",
                    "dfn",
                )
            },
        ),
        (
            "Detected item",
            {
                "fields": (
                    "project_name",
                    "primary_feed",
                    "visible_type",
                    "detected_box_type",
                    "calculated_box_type",
                    "confidence",
                )
            },
        ),
        (
            "Splitters",
            {
                "fields": (
                    "splitter_lines",
                    "has_p",
                    "s_splitter",
                    "t_splitter",
                )
            },
        ),
        (
            "Billing quantities",
            {
                "fields": (
                    "splice_count",
                    "c108_ug",
                    "c109_splices",
                    "c110_splitters",
                )
            },
        ),
        (
            "Review",
            {
                "fields": (
                    "needs_review",
                    "is_duplicate",
                    "observation",
                    "created_at",
                )
            },
        ),
    )


# =============================================================================
# MATERIAL CATALOG
# =============================================================================


@admin.register(MaterialCatalogItem)
class MaterialCatalogItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "material_type",
        "category",
        "short_material_name",
        "uom",
        "auto_rule",
        "display_order",
        "is_active",
        "updated_at",
    )

    list_filter = (
        "is_active",
        "auto_rule",
        "material_type",
        "category",
        "uom",
        "created_at",
        "updated_at",
    )

    search_fields = (
        "code",
        "material_type",
        "category",
        "material_name",
        "uom",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "display_order",
        "id",
    )

    list_editable = (
        "display_order",
        "is_active",
    )

    fieldsets = (
        (
            "Material",
            {
                "fields": (
                    "code",
                    "material_type",
                    "category",
                    "material_name",
                    "uom",
                )
            },
        ),
        (
            "Automatic calculation",
            {
                "fields": (
                    "auto_rule",
                    "display_order",
                    "is_active",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Material")
    def short_material_name(self, obj):
        material_name = obj.material_name or ""

        if len(material_name) <= 90:
            return material_name

        return f"{material_name[:87]}..."


# =============================================================================
# MATERIAL REQUEST ITEMS INLINE
# =============================================================================


class PlanReaderMaterialRequestItemInline(admin.TabularInline):
    model = PlanReaderMaterialRequestItem

    extra = 0

    fields = (
        "display_order",
        "material_type",
        "category",
        "material_name",
        "uom",
        "quantity_requested",
        "quantity_received",
        "source",
        "auto_rule",
        "automatic_quantity",
        "is_active",
    )

    readonly_fields = (
        "source",
        "auto_rule",
        "automatic_quantity",
    )

    ordering = (
        "display_order",
        "id",
    )

    show_change_link = True


# =============================================================================
# MATERIAL REQUEST HEADER
# =============================================================================


@admin.register(PlanReaderMaterialRequest)
class PlanReaderMaterialRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job",
        "dfn",
        "market",
        "request_date",
        "status",
        "requested_items_count_display",
        "total_requested_quantity_display",
        "created_by",
        "generated_at",
        "updated_at",
    )

    list_filter = (
        "status",
        "market",
        "request_date",
        "generated_at",
        "created_at",
        "updated_at",
    )

    search_fields = (
        "id",
        "job__id",
        "job__original_filename",
        "job__client",
        "job__city",
        "job__project",
        "job__office",
        "job__co",
        "job__dfn",
        "dfn",
        "market",
        "subcontractor",
        "contractor_employee_name",
        "created_by__username",
        "created_by__first_name",
        "created_by__last_name",
        "created_by__email",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "generated_at",
        "requested_items_count_display",
        "total_requested_quantity_display",
    )

    list_select_related = (
        "job",
        "created_by",
        "updated_by",
    )

    ordering = ("-created_at",)

    inlines = (PlanReaderMaterialRequestItemInline,)

    fieldsets = (
        (
            "Plan Reader source",
            {
                "fields": (
                    "job",
                    "status",
                )
            },
        ),
        (
            "Request information",
            {
                "fields": (
                    "subcontractor",
                    "request_date",
                    "market",
                    "dfn",
                    "contractor_employee_name",
                    "contractor_employee_signature",
                    "notes",
                )
            },
        ),
        (
            "Totals",
            {
                "fields": (
                    "requested_items_count_display",
                    "total_requested_quantity_display",
                )
            },
        ),
        (
            "Generated PDF",
            {
                "fields": (
                    "generated_pdf",
                    "generated_at",
                )
            },
        ),
        (
            "Audit",
            {
                "fields": (
                    "created_by",
                    "updated_by",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Requested items")
    def requested_items_count_display(self, obj):
        if not obj or not obj.pk:
            return 0

        return obj.requested_items_count

    @admin.display(description="Total requested quantity")
    def total_requested_quantity_display(self, obj):
        if not obj or not obj.pk:
            return 0

        return obj.total_requested_quantity


# =============================================================================
# MATERIAL REQUEST ITEMS
# =============================================================================


@admin.register(PlanReaderMaterialRequestItem)
class PlanReaderMaterialRequestItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "material_request",
        "display_order",
        "short_material_name",
        "uom",
        "quantity_requested",
        "quantity_received",
        "automatic_quantity",
        "source",
        "auto_rule",
        "was_modified_display",
        "is_active",
    )

    list_filter = (
        "source",
        "auto_rule",
        "is_active",
        "material_type",
        "category",
        "uom",
        "created_at",
        "updated_at",
    )

    search_fields = (
        "id",
        "material_request__id",
        "material_request__job__id",
        "material_request__job__original_filename",
        "material_request__job__co",
        "material_request__job__dfn",
        "material_request__dfn",
        "catalog_item__code",
        "material_type",
        "category",
        "material_name",
        "calculation_note",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "was_modified_display",
    )

    list_select_related = (
        "material_request",
        "material_request__job",
        "catalog_item",
    )

    ordering = (
        "-material_request_id",
        "display_order",
        "id",
    )

    fieldsets = (
        (
            "Material Request",
            {
                "fields": (
                    "material_request",
                    "catalog_item",
                )
            },
        ),
        (
            "Material snapshot",
            {
                "fields": (
                    "material_type",
                    "category",
                    "material_name",
                    "uom",
                )
            },
        ),
        (
            "Quantities",
            {
                "fields": (
                    "quantity_requested",
                    "quantity_received",
                    "automatic_quantity",
                    "was_modified_display",
                )
            },
        ),
        (
            "Calculation",
            {
                "fields": (
                    "source",
                    "auto_rule",
                    "calculation_note",
                )
            },
        ),
        (
            "Display",
            {
                "fields": (
                    "display_order",
                    "is_active",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    @admin.display(description="Material")
    def short_material_name(self, obj):
        material_name = obj.material_name or ""

        if len(material_name) <= 90:
            return material_name

        return f"{material_name[:87]}..."

    @admin.display(
        description="User modified",
        boolean=True,
    )
    def was_modified_display(self, obj):
        return obj.was_automatically_modified
