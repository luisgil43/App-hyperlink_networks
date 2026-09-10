from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# ============================================================
# COMMON CHOICES
# ============================================================


class PlanningResourceType(models.TextChoices):
    CREW = "crew", "Crew"
    TECHNICIAN = "technician", "Technician"
    OTHER = "other", "Other"


class PlanningUnit(models.TextChoices):
    BOX = "box", "Box"
    FOOT = "ft", "Foot"
    SITE = "site", "Site"
    EACH = "ea", "Each"
    HOUR = "hour", "Hour"
    DAY = "day", "Day"
    OTHER = "other", "Other"


class PlanningAssignmentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On Hold"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


# ============================================================
# CLIENTS
# ============================================================


class PlanningClient(models.Model):
    """
    Client catalog used by Planning.

    Planning is intentionally not tied to a single client.
    One client can have assignments in many cities/markets and one
    location can contain assignments from multiple clients.
    """

    name = models.CharField(
        max_length=180,
        unique=True,
    )

    code = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        unique=True,
        help_text="Optional internal or client-facing short code.",
    )

    is_active = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Planning Client"
        verbose_name_plural = "Planning Clients"

    def __str__(self):
        return self.name


# ============================================================
# LOCATIONS
# ============================================================


class PlanningLocation(models.Model):
    """
    Geographic / operational location.

    City and Market are intentionally stored separately because they
    do not necessarily represent the same organizational concept.

    Example:
        Country: United States
        State: Wisconsin
        City: Green Bay
        Market: Wisconsin North
    """

    country = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
    )

    state = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
    )

    city = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
    )

    market = models.CharField(
        max_length=120,
        blank=True,
        db_index=True,
    )

    is_active = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "country",
            "state",
            "city",
            "market",
        ]
        indexes = [
            models.Index(
                fields=["city", "market"],
                name="planning_loc_city_market_idx",
            ),
        ]
        verbose_name = "Planning Location"
        verbose_name_plural = "Planning Locations"

    def __str__(self):
        parts = [
            self.city,
            self.state,
            self.market,
            self.country,
        ]

        parts = [part for part in parts if part]

        if not parts:
            return f"Location #{self.pk}" if self.pk else "Planning Location"

        return " / ".join(parts)


# ============================================================
# ASSIGNMENTS
# ============================================================


class PlanningAssignment(models.Model):
    """
    Root business entity of the Planning module.

    An Assignment may represent:
    - NTP
    - Work Order
    - Client assignment
    - Project package
    - Future assignment structures not based on DFN

    DFN is deliberately NOT the root entity.

    One Assignment may contain:
    - zero DFNs,
    - one DFN,
    - many DFNs.
    """

    client = models.ForeignKey(
        PlanningClient,
        on_delete=models.PROTECT,
        related_name="assignments",
    )

    location = models.ForeignKey(
        PlanningLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )

    name = models.CharField(
        max_length=220,
    )

    client_reference = models.CharField(
        max_length=150,
        blank=True,
        db_index=True,
        help_text=(
            "Client assignment reference, NTP number, Work Order, "
            "or other external identifier."
        ),
    )

    assignment_type = models.CharField(
        max_length=80,
        blank=True,
        help_text=(
            "Optional descriptive type such as NTP, Work Order, "
            "Project Package, etc."
        ),
    )

    issue_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
    )

    pc = models.CharField(
        max_length=80,
        blank=True,
        help_text="Optional PC or equivalent client identifier.",
    )

    contractor = models.CharField(
        max_length=150,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=PlanningAssignmentStatus.choices,
        default=PlanningAssignmentStatus.DRAFT,
        db_index=True,
    )

    source_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Optional source document, email reference, uploaded file "
            "or other origin reference."
        ),
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="planning_assignments_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "-issue_date",
            "client__name",
            "name",
        ]
        indexes = [
            models.Index(
                fields=["client", "status"],
                name="planning_asg_client_status_idx",
            ),
            models.Index(
                fields=["location", "status"],
                name="planning_asg_loc_status_idx",
            ),
        ]
        verbose_name = "Planning Assignment"
        verbose_name_plural = "Planning Assignments"

    def __str__(self):
        if self.client_reference:
            return f"{self.client.name} - {self.client_reference}"

        return f"{self.client.name} - {self.name}"


# ============================================================
# DFN
# ============================================================


class PlanningDFN(models.Model):
    """
    Optional DFN contained inside an Assignment.

    DFN is an important planning identifier for current projects,
    but it is not required for every future client or assignment type.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        ON_HOLD = "on_hold", "On Hold"

    assignment = models.ForeignKey(
        PlanningAssignment,
        on_delete=models.CASCADE,
        related_name="dfns",
        null=True,
        blank=True,
        help_text=(
            "Assignment that owns this DFN. Nullable during the initial "
            "migration from the legacy PlanningDFN structure."
        ),
    )

    code = models.CharField(
        max_length=80,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="planning_dfns_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "code"],
                name="uniq_dfn_code_per_assignment",
            )
        ]
        indexes = [
            models.Index(
                fields=["assignment", "status"],
                name="planning_dfn_asg_status_idx",
            ),
        ]
        verbose_name = "Planning DFN"
        verbose_name_plural = "Planning DFNs"

    def __str__(self):
        return self.code


# ============================================================
# PLANNING CALENDAR
# ============================================================


class PlanningCalendar(models.Model):
    """
    Working calendar used by the scheduling engine.

    It determines which weekdays normally count as working days.
    Specific date exceptions are stored separately.
    """

    name = models.CharField(
        max_length=120,
        unique=True,
    )

    monday = models.BooleanField(default=True)
    tuesday = models.BooleanField(default=True)
    wednesday = models.BooleanField(default=True)
    thursday = models.BooleanField(default=True)
    friday = models.BooleanField(default=True)
    saturday = models.BooleanField(default=False)
    sunday = models.BooleanField(default=False)

    daily_work_hours = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("8.00"),
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Planning Calendar"
        verbose_name_plural = "Planning Calendars"

    def __str__(self):
        return self.name


class PlanningCalendarException(models.Model):
    """
    Overrides the normal weekday behavior for one particular date.

    Examples:
    - Holiday on a normally-working Monday.
    - Special working Saturday.
    """

    calendar = models.ForeignKey(
        PlanningCalendar,
        on_delete=models.CASCADE,
        related_name="exceptions",
    )

    date = models.DateField()

    is_working_day = models.BooleanField(
        help_text=(
            "Enable when this date must count as a working day, "
            "even if its weekday is normally non-working."
        )
    )

    note = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["calendar", "date"],
                name="uniq_planning_calendar_exception_date",
            )
        ]
        verbose_name = "Planning Calendar Exception"
        verbose_name_plural = "Planning Calendar Exceptions"

    def __str__(self):
        return f"{self.calendar} - {self.date}"


# ============================================================
# ACTIVITY CATALOG
# ============================================================


class PlanningActivityType(models.Model):
    """
    Persistent configurable catalog of Planning activities.

    Activities may be:
    - created manually by a planner;
    - discovered in an imported NTP and confirmed with the Assignment.

    Code alone is not globally unique because the same client code may
    represent different variants, for example:

        C-108 - UG Splicing / Testing
        C-108 - AER Splicing / Testing
    """

    code = models.CharField(
        max_length=50,
        db_index=True,
    )

    name = models.CharField(
        max_length=180,
    )

    default_unit = models.CharField(
        max_length=20,
        choices=PlanningUnit.choices,
        default=PlanningUnit.OTHER,
    )

    default_resource_type = models.CharField(
        max_length=20,
        choices=PlanningResourceType.choices,
        default=PlanningResourceType.CREW,
    )

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    display_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "display_order",
            "code",
            "name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "code",
                    "name",
                ],
                name="uniq_planning_activity_code_name",
            ),
        ]
        verbose_name = "Planning Activity Type"
        verbose_name_plural = "Planning Activity Types"

    def clean(self):
        super().clean()

        self.code = (self.code or "").strip().upper()

        self.name = (self.name or "").strip()

    def save(
        self,
        *args,
        **kwargs,
    ):
        self.code = (self.code or "").strip().upper()

        self.name = (self.name or "").strip()

        super().save(
            *args,
            **kwargs,
        )

    def __str__(self):
        return f"{self.code} - {self.name}"


# ============================================================
# PRODUCTIVITY PROFILES
# ============================================================


class ProductivityProfile(models.Model):
    """
    Reusable productivity assumptions.

    Examples:
        Splicing Standard:
            8 boxes / technician / day

        Fiber Placement Standard:
            4000 ft / crew / day

    These values are editable database records, not constants.
    """

    name = models.CharField(
        max_length=150,
    )

    activity_type = models.ForeignKey(
        PlanningActivityType,
        on_delete=models.PROTECT,
        related_name="productivity_profiles",
    )

    resource_type = models.CharField(
        max_length=20,
        choices=PlanningResourceType.choices,
    )

    production_rate = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Production per resource per working day.",
    )

    unit = models.CharField(
        max_length=20,
        choices=PlanningUnit.choices,
    )

    default_resource_count = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("1.00"),
    )

    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["activity_type__code", "name"]
        verbose_name = "Productivity Profile"
        verbose_name_plural = "Productivity Profiles"

    def clean(self):
        super().clean()

        if self.production_rate is not None and self.production_rate <= 0:
            raise ValidationError(
                {"production_rate": "Production rate must be greater than zero."}
            )

        if self.default_resource_count is not None and self.default_resource_count <= 0:
            raise ValidationError(
                {
                    "default_resource_count": (
                        "Default resource count must be greater than zero."
                    )
                }
            )

    def __str__(self):
        return (
            f"{self.name} - "
            f"{self.production_rate} {self.get_unit_display()} / "
            f"{self.get_resource_type_display()} / day"
        )


# ============================================================
# CLIENT BASELINE
# ============================================================


class PlanningBaselineActivity(models.Model):
    """
    Client-provided baseline activity.

    Baseline belongs primarily to the Assignment.

    A DFN may optionally be attached when the activity belongs to a
    specific DFN. This allows Planning to support assignments that do
    not use DFNs at all.

    Client dates are preserved independently from internal replanning.
    """

    assignment = models.ForeignKey(
        PlanningAssignment,
        on_delete=models.CASCADE,
        related_name="baseline_activities",
        null=True,
        blank=True,
        help_text=(
            "Assignment that owns this baseline activity. Nullable during "
            "the initial migration from the DFN-only structure."
        ),
    )

    dfn = models.ForeignKey(
        PlanningDFN,
        on_delete=models.CASCADE,
        related_name="baseline_activities",
        null=True,
        blank=True,
    )

    activity_type = models.ForeignKey(
        PlanningActivityType,
        on_delete=models.PROTECT,
        related_name="baseline_activities",
    )

    sequence = models.PositiveIntegerField(default=1)

    client_start_date = models.DateField(
        null=True,
        blank=True,
    )

    client_end_date = models.DateField(
        null=True,
        blank=True,
    )

    quantity = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
    )

    unit = models.CharField(
        max_length=20,
        choices=PlanningUnit.choices,
        default=PlanningUnit.OTHER,
    )

    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )

    client_total = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "assignment",
            "sequence",
            "activity_type__code",
        ]
        indexes = [
            models.Index(
                fields=["assignment", "client_end_date"],
                name="planning_base_asg_end_idx",
            ),
        ]
        verbose_name = "Client Baseline Activity"
        verbose_name_plural = "Client Baseline Activities"

    def clean(self):
        super().clean()

        if (
            self.client_start_date
            and self.client_end_date
            and self.client_end_date < self.client_start_date
        ):
            raise ValidationError(
                {
                    "client_end_date": (
                        "Client end date cannot be earlier than client start date."
                    )
                }
            )

        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Quantity cannot be negative."})

        if (
            self.assignment_id
            and self.dfn_id
            and self.dfn.assignment_id
            and self.dfn.assignment_id != self.assignment_id
        ):
            raise ValidationError(
                {
                    "dfn": (
                        "DFN must belong to the same Assignment as "
                        "the baseline activity."
                    )
                }
            )

    def __str__(self):
        owner = self.dfn.code if self.dfn_id else str(self.assignment)

        return f"{owner} - " f"{self.activity_type.code} - Client Baseline"


# ============================================================
# MASTER PLAN
# ============================================================


class MasterPlan(models.Model):
    """
    Global high-level planning container.

    The Master Plan is not tied to one client, city or market.

    Its portfolio can later be viewed dynamically as:
    - General
    - By Client
    - By City
    - By Market
    - Client + City
    - Other filtered scopes

    This prevents creation of separate planning systems for each scope.
    """

    name = models.CharField(
        max_length=180,
    )

    description = models.TextField(blank=True)

    default_calendar = models.ForeignKey(
        PlanningCalendar,
        on_delete=models.PROTECT,
        related_name="master_plans",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="master_plans_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Master Plan"
        verbose_name_plural = "Master Plans"

    def __str__(self):
        return self.name


# ============================================================
# MASTER PLAN VERSION
# ============================================================


class MasterPlanVersion(models.Model):
    """
    Versioned High-Level Plan.

    Draft versions may be recalculated and simulated.

    Once frozen, the version remains historical and replanning should
    create a new version instead of silently overwriting assumptions.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FROZEN = "frozen", "Frozen"
        SUPERSEDED = "superseded", "Superseded"

    master_plan = models.ForeignKey(
        MasterPlan,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    version_number = models.PositiveIntegerField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    title = models.CharField(
        max_length=180,
        blank=True,
    )

    calendar = models.ForeignKey(
        PlanningCalendar,
        on_delete=models.PROTECT,
        related_name="master_plan_versions",
        null=True,
        blank=True,
    )

    assumptions_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Historical snapshot of global assumptions used when "
            "this version was calculated or frozen."
        ),
    )

    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="master_plan_versions_created",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="master_plan_versions_frozen",
    )

    frozen_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = [
            "master_plan",
            "-version_number",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["master_plan", "version_number"],
                name="uniq_master_plan_version_number",
            )
        ]
        verbose_name = "Master Plan Version"
        verbose_name_plural = "Master Plan Versions"

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    def mark_frozen(self, user=None):
        self.status = self.Status.FROZEN
        self.frozen_at = timezone.now()

        if user is not None:
            self.frozen_by = user

    def __str__(self):
        return f"{self.master_plan.name} " f"v{self.version_number}"


# ============================================================
# ASSIGNMENT INSIDE A MASTER PLAN VERSION
# ============================================================


class MasterPlanAssignment(models.Model):
    """
    Places one Assignment inside one Master Plan version.

    This is the true portfolio-level planning entry.

    Priority determines high-level portfolio ordering and can later
    participate in resource/capacity scheduling.

    Planned dates represent Hyperlink's internal High-Level Plan,
    never the Client Baseline.

    An Assignment may use its own working calendar. When no specific
    calendar is assigned here, the scheduling engine may fall back to
    the Master Plan Version calendar and then to the Master Plan
    default calendar.
    """

    version = models.ForeignKey(
        MasterPlanVersion,
        on_delete=models.CASCADE,
        related_name="assignment_entries",
    )

    assignment = models.ForeignKey(
        PlanningAssignment,
        on_delete=models.PROTECT,
        related_name="master_plan_entries",
    )

    calendar = models.ForeignKey(
        PlanningCalendar,
        on_delete=models.PROTECT,
        related_name="master_plan_assignments",
        null=True,
        blank=True,
        help_text=(
            "Assignment-specific working calendar used by the High-Level Plan. "
            "When empty, the Master Plan version/default calendar is used."
        ),
    )

    priority = models.PositiveIntegerField(
        default=1,
        db_index=True,
    )

    planned_start_date = models.DateField(
        null=True,
        blank=True,
    )

    planned_end_date = models.DateField(
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "version",
            "priority",
            "assignment__name",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "assignment"],
                name="uniq_assignment_per_master_plan_version",
            )
        ]
        indexes = [
            models.Index(
                fields=["version", "priority"],
                name="mp_asg_ver_priority_idx",
            ),
        ]
        verbose_name = "Master Plan Assignment"
        verbose_name_plural = "Master Plan Assignments"

    def clean(self):
        super().clean()

        if (
            self.planned_start_date
            and self.planned_end_date
            and self.planned_end_date < self.planned_start_date
        ):
            raise ValidationError(
                {
                    "planned_end_date": (
                        "Planned end date cannot be earlier than planned start date."
                    )
                }
            )

    @property
    def effective_calendar(self):
        """
        Returns the working calendar that should apply to this Assignment.

        Priority:
        1. Assignment-specific calendar.
        2. Master Plan Version calendar.
        3. Master Plan default calendar.
        """

        if self.calendar_id:
            return self.calendar

        if self.version.calendar_id:
            return self.version.calendar

        if self.version.master_plan.default_calendar_id:
            return self.version.master_plan.default_calendar

        return None

    def __str__(self):
        return f"{self.version} - {self.assignment}"


# ============================================================
# OPTIONAL DFN INSIDE MASTER PLAN ASSIGNMENT
# ============================================================


class MasterPlanDFN(models.Model):
    """
    Optional DFN-level planning group inside a Master Plan Assignment.

    This preserves detailed DFN scheduling for clients that use DFNs
    without making DFN mandatory for the Planning architecture.
    """

    master_plan_assignment = models.ForeignKey(
        MasterPlanAssignment,
        on_delete=models.CASCADE,
        related_name="dfn_entries",
        null=True,
        blank=True,
        help_text=(
            "Master Plan Assignment that owns this DFN entry. "
            "Nullable during migration from the original DFN-only model."
        ),
    )

    dfn = models.ForeignKey(
        PlanningDFN,
        on_delete=models.PROTECT,
        related_name="master_plan_dfn_entries",
    )

    sequence = models.PositiveIntegerField(
        default=1,
        db_index=True,
    )

    planned_start_date = models.DateField(
        null=True,
        blank=True,
    )

    planned_end_date = models.DateField(
        null=True,
        blank=True,
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "master_plan_assignment",
            "sequence",
            "dfn__code",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["master_plan_assignment", "dfn"],
                name="uniq_dfn_per_master_plan_assignment",
            )
        ]
        verbose_name = "Master Plan DFN"
        verbose_name_plural = "Master Plan DFNs"

    def clean(self):
        super().clean()

        if (
            self.planned_start_date
            and self.planned_end_date
            and self.planned_end_date < self.planned_start_date
        ):
            raise ValidationError(
                {
                    "planned_end_date": (
                        "Planned end date cannot be earlier than " "planned start date."
                    )
                }
            )

        if (
            self.master_plan_assignment_id
            and self.dfn_id
            and self.dfn.assignment_id
            and self.dfn.assignment_id != self.master_plan_assignment.assignment_id
        ):
            raise ValidationError(
                {
                    "dfn": (
                        "DFN must belong to the same Assignment as "
                        "the Master Plan Assignment."
                    )
                }
            )

    def __str__(self):
        return f"{self.master_plan_assignment} - " f"{self.dfn.code}"


# ============================================================
# HIGH-LEVEL PLAN ACTIVITIES
# ============================================================


class MasterPlanActivity(models.Model):
    """
    High-Level Plan activity.

    Activity belongs primarily to a Master Plan Assignment.

    master_plan_dfn is optional because some assignments may not use
    DFNs at all.

    Exact resource/rate assumptions are stored directly on the activity
    so frozen plan versions remain historically reproducible even if a
    reusable ProductivityProfile changes later.
    """

    master_plan_assignment = models.ForeignKey(
        MasterPlanAssignment,
        on_delete=models.CASCADE,
        related_name="activities",
        null=True,
        blank=True,
        help_text=(
            "Master Plan Assignment that owns this activity. "
            "Nullable during migration from the original DFN-only model."
        ),
    )

    master_plan_dfn = models.ForeignKey(
        MasterPlanDFN,
        on_delete=models.CASCADE,
        related_name="activities",
        null=True,
        blank=True,
    )

    baseline_activity = models.ForeignKey(
        PlanningBaselineActivity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="master_plan_activities",
    )

    activity_type = models.ForeignKey(
        PlanningActivityType,
        on_delete=models.PROTECT,
        related_name="master_plan_activities",
    )

    productivity_profile = models.ForeignKey(
        ProductivityProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="master_plan_activities",
    )

    sequence = models.PositiveIntegerField(default=1)

    quantity = models.DecimalField(
        max_digits=16,
        decimal_places=4,
    )

    unit = models.CharField(
        max_length=20,
        choices=PlanningUnit.choices,
        default=PlanningUnit.OTHER,
    )

    resource_type = models.CharField(
        max_length=20,
        choices=PlanningResourceType.choices,
    )

    resource_count = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("1.00"),
    )

    production_rate = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        help_text="Production per resource per working day.",
    )

    planned_start_date = models.DateField(
        null=True,
        blank=True,
    )

    planned_end_date = models.DateField(
        null=True,
        blank=True,
    )

    calculated_workdays = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        null=True,
        blank=True,
        help_text=(
            "Calculated working-day duration stored as part of this "
            "Master Plan version."
        ),
    )

    assumptions_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text=("Optional extra assumptions specific to this activity."),
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "master_plan_assignment",
            "sequence",
            "activity_type__code",
        ]
        indexes = [
            models.Index(
                fields=["master_plan_assignment", "sequence"],
                name="master_act_asg_sequence_idx",
            ),
        ]
        verbose_name = "Master Plan Activity"
        verbose_name_plural = "Master Plan Activities"

    def clean(self):
        super().clean()

        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Quantity cannot be negative."})

        if self.resource_count is not None and self.resource_count <= 0:
            raise ValidationError(
                {"resource_count": ("Resource count must be greater than zero.")}
            )

        if self.production_rate is not None and self.production_rate <= 0:
            raise ValidationError(
                {"production_rate": ("Production rate must be greater than zero.")}
            )

        if (
            self.planned_start_date
            and self.planned_end_date
            and self.planned_end_date < self.planned_start_date
        ):
            raise ValidationError(
                {
                    "planned_end_date": (
                        "Planned end date cannot be earlier than " "planned start date."
                    )
                }
            )

        if (
            self.master_plan_assignment_id
            and self.master_plan_dfn_id
            and self.master_plan_dfn.master_plan_assignment_id
            != self.master_plan_assignment_id
        ):
            raise ValidationError(
                {
                    "master_plan_dfn": (
                        "DFN entry must belong to the same " "Master Plan Assignment."
                    )
                }
            )

        if (
            self.master_plan_assignment_id
            and self.baseline_activity_id
            and self.baseline_activity.assignment_id
            and self.baseline_activity.assignment_id
            != self.master_plan_assignment.assignment_id
        ):
            raise ValidationError(
                {
                    "baseline_activity": (
                        "Baseline activity must belong to the same " "Assignment."
                    )
                }
            )

        if (
            self.master_plan_dfn_id
            and self.baseline_activity_id
            and self.baseline_activity.dfn_id
            and self.baseline_activity.dfn_id != self.master_plan_dfn.dfn_id
        ):
            raise ValidationError(
                {
                    "baseline_activity": (
                        "Baseline activity DFN must match the " "Master Plan DFN."
                    )
                }
            )

    def theoretical_workdays(self):
        """
        Pure deterministic capacity calculation.

        quantity / (production_rate * resource_count)

        This method deliberately does not consider:
        - weekends,
        - holidays,
        - dependencies,
        - shared-resource contention,
        - portfolio sequencing.

        Those belong to the scheduling/capacity services.
        """

        if (
            self.quantity is None
            or self.production_rate is None
            or self.resource_count is None
            or self.production_rate <= 0
            or self.resource_count <= 0
        ):
            return None

        daily_capacity = self.production_rate * self.resource_count

        if daily_capacity <= 0:
            return None

        return self.quantity / daily_capacity

    @property
    def daily_capacity(self):
        if self.production_rate is None or self.resource_count is None:
            return None

        return self.production_rate * self.resource_count

    def __str__(self):
        owner = (
            self.master_plan_dfn.dfn.code
            if self.master_plan_dfn_id
            else str(self.master_plan_assignment)
        )

        return f"{owner} - " f"{self.activity_type.code}"


# ============================================================
# ACTIVITY DEPENDENCIES
# ============================================================


class MasterPlanActivityDependency(models.Model):
    """
    Allows activities to depend on other activities.

    Example:
        Splicing cannot start until Fiber Placement has completed
        the required predecessor work.

    Dependencies must remain inside the same Master Plan version.
    """

    predecessor = models.ForeignKey(
        MasterPlanActivity,
        on_delete=models.CASCADE,
        related_name="successor_dependencies",
    )

    successor = models.ForeignKey(
        MasterPlanActivity,
        on_delete=models.CASCADE,
        related_name="predecessor_dependencies",
    )

    lag_workdays = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    notes = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["predecessor", "successor"],
                name="uniq_master_plan_activity_dependency",
            )
        ]
        verbose_name = "Master Plan Activity Dependency"
        verbose_name_plural = "Master Plan Activity Dependencies"

    def clean(self):
        super().clean()

        if (
            self.predecessor_id
            and self.successor_id
            and self.predecessor_id == self.successor_id
        ):
            raise ValidationError("An activity cannot depend on itself.")

        predecessor_version_id = None
        successor_version_id = None

        if self.predecessor_id and self.predecessor.master_plan_assignment_id:
            predecessor_version_id = self.predecessor.master_plan_assignment.version_id

        if self.successor_id and self.successor.master_plan_assignment_id:
            successor_version_id = self.successor.master_plan_assignment.version_id

        if (
            predecessor_version_id
            and successor_version_id
            and predecessor_version_id != successor_version_id
        ):
            raise ValidationError(
                "Both activities must belong to the same Master Plan version."
            )

    def __str__(self):
        return f"{self.predecessor} -> " f"{self.successor}"
