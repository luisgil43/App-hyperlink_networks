# invoicing/views_api.py

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.http import require_GET

from .models import Customer, ItemCode


@login_required
@require_GET
def api_customers(request):
    """
    Devuelve clientes para el selector del template.
    Ahora, además del customer, trae las variantes de precios (city / project)
    que existan en ItemCode para el mismo 'client' del customer.

    Respuesta:
    {
      "results": [
        {
          "id": ...,
          "name": ...,
          "mnemonic": ...,
          "street_1": ...,
          "city": ...,
          "state": ...,
          "zip_code": ...,
          "email": ...,
          "phone": ...,
          "client": "...",
          "variants": [
             {"city": "...", "project": "..."},
             ...
          ]
        },
        ...
      ]
    }
    """
    q = (request.GET.get("q") or "").strip()

    # tu código original: lista global de customers
    qs = Customer.objects.all().order_by("name")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(mnemonic__icontains=q)
            | Q(client__icontains=q)        # <- añadimos client al search
        )

    customers = list(qs[:25])

    # 1) recolectar los client de esos customers
    client_labels = [c.client for c in customers if c.client]

    # 2) buscar en ItemCode qué combinaciones reales hay para esos client
    variants_by_client = {}
    if client_labels:
        ic_qs = (
            ItemCode.objects
            .filter(client__in=client_labels)
            .values("client", "city", "project")
            .distinct()
        )
        for row in ic_qs:
            cli = row["client"] or ""
            variants_by_client.setdefault(cli, []).append(
                {
                    "city": row["city"] or "",
                    "project": row["project"] or "",
                }
            )

    # 3) armar respuesta
    results = []
    for c in customers:
        cli = c.client or ""
        results.append(
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
                "client": cli,
                # si este customer tiene itemcodes por ciudad/proyecto, aquí vienen
                "variants": variants_by_client.get(cli, []),
            }
        )

    return JsonResponse({"results": results})


@login_required
@require_GET
def api_itemcodes(request):
    """
    Autocomplete de Job Codes.
    - Filtra SIEMPRE por el cliente seleccionado (nombre exacto).
    - Puede filtrar también por city y project (vienen del modal).
    - Busca por job_code (startswith/contains) y por descripción.
    - Devuelve también city y project para poder mostrar en el dropdown.

    Parámetros esperados:
      ?q=...          texto que escribe el usuario
      ?client=...     (obligatorio en tu flujo actual)
      ?city=...       (opcional)
      ?project=...    (opcional)
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    q       = (request.GET.get("q") or "").strip()
    client  = (request.GET.get("client") or "").strip()
    city    = (request.GET.get("city") or "").strip()
    project = (request.GET.get("project") or "").strip()

    qs = ItemCode.objects.all()

    # Cliente: debe coincidir con el "client" del customer elegido
    if client:
      # usamos iexact para no depender de mayúsculas
        qs = qs.filter(client__iexact=client)

    # si el frontend ya eligió ciudad en el modal, filtramos más
    if city:
        qs = qs.filter(city__iexact=city)

    # si el frontend ya eligió proyecto, filtramos más
    if project:
        qs = qs.filter(project__iexact=project)

    if q:
        qs = qs.filter(
            Q(job_code__istartswith=q)
            | Q(job_code__icontains=q)
            | Q(description__icontains=q)
        )

    qs = qs.order_by("job_code", "city", "project")[:25]

    data = [
        {
            "job_code": it.job_code,
            "description": it.description,
            "uom": it.uom,
            "rate": float(it.rate or 0),
            "city": it.city or "",
            "project": it.project or "",
            "client": it.client or "",
        }
        for it in qs
    ]
    return JsonResponse({"results": data})