from datetime import timedelta
from rrhh.models import Feriado


def contar_dias_habiles(inicio, fin):
    dias_habiles = 0
    delta = fin - inicio
    for i in range(delta.days + 1):
        dia = inicio + timedelta(days=i)
        if dia.weekday() < 5 and not Feriado.objects.filter(fecha=dia).exists():
            dias_habiles += 1
    return dias_habiles
