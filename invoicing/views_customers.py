from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, Paginator
from django.db import IntegrityError, models
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import Customer  # <-- modelo sólo en models.py


@login_required
def customers_list(request):
    q        = (request.GET.get("q") or "").strip()
    state    = (request.GET.get("state") or "").strip().upper()
    status   = (request.GET.get("status") or "active").strip().lower()
    per_page = int(request.GET.get("per_page") or request.GET.get("cantidad") or 10)
    page     = int(request.GET.get("page") or 1)

    # 1) Construimos un queryset base con los filtros que afectan la lista
    base_qs = Customer.objects.all()
    if status in ("active", "archived"):
        base_qs = base_qs.filter(status=status)
    if q:
        base_qs = base_qs.filter(
            models.Q(name__icontains=q)
            | models.Q(email__icontains=q)
            | models.Q(phone__icontains=q)
            | models.Q(mnemonic__icontains=q)   # <-- ya estaba
            | models.Q(client__icontains=q)    # <-- NEW: buscar también por "client"
        )

    # 2) Estados disponibles SOLO de lo que existe en DB (según q/status)
    states_list = (
        base_qs.exclude(state="")
               .values_list("state", flat=True)
               .distinct()
               .order_by("state")
    )

    # 3) Ahora aplicamos el filtro por state (si lo hay) para la tabla
    qs = base_qs
    if state:
        qs = qs.filter(state__iexact=state)

    paginator = Paginator(qs, per_page)
    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages or 1)

    ctx = {
        "page_title": "Customers",
        "page_obj": page_obj,
        "paginator": paginator,
        "q": q,
        "state": state,
        "status": status,
        "per_page": per_page,
        "states_list": states_list,  # <-- NUEVO: úsalo en la plantilla
    }
    return render(request, "invoicing/customers_list.html", ctx)


@login_required
def customers_create(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    # --- Required fields (all except phone) ---
    def _req(name, label):
        val = (request.POST.get(name) or "").strip()
        if not val:
            raise ValueError(f"{label} is required.")
        return val

    try:
        name      = _req("name", "Name")
        mnemonic  = _req("mnemonic", "Mnemonic").upper()
        client   = _req("client", "Client")
        email     = _req("email", "Email")
        street_1  = _req("street_1", "Street Address")
        city      = _req("city", "City")
        state     = _req("state", "State").upper()
        zip_code  = _req("zip_code", "ZIP")
        status_in = _req("status", "Status").lower()
        if status_in not in (Customer.STATUS_ACTIVE, Customer.STATUS_ARCHIVED):
            return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    # phone es opcional
    phone = (request.POST.get("phone") or "").strip()

    try:
        c = Customer.objects.create(
            name=name,
            mnemonic=mnemonic,
            client=client,          # <-- NEW
            email=email,
            phone=phone,
            street_1=street_1,
            city=city,
            state=state,
            zip_code=zip_code,
            status=status_in,
        )
    except IntegrityError:
        return JsonResponse({"ok": False, "error": "Mnemonic already exists."}, status=409)

    return JsonResponse({"ok": True, "id": c.id})


@login_required
def customers_detail(request):
    cid = request.GET.get("id")
    c = get_object_or_404(Customer, pk=cid)
    data = {
        "id": c.id,
        "mnemonic": c.mnemonic or "",
        "client": c.client or "",   # <-- NEW (ya lo traías, lo dejamos)
        "name": c.name,
        "email": c.email or "",
        "phone": c.phone or "",
        "street_1": c.street_1 or "",
        "city": c.city or "",
        "state": c.state or "",
        "zip_code": c.zip_code or "",
        "status": c.status,
    }
    return JsonResponse({"ok": True, "customer": data})


@login_required
def customers_update(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    cid = request.POST.get("id")
    c = get_object_or_404(Customer, pk=cid)

    # --- Required validations (all except phone) ---
    def _req(name, label, default=""):
        val = (request.POST.get(name) or default).strip()
        if not val:
            raise ValueError(f"{label} is required.")
        return val

    try:
        c.name     = _req("name", "Name", c.name)
        c.mnemonic = _req("mnemonic", "Mnemonic", c.mnemonic or "").upper()
        c.client  = _req("client", "Client", c.client or "")
        c.email    = _req("email", "Email", c.email or "")
        c.street_1 = _req("street_1", "Street Address", c.street_1 or "")
        c.city     = _req("city", "City", c.city or "")
        c.state    = _req("state", "State", c.state or "").upper()
        c.zip_code = _req("zip_code", "ZIP", c.zip_code or "")
        status_in  = _req("status", "Status", c.status or Customer.STATUS_ACTIVE).lower()
        if status_in not in (Customer.STATUS_ACTIVE, Customer.STATUS_ARCHIVED):
            return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
        c.status   = status_in
    except ValueError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)

    # phone opcional
    c.phone = (request.POST.get("phone") or "").strip()

    try:
        c.save()
    except IntegrityError:
        return JsonResponse({"ok": False, "error": "Mnemonic already exists."}, status=409)

    return JsonResponse({"ok": True})


@login_required
def customers_delete(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cid = request.POST.get("id")
    c = get_object_or_404(Customer, pk=cid)
    c.delete()
    return JsonResponse({"ok": True})