import json
import mimetypes
import os
import re
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage as StdEmailMessage
from email.utils import formatdate, make_msgid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import select_template
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from weasyprint import HTML

from .models import (BrandingProfile, BrandingSettings, Customer, Invoice,
                     ItemCode)
from .utils_branding import get_active_branding


@login_required
def invoices_list(request):
    qs = Invoice.objects.filter(owner=request.user).select_related("customer")

    # Si quedó algún 'issued' legacy, normaliza a pending
    Invoice.objects.filter(owner=request.user, status="issued").update(status=Invoice.STATUS_PENDING)

    # Auto: pasar a OVERDUE si corresponde
    today = timezone.localdate()
    Invoice.objects.filter(
        owner=request.user,
        status=Invoice.STATUS_PENDING,
        due_date__isnull=False,
        due_date__lt=today,
    ).update(status=Invoice.STATUS_OVERDUE)

    # Filtros
    q        = (request.GET.get("q") or "").strip()
    f_from   = (request.GET.get("from") or "").strip()
    f_to     = (request.GET.get("to") or "").strip()
    f_status = (request.GET.get("status") or "").strip()
    per      = (request.GET.get("per") or "10").lower()
    page     = int(request.GET.get("page") or 1)

    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(customer__name__icontains=q))

    def _parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    d_from = _parse_date(f_from)
    d_to   = _parse_date(f_to)
    if d_from: qs = qs.filter(issue_date__gte=d_from)
    if d_to:   qs = qs.filter(issue_date__lte=d_to)

    if f_status in {Invoice.STATUS_PENDING, Invoice.STATUS_OVERDUE, Invoice.STATUS_PAID, Invoice.STATUS_VOID}:
        qs = qs.filter(status=f_status)

    # Paginación
    pagina = None
    items  = None
    if per == "all":
        items = list(qs[:2000])
        per_page = "all"
    else:
        try: per_int = int(per)
        except Exception: per_int = 10
        paginator = Paginator(qs, per_int)
        pagina    = paginator.get_page(page)
        items     = pagina.object_list
        per_page  = str(per_int)

    ctx = {
        "page_title": "Invoices",
        "items": items,
        "pagina": pagina,
        "per_page": per_page,
        "q": q, "f_from": f_from, "f_to": f_to, "f_status": f_status,
    }
    return render(request, "invoicing/invoices_list.html", ctx)


@login_required
@require_POST
def invoice_set_status(request):
    iid = request.POST.get("id")
    st  = (request.POST.get("status") or "").strip()
    inv = get_object_or_404(Invoice, id=iid, owner=request.user)

    allowed = {Invoice.STATUS_PENDING, Invoice.STATUS_OVERDUE, Invoice.STATUS_PAID, Invoice.STATUS_VOID}
    if st not in allowed:
        return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)

    inv.status = st
    inv.save(update_fields=["status"])
    return JsonResponse({"ok": True})


@login_required
@require_POST
def invoice_delete(request):
    """Borrado real (hard delete) manteniendo el modal actual."""
    iid = request.POST.get("id")
    inv = get_object_or_404(Invoice, id=iid, owner=request.user)
    inv.delete()
    return JsonResponse({"ok": True})


@login_required
def invoice_compose_eml(request, iid: int):
    """
    Genera un .eml en modo BORRADOR (X-Unsent: 1), sin 'From' ni 'Date',
    con cuerpo texto+HTML, logo inline (si existe) y el PDF adjunto.
    No envía nada; el usuario elige la cuenta y el destinatario al abrirlo.
    """
    inv = get_object_or_404(Invoice, id=iid, owner=request.user)

    to = (request.GET.get("to") or getattr(inv.customer, "email", "") or "").strip()
    subject = (request.GET.get("subject") or f"Invoice {inv.number}").strip()

    text_body = (
        "Hello,\n\n"
        f"Please find the invoice {inv.number} attached.\n\n"
        "Regards,\n"
    )

    # ----- Logo inline (opcional) -----
    import mimetypes
    import os
    from email.utils import make_msgid
    logo_bytes = None
    logo_maintype, logo_subtype = "image", "png"
    logo_filename = "brand-logo.png"
    try:
        prof = inv.branding_profile
        if prof:
            for field in ("logo", "primary_logo", "square_logo", "image"):
                if hasattr(prof, field):
                    f = getattr(prof, field)
                    if f:
                        f.open("rb")
                        try:
                            logo_bytes = f.read()
                            logo_filename = os.path.basename(getattr(f, "name", logo_filename)) or logo_filename
                            guessed = mimetypes.guess_type(logo_filename)[0] or "image/png"
                            logo_maintype, logo_subtype = guessed.split("/", 1)
                        finally:
                            f.close()
                        break
    except Exception:
        logo_bytes = None

    logo_cid = make_msgid(domain="hyperlink.local")
    logo_cid_ref = logo_cid[1:-1]
    if logo_bytes:
        html_body = f"""
        <div style="font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;color:#111">
          <p>Hello,</p>
          <p>Please find the invoice <b>{inv.number}</b> attached.</p>
          <p style="margin:16px 0">
            <img src="cid:{logo_cid_ref}" alt="Brand logo" style="max-width:240px;height:auto;border:0;display:block"/>
          </p>
          <p>Regards,</p>
        </div>
        """
    else:
        html_body = f"""
        <div style="font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;font-size:14px;color:#111">
          <p>Hello,</p>
          <p>Please find the invoice <b>{inv.number}</b> attached.</p>
          <p>Regards,</p>
        </div>
        """

    # ----- PDF adjunto -----
    pdf_bytes = b""
    if inv.pdf:
        inv.pdf.open("rb")
        try:
            pdf_bytes = inv.pdf.read()
        finally:
            inv.pdf.close()
    else:
        # Fallback: render rápido si no hay PDF guardado
        tpl, _ = _resolve_invoice_template(inv.template_key or "invoice_t1")
        if tpl:
            from decimal import Decimal

            from weasyprint import HTML
            ctx = {
                "invoice": {
                    "number": inv.number,
                    "issue_date": inv.issue_date,
                    "due_date": inv.due_date,
                    "subtotal": Decimal("0.00"),
                    "tax_percent": Decimal("0"),
                    "tax_amount": Decimal("0.00"),
                    "total": inv.total,
                    "currency_symbol": "$",
                    "notes": "",
                    "terms": "",
                    "status": inv.status,
                },
                "customer": inv.customer,
                "profile": inv.branding_profile,
                "items": [],
                "branding": None,
                "pdf_mode": True,
                "request": request,
            }
            pdf_bytes = HTML(
                string=tpl.render(ctx, request),
                base_url=request.build_absolute_uri("/"),
            ).write_pdf()

    # ----- Construcción del .eml como BORRADOR -----
    msg = StdEmailMessage()
    msg["X-Unsent"] = "1"          # <- clave: lo abre como draft
    # No ponemos From ni Date
    if to:
        msg["To"] = to            # opcional: puedes dejarlo vacío para escribirlo al abrir
    msg["Subject"] = subject

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if logo_bytes:
        # adjuntamos el logo al 'text/html' como related
        html_part = None
        for part in msg.iter_parts():
            if part.get_content_type() == "text/html":
                html_part = part
                break
        if html_part is not None:
            html_part.add_related(
                logo_bytes,
                maintype=logo_maintype,
                subtype=logo_subtype,
                cid=logo_cid,
                filename=logo_filename,
            )

    if pdf_bytes:
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=f"Invoice-{inv.number}.pdf",
        )

    eml_bytes = msg.as_bytes()
    from django.http import HttpResponse
    resp = HttpResponse(eml_bytes, content_type="message/rfc822")
    resp["Content-Disposition"] = f'attachment; filename="Invoice-{inv.number}.eml"'
    return resp
# ---------- Prefill para duplicar (no emite) ----------
@login_required
@require_GET
def invoice_prefill_api(request):
    """
    Devuelve JSON con datos de una factura para precargar la pantalla de 'Nueva factura'.
    Prioridad: InvoiceLine -> JSONField Invoice.lines
    """
    iid = request.GET.get("id")
    inv = get_object_or_404(Invoice, id=iid, owner=request.user)

    items = []

    # 1) Si tienes un modelo de líneas real
    try:
        from .models import InvoiceLine  # si no existe, saltará a except
        for ln in InvoiceLine.objects.filter(invoice=inv).order_by("id"):
            items.append({
                "daily": ln.daily or "",
                "job_code": ln.job_code or "",
                "description": ln.description or "",
                "qty": str(getattr(ln, "qty", "") or ""),
                "uom": ln.uom or "",
                "rate": str(getattr(ln, "rate", "") or ""),
            })
    except Exception:
        pass

    # 2) Fallback: JSONField guardado en Invoice.lines
    if not items:
        raw = getattr(inv, "lines", None) or []
        for it in raw:
            items.append({
                "daily":       (it.get("daily") or ""),
                "job_code":    (it.get("job_code") or ""),
                "description": (it.get("description") or ""),
                "qty":         str(it.get("qty", "")),
                "uom":         (it.get("uom") or ""),
                "rate":        str(it.get("rate", "")),
            })

    data = {
    "customer_id": inv.customer_id,
    "customer_name": getattr(inv.customer, "name", ""),
    "customer": {  # <-- NUEVO: datos completos para pintar la tarjeta
        "id": inv.customer_id,
        "name": getattr(inv.customer, "name", "") or "",
        "street_1": getattr(inv.customer, "street_1", "") or "",
        "city": getattr(inv.customer, "city", "") or "",
        "state": getattr(inv.customer, "state", "") or "",
        "zip_code": getattr(inv.customer, "zip_code", "") or "",
        "email": getattr(inv.customer, "email", "") or "",
        "phone": getattr(inv.customer, "phone", "") or "",
         
    },
    "issue_date": inv.issue_date.isoformat(),  # ya lo estábamos enviando, si no estaba agrégalo
    "due_date": inv.due_date.isoformat() if inv.due_date else "",
    "currency_symbol": "$",
    "tax_percent": "0",

    "notes": getattr(inv, "notes", "") or "",
    "terms": getattr(inv, "terms", "") or "",
    "items": items,
}
    return JsonResponse({"ok": True, "prefill": data})


# ---------- Helpers usados por create/duplicate ----------
def _parse_date_iso(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _next_number_with_prefix(owner, prefix: str, width: int = 6) -> str:
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
    candidate = (number or "").strip()
    if not candidate or candidate.upper() == "AUTO":
        raw_prefix = (
            getattr(customer, "mnemonic", None)
            or getattr(profile, "invoice_prefix", "")
            or "INV"
        )
        import re as _re
        prefix = _re.sub(r"[^A-Za-z0-9]+", "", raw_prefix).upper() or "INV"
        return _next_number_with_prefix(owner, prefix, width=6)

    if Invoice.objects.filter(owner=owner, number=candidate).exists():
        m = re.match(r"^(?P<pre>.+?)-(?P<num>\d+)$", candidate)
        if m:
            pre = m.group("pre")
            width = len(m.group("num"))
            return _next_number_with_prefix(owner, pre, width=width)
        import re as _re
        pre = _re.sub(r"[^A-Za-z0-9]+", "", candidate).upper() or "INV"
        return _next_number_with_prefix(owner, pre, width=6)

    return candidate


def _normalize_template_key(key: str) -> str:
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
    name = _normalize_template_key(template_key) + ".html"
    candidates = [
        f"invoicing/invoice_templates/{name}",
        "invoicing/invoice.html",
    ]
    try:
        return select_template(candidates), candidates
    except Exception:
        return None, candidates


# ---------- Compose (se deja tal cual, solo agregamos contexto opcional) ----------
@login_required
def invoice_new(request):
    bs, _ = BrandingSettings.objects.get_or_create(owner=request.user)
    profile = None
    pid = request.GET.get("profile_id")
    if pid:
        profile = BrandingProfile.objects.filter(owner=request.user, id=pid).first()
    if not profile and bs.default_profile_id:
        profile = BrandingProfile.objects.filter(owner=request.user, id=bs.default_profile_id).first()

    branding = get_active_branding(request.user, profile.id if profile else None)
    chosen_key = (profile.template_key if profile and profile.template_key else "classic")

    preview_url = f'{reverse("invoicing:template_preview", kwargs={"key": chosen_key})}'
    if profile:
        preview_url += f"?profile_id={profile.id}"

    duplicate_id = (request.GET.get("duplicate_id") or "").strip()
    prefill_api = reverse("invoicing:api_invoice_prefill") + f"?id={duplicate_id}" if duplicate_id else ""

    return render(request, "invoicing/invoice_new.html", {
        "page_title": "New Invoice",
        "branding": branding,
        "preview_url": preview_url,
        "profile": profile,
        "prefill_from_id": duplicate_id,
        "prefill_api": prefill_api,
    })


# ---------- Crear (sin cambios funcionales salvo que emitimos en 'pending') ----------
@login_required
@require_POST
def invoice_create_api(request):
    data = json.loads(request.body.decode("utf-8") or "{}")

    cust_id = data.get("customer_id")
    customer = get_object_or_404(Customer, id=cust_id)

    issue_date = _parse_date_iso(data.get("issue_date")) or timezone.now().date()
    due_date   = _parse_date_iso(data.get("due_date"))
    items      = data.get("items") or []
    currency   = (data.get("currency_symbol") or "$").strip() or "$"
    tax_pct    = Decimal(str(data.get("tax_percent") or "0"))
    notes      = data.get("notes") or ""
    terms      = data.get("terms") or ""

    profile = None
    pid = data.get("profile_id")
    if pid:
        profile = BrandingProfile.objects.filter(owner=request.user, id=pid).first()
    template_key = data.get("template_key") or "invoice_t1"

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

    requested_number = (data.get("number") or "").strip()
    final_number = _ensure_unique_number(
        request.user, requested_number, profile=profile, customer=customer
    )

    tpl, _ = _resolve_invoice_template(template_key)
    if not tpl:
        return JsonResponse({"ok": False, "error": "Invoice template not found."}, status=400)

    with transaction.atomic():
        # 1) Crear la factura **guardando también notes/terms**
        inv = Invoice.objects.create(
            owner=request.user,
            customer=customer,
            number=final_number,
            issue_date=issue_date,
            due_date=due_date,
            total=total,
            branding_profile=profile,
            template_key=_normalize_template_key(template_key),
            status=Invoice.STATUS_PENDING,
            notes=notes,          # <-- agregado
            terms=terms,          # <-- agregado
        )

        # 2) Persistir líneas
        InvoiceLine = None
        try:
            from .models import InvoiceLine as _InvoiceLine
            InvoiceLine = _InvoiceLine
        except Exception:
            InvoiceLine = None

        if InvoiceLine:
            line_objs = []
            for it in items:
                qty  = Decimal(str(it.get("qty") or 0))
                rate = Decimal(str(it.get("rate") or 0))
                line_objs.append(InvoiceLine(
                    invoice=inv,
                    daily=(it.get("daily") or "").strip(),
                    job_code=(it.get("job_code") or "").strip(),
                    description=(it.get("description") or "").strip(),
                    qty=qty,
                    uom=(it.get("uom") or "").strip(),
                    rate=rate,
                ))
            if line_objs:
                InvoiceLine.objects.bulk_create(line_objs)
        else:
            if any(f.name == "lines" for f in Invoice._meta.get_fields()):
                inv.lines = norm_items
                inv.save(update_fields=["lines"])

        # 3) Generar y adjuntar PDF
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
                "notes": notes,   # ya se usan en PDF
                "terms": terms,   # ya se usan en PDF
                "status": inv.status,
            },
            "customer": customer,
            "profile": profile,
            "items": norm_items,
            "branding": None,
            "pdf_mode": True,
            "request": request,
        }

        pdf_bytes = HTML(string=tpl.render(ctx, request),
                         base_url=request.build_absolute_uri("/")).write_pdf()
        inv.pdf.save(f"invoice-{final_number}.pdf", ContentFile(pdf_bytes), save=True)

    return JsonResponse({"ok": True, "id": inv.id, "number": final_number, "pdf_url": inv.pdf.url})


@require_GET
@login_required
def invoice_next_number_api(request):
    cust_id   = request.GET.get("customer_id")
    profile_id= request.GET.get("profile_id")

    customer = None
    profile  = None
    if cust_id:
        customer = get_object_or_404(Customer, id=cust_id)
    if profile_id:
        profile = BrandingProfile.objects.filter(owner=request.user, id=profile_id).first()

    raw_prefix = getattr(customer, "mnemonic", None) or getattr(profile, "invoice_prefix", "") or "INV"
    import re as _re
    prefix = _re.sub(r"[^A-Za-z0-9]+", "", raw_prefix).upper() or "INV"

    number = _next_number_with_prefix(request.user, prefix, width=6)
    return JsonResponse({"ok": True, "number": number})