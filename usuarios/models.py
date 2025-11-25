import json
from datetime import date

import pyotp
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from utils.paths import upload_to  # 👈 Usamos la ruta dinámica

# ===== Labels en inglés para mostrar en formularios / documentos =====
# Mapeo interno -> etiqueta en inglés
ROLE_LABELS_EN = {
    "admin": "Admin",
    "pm": "Project Manager",
    "supervisor": "Supervisor",
    "rrhh": "HR",
    "prevencion": "Safety / Prevention",
    "logistica": "Logistics",
    "bodeguero": "Warehouse",
    "flota": "Fleet",
    "subcontrato": "Subcontract",
    "facturacion": "Billing / Finance",
    "usuario": "User",
    "usuario_historial": "History access",
    "emision_facturacion": "Invoice Issuance",
    
    
}


def get_role_label_en(internal_name: str) -> str:
    """
    Devuelve la etiqueta en inglés para un rol interno.
    Si no lo encontramos, devolvemos el nombre tal cual.
    """
    if not internal_name:
        return ""
    key = str(internal_name).strip().lower()
    return ROLE_LABELS_EN.get(key, internal_name)
class Rol(models.Model):
    nombre = models.CharField(max_length=30, unique=True)

    @property
    def label_en(self):
        # para usar como rol.label_en en templates
        return get_role_label_en(self.nombre)

    def __str__(self):
        return self.nombre
    
    
class CustomUser(AbstractUser):
    identidad = models.CharField(max_length=20, blank=True, null=True)
    roles = models.ManyToManyField("usuarios.Rol", blank=True)
    proyectos = models.ManyToManyField('facturacion.Proyecto',through='ProyectoAsignacion',related_name='usuarios',blank=True)
    # Firma digital → ahora en Wasabi
    firma_digital = models.ImageField(
        upload_to=upload_to,
        blank=True,
        null=True
    )

    # Responsables jerárquicos
    supervisor = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='supervisados')
    pm = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='pms')
    rrhh_encargado = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='rrhhs')
    prevencionista = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='prevencionistas')
    logistica_encargado = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='logisticas')
    es_bodeguero_encargado = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='bodeguero')
    encargado_flota = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='flotas')
    encargado_subcontrato = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='subcontratos')
    encargado_facturacion = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='facturaciones')

    # Días consumidos externamente
    dias_vacaciones_consumidos = models.DecimalField(
        max_digits=6, decimal_places=2, default=0,
        help_text="Días de vacaciones que ya ha consumido fuera del sistema"
    )

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"

    def tiene_rol(self, *roles):
        """
        Devuelve True si el usuario tiene AL MENOS uno de los roles indicados.
        Acepta 1 o N strings, o un iterable (lista/tupla/conjunto) con roles.

        Ejemplos:
            user.tiene_rol("admin")
            user.tiene_rol("admin", "pm", "facturacion")
            user.tiene_rol(["admin", "pm"])
        El superuser siempre devuelve True.
        """
        if self.is_superuser:
            return True

        # Aceptar iterable como único argumento (["admin","pm"])
        if len(roles) == 1 and isinstance(roles[0], (list, tuple, set)):
            roles = tuple(roles[0])

        roles_busqueda = {str(r).strip().lower() for r in roles if r}
        if not roles_busqueda:
            return False

        roles_usuario = {r.nombre.strip().lower() for r in self.roles.all()}
        # Intersección no vacía => tiene algún rol solicitado
        return not roles_usuario.isdisjoint(roles_busqueda)

    @property
    def es_usuario(self):
        return self.tiene_rol('usuario') or self.is_superuser

    @property
    def es_supervisor(self):
        return self.tiene_rol('supervisor') or self.is_superuser

    @property
    def es_pm(self):
        return self.tiene_rol('pm') or self.is_superuser

    @property
    def es_rrhh(self):
        return self.tiene_rol('rrhh') or self.is_superuser

    @property
    def es_prevencion(self):
        return self.tiene_rol('prevencion') or self.is_superuser

    @property
    def es_logistica(self):
        return self.tiene_rol('logistica') or self.is_superuser

    @property
    def es_bodeguero(self):
        return self.tiene_rol('bodeguero') or self.is_superuser

    @property
    def es_flota(self):
        return self.tiene_rol('flota') or self.is_superuser

    @property
    def es_subcontrato(self):
        return self.tiene_rol('subcontrato') or self.is_superuser

    @property
    def es_facturacion(self):
        return self.tiene_rol('facturacion') or self.is_superuser
    @property
    def es_emision_facturacion(self):
        return self.tiene_rol('emision_facturacion') or self.is_superuser

    @property
    def es_admin_general(self):
        return self.tiene_rol('admin') or self.is_superuser

    @property
    def es_usuario_historial(self):
        return self.tiene_rol('usuario_historial') or self.is_superuser
    
    
    
    @property
    def rol(self):
        primer_rol = self.roles.first()
        return primer_rol.nombre if primer_rol else None

    def obtener_dias_vacaciones_disponibles(self):
        from rrhh.models import ContratoTrabajo, SolicitudVacaciones
        contrato = ContratoTrabajo.objects.filter(
            tecnico=self).order_by('fecha_inicio').first()
        if not contrato or not contrato.fecha_inicio:
            return 0
        dias_trabajados = (date.today() - contrato.fecha_inicio).days
        dias_generados = dias_trabajados * 0.04166
        dias_consumidos_manualmente = float(
            self.dias_vacaciones_consumidos or 0)
        dias_aprobados = SolicitudVacaciones.objects.filter(
            usuario=self, estatus='aprobada'
        ).aggregate(total=Sum('dias_solicitados'))['total'] or 0
        total_disponible = dias_generados - \
            dias_consumidos_manualmente - float(dias_aprobados)
        return round(total_disponible, 2)
    
    two_factor_enabled = models.BooleanField(
        default=False,
        help_text="Si está activo, el usuario debe ingresar un código TOTP al iniciar sesión."
    )
    two_factor_secret = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Secreto base32 usado para generar códigos TOTP (Google Authenticator, etc.)."
    )

    def get_or_create_two_factor_secret(self):
        """
        Genera (si hace falta) y retorna el secreto TOTP del usuario.
        """
        if not self.two_factor_secret:
            # pyotp recomienda base32
            self.two_factor_secret = pyotp.random_base32()
            self.save(update_fields=["two_factor_secret"])
        return self.two_factor_secret
    
    def reset_two_factor(self):
        """
        Resetea completamente el 2FA del usuario:
          - Desactiva el flag
          - Limpia el secreto
          - Elimina dispositivos de confianza
        """
        from .models import \
            TrustedDevice  # evitar import circular si hace falta

        self.two_factor_enabled = False
        self.two_factor_secret = ""
        self.save(update_fields=["two_factor_enabled", "two_factor_secret"])

        TrustedDevice.objects.filter(user=self).delete()


    def __str__(self):
        nombre = self.get_full_name() or self.username
        return f"{self.identidad or 'Sin RUT'} - {nombre}"


class FirmaRepresentanteLegal(models.Model):
    archivo = models.FileField(
        upload_to=upload_to,
        verbose_name="Firma del Representante Legal"
    )
    fecha_subida = models.DateTimeField(null=False, blank=False)

    def __str__(self):
        return f"Firma representante legal (ID: {self.pk})"

class ProyectoAsignacion(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='proyectoasignacion_set'
    )
    proyecto = models.ForeignKey(
        'facturacion.Proyecto',
        on_delete=models.CASCADE,
        related_name='asignaciones'
    )
    # Visibilidad
    include_history = models.BooleanField(default=True)
    start_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('usuario', 'proyecto')

    def __str__(self):
        return f'{self.usuario.username} → {self.proyecto.nombre}'
    
class Notificacion(models.Model):
    usuario = models.ForeignKey(
        'usuarios.CustomUser', on_delete=models.CASCADE)
    mensaje = models.TextField()
    url = models.URLField(null=True, blank=True)
    tipo = models.CharField(max_length=20, default='info')
    leido = models.BooleanField(default=False)
    fecha = models.DateTimeField(auto_now_add=True)
    para_roles = models.TextField(null=True, blank=True)  # Antes era JSONField

    def roles_lista(self):
        try:
            return json.loads(self.para_roles or '[]')
        except json.JSONDecodeError:
            return []

    class Meta:
        ordering = ['-fecha']


class TrustedDevice(models.Model):
    """
    Dispositivo marcado como confiable para no pedir 2FA durante un tiempo.
    Se vincula a un usuario y se identifica por un token almacenado en cookie.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trusted_devices",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    user_agent = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        verbose_name = "Trusted device"
        verbose_name_plural = "Trusted devices"

    def is_valid(self):
        return self.expires_at > timezone.now()

    def __str__(self):
        return f"{self.user.username} - {self.token[:8]}..."