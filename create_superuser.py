from django.contrib.auth.models import User
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mv_construcciones.settings')
django.setup()


username = "admin"
email = "luisggil01@gmail.com"
password = "TuPasswordSeguro"

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(
        username=username, email=email, password=password)
    print("Superuser creado")
else:
    print("Superuser ya existe")
