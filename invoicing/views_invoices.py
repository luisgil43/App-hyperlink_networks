# invoicing/views_invoices.py
import json
import re
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string, select_template
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from weasyprint import HTML  # pip install weasyprint

from .models import BrandingProfile, BrandingSettings, Customer, Invoice
from .utils_branding import get_active_branding


@login_required
def invoices_list(request):
    """
    Issued invoices list with auto-filters + app-wide pagination behavior.
    """
    qs = Invoice.objects.filter(owner=request.user).select_related("customer")

    # ---- Filters ----
    q        = (request.GET.get("q") or "").strip()
    f_from   = (request.GET.get("from") or "").strip()   # YYYY-MM-DD
    f_to     = (request.GET.get("to") or "").strip()     # YYYY-MM-DD
    f_status = (request.GET.get("status") or "").strip() # draft/issued/paid/void
    per      = (request.GET.get("per") or "10").lower()
    page     = int(request.GET.get("page") or 1)

    if q:
        qs = qs.filter(
            Q(number__icontains=q) |
            Q(customer__name__icontains=q)
        )

    def _parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    d_from = _parse_date(f_from)
    d_to   = _parse_date(f_to)
    if d_from:
        qs = qs.filter(issue_date__gte=d_from)
    if d_to:
        qs = qs.filter(issue_date__lte=d_to)

    if f_status in {Invoice.STATUS_DRAFT, Invoice.STATUS_ISSUED, Invoice.STATUS_PAID, Invoice.STATUS_VOID}:
        qs = qs.filter(status=f_status)

    # ---- Pagination ----
    pagina = None
    items  = None
    if per == "all":
        items = list(qs[:2000])  # hard cap para no volar el navegador
        per_page = "all"
    else:
        try:
            per_int = int(per)
        except Exception:
            per_int = 10
        paginator = Paginator(qs, per_int)
        pagina    = paginator.get_page(page)
        items     = pagina.object_list
        per_page  = str(per_int)

    ctx = {
        "page_title": "Invoices",
        "items": items,
        "pagina": pagina,           # si hay paginator
        "per_page": per_page,
        "q": q, "f_from": f_from, "f_to": f_to, "f_status": f_status,
    }
    return render(request, "invoicing/invoices_list.html", ctx)


@login_required
def invoice_delete(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    iid = request.POST.get("id")
    inv = get_object_or_404(Invoice, id=iid, owner=request.user)
    inv.delete()
    return JsonResponse({"ok": True})


@login_required
def invoice_new(request):
    """
    Opens a 'compose' page that loads the selected template for the user's active branding.
    (Solo carga el template elegido; el flujo de guardado/emitir se hará luego.)
    """
    bs, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    profile = None
    # Puedes permitir cambiar el perfil por ?profile_id=
    pid = request.GET.get("profile_id")
    if pid:
        profile = BrandingProfile.objects.filter(owner=request.user, id=pid).first()
    if not profile and bs.default_profile_id:
        profile = BrandingProfile.objects.filter(owner=request.user, id=bs.default_profile_id).first()

    # Fallback: si no hay perfil, igual abre con branding por defecto
    branding = get_active_branding(request.user, profile.id if profile else None)
    chosen_key = (profile.template_key if profile and profile.template_key else "classic")

    # Iframe -> reutilizamos el preview del template para “cargar el template seleccionado”
    preview_url = f'{reverse("invoicing:template_preview", kwargs={"key": chosen_key})}'
    if profile:
        preview_url += f"?profile_id={profile.id}"

    return render(request, "invoicing/invoice_new.html", {
        "page_title": "New Invoice",
        "branding": branding,
        "preview_url": preview_url,
        "profile": profile,
    })






# ----------------- Helpers -----------------

def _parse_date_iso(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _next_number_with_prefix(owner, prefix: str, width: int = 6) -> str:
    """Siguiente número disponible para un prefijo (p.ej. ITG-000123)."""
    prefix = prefix.strip()
    base = f"{prefix}-"
    maxn = 0
    for num in (
        Invoice.objects.filter(owner=owner, number__startswith=base)
        .values_list("number", flat=True)
    ):
        m = re.match(rf"^{re.escape(prefix)}-(\d+)$", num)
        if m:
            maxn = max(maxn, int(m.group(1)))
    return f"{prefix}-{str(maxn + 1).zfill(width)}"


def _ensure_unique_number(owner, number: str, profile=None, customer=None) -> str:
    """
    Garantiza unicidad (owner, number). Si viene vacío/'AUTO' o ya existe:
    - usa mnemonic del cliente o invoice_prefix del perfil como prefijo;
    - si es <pref>-<NNNN>, incrementa correlativamente.
    """
    candidate = (number or "").strip()

    if not candidate or candidate.upper() == "AUTO":
        raw_prefix = (
            getattr(customer, "mnemonic", None)
            or getattr(profile, "invoice_prefix", "")
            or "INV"
        )
        prefix = re.sub(r"[^A-Za-z0-9]+", "", raw_prefix).upper() or "INV"
        return _next_number_with_prefix(owner, prefix, width=6)

    if Invoice.objects.filter(owner=owner, number=candidate).exists():
        m = re.match(r"^(?P<pre>.+?)-(?P<num>\d+)$", candidate)
        if m:
            pre = m.group("pre")
            width = len(m.group("num"))
            return _next_number_with_prefix(owner, pre, width=width)
        pre = re.sub(r"[^A-Za-z0-9]+", "", candidate).upper() or "INV"
        return _next_number_with_prefix(owner, pre, width=6)

    return candidate


def _normalize_template_key(key: str) -> str:
    """
    Acepta: invoice_t1..t5 | t1..t5 | 'classic' -> 'invoice_t1'.
    Devuelve el nombre base SIN .html.
    """
    k = (key or "").strip().lower()
    if k.startswith("invoice_t"):
        return k
    m = re.fullmatch(r"t([1-5])", k)
    if m:
        return f"invoice_t{m.group(1)}"
    if k in {"classic", "default"}:
        return "invoice_t1"
    return "invoice_t1"


def _resolve_invoice_template(template_key: str):
    """
    Devuelve (template, tried_list). Usa la ruta correcta para app templates:
    'invoicing/invoice_templates/<name>.html'
    """
    name = _normalize_template_key(template_key) + ".html"
    candidates = [
        f"invoicing/invoice_templates/{name}",  # <-- donde dijiste que están
        "invoicing/invoice.html",               # fallback opcional unificado
    ]
    try:
        return select_template(candidates), candidates
    except Exception:
        return None, candidates


# ----------------- API -----------------

@login_required
@require_POST
def invoice_create_api(request):
    """
    Crea la Invoice, genera el PDF con WeasyPrint y lo sube a Wasabi (igual que el logo).
    Devuelve {ok, id, number, pdf_url} o {ok: False, error: "..."}.
    """
    data = json.loads(request.body.decode("utf-8") or "{}")

    # --- Datos base ---
    cust_id = data.get("customer_id")
    customer = get_object_or_404(Customer, id=cust_id)  # agrega filtro por owner si aplica

    issue_date = _parse_date_iso(data.get("issue_date")) or timezone.now().date()
    due_date   = _parse_date_iso(data.get("due_date"))
    items      = data.get("items") or []
    currency   = (data.get("currency_symbol") or "$").strip() or "$"
    tax_pct    = Decimal(str(data.get("tax_percent") or "0"))
    notes      = data.get("notes") or ""
    terms      = data.get("terms") or ""

    # Branding / template
    profile = None
    pid = data.get("profile_id")
    if pid:
        profile = BrandingProfile.objects.filter(owner=request.user, id=pid).first()
    template_key = data.get("template_key") or "invoice_t1"

    # --- Totales ---
    subtotal = Decimal("0.00")
    norm_items = []
    for it in items:
        qty  = Decimal(str(it.get("qty") or 0))
        rate = Decimal(str(it.get("rate") or 0))
        amt  = (qty if qty > 0 else Decimal("0")) * (rate if rate > 0 else Decimal("0"))
        subtotal += amt
        norm_items.append({
            "daily": (it.get("daily") or "").strip(),
            "job_code": (it.get("job_code") or "").strip(),
            "description": (it.get("description") or "").strip(),
            "qty": f"{qty:.2f}",
            "uom": (it.get("uom") or "").strip(),
            "rate": f"{rate:.2f}",
            "amount": f"{amt:.2f}",
        })

    tax_amount = (subtotal * (tax_pct / Decimal("100"))).quantize(Decimal("0.01"))
    total      = (subtotal + tax_amount).quantize(Decimal("0.01"))

    # --- Número único ---
    requested_number = (data.get("number") or "").strip()
    final_number = _ensure_unique_number(
        request.user, requested_number, profile=profile, customer=customer
    )

    # --- Resolver plantilla ---
    tpl, tried = _resolve_invoice_template(template_key)
    if not tpl:
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "Invoice template not found. Put your file en: "
                    + ", ".join(tried)
                ),
                "tried": tried,
            },
            status=400,
        )

    # --- Crear + PDF (Wasabi) ---
    with transaction.atomic():
        inv = Invoice.objects.create(
            owner=request.user,
            customer=customer,
            number=final_number,
            issue_date=issue_date,
            total=total,
            branding_profile=profile,
            template_key=_normalize_template_key(template_key),
            status=Invoice.STATUS_ISSUED,
        )

        ctx = {
            "invoice": {
                "number": final_number,
                "issue_date": issue_date,
                "due_date": due_date,
                "subtotal": subtotal,
                "tax_percent": tax_pct,
                "tax_amount": tax_amount,
                "total": total,
                "currency_symbol": currency,
                "notes": notes,
                "terms": terms,
                "status": inv.status,
            },
            "customer": customer,
            "profile": profile,
            "items": norm_items,
            "branding": None,
            "pdf_mode": True,  # tu template debe ocultar inputs/JS con esto
            "request": request,
        }

        html = tpl.render(ctx, request)
        pdf_bytes = HTML(
            string=html,
            base_url=request.build_absolute_uri("/")  # resuelve estáticos/URLs absolutas
        ).write_pdf()

        # Usa el storage del FileField (wasabi_storage), igual que el logo
        inv.pdf.save(f"invoice-{final_number}.pdf", ContentFile(pdf_bytes), save=True)

    return JsonResponse({"ok": True, "id": inv.id, "number": final_number, "pdf_url": inv.pdf.url})


# --- NEXT NUMBER API ---------------------------------------------------------
from django.views.decorators.http import require_GET


def _clean_prefix(raw: str, default: str = "INV") -> str:
    import re
    s = (raw or "").strip()
    s = re.sub(r"[^A-Za-z0-9]+", "", s).upper()
    return s or default

@login_required
@require_GET
def invoice_next_number_api(request):
    """
    Devuelve el siguiente número sugerido según el prefijo del cliente/perfil.
    GET params: ?customer_id=... | ?profile_id=...
    Respuesta: {"ok": True, "number": "ITG-000123"}
    """
    cust_id   = request.GET.get("customer_id")
    profile_id= request.GET.get("profile_id")

    customer = None
    profile  = None
    if cust_id:
        from .models import Customer
        customer = get_object_or_404(Customer, id=cust_id)
    if profile_id:
        from .models import BrandingProfile
        profile = BrandingProfile.objects.filter(owner=request.user, id=profile_id).first()

    # prefijo por prioridad: mnemonic cliente -> invoice_prefix perfil -> "INV"
    raw_prefix = getattr(customer, "mnemonic", None) or getattr(profile, "invoice_prefix", "") or "INV"
    prefix = _clean_prefix(raw_prefix, default="INV")

    number = _next_number_with_prefix(request.user, prefix, width=6)
    return JsonResponse({"ok": True, "number": number})