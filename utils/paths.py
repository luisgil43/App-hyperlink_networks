def upload_to(instance, filename):
    """
    Genera una ruta dinámica para guardar archivos en Wasabi.
    Estructura: <proyecto>/<app>/<modelo>/<id>/<archivo>
    Ejemplo: hyperlink/rrhh/contract/23/contrato.pdf
    """
    project = "hyperlink"  # Identificador del proyecto
    app_name = instance._meta.app_label
    model_name = instance.__class__.__name__.lower()
    return f"{project}/{app_name}/{model_name}/{instance.pk or 'temp'}/{filename}"
