# invoicing/views_templates.py
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.clickjacking import (xframe_options_exempt,
                                                  xframe_options_sameorigin)

from usuarios.decoradores import rol_requerido

from .models import BrandingProfile, BrandingSettings
from .utils_branding import get_active_branding

# Catálogo fijo de templates (key -> nombre y archivo)
TEMPLATES_CATALOG = [
    {"key": "classic", "name": "Classic", "file": "invoicing/invoice_templates/invoice_t1.html"},
    {"key": "modern",  "name": "Modern",  "file": "invoicing/invoice_templates/invoice_t2.html"},
    {"key": "minimal", "name": "Minimal", "file": "invoicing/invoice_templates/invoice_t3.html"},
    {"key": "bold",    "name": "Bold",    "file": "invoicing/invoice_templates/invoice_t4.html"},
    {"key": "elegant", "name": "Elegant", "file": "invoicing/invoice_templates/invoice_t5.html"},
]
CATALOG_INDEX = {t["key"]: t for t in TEMPLATES_CATALOG}


@login_required
@rol_requerido("admin", "facturacion")
def view_Templates(request):
    """Galería para previsualizar y elegir template por perfil de branding."""
    bs, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    profiles = list(BrandingProfile.objects.filter(owner=request.user).order_by("name"))
    active_profile = None
    if profiles:
        # Perfil seleccionado por ?profile_id=..., si no, default, si no, primero
        pid = request.GET.get("profile_id")
        if pid:
            active_profile = next((p for p in profiles if str(p.id) == str(pid)), None)
        if not active_profile and bs.default_profile_id:
            active_profile = next((p for p in profiles if p.id == bs.default_profile_id), None)
        if not active_profile:
            active_profile = profiles[0]

    branding = get_active_branding(request.user, active_profile.id if active_profile else None)

    cards = []
    for t in TEMPLATES_CATALOG:
        cards.append({
            "key": t["key"],
            "name": t["name"],
            "preview_url": f'{reverse("invoicing:template_preview", kwargs={"key": t["key"]})}?profile_id={active_profile.id if active_profile else ""}',
            "in_use": (active_profile and (active_profile.template_key or "classic") == t["key"]),
        })

    ctx = {
        "page_title": "Templates",
        "profiles": profiles,
        "active_profile": active_profile,
        "cards": cards,
        "branding": branding,
    }
    return render(request, "invoicing/templates_gallery.html", ctx)


@login_required
@rol_requerido("admin", "facturacion")
def template_set(request):
    """Asigna el template al perfil indicado."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    key = (request.POST.get("key") or "").strip()
    profile_id = request.POST.get("profile_id")
    if key not in CATALOG_INDEX:
        return JsonResponse({"ok": False, "error": "Unknown template."}, status=400)

    if profile_id:
        p = get_object_or_404(BrandingProfile, id=profile_id, owner=request.user)
    else:
        bs, _ = BrandingSettings.objects.get_or_create(owner=request.user)
        if not bs.default_profile_id:
            return JsonResponse({"ok": False, "error": "No profile selected."}, status=400)
        p = get_object_or_404(BrandingProfile, id=bs.default_profile_id, owner=request.user)

    p.template_key = key
    p.save(update_fields=["template_key"])
    return JsonResponse({"ok": True})


@login_required
@xframe_options_exempt         # permite ser embebido en iframe aunque tengas X_FRAME_OPTIONS=DENY
# @xframe_options_sameorigin   # alternativa si prefieres permitir solo mismo origen
def template_preview(request, key: str):
    """Render del preview (para iframe) usando colores del branding."""
    tpl = CATALOG_INDEX.get(key)
    if not tpl:
        # fallback simple para no romper el iframe si key es inválido
        return render(request, "invoicing/invoice_templates/unknown.html", status=404)

    # Perfil / branding para paleta
    profile = None
    pid = request.GET.get("profile_id")
    if pid:
        profile = (BrandingProfile.objects
                   .filter(id=pid)           # << antes: filter(owner=request.user, id=pid)
                   .select_related("logo")
                   .first())
    else:
        # 1) Intentar default "global"
        bs_global = (BrandingSettings.objects
                     .filter(default_profile__isnull=False)
                     .order_by('-id')
                     .first())
        if bs_global and bs_global.default_profile_id:
            profile = (BrandingProfile.objects
                       .filter(id=bs_global.default_profile_id)
                       .select_related("logo")
                       .first())

        # 2) Fallback: default del usuario (como lo tenías)
        if not profile:
            bs, _ = BrandingSettings.objects.get_or_create(owner=request.user)
            if bs.default_profile_id:
                profile = (BrandingProfile.objects
                           .filter(owner=request.user, id=bs.default_profile_id)
                           .select_related("logo")
                           .first())

    branding = get_active_branding(request.user, profile.id if profile else None)

    # Datos de empresa (desde el perfil si existen)
    company = {
        "name":  (profile.company_name    if profile and profile.company_name    else "Your Company LLC"),
        "address": (profile.company_address if profile and profile.company_address else "123 Main St, Suite 200"),
        "city":  (profile.company_city    if profile and profile.company_city    else "Miami, FL"),
        "email": (profile.company_email   if profile and profile.company_email   else "billing@company.com"),
        "phone": (profile.company_phone   if profile and profile.company_phone   else "+1 (305) 555-1234"),
        "logo_url": branding.logo_url,
    }

    # Datos dummy para el preview (coinciden con tu estructura de items)
    invoice = {
        "number": f'{branding.invoice_prefix or "INV-"}00125',
        "date": "2025-10-23",
        "due":  "2025-11-22",
        # añadimos estas dos claves porque el template usa issue_date/due_date
        "issue_date": "2025-10-23",
        "due_date": "2025-11-22",
        "notes": "Thank you for your business. Please remit payment within 30 days.",
        "subtotal": "3,810.00",
        "tax_label": "Tax (7%)",
        "tax": "266.70",
        "total": "4,076.70",
        "status": "DUE",
        "currency_symbol": "$",
    }
    customer = {
        "name": "Planix Corp.",
        "address": "500 Market Street",
        "city": "San Francisco, CA 94103",
        "email": "ap@planix.com",
        "phone": "+1 (415) 555-0001",
    }
    items = [
        {
            "daily_number": "D-0001",
            "job_code": "NET-ARCH",
            "description": "Network design & architecture",
            "qty": 12,
            "unit": "hr",
            "rate": "150.00",
            "amount": "1,800.00",
        },
        {
            "daily_number": "D-0002",
            "job_code": "HW-PROV",
            "description": "Hardware provisioning",
            "qty": 1,
            "unit": "lot",
            "rate": "1250.00",
            "amount": "1,250.00",
        },
        {
            "daily_number": "D-0003",
            "job_code": "ONSITE",
            "description": "On-site installation",
            "qty": 8,
            "unit": "hr",
            "rate": "95.00",
            "amount": "760.00",
        },
    ]

    response = render(request, tpl["file"], {
        "branding": branding,
        "profile": profile or branding,   # <-- agregado para que el template use profile.*
        "company": company,
        "invoice": invoice,
        "customer": customer,
        "items": items,
        "template_name": tpl["name"],
    })
    # Por si tienes un middleware que pisa el header:
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response