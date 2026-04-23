import os
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.module_loading import import_string
from django.utils.text import slugify

WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()


class OmbordingEntryMode(models.TextChoices):
    INTERNAL = "internal", "Internal"
    PUBLIC_LINK = "public_link", "Public Link"


class OmbordingStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_USER = "pending_user", "Pending User"
    IN_CORRECTION = "in_correction", "In Correction"
    IN_REVIEW = "in_review", "Pending Review"
    REJECTED = "rejected", "Rejected"
    APPROVED = "approved", "Approved"
    EXPIRED = "expired", "Expired"


class OmbordingStep(models.TextChoices):
    INITIAL = "initial", "Initial Setup"
    PERSONAL = "personal", "Personal & Emergency"
    IDENTITY = "identity", "Identity & Work Authorization"
    SIGNATURE = "signature", "Signature & Documents"
    BANKING = "banking", "Banking"
    REVIEW = "review", "Review"


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DocumentKey(models.TextChoices):
    CONTRACTOR_AGREEMENT_BASE = (
        "contractor_agreement_base",
        "Independent Contractor Agreement Base",
    )
    EXHIBIT_BASE = "exhibit_base", "Exhibit Base"
    W9_BASE = "w9_base", "W-9 Base"

    PASSPORT_FRONT = "passport_front", "Passport Front"
    PASSPORT_BACK = "passport_back", "Passport Back"
    ADDRESS_PROOF = "address_proof", "Address Proof"
    SSN_FRONT = "ssn_front", "Social Security Front"
    SSN_BACK = "ssn_back", "Social Security Back"
    WORK_PERMIT_FRONT = "work_permit_front", "Work Permit Front"
    WORK_PERMIT_BACK = "work_permit_back", "Work Permit Back"
    DRIVER_LICENSE_FRONT = "driver_license_front", "Driver License Front"
    DRIVER_LICENSE_BACK = "driver_license_back", "Driver License Back"

    CONTRACTOR_AGREEMENT_FILLED = (
        "contractor_agreement_filled",
        "Independent Contractor Agreement Filled",
    )
    EXHIBIT_FILLED = "exhibit_filled", "Exhibit Filled"
    W9_FILLED = "w9_filled", "W-9 Filled"


STEP_ORDER = {
    OmbordingStep.INITIAL: 1,
    OmbordingStep.PERSONAL: 2,
    OmbordingStep.IDENTITY: 3,
    OmbordingStep.SIGNATURE: 4,
    OmbordingStep.BANKING: 5,
    OmbordingStep.REVIEW: 6,
}


def _safe_ext(filename: str, default: str = ".pdf") -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext or default


def ombording_document_upload_path(instance, filename):
    ombording = instance.ombording
    worker_slug = (
        slugify(ombording.full_name or f"ombording-{ombording.id}")
        or f"ombording-{ombording.id}"
    )
    doc_slug = slugify(instance.document_key or "document") or "document"
    ext = _safe_ext(filename, ".pdf")
    return (
        f"ombording/{ombording.id}/{worker_slug}/documents/"
        f"{doc_slug}_{uuid4().hex}{ext}"
    )


def ombording_signature_upload_path(instance, filename):
    ombording = instance.ombording
    worker_slug = (
        slugify(ombording.full_name or f"ombording-{ombording.id}")
        or f"ombording-{ombording.id}"
    )
    ext = _safe_ext(filename, ".png")
    return (
        f"ombording/{ombording.id}/{worker_slug}/signature/"
        f"signature_{uuid4().hex}{ext}"
    )


def ombording_temp_upload_path(instance, filename):
    ext = _safe_ext(filename, ".bin")
    field_slug = slugify(instance.field_name or "file") or "file"
    return f"ombording/temp/{instance.session_key}/" f"{field_slug}_{uuid4().hex}{ext}"


class Position(models.Model):
    name = models.CharField(max_length=150, unique=True, verbose_name="Name")
    is_active = models.BooleanField(default=True, verbose_name="Active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Position"
        verbose_name_plural = "Positions"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Ombording(models.Model):
    first_name = models.CharField(max_length=120, blank=True, verbose_name="First Name")
    last_name = models.CharField(max_length=120, blank=True, verbose_name="Last Name")
    email = models.EmailField(blank=True, verbose_name="Email")
    position = models.ForeignKey(
        Position,
        on_delete=models.PROTECT,
        related_name="ombordings",
        verbose_name="Position",
    )

    link_token = models.CharField(
        max_length=80, unique=True, blank=True, verbose_name="Link Token"
    )
    public_access_code = models.CharField(
        max_length=12,
        blank=True,
        verbose_name="Public Access Code",
    )
    public_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Public Verified At",
    )
    link_expires_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Link Expires At"
    )
    send_email_on_create = models.BooleanField(
        default=False, verbose_name="Send Email On Create"
    )

    entry_mode = models.CharField(
        max_length=20,
        choices=OmbordingEntryMode.choices,
        default=OmbordingEntryMode.INTERNAL,
        verbose_name="Entry Mode",
    )
    documents_generated_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Documents Generated At"
    )
    worker_signed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Worker Signed At"
    )

    status = models.CharField(
        max_length=30,
        choices=OmbordingStatus.choices,
        default=OmbordingStatus.DRAFT,
        verbose_name="Status",
    )
    current_step = models.CharField(
        max_length=30,
        choices=OmbordingStep.choices,
        default=OmbordingStep.INITIAL,
        verbose_name="Current Step",
    )

    internal_notes = models.TextField(blank=True, verbose_name="Internal Notes")
    rejection_note = models.TextField(blank=True, verbose_name="Rejection Note")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombordings_created",
        verbose_name="Created By",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombordings_reviewed",
        verbose_name="Reviewed By",
    )
    approved_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Approved At"
    )
    rejected_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Rejected At"
    )
    submitted_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Submitted At"
    )

    date_of_birth = models.DateField(
        null=True, blank=True, verbose_name="Date of Birth"
    )
    age = models.PositiveIntegerField(null=True, blank=True, verbose_name="Age")
    nationality = models.CharField(
        max_length=120, blank=True, verbose_name="Nationality"
    )

    street_address = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Street Address",
    )
    apt_suite = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Apt / Suite",
    )
    city = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="City",
    )
    state = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="State",
    )
    zip_code = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="ZIP Code",
    )

    phone_number = models.CharField(
        max_length=50, blank=True, verbose_name="Phone Number"
    )

    emergency_contact_name = models.CharField(
        max_length=150, blank=True, verbose_name="Emergency Contact Name"
    )
    emergency_contact_phone = models.CharField(
        max_length=50, blank=True, verbose_name="Emergency Contact Phone"
    )
    emergency_contact_relationship = models.CharField(
        max_length=100, blank=True, verbose_name="Emergency Contact Relationship"
    )

    has_ssn = models.BooleanField(
        null=True, blank=True, verbose_name="Has Social Security"
    )
    ssn_number = models.CharField(
        max_length=50, blank=True, verbose_name="Social Security Number"
    )

    passport_number = models.CharField(
        max_length=50, blank=True, verbose_name="Passport Number"
    )
    has_work_permit = models.BooleanField(
        null=True, blank=True, verbose_name="Has Work Permit"
    )
    has_driver_license = models.BooleanField(
        null=True, blank=True, verbose_name="Has Driver License"
    )

    bank_name = models.CharField(max_length=150, blank=True, verbose_name="Bank Name")
    account_type = models.CharField(
        max_length=50, blank=True, verbose_name="Account Type"
    )
    routing_number = models.CharField(
        max_length=50, blank=True, verbose_name="Routing Number"
    )
    account_number = models.CharField(
        max_length=50, blank=True, verbose_name="Account Number"
    )

    business_name = models.CharField(
        max_length=150, blank=True, verbose_name="Business Name"
    )
    w9_tax_classification = models.CharField(
        max_length=50, blank=True, verbose_name="W-9 Tax Classification"
    )

    w9_llc_classification = models.CharField(
        max_length=1,
        blank=True,
        verbose_name="W-9 LLC Classification",
    )
    w9_other_text = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="W-9 Other Text",
    )
    w9_part3b_required = models.BooleanField(
        null=True,
        blank=True,
        verbose_name="W-9 Part 3b Required",
    )
    w9_exempt_payee_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="W-9 Exempt Payee Code",
    )
    w9_fatca_exemption_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="W-9 FATCA Exemption Code",
    )
    w9_account_numbers = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="W-9 Account Numbers",
    )
    ein_number = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Employer Identification Number",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ombording"
        verbose_name_plural = "Ombordings"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name or 'Unnamed'} - {self.position.name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self):
        a = (self.first_name[:1] or "").upper()
        b = (self.last_name[:1] or "").upper()
        return f"{a}{b}".strip()
    
    @property
    def review_progress(self):
        total = self.documents.count()
        approved = self.documents.filter(review_status=ReviewStatus.APPROVED).count()
        return approved, total
    
    @property
    def full_address(self):
        line1 = " ".join(
            x for x in [self.street_address.strip(), self.apt_suite.strip()] if x
        ).strip()
        line2 = ", ".join(
            x
            for x in [
                self.city.strip(),
                self.state.strip(),
            ]
            if x
        ).strip()

        if self.zip_code:
            line2 = (
                f"{line2} {self.zip_code}".strip() if line2 else self.zip_code.strip()
            )

        return "\n".join(x for x in [line1, line2] if x)

    def ensure_link_token(self):
        if not self.link_token:
            self.link_token = get_random_string(48)

    def ensure_public_access_code(self):
        if not self.public_access_code:
            self.public_access_code = get_random_string(
                6,
                allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789",
            )

    def refresh_age(self):
        if not self.date_of_birth:
            self.age = None
            return

        today = timezone.localdate()
        self.age = (
            today.year
            - self.date_of_birth.year
            - (
                (today.month, today.day)
                < (self.date_of_birth.month, self.date_of_birth.day)
            )
        )

    def is_initial_complete(self):
        return bool(
            self.first_name and self.last_name and self.email and self.position_id
        )

    def is_personal_complete(self):
        return bool(
            self.date_of_birth
            and self.nationality
            and self.street_address
            and self.city
            and self.state
            and self.zip_code
            and self.phone_number
            and self.emergency_contact_name
            and self.emergency_contact_phone
            and self.emergency_contact_relationship
        )

    def needs_passport(self):
        nationality = (self.nationality or "").strip().lower()
        return nationality != "united states" or self.has_ssn is False

    def is_identity_complete(self):
        if (
            self.has_ssn is None
            or self.has_work_permit is None
            or self.has_driver_license is None
        ):
            return False
        if self.has_ssn is True and not self.ssn_number:
            return False
        if self.needs_passport() and not self.passport_number:
            return False
        return True

    def is_banking_complete(self):
        return bool(
            self.bank_name
            and self.account_type
            and self.routing_number
            and self.account_number
        )

    def is_admin_complete(self):
        return (
            self.is_initial_complete()
            and self.is_personal_complete()
            and self.is_identity_complete()
            and self.is_banking_complete()
        )

    def should_generate_signed_documents(self):
        if self.entry_mode == OmbordingEntryMode.INTERNAL:
            return True
        return bool(
            hasattr(self, "signature")
            and self.signature
            and self.signature.signature_file
        )

    def signature_full_name(self):
        if (
            hasattr(self, "signature")
            and self.signature
            and self.signature.signature_name
        ):
            return self.signature.signature_name.strip()
        return self.full_name

    def signature_initials_value(self):
        if hasattr(self, "signature") and self.signature and self.signature.initials:
            return self.signature.initials.strip()
        return self.initials

    def signing_date_value(self):
        dt = self.worker_signed_at or self.updated_at or timezone.now()
        local_dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
        return local_dt.strftime("%m/%d/%Y")

    def _initial_fields_changed(self):
        if not self.pk:
            return False

        previous = (
            type(self)
            .objects.filter(pk=self.pk)
            .values(
                "first_name",
                "last_name",
                "email",
                "position_id",
            )
            .first()
        )
        if not previous:
            return False

        return any(
            [
                (previous.get("first_name") or "") != (self.first_name or ""),
                (previous.get("last_name") or "") != (self.last_name or ""),
                (previous.get("email") or "") != (self.email or ""),
                previous.get("position_id") != self.position_id,
            ]
        )

    def rotate_public_credentials(self):
        self.link_token = get_random_string(48)
        self.public_access_code = get_random_string(
            6,
            allowed_chars="ABCDEFGHJKLMNPQRSTUVWXYZ23456789",
        )
        self.public_verified_at = None
        self.worker_signed_at = None
        self.documents_generated_at = None
        self.link_expires_at = timezone.now() + timezone.timedelta(days=7)

    def save(self, *args, **kwargs):
        should_rotate = self._initial_fields_changed()

        self.ensure_link_token()
        self.ensure_public_access_code()
        self.refresh_age()

        if should_rotate:
            self.rotate_public_credentials()

        super().save(*args, **kwargs)


class OmbordingFieldReview(models.Model):
    ombording = models.ForeignKey(
        Ombording,
        on_delete=models.CASCADE,
        related_name="field_reviews",
        verbose_name="Ombording",
    )
    field_key = models.CharField(max_length=100, verbose_name="Field Key")
    field_label = models.CharField(max_length=200, verbose_name="Field Label")
    step = models.CharField(
        max_length=30, choices=OmbordingStep.choices, verbose_name="Step"
    )
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        verbose_name="Review Status",
    )
    review_comment = models.TextField(blank=True, verbose_name="Review Comment")
    is_locked = models.BooleanField(default=False, verbose_name="Locked")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombording_field_reviews_done",
        verbose_name="Reviewed By",
    )
    reviewed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Reviewed At"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ombording Field Review"
        verbose_name_plural = "Ombording Field Reviews"
        ordering = ["field_label"]
        unique_together = [("ombording", "field_key")]

    def __str__(self):
        return f"{self.ombording_id} - {self.field_key}"


class OmbordingDocument(models.Model):
    ombording = models.ForeignKey(
        Ombording,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Ombording",
    )
    document_key = models.CharField(
        max_length=60, choices=DocumentKey.choices, verbose_name="Document Key"
    )
    label = models.CharField(max_length=200, verbose_name="Label")
    file = models.FileField(
        upload_to=ombording_document_upload_path,
        storage=wasabi_storage,
        max_length=1024,
        verbose_name="File",
    )
    original_name = models.CharField(
        max_length=255, blank=True, verbose_name="Original Name"
    )
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        verbose_name="Review Status",
    )
    review_comment = models.TextField(blank=True, verbose_name="Review Comment")
    is_locked = models.BooleanField(default=False, verbose_name="Locked")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombording_documents_uploaded",
        verbose_name="Uploaded By",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombording_documents_reviewed",
        verbose_name="Reviewed By",
    )
    reviewed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Reviewed At"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ombording Document"
        verbose_name_plural = "Ombording Documents"
        ordering = ["document_key", "-created_at"]

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = os.path.basename(self.file.name)
        super().save(*args, **kwargs)


class OmbordingTempUpload(models.Model):
    session_key = models.CharField(max_length=100, db_index=True)
    field_name = models.CharField(max_length=100, db_index=True)
    file = models.FileField(
        upload_to=ombording_temp_upload_path,
        storage=wasabi_storage,
        max_length=1024,
        verbose_name="File",
    )
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombording_temp_uploads",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ombording Temp Upload"
        verbose_name_plural = "Ombording Temp Uploads"
        ordering = ["-created_at"]
        unique_together = [("session_key", "field_name")]

    def __str__(self):
        return f"{self.session_key} - {self.field_name}"

    def save(self, *args, **kwargs):
        if self.file and not self.original_name:
            self.original_name = os.path.basename(self.file.name)
        super().save(*args, **kwargs)


class OmbordingSignature(models.Model):
    ombording = models.OneToOneField(
        Ombording,
        on_delete=models.CASCADE,
        related_name="signature",
        verbose_name="Ombording",
    )
    signature_name = models.CharField(
        max_length=150, blank=True, verbose_name="Signature Name"
    )
    initials = models.CharField(max_length=10, blank=True, verbose_name="Initials")
    signature_file = models.ImageField(
        upload_to=ombording_signature_upload_path,
        storage=wasabi_storage,
        null=True,
        blank=True,
        max_length=1024,
        verbose_name="Signature File",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Ombording Signature"
        verbose_name_plural = "Ombording Signatures"

    def __str__(self):
        return f"Signature - {self.ombording_id}"


class OmbordingAuditLog(models.Model):
    ombording = models.ForeignKey(
        Ombording,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        verbose_name="Ombording",
    )
    action = models.CharField(max_length=120, verbose_name="Action")
    detail = models.TextField(blank=True, verbose_name="Detail")
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ombording_audit_logs",
        verbose_name="Performed By",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ombording Audit Log"
        verbose_name_plural = "Ombording Audit Logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ombording_id} - {self.action}"


class OmbordingEmailLog(models.Model):
    ombording = models.ForeignKey(
        Ombording,
        on_delete=models.CASCADE,
        related_name="email_logs",
        verbose_name="Ombording",
    )
    email_type = models.CharField(max_length=80, verbose_name="Email Type")
    subject = models.CharField(max_length=255, verbose_name="Subject")
    recipient = models.EmailField(verbose_name="Recipient")
    success = models.BooleanField(default=False, verbose_name="Success")
    error_message = models.TextField(blank=True, verbose_name="Error Message")
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ombording Email Log"
        verbose_name_plural = "Ombording Email Logs"
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.email_type} - {self.recipient}"
