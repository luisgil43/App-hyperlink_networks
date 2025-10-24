# invoicing/views_api.py
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

# 👇 Importa los modelos reales desde invoicing.models
from .models import Customer, ItemCode


@login_required
@require_GET
def api_customers(request):
    """
    Devuelve hasta 25 clientes para el selector del template.
    Filtra por name/email/phone/mnemonic con ?q=
    """
    q = (request.GET.get("q") or "").strip()

    qs = Customer.objects.all().order_by("name")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(mnemonic__icontains=q)
        )

    results = [
        {
            "id": c.id,
            "name": c.name or "",
            "mnemonic": c.mnemonic or "",
            "street_1": c.street_1 or "",
            "city": c.city or "",
            "state": c.state or "",
            "zip_code": c.zip_code or "",
            "email": c.email or "",
            "phone": c.phone or "",
        }
        for c in qs[:25]
    ]
    return JsonResponse({"results": results})



from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse

from .models import ItemCode


@login_required
@require_GET
def api_itemcodes(request):
    """
    Autocomplete de Job Codes.
    - Filtra SIEMPRE por el cliente seleccionado (nombre exacto).
    - Busca por job_code (startswith/contains) y por descripción.
    - Devuelve también la 'city' para desambiguar códigos repetidos.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    q = (request.GET.get("q") or "").strip()
    client = (request.GET.get("client") or "").strip()

    qs = ItemCode.objects.all()

    # Cliente: debe coincidir con el nombre EXACTO del Customer seleccionado
    if client:
        qs = qs.filter(client__iexact=client)

    if q:
        qs = qs.filter(
            Q(job_code__istartswith=q) |
            Q(job_code__icontains=q)   |
            Q(description__icontains=q)
        )

    qs = qs.order_by("job_code", "city")[:25]
    data = list(qs.values("job_code", "description", "uom", "rate", "city"))
    return JsonResponse({"results": data})