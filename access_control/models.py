from django.db import models


class AccessPermission(models.Model):
    """
    Catálogo de permisos configurables desde Access Matrix.
    Ejemplo:
      billing.view_technical_amounts
      billing.view_company_amounts
    """

    key = models.CharField(max_length=120, unique=True)
    label = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    module = models.CharField(max_length=80, default="General")
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["module", "order", "label"]
        verbose_name = "Access permission"
        verbose_name_plural = "Access permissions"

    def __str__(self):
        return f"{self.module} - {self.label}"


class RoleAccessPermission(models.Model):
    """
    Relación entre un rol del sistema y un permiso.
    """

    permission = models.ForeignKey(
        AccessPermission,
        on_delete=models.CASCADE,
        related_name="role_permissions",
    )
    role_name = models.CharField(max_length=50, db_index=True)
    enabled = models.BooleanField(default=False)

    class Meta:
        unique_together = ("permission", "role_name")
        ordering = ["permission__module", "permission__order", "role_name"]
        verbose_name = "Role access permission"
        verbose_name_plural = "Role access permissions"

    def save(self, *args, **kwargs):
        if self.role_name:
            self.role_name = self.role_name.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        status = "enabled" if self.enabled else "disabled"
        return f"{self.role_name} -> {self.permission.key} ({status})"
