# (es) Vista mínima para no romper, UI en inglés como pediste
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


@login_required
def customers_list(request):
    return HttpResponse("Customers — placeholder")