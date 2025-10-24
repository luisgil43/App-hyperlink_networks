# invoicing/utils_branding.py
from dataclasses import dataclass
from typing import Optional

from .models import BrandingProfile, BrandingSettings


@dataclass
class ActiveBranding:
    name: str
    theme: str
    primary_color: str
    secondary_color: str
    accent_color: str
    invoice_prefix: str
    logo_url: str | None
    template_key: str | None


def get_active_branding(user, profile_id: Optional[int] = None) -> ActiveBranding:
    bs, _ = BrandingSettings.objects.get_or_create(owner=user)

    profile = None
    if profile_id:
        profile = BrandingProfile.objects.filter(owner=user, id=profile_id).select_related("logo").first()
    if not profile and bs.default_profile_id:
        profile = BrandingProfile.objects.filter(owner=user, id=bs.default_profile_id).select_related("logo").first()

    if profile:
        return ActiveBranding(
            name=profile.name,
            theme=profile.theme,
            primary_color=profile.primary_color,
            secondary_color=profile.secondary_color,
            accent_color=profile.accent_color,
            invoice_prefix=profile.invoice_prefix,
            logo_url=(profile.logo.url if profile.logo else None),
            template_key=profile.template_key or "classic",
        )

    return ActiveBranding(
        name="Default",
        theme="light",
        primary_color=bs.primary_color,
        secondary_color=bs.secondary_color,
        accent_color=bs.accent_color,
        invoice_prefix=bs.invoice_prefix,
        logo_url=(bs.default_logo.url if bs.default_logo else None),
        template_key="classic",
    )