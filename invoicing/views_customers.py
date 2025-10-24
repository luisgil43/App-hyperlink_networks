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
            | models.Q(mnemonic__icontains=q)   # <-- NUEVO
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

    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)

    # NUEVO: normalizamos el nemónico (opcional, mayúsculas)
    raw_mn = (request.POST.get("mnemonic") or "").strip()
    mnemonic = raw_mn.upper() or None

    try:
        c = Customer.objects.create(
            name     = name,
            email    = (request.POST.get("email") or "").strip() or None,
            phone    = (request.POST.get("phone") or "").strip(),
            street_1 = (request.POST.get("street_1") or "").strip(),
            city     = (request.POST.get("city") or "").strip(),
            state    = (request.POST.get("state") or "").strip().upper(),
            zip_code = (request.POST.get("zip_code") or "").strip(),
            status   = Customer.STATUS_ACTIVE,
            mnemonic = mnemonic,   # <-- NUEVO
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
        "mnemonic": c.mnemonic or "",   # <-- NUEVO
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

    c.name     = (request.POST.get("name") or "").strip() or c.name
    c.email    = (request.POST.get("email") or "").strip() or None
    c.phone    = (request.POST.get("phone") or "").strip()
    c.street_1 = (request.POST.get("street_1") or "").strip()
    c.city     = (request.POST.get("city") or "").strip()
    c.state    = (request.POST.get("state") or "").strip().upper()
    c.zip_code = (request.POST.get("zip_code") or "").strip()

    # NUEVO: nemónico (None si viene vacío)
    raw_mn = (request.POST.get("mnemonic") or "").strip()
    c.mnemonic = raw_mn.upper() or None

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