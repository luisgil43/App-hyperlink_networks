from django.db import models


class Customer(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    # NUEVO: nemónico/código corto del cliente
    mnemonic = models.CharField(max_length=20, blank=True, null=True, unique=True)  # NUEVO

    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=40, blank=True)

    street_1 = models.CharField("Street Address", max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)   # USPS
    zip_code = models.CharField(max_length=10, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name
    
# invoicing/models.py
import os
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.module_loading import import_string

# Same cloud storage you already use
WasabiStorageClass = import_string(settings.DEFAULT_FILE_STORAGE)
wasabi_storage = WasabiStorageClass()


def upload_to_brand_logo(instance, filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    ext = (ext or ".png").lower()
    return f"finanzas/facturacion/logos/{instance.owner_id}/{uuid4().hex}{ext}"

# Alias kept for old migrations compatibility
brand_logo_upload_to = upload_to_brand_logo


class BrandLogo(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="branding_logos",
    )
    file = models.ImageField(
        upload_to=upload_to_brand_logo,
        storage=wasabi_storage,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp", "svg"])],
        max_length=1024,
    )
    label = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def url(self):
        return self.file.url

    @property
    def filename(self):
        return self.label or os.path.basename(self.file.name)


# Multiple brandings per user
class BrandingProfile(models.Model):
    THEME_CHOICES = [("light", "Light"), ("dark", "Dark")]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="branding_profiles",
    )
    # Visual / branding
    name = models.CharField(max_length=60)
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="light")
    primary_color   = models.CharField(max_length=7, default="#0ea5e9")
    secondary_color = models.CharField(max_length=7, default="#0f172a")
    accent_color    = models.CharField(max_length=7, default="#22c55e")
    invoice_prefix  = models.CharField(max_length=10, blank=True, default="")
    logo = models.ForeignKey(
        BrandLogo, null=True, blank=True, on_delete=models.SET_NULL, related_name="used_in_profiles"
    )

    # Company info (used on invoices)
    company_name    = models.CharField(max_length=120, blank=True, default="")
    company_address = models.CharField(max_length=200, blank=True, default="")
    company_city    = models.CharField(max_length=100, blank=True, default="")
    company_email   = models.EmailField(blank=True, default="")
    company_phone   = models.CharField(max_length=40, blank=True, default="")

    # NEW: chosen invoice template key (catalog fixed below in views)
    template_key = models.CharField(max_length=30, blank=True, default="classic")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("owner", "name")]
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.owner})"


# Account settings (default logo/profile)
class BrandingSettings(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="branding_settings",
    )
    # Back-compat fields
    primary_color   = models.CharField(max_length=7, default="#0ea5e9")
    secondary_color = models.CharField(max_length=7, default="#0f172a")
    accent_color    = models.CharField(max_length=7, default="#22c55e")
    invoice_prefix  = models.CharField(max_length=10, blank=True, default="")
    default_logo    = models.ForeignKey(
        "BrandLogo", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Default profile used for invoicing
    default_profile = models.ForeignKey(
        "BrandingProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)


# --- Item Codes -------------------------------------------------------------
from decimal import Decimal

from django.core.validators import MinValueValidator


class ItemCode(models.Model):
    # Campos de catálogo
    city         = models.CharField(max_length=100, blank=True, default="")
    project      = models.CharField(max_length=120, blank=True, default="")
    office       = models.CharField(max_length=120, blank=True, default="")
    client       = models.CharField(max_length=160, blank=True, default="")
    work_type    = models.CharField(max_length=120, blank=True, default="")
    job_code     = models.CharField(max_length=60, unique=True)  # clave natural
    description  = models.CharField(max_length=300, blank=True, default="")
    uom          = models.CharField("Unit of Measure", max_length=30, blank=True, default="")
    rate         = models.DecimalField(max_digits=12, decimal_places=2,
                                       validators=[MinValueValidator(Decimal("0"))],
                                       default=Decimal("0"))

    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["job_code"]

    def __str__(self):
        return f"{self.job_code} — {self.description or 'Item'}"
    

# --- Invoices (issued) ---
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone
from django.utils.text import slugify

"""
def upload_to_invoice_pdf(instance, filename: str) -> str:
    # mismo storage ya configurado: wasabi_storage
    return f"finanzas/facturacion/invoices/{instance.owner_id}/{uuid4().hex}.pdf"""



def upload_to_invoice_pdf(instance, filename: str) -> str:
  
    def safe_segment(s: str) -> str:
        s = (s or "").strip()
        # Evitar separadores de ruta
        s = s.replace("/", "-").replace("\\", "-")
        # Opcional: si prefieres slugs sin espacios, descomenta la línea siguiente:
        # s = slugify(s, allow_unicode=True)
        return s or "Sin-Nombre"

    customer_dir = safe_segment(getattr(instance.customer, "name", "Cliente"))
    invoice_num  = safe_segment(getattr(instance, "number", uuid4().hex))

    return f"finanzas/facturacion/invoices/{customer_dir}/{invoice_num}.pdf"

class Invoice(models.Model):
    STATUS_DRAFT   = "draft"
    STATUS_ISSUED  = "issued"
    STATUS_PAID    = "paid"
    STATUS_VOID    = "void"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_PAID, "Paid"),
        (STATUS_VOID, "Void"),
    ]

    owner    = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="invoices")
    customer = models.ForeignKey("Customer", on_delete=models.PROTECT, related_name="invoices")

    number      = models.CharField(max_length=40)   # p.ej. CCU-000012
    issue_date  = models.DateField(default=timezone.now)
    total       = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    pdf         = models.FileField(upload_to=upload_to_invoice_pdf, storage=wasabi_storage, blank=True, null=True)

    # branding/template usados al emitir
    branding_profile = models.ForeignKey("BrandingProfile", null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices")
    template_key     = models.CharField(max_length=30, blank=True, default="classic")

    status      = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ISSUED)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes  = [
            models.Index(fields=["owner", "number"]),
            models.Index(fields=["owner", "issue_date"]),
            models.Index(fields=["owner", "status"]),
        ]
        unique_together = [("owner", "number")]

    def __str__(self):
        return f"{self.number} · {self.customer.name}"

    @property
    def pdf_url(self) -> str:
        try:
            return self.pdf.url if self.pdf else ""
        except Exception:
            return ""