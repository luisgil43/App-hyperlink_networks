from django.db.models.signals import post_save
from django.dispatch import receiver
from usuarios.models import CustomUser
from rrhh.models import FichaIngreso


@receiver(post_save, sender=CustomUser)
def asociar_ficha_al_crear_usuario(sender, instance, created, **kwargs):
    if instance.identidad:
        print(f"🟡 Buscando ficha con rut = {instance.identidad}")
        ficha = FichaIngreso.objects.filter(
            usuario__isnull=True, rut=instance.identidad).first()
        if ficha:
            ficha.usuario = instance
            ficha.save()
            print(f"🟢 Ficha asociada al usuario {instance.username}")
        else:
            print("🔴 No se encontró ficha con ese rut.")
