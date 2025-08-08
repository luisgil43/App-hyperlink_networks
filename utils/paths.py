import os
from uuid import uuid4
from django.utils import timezone
from django.utils.text import slugify


def upload_to(instance, filename):
    """
    - CartolaMovimiento -> facturacion/cartolamovimiento/<categoria>/<YYYY-MM-DD>/<base-unique>.<ext>
    - Genérico -> <proyecto>/<app>/<modelo>/<id|temp>/<archivo>
    """
    model_lower = getattr(instance._meta, "model_name", "").lower()

    # === Caso especial: CartolaMovimiento (sin depender del app_label) ===
    if model_lower == "cartolamovimiento":
        # Categoria desde el tipo (fallback 'otros')
        categoria = getattr(getattr(instance, "tipo", None),
                            "categoria", "otros") or "otros"
        categoria_slug = slugify(categoria, allow_unicode=True)

        # Fecha del movimiento si existe; si no, hoy (timezone-aware)
        dt = getattr(instance, "fecha", None)
        if dt:
            try:
                fecha = timezone.localdate(dt)
            except Exception:
                fecha = timezone.localdate()
        else:
            fecha = timezone.localdate()

        # Nombre único
        base, ext = os.path.splitext(filename)
        base_slug = slugify(base, allow_unicode=True) or "comprobante"
        unique = uuid4().hex[:8]

        return f"facturacion/cartolamovimiento/{categoria_slug}/{fecha:%Y-%m-%d}/{base_slug}-{unique}{ext.lower()}"

    # === Ruta genérica para TODO lo demás ===
    project = "hyperlink"  # nombre lógico del proyecto en el bucket hyperlink-networks
    app_name = instance._meta.app_label
    model_name = instance.__class__.__name__.lower()
    pk_or_temp = instance.pk or "temp"
    return f"{project}/{app_name}/{model_name}/{pk_or_temp}/{filename}"
