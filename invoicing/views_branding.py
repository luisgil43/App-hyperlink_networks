# invoicing/views_branding.py
import os

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import BrandingProfile, BrandingSettings, BrandLogo

ALLOWED_MIMES = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}
MAX_LOGOS     = 5


def _serialize_logo(l, default_logo_id=None):
    return {
        "id": l.id,
        "url": l.file.url,
        "filename": l.filename,
        "is_primary": (default_logo_id == l.id),
    }


def _serialize_profile(p, default_profile_id=None):
    return {
        "id": p.id,
        "name": p.name,
        "theme": p.theme,
        "primary_color": p.primary_color,
        "secondary_color": p.secondary_color,
        "accent_color": p.accent_color,
        "invoice_prefix": p.invoice_prefix,
        "logo": {"id": p.logo_id, "url": (p.logo.url if p.logo else "")},
        "is_default": (p.id == default_profile_id),
        # ---- NUEVO: datos empresa ----
        "company_name": p.company_name,
        "company_address": p.company_address,
        "company_city": p.company_city,
        "company_email": p.company_email,
        "company_phone": p.company_phone,
    }


@login_required
def view_Branding(request):
    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)

    logos_qs = BrandLogo.objects.filter(owner=request.user).order_by("-created_at")[:MAX_LOGOS]
    profiles_qs = BrandingProfile.objects.filter(owner=request.user).order_by("-updated_at")

    ctx = {
        "page_title": "Branding & Logos",
        "logos": [_serialize_logo(l, settings_obj.default_logo_id) for l in logos_qs],
        "profiles": [_serialize_profile(p, settings_obj.default_profile_id) for p in profiles_qs],
        "max_logos": MAX_LOGOS,
        "default_profile_id": settings_obj.default_profile_id or 0,
    }
    return render(request, "invoicing/branding.html", ctx)


# ---------------------------- LOGOS ---------------------------- #

@login_required
def branding_upload(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    if BrandLogo.objects.filter(owner=request.user).count() >= MAX_LOGOS:
        return JsonResponse({"ok": False, "error": f"Max {MAX_LOGOS} logos allowed."}, status=400)

    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"ok": False, "error": "No file."}, status=400)
    ct = (getattr(f, "content_type", "") or "").lower()
    if ct not in ALLOWED_MIMES:
        return JsonResponse({"ok": False, "error": "Only PNG, JPG/JPEG, WEBP or SVG."}, status=415)

    logo = BrandLogo.objects.create(owner=request.user, file=f, label=(f.name or ""))

    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    if settings_obj.default_logo_id is None:
        settings_obj.default_logo = logo
        settings_obj.save(update_fields=["default_logo"])

    return JsonResponse({"ok": True, "logo": _serialize_logo(logo, settings_obj.default_logo_id)})


@login_required
def branding_delete(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    lid = request.POST.get("id")
    logo = get_object_or_404(BrandLogo, id=lid, owner=request.user)

    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    try:
        if logo.file:
            logo.file.delete(save=False)
    except Exception:
        pass
    was_default_logo = (settings_obj.default_logo_id == logo.id)
    logo.delete()

    if was_default_logo:
        nxt = BrandLogo.objects.filter(owner=request.user).order_by("-created_at").first()
        settings_obj.default_logo = nxt if nxt else None
        settings_obj.save(update_fields=["default_logo"])

    return JsonResponse({"ok": True})


@login_required
def branding_set_primary(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    lid = request.POST.get("id")
    logo = get_object_or_404(BrandLogo, id=lid, owner=request.user)

    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    settings_obj.default_logo = logo
    settings_obj.save(update_fields=["default_logo"])
    return JsonResponse({"ok": True})


# ------------------------ BRANDING PROFILES (CRUD) ------------------------ #

@login_required
def profile_save(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    pid = request.POST.get("id")  # si viene, es update

    # Visual / branding
    name = (request.POST.get("name") or "").strip()
    theme = (request.POST.get("theme") or "light").strip()
    pc = (request.POST.get("primary_color") or "#0ea5e9").strip()
    sc = (request.POST.get("secondary_color") or "#0f172a").strip()
    ac = (request.POST.get("accent_color") or "#22c55e").strip()
    ip = (request.POST.get("invoice_prefix") or "").strip()
    logo_id = request.POST.get("logo_id") or None

    # ---- NUEVO: datos empresa ----
    company_name    = (request.POST.get("company_name") or "").strip()
    company_address = (request.POST.get("company_address") or "").strip()
    company_city    = (request.POST.get("company_city") or "").strip()
    company_email   = (request.POST.get("company_email") or "").strip()
    company_phone   = (request.POST.get("company_phone") or "").strip()

    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    logo = None
    if logo_id:
        logo = get_object_or_404(BrandLogo, id=logo_id, owner=request.user)

    if pid:
        p = get_object_or_404(BrandingProfile, id=pid, owner=request.user)
        p.name = name
        p.theme = theme
        p.primary_color, p.secondary_color, p.accent_color = pc, sc, ac
        p.invoice_prefix = ip
        p.logo = logo
        # nuevos campos
        p.company_name = company_name
        p.company_address = company_address
        p.company_city = company_city
        p.company_email = company_email
        p.company_phone = company_phone
        try:
            p.save()
        except IntegrityError:
            return JsonResponse({"ok": False, "error": "A profile with that name already exists."}, status=409)
    else:
        try:
            p = BrandingProfile.objects.create(
                owner=request.user, name=name, theme=theme,
                primary_color=pc, secondary_color=sc, accent_color=ac,
                invoice_prefix=ip, logo=logo,
                company_name=company_name,
                company_address=company_address,
                company_city=company_city,
                company_email=company_email,
                company_phone=company_phone,
            )
        except IntegrityError:
            return JsonResponse({"ok": False, "error": "A profile with that name already exists."}, status=409)

        # Primer perfil => default
        settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
        if settings_obj.default_profile_id is None:
            settings_obj.default_profile = p
            settings_obj.save(update_fields=["default_profile"])

    return JsonResponse({"ok": True, "profile": _serialize_profile(p)})


@login_required
def profile_detail(request):
    pid = request.GET.get("id")
    p = get_object_or_404(BrandingProfile, id=pid, owner=request.user)
    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    return JsonResponse({"ok": True, "profile": _serialize_profile(p, settings_obj.default_profile_id)})


@login_required
def profile_delete(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    pid = request.POST.get("id")
    p = get_object_or_404(BrandingProfile, id=pid, owner=request.user)

    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    was_default = (settings_obj.default_profile_id == p.id)
    p.delete()

    if was_default:
        nxt = BrandingProfile.objects.filter(owner=request.user).order_by("-updated_at").first()
        settings_obj.default_profile = nxt if nxt else None
        settings_obj.save(update_fields=["default_profile"])

    return JsonResponse({"ok": True})


@login_required
def profile_set_default(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    pid = request.POST.get("id")
    p = get_object_or_404(BrandingProfile, id=pid, owner=request.user)

    settings_obj, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    settings_obj.default_profile = p
    settings_obj.save(update_fields=["default_profile"])
    return JsonResponse({"ok": True})