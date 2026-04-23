from django.db import migrations, models
from django.utils.crypto import get_random_string


def fill_public_access_codes(apps, schema_editor):
    Ombording = apps.get_model("ombording", "Ombording")
    for obj in Ombording.objects.all():
        changed = False

        if not obj.link_token:
            obj.link_token = get_random_string(48)
            changed = True

        if not obj.public_access_code:
            obj.public_access_code = get_random_string(6, allowed_chars="0123456789")
            changed = True

        if changed:
            obj.save(update_fields=["link_token", "public_access_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("ombording", "0006_remove_ombording_address_ombording_apt_suite_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ombording",
            name="public_access_code",
            field=models.CharField(
                blank=True,
                max_length=12,
                verbose_name="Public Access Code",
            ),
        ),
        migrations.AddField(
            model_name="ombording",
            name="public_verified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Public Verified At",
            ),
        ),
        migrations.RunPython(fill_public_access_codes, migrations.RunPython.noop),
    ]
